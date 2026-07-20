# Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add privacy-safe JSONL traces, model/tool spans, Token and configurable cost statistics, structured diagnostic logs, and trace query commands for each `ask` call and `chat` turn.

**Architecture:** `ModelGateway` attaches normalized usage to API-neutral outcomes, while an explicitly passed `TraceRecorder` observes Agent model/tool boundaries without changing `ToolRegistry`. The CLI owns per-turn recorder lifecycle and appends completed immutable records through `TraceStore`; structured stderr logs remain separate from the stable JSONL trace schema.

**Tech Stack:** Python 3.10+, dataclasses, `Decimal`, standard-library `json`/`logging`/`time`, Typer, OpenAI Python SDK boundary, pytest, uv.

## Global Constraints

- Persist traces only at `<workspace>/.cdy-agent/traces.jsonl`, one versioned UTF-8 JSON object per line.
- Never store or log prompts, model reply text, tool arguments, confirmation text, tool results, API keys, complete environment variables, or exception messages.
- `ask` creates one trace per actual Agent run; `chat` creates one trace per actual user turn and records its session ID; blank input and exit/EOF create none.
- `CDY_AGENT_INPUT_COST_PER_MILLION` and `CDY_AGENT_OUTPUT_COST_PER_MILLION` must both be absent or both be non-negative decimals.
- `CDY_AGENT_LOG_LEVEL` accepts exactly `DEBUG`, `INFO`, `WARNING`, or `ERROR`, defaulting to `WARNING`.
- Observability construction or persistence failures warn on stderr but never replace the primary Agent result or error.
- Do not add filtering, pagination, deletion, rotation, cleanup, concurrent-writer guarantees, built-in model prices, external telemetry, configuration files, streaming, or evaluation framework support.
- Tests must use temporary workspaces and fake SDK/gateway objects without network or real credentials.

---

## File Structure

- Create `src/cdy_agent/observability/__init__.py`: public observability exports.
- Create `src/cdy_agent/observability/models.py`: immutable usage, cost, span, and trace records plus strict JSON conversion.
- Create `src/cdy_agent/observability/pricing.py`: paired environment-price parsing and exact cost calculation.
- Create `src/cdy_agent/observability/recorder.py`: one-turn trace/span lifecycle and aggregation.
- Create `src/cdy_agent/observability/store.py`: append-only JSONL persistence and strict queries.
- Create `src/cdy_agent/observability/logging.py`: safe JSON stderr diagnostics and log-level parsing.
- Modify `src/cdy_agent/openai_client.py`: attach normalized usage to every model outcome.
- Modify `src/cdy_agent/agent.py`: record model and tool spans through an optional per-run recorder.
- Modify `src/cdy_agent/cli.py`: create/save per-turn traces and expose `traces list/show`.
- Modify `README.md`: document trace location, query commands, price configuration, logging, and privacy boundary.
- Create `tests/test_observability_models.py`, `tests/test_observability_pricing.py`, `tests/test_trace_recorder.py`, `tests/test_trace_store.py`, and `tests/test_observability_logging.py`.
- Modify `tests/test_openai_client.py`, `tests/test_agent.py`, and `tests/test_cli.py` for integration and regression coverage.

### Task 1: Immutable usage, trace, and pricing domain

**Files:**
- Create: `src/cdy_agent/observability/__init__.py`
- Create: `src/cdy_agent/observability/models.py`
- Create: `src/cdy_agent/observability/pricing.py`
- Create: `tests/test_observability_models.py`
- Create: `tests/test_observability_pricing.py`

**Interfaces:**
- Produces: `TokenUsage(input_tokens: int, output_tokens: int)` with computed `total_tokens` and `to_dict()`.
- Produces: `EstimatedCost(input_cost: Decimal, output_cost: Decimal)` with computed `total_cost` and stable string JSON values.
- Produces: immutable `ModelCallSpan`, `ToolCallSpan`, and `TraceRecord` with `to_dict()` and `TraceRecord.from_dict()`.
- Produces: `Pricing(input_per_million: Decimal, output_per_million: Decimal)`, `resolve_pricing() -> Pricing | None`, and `estimate_cost(usage, pricing) -> EstimatedCost | None`.

- [ ] **Step 1: Write failing model and strict round-trip tests**

```python
# tests/test_observability_models.py
from decimal import Decimal

import pytest

from cdy_agent.observability.models import (
    EstimatedCost,
    ModelCallSpan,
    TokenUsage,
    ToolCallSpan,
    TraceRecord,
)


def sample_trace() -> TraceRecord:
    usage = TokenUsage(10, 4)
    return TraceRecord(
        schema_version=1,
        trace_id="52c809c6-6e55-4ff1-9220-e4f90a4f6774",
        started_at="2026-07-20T08:30:00.000000Z",
        duration_ms=15,
        command="chat",
        status="succeeded",
        model="test-model",
        api_mode="responses",
        session_id="f8605a17-cf86-46ce-87ad-7db57533e5dc",
        error_type=None,
        usage=usage,
        estimated_cost=EstimatedCost(Decimal("0.000010"), Decimal("0.000008")),
        model_calls=(ModelCallSpan("0cebd5c2-7d4c-4655-a997-f31e05eb74a5", 1, 8, "succeeded", None, usage),),
        tool_calls=(ToolCallSpan("89be39ea-9485-49f1-977f-70d5e663cf3d", 1, "read_file", 3, "succeeded", None),),
    )


def test_trace_round_trip_uses_stable_json_values() -> None:
    record = sample_trace()
    payload = record.to_dict()
    assert payload["usage"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert payload["estimated_cost"] == {"input_cost": "0.000010", "output_cost": "0.000008", "total_cost": "0.000018"}
    assert TraceRecord.from_dict(payload) == record


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"trace_id": "short"},
        {"duration_ms": -1},
        {"command": "eval"},
        {"status": "running"},
        {"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 99}},
    ],
)
def test_trace_rejects_invalid_or_inconsistent_payload(change: dict[str, object]) -> None:
    payload = sample_trace().to_dict()
    payload.update(change)
    with pytest.raises(ValueError):
        TraceRecord.from_dict(payload)
```

- [ ] **Step 2: Run model tests to verify they fail**

Run: `uv run pytest tests/test_observability_models.py -q`

Expected: FAIL during collection because `cdy_agent.observability` does not exist.

- [ ] **Step 3: Implement immutable models and strict conversion**

```python
# src/cdy_agent/observability/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a complete UUID.")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError(f"{field} must be a complete UUID.") from None
    if str(parsed) != value:
        raise ValueError(f"{field} must be a canonical UUID.")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        _non_negative_int(self.input_tokens, "input_tokens")
        _non_negative_int(self.output_tokens, "output_tokens")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "total_tokens": self.total_tokens}

    @classmethod
    def from_dict(cls, value: object) -> "TokenUsage":
        if not isinstance(value, dict) or set(value) != {"input_tokens", "output_tokens", "total_tokens"}:
            raise ValueError("usage has an invalid shape.")
        usage = cls(_non_negative_int(value["input_tokens"], "input_tokens"), _non_negative_int(value["output_tokens"], "output_tokens"))
        if value["total_tokens"] != usage.total_tokens:
            raise ValueError("total_tokens is inconsistent.")
        return usage


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class EstimatedCost:
    input_cost: Decimal
    output_cost: Decimal

    def __post_init__(self) -> None:
        if any(not value.is_finite() or value < 0 for value in (self.input_cost, self.output_cost)):
            raise ValueError("estimated costs must be finite and non-negative.")

    @property
    def total_cost(self) -> Decimal:
        return self.input_cost + self.output_cost

    def to_dict(self) -> dict[str, str]:
        return {"input_cost": _decimal_text(self.input_cost), "output_cost": _decimal_text(self.output_cost), "total_cost": _decimal_text(self.total_cost)}

    @classmethod
    def from_dict(cls, value: object) -> "EstimatedCost":
        if not isinstance(value, dict) or set(value) != {"input_cost", "output_cost", "total_cost"}:
            raise ValueError("estimated_cost has an invalid shape.")
        try:
            result = cls(Decimal(value["input_cost"]), Decimal(value["output_cost"]))
            total = Decimal(value["total_cost"])
        except (TypeError, ArithmeticError):
            raise ValueError("estimated_cost contains invalid decimals.") from None
        if min(result.input_cost, result.output_cost) < 0 or total != result.total_cost:
            raise ValueError("estimated_cost is inconsistent.")
        return result


@dataclass(frozen=True)
class ModelCallSpan:
    span_id: str
    sequence: int
    duration_ms: int
    status: str
    error_type: str | None
    usage: TokenUsage | None

    def to_dict(self) -> dict[str, Any]:
        return {"span_id": self.span_id, "sequence": self.sequence, "duration_ms": self.duration_ms, "status": self.status, "error_type": self.error_type, "usage": None if self.usage is None else self.usage.to_dict()}


@dataclass(frozen=True)
class ToolCallSpan:
    span_id: str
    sequence: int
    tool_name: str
    duration_ms: int
    status: str
    error_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"span_id": self.span_id, "sequence": self.sequence, "tool_name": self.tool_name, "duration_ms": self.duration_ms, "status": self.status, "error_type": self.error_type}


@dataclass(frozen=True)
class TraceRecord:
    schema_version: int
    trace_id: str
    started_at: str
    duration_ms: int
    command: str
    status: str
    model: str
    api_mode: str
    session_id: str | None
    error_type: str | None
    usage: TokenUsage | None
    estimated_cost: EstimatedCost | None
    model_calls: tuple[ModelCallSpan, ...]
    tool_calls: tuple[ToolCallSpan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "trace_id": self.trace_id, "started_at": self.started_at, "duration_ms": self.duration_ms, "command": self.command, "status": self.status, "model": self.model, "api_mode": self.api_mode, "session_id": self.session_id, "error_type": self.error_type, "usage": None if self.usage is None else self.usage.to_dict(), "estimated_cost": None if self.estimated_cost is None else self.estimated_cost.to_dict(), "model_calls": [span.to_dict() for span in self.model_calls], "tool_calls": [span.to_dict() for span in self.tool_calls]}

    @classmethod
    def from_dict(cls, value: object) -> "TraceRecord":
        payload = _exact_dict(value, {
            "schema_version", "trace_id", "started_at", "duration_ms", "command",
            "status", "model", "api_mode", "session_id", "error_type", "usage",
            "estimated_cost", "model_calls", "tool_calls",
        }, "trace")
        if payload["schema_version"] != 1:
            raise ValueError("Unsupported trace schema version.")
        started_at = _utc_timestamp(payload["started_at"])
        command = _choice(payload["command"], {"ask", "chat"}, "command")
        status = _choice(payload["status"], {"succeeded", "failed"}, "status")
        model = _non_empty_string(payload["model"], "model")
        api_mode = _choice(payload["api_mode"], {"responses", "chat_completions"}, "api_mode")
        session_id = None if payload["session_id"] is None else _uuid(payload["session_id"], "session_id")
        if (command == "ask") != (session_id is None):
            raise ValueError("session_id does not match command.")
        error_type = _error_type(payload["error_type"], status)
        usage = None if payload["usage"] is None else TokenUsage.from_dict(payload["usage"])
        cost = None if payload["estimated_cost"] is None else EstimatedCost.from_dict(payload["estimated_cost"])
        if usage is None and cost is not None:
            raise ValueError("estimated_cost requires usage.")
        if not isinstance(payload["model_calls"], list) or not isinstance(payload["tool_calls"], list):
            raise ValueError("trace spans must be arrays.")
        model_calls = tuple(_model_span(item) for item in payload["model_calls"])
        tool_calls = tuple(_tool_span(item) for item in payload["tool_calls"])
        if [span.sequence for span in model_calls] != list(range(1, len(model_calls) + 1)):
            raise ValueError("model call sequence is invalid.")
        if [span.sequence for span in tool_calls] != list(range(1, len(tool_calls) + 1)):
            raise ValueError("tool call sequence is invalid.")
        known = [span.usage for span in model_calls if span.usage is not None]
        aggregate = None if not known else TokenUsage(
            sum(item.input_tokens for item in known),
            sum(item.output_tokens for item in known),
        )
        if usage != aggregate:
            raise ValueError("trace usage is inconsistent with model calls.")
        return cls(1, _uuid(payload["trace_id"], "trace_id"), started_at,
                   _non_negative_int(payload["duration_ms"], "duration_ms"),
                   command, status, model, api_mode, session_id, error_type, usage,
                   cost, model_calls, tool_calls)


def _exact_dict(value: object, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{field} has an invalid shape.")
    return value


def _choice(value: object, choices: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{field} has an invalid value.")
    return value


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty.")
    return value


def _utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("started_at must be a UTC timestamp.")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError("started_at must be a UTC timestamp.") from None
    return value


def _error_type(value: object, status: str) -> str | None:
    if status == "succeeded":
        if value is not None:
            raise ValueError("successful records cannot have error_type.")
        return None
    return _non_empty_string(value, "error_type")


def _model_span(value: object) -> ModelCallSpan:
    payload = _exact_dict(value, {"span_id", "sequence", "duration_ms", "status", "error_type", "usage"}, "model span")
    status = _choice(payload["status"], {"succeeded", "failed"}, "status")
    usage = None if payload["usage"] is None else TokenUsage.from_dict(payload["usage"])
    return ModelCallSpan(_uuid(payload["span_id"], "span_id"),
                         _positive_int(payload["sequence"], "sequence"),
                         _non_negative_int(payload["duration_ms"], "duration_ms"),
                         status, _error_type(payload["error_type"], status), usage)


def _tool_span(value: object) -> ToolCallSpan:
    payload = _exact_dict(value, {"span_id", "sequence", "tool_name", "duration_ms", "status", "error_type"}, "tool span")
    status = _choice(payload["status"], {"succeeded", "failed"}, "status")
    return ToolCallSpan(_uuid(payload["span_id"], "span_id"),
                        _positive_int(payload["sequence"], "sequence"),
                        _non_empty_string(payload["tool_name"], "tool_name"),
                        _non_negative_int(payload["duration_ms"], "duration_ms"),
                        status, _error_type(payload["error_type"], status))


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result == 0:
        raise ValueError(f"{field} must be positive.")
    return result
```

This completes all parsing paths used by `TraceRecord.from_dict()`; do not add permissive fallback parsing.

- [ ] **Step 4: Write failing pricing tests**

```python
# tests/test_observability_pricing.py
from decimal import Decimal

import pytest

from cdy_agent.observability.models import TokenUsage
from cdy_agent.observability.pricing import Pricing, estimate_cost, resolve_pricing


def test_resolve_pricing_and_estimate_exact_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDY_AGENT_INPUT_COST_PER_MILLION", "1.25")
    monkeypatch.setenv("CDY_AGENT_OUTPUT_COST_PER_MILLION", "2.5")
    pricing = resolve_pricing()
    assert pricing == Pricing(Decimal("1.25"), Decimal("2.5"))
    cost = estimate_cost(TokenUsage(800, 200), pricing)
    assert cost is not None
    assert cost.input_cost == Decimal("0.00100")
    assert cost.output_cost == Decimal("0.0005")


def test_absent_pricing_keeps_cost_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDY_AGENT_INPUT_COST_PER_MILLION", raising=False)
    monkeypatch.delenv("CDY_AGENT_OUTPUT_COST_PER_MILLION", raising=False)
    assert resolve_pricing() is None
    assert estimate_cost(TokenUsage(1, 1), None) is None


@pytest.mark.parametrize(
    ("input_price", "output_price"),
    [("1", None), (None, "2"), ("bad", "2"), ("-1", "2"), ("NaN", "2")],
)
def test_resolve_pricing_rejects_partial_or_invalid_values(monkeypatch, input_price, output_price) -> None:
    for name, value in (("CDY_AGENT_INPUT_COST_PER_MILLION", input_price), ("CDY_AGENT_OUTPUT_COST_PER_MILLION", output_price)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="cost per million"):
        resolve_pricing()
```

- [ ] **Step 5: Implement pricing parsing and estimation**

```python
# src/cdy_agent/observability/pricing.py
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import EstimatedCost, TokenUsage

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Pricing:
    input_per_million: Decimal
    output_per_million: Decimal


def resolve_pricing() -> Pricing | None:
    raw_input = os.getenv("CDY_AGENT_INPUT_COST_PER_MILLION")
    raw_output = os.getenv("CDY_AGENT_OUTPUT_COST_PER_MILLION")
    if raw_input is None and raw_output is None:
        return None
    if raw_input is None or raw_output is None:
        raise ValueError("Input and output cost per million must be configured together.")
    try:
        values = (Decimal(raw_input.strip()), Decimal(raw_output.strip()))
    except (InvalidOperation, ValueError):
        raise ValueError("Token cost per million must be a non-negative decimal.") from None
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("Token cost per million must be a non-negative decimal.")
    return Pricing(*values)


def estimate_cost(usage: TokenUsage, pricing: Pricing | None) -> EstimatedCost | None:
    if pricing is None:
        return None
    return EstimatedCost(
        Decimal(usage.input_tokens) * pricing.input_per_million / MILLION,
        Decimal(usage.output_tokens) * pricing.output_per_million / MILLION,
    )
```

Export all public types/functions from `observability/__init__.py`.

- [ ] **Step 6: Run focused tests and commit**

Run: `uv run pytest tests/test_observability_models.py tests/test_observability_pricing.py -q`

Expected: PASS.

```bash
git add src/cdy_agent/observability tests/test_observability_models.py tests/test_observability_pricing.py
git commit -m "Add observability domain models"
```

### Task 2: Normalize SDK Token usage

**Files:**
- Modify: `src/cdy_agent/openai_client.py`
- Modify: `tests/test_openai_client.py`

**Interfaces:**
- Consumes: `TokenUsage(input_tokens: int, output_tokens: int)`.
- Produces: `FinalResponse(text: str, usage: TokenUsage | None = None)`.
- Produces: `ToolCallResponse(calls, continuation, usage: TokenUsage | None = None)`.

- [ ] **Step 1: Add failing Responses and Chat usage tests**

```python
def test_gateway_normalizes_responses_usage() -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1", output_text="Done", output=[],
        usage=SimpleNamespace(input_tokens=12, output_tokens=3),
    ))
    outcome = openai_client.ModelGateway(model="m", api_mode="responses", client=client).create(
        (Message("user", "secret prompt"),), ()
    )
    assert outcome == openai_client.FinalResponse("Done", TokenUsage(12, 3))


def test_gateway_normalizes_chat_usage() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Done", tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2),
    ))
    outcome = openai_client.ModelGateway(model="m", api_mode="chat_completions", client=client).create(
        (Message("user", "secret prompt"),), ()
    )
    assert outcome == openai_client.FinalResponse("Done", TokenUsage(9, 2))


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_gateway_allows_missing_usage(api_mode: str) -> None:
    client = FakeClient(responses_output="Done", chat_output="Done")
    outcome = openai_client.ModelGateway(model="m", api_mode=api_mode, client=client).create(
        (Message("user", "Hello"),), ()
    )
    assert outcome.usage is None
```

Import `TokenUsage` in the test.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_openai_client.py -q`

Expected: FAIL because outcomes have no `usage` field.

- [ ] **Step 3: Attach usage to every gateway outcome**

```python
# additions in src/cdy_agent/openai_client.py
from .observability.models import TokenUsage

@dataclass(frozen=True)
class FinalResponse:
    text: str
    usage: TokenUsage | None = None

@dataclass(frozen=True)
class ToolCallResponse:
    calls: tuple[ToolCall, ...]
    continuation: ResponsesContinuation | ChatContinuation
    usage: TokenUsage | None = None


def _response_usage(response: object, input_name: str, output_name: str) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, input_name, None)
    output_tokens = getattr(usage, output_name, None)
    try:
        return TokenUsage(input_tokens, output_tokens)
    except (TypeError, ValueError):
        raise RuntimeError("OpenAI returned an unsupported response.") from None
```

In `_create_response`, compute `usage = _response_usage(response, "input_tokens", "output_tokens")` once and pass it to both `ToolCallResponse` and `_final_response`. In `_create_chat_completion`, compute `usage = _response_usage(response, "prompt_tokens", "completion_tokens")` before parsing choices and pass it to both outcomes. Change `_final_response(text, usage=None)` to preserve the usage.

- [ ] **Step 4: Run gateway and regression tests and commit**

Run: `uv run pytest tests/test_openai_client.py tests/test_agent.py -q`

Expected: PASS; existing outcome constructors remain compatible because usage defaults to `None`.

```bash
git add src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Record model token usage"
```

### Task 3: Record one Agent turn and safe structured events

**Files:**
- Create: `src/cdy_agent/observability/logging.py`
- Create: `src/cdy_agent/observability/recorder.py`
- Create: `tests/test_observability_logging.py`
- Create: `tests/test_trace_recorder.py`
- Modify: `src/cdy_agent/observability/__init__.py`

**Interfaces:**
- Produces: `resolve_log_level() -> int`, `configure_structured_logging(level: int) -> None`, and `log_event(level, event, **fields)`.
- Produces: `TraceRecorder(command, model, api_mode, session_id=None, pricing=None, *, clock=time.perf_counter, now=utc_now, uuid_factory=uuid4)`.
- Produces: `start_model_call()`, `finish_model_call(token, usage, error=None)`, `start_tool_call(name)`, `finish_tool_call(token, ok, error_type=None)`, and `finish(error=None) -> TraceRecord`.

- [ ] **Step 1: Write failing recorder aggregation tests**

```python
# tests/test_trace_recorder.py
from decimal import Decimal

from cdy_agent.observability import Pricing, TokenUsage, TraceRecorder


def test_recorder_aggregates_known_usage_and_cost() -> None:
    ticks = iter([10.000, 10.005, 10.007, 10.010, 10.015, 10.020])
    recorder = TraceRecorder(
        "ask", "model", "responses",
        pricing=Pricing(Decimal("1"), Decimal("2")),
        clock=lambda: next(ticks),
        now=lambda: "2026-07-20T08:30:00.000000Z",
    )
    first = recorder.start_model_call()
    recorder.finish_model_call(first, TokenUsage(100, 10))
    second = recorder.start_model_call()
    recorder.finish_model_call(second, None)
    record = recorder.finish()
    assert record.status == "succeeded"
    assert record.usage == TokenUsage(100, 10)
    assert record.estimated_cost.total_cost == Decimal("0.00012")
    assert [span.sequence for span in record.model_calls] == [1, 2]


def test_recorder_marks_failures_without_exception_messages() -> None:
    recorder = TraceRecorder("chat", "model", "chat_completions", session_id="f8605a17-cf86-46ce-87ad-7db57533e5dc")
    token = recorder.start_tool_call("read_file")
    recorder.finish_tool_call(token, ok=False, error_type="invalid_arguments")
    record = recorder.finish(RuntimeError("secret response body"))
    assert record.status == "failed"
    assert record.error_type == "RuntimeError"
    assert record.tool_calls[0].error_type == "invalid_arguments"
    assert "secret response body" not in str(record.to_dict())
```

- [ ] **Step 2: Write failing logging tests**

```python
# tests/test_observability_logging.py
import json
import logging

import pytest

from cdy_agent.observability.logging import configure_structured_logging, log_event, resolve_log_level


def test_log_level_defaults_and_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDY_AGENT_LOG_LEVEL", raising=False)
    assert resolve_log_level() == logging.WARNING
    monkeypatch.setenv("CDY_AGENT_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="CDY_AGENT_LOG_LEVEL"):
        resolve_log_level()


def test_structured_log_contains_only_explicit_safe_fields(capsys) -> None:
    configure_structured_logging(logging.DEBUG)
    log_event(logging.INFO, "trace_finished", trace_id="safe-id", status="failed", duration_ms=4)
    payload = json.loads(capsys.readouterr().err)
    assert payload["event"] == "trace_finished"
    assert payload["trace_id"] == "safe-id"
    assert set(payload) == {"timestamp", "level", "event", "trace_id", "status", "duration_ms"}
```

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run pytest tests/test_trace_recorder.py tests/test_observability_logging.py -q`

Expected: FAIL because recorder and logging modules do not exist.

- [ ] **Step 4: Implement safe JSON logging**

```python
# src/cdy_agent/observability/logging.py
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

LOGGER = logging.getLogger("cdy_agent.observability")
LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "event": record.msg,
        }
        payload.update(getattr(record, "safe_fields", {}))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def resolve_log_level() -> int:
    configured = os.getenv("CDY_AGENT_LOG_LEVEL", "WARNING").strip().upper()
    if configured not in LEVELS:
        raise ValueError("CDY_AGENT_LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR.")
    return LEVELS[configured]


def configure_structured_logging(level: int) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    LOGGER.handlers[:] = [handler]
    LOGGER.setLevel(level)
    LOGGER.propagate = False


def log_event(level: int, event: str, **fields: object) -> None:
    LOGGER.log(level, event, extra={"safe_fields": fields})
```

- [ ] **Step 5: Implement recorder lifecycle**

```python
# src/cdy_agent/observability/recorder.py
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .logging import log_event
from .models import ModelCallSpan, TokenUsage, ToolCallSpan, TraceRecord
from .pricing import Pricing, estimate_cost


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _OpenSpan:
    span_id: str
    sequence: int
    started: float
    tool_name: str | None = None


class TraceRecorder:
    def __init__(
        self,
        command: str,
        model: str,
        api_mode: str,
        session_id: str | None = None,
        pricing: Pricing | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
        now: Callable[[], str] = utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if command not in {"ask", "chat"}:
            raise ValueError("Unsupported trace command.")
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError("Unsupported trace API mode.")
        if not model.strip():
            raise ValueError("Trace model must not be empty.")
        if command == "ask" and session_id is not None:
            raise ValueError("ask traces cannot have a session ID.")
        if command == "chat":
            try:
                if session_id is None or str(UUID(session_id)) != session_id:
                    raise ValueError
            except ValueError:
                raise ValueError("chat traces require a complete session UUID.") from None
        self.trace_id = str(uuid_factory())
        self.command = command
        self.model = model
        self.api_mode = api_mode
        self.session_id = session_id
        self._pricing = pricing
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._started_at = now()
        self._started = clock()
        self._model_calls: list[ModelCallSpan] = []
        self._tool_calls: list[ToolCallSpan] = []
        self._finished = False
        log_event(logging.INFO, "trace_started", trace_id=self.trace_id, status="started")

    def start_model_call(self) -> _OpenSpan:
        return _OpenSpan(str(self._uuid_factory()), len(self._model_calls) + 1, self._clock())

    def finish_model_call(
        self,
        token: _OpenSpan,
        usage: TokenUsage | None,
        error: Exception | None = None,
    ) -> None:
        duration = self._duration(token.started)
        status = "failed" if error is not None else "succeeded"
        error_type = type(error).__name__ if error is not None else None
        self._model_calls.append(ModelCallSpan(token.span_id, token.sequence, duration, status, error_type, usage))
        log_event(logging.DEBUG, "model_call_finished", trace_id=self.trace_id,
                  span_id=token.span_id, status=status, duration_ms=duration)

    def start_tool_call(self, tool_name: str) -> _OpenSpan:
        return _OpenSpan(str(self._uuid_factory()), len(self._tool_calls) + 1, self._clock(), tool_name)

    def finish_tool_call(
        self,
        token: _OpenSpan,
        *,
        ok: bool,
        error_type: str | None = None,
    ) -> None:
        if token.tool_name is None:
            raise ValueError("Tool span token is invalid.")
        duration = self._duration(token.started)
        status = "succeeded" if ok else "failed"
        normalized_error = None if ok else (error_type or "tool_error")
        self._tool_calls.append(ToolCallSpan(token.span_id, token.sequence, token.tool_name, duration, status, normalized_error))
        log_event(logging.DEBUG, "tool_call_finished", trace_id=self.trace_id,
                  span_id=token.span_id, status=status, duration_ms=duration)

    def finish(self, error: Exception | None = None) -> TraceRecord:
        if self._finished:
            raise RuntimeError("Trace recorder is already finished.")
        self._finished = True
        known = [span.usage for span in self._model_calls if span.usage is not None]
        usage = None if not known else TokenUsage(
            sum(item.input_tokens for item in known),
            sum(item.output_tokens for item in known),
        )
        duration = self._duration(self._started)
        status = "failed" if error is not None else "succeeded"
        record = TraceRecord(
            1, self.trace_id, self._started_at, duration, self.command, status,
            self.model, self.api_mode, self.session_id,
            type(error).__name__ if error is not None else None,
            usage, estimate_cost(usage, self._pricing) if usage is not None else None,
            tuple(self._model_calls), tuple(self._tool_calls),
        )
        log_event(logging.INFO, "trace_finished", trace_id=self.trace_id,
                  status=status, duration_ms=duration)
        return record

    def _duration(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))
```

The public methods deliberately have no prompt/reply/arguments/result parameters. Add these boundary tests:

```python
def test_recorder_rejects_invalid_session_semantics() -> None:
    with pytest.raises(ValueError, match="ask traces"):
        TraceRecorder("ask", "m", "responses", session_id="f8605a17-cf86-46ce-87ad-7db57533e5dc")
    with pytest.raises(ValueError, match="session UUID"):
        TraceRecorder("chat", "m", "responses", session_id=None)


def test_recorder_can_finish_once_with_unknown_usage() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    token = recorder.start_model_call()
    recorder.finish_model_call(token, None)
    record = recorder.finish()
    assert record.usage is None
    assert record.estimated_cost is None
    with pytest.raises(RuntimeError, match="already finished"):
        recorder.finish()
```

- [ ] **Step 6: Run focused tests and commit**

Run: `uv run pytest tests/test_trace_recorder.py tests/test_observability_logging.py -q`

Expected: PASS.

```bash
git add src/cdy_agent/observability tests/test_trace_recorder.py tests/test_observability_logging.py
git commit -m "Add trace recorder and structured logs"
```

### Task 4: Instrument Agent model and tool boundaries

**Files:**
- Modify: `src/cdy_agent/agent.py`
- Modify: `tests/test_agent.py`

**Interfaces:**
- Consumes: outcome `.usage`, `TraceRecorder.start_*`, `finish_*`.
- Produces: `Agent.run(messages, recorder: TraceRecorder | None = None) -> str`.

- [ ] **Step 1: Write failing Agent span tests**

Add this recorder double before the tests:

```python
class SpyRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []
        self.model_sequence = 0
        self.tool_sequence = 0

    def start_model_call(self) -> int:
        self.model_sequence += 1
        self.events.append(("model", "start", self.model_sequence))
        return self.model_sequence

    def finish_model_call(self, token: int, usage: TokenUsage | None, error: Exception | None = None) -> None:
        self.events.append(("model", "finish", token, usage, error))

    def start_tool_call(self, tool_name: str) -> int:
        self.tool_sequence += 1
        self.events.append(("tool", "start", self.tool_sequence, tool_name))
        return self.tool_sequence

    def finish_tool_call(self, token: int, *, ok: bool, error_type: str | None = None) -> None:
        self.events.append(("tool", "finish", token, ok, error_type))
```

Then add:

```python
def test_agent_records_model_and_tool_spans() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeGateway([
        ToolCallResponse(calls, ResponsesContinuation("next"), TokenUsage(8, 2)),
        FinalResponse("done", TokenUsage(4, 3)),
    ])
    recorder = SpyRecorder()
    assert Agent(gateway, FakeRegistry(), lambda _: True).run([Message("user", "secret")], recorder) == "done"
    assert recorder.events == [
        ("model", "start", 1), ("model", "finish", 1, TokenUsage(8, 2), None),
        ("tool", "start", 1, "echo"), ("tool", "finish", 1, True, None),
        ("model", "start", 2), ("model", "finish", 2, TokenUsage(4, 3), None),
    ]


def test_agent_records_model_exception_and_reraises() -> None:
    gateway = FakeGateway([RuntimeError("provider secret")])
    recorder = SpyRecorder()
    with pytest.raises(RuntimeError, match="provider secret"):
        Agent(gateway, FakeRegistry(), lambda _: True).run([Message("user", "hello")], recorder)
    assert recorder.events[-1][0:3] == ("model", "finish", 1)
    assert isinstance(recorder.events[-1][-1], RuntimeError)


def test_agent_marks_structured_tool_failure() -> None:
    registry = FakeRegistry()
    registry.result = ToolResult.failure("approval_denied", "secret detail")
    recorder = SpyRecorder()
    gateway = FakeGateway([ToolCallResponse((ToolCall("1", "echo", "{}"),), ResponsesContinuation("next")), FinalResponse("done")])
    Agent(gateway, registry, lambda _: False).run([Message("user", "hello")], recorder)
    assert ("tool", "finish", 1, False, "approval_denied") in recorder.events
```

Adjust the existing doubles exactly as follows:

```python
class FakeGateway:
    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.result = ToolResult.success({"value": "echo"})

    def execute(self, call: ToolCall, confirm: object) -> ToolResult:
        self.calls.append(call)
        if self.result.ok:
            return ToolResult.success({"value": call.name})
        return self.result
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_agent.py -q`

Expected: FAIL because `Agent.run` does not accept a recorder or emit spans.

- [ ] **Step 3: Add optional instrumentation without changing registry**

```python
def run(self, messages: Sequence[Message], recorder: TraceRecorder | None = None) -> str:
    if not messages:
        raise ValueError("Conversation history must not be empty.")
    continuation = None
    outputs: tuple[tuple[str, str], ...] = ()
    for _ in range(self._max_model_calls):
        model_span = recorder.start_model_call() if recorder else None
        try:
            outcome = self._gateway.create(messages=messages, tools=self._registry.definitions, continuation=continuation, tool_outputs=outputs)
        except Exception as exc:
            if recorder and model_span:
                recorder.finish_model_call(model_span, None, exc)
            raise
        if recorder and model_span:
            recorder.finish_model_call(model_span, outcome.usage)
        if isinstance(outcome, FinalResponse):
            return outcome.text
        completed_outputs = []
        for call in outcome.calls:
            tool_span = recorder.start_tool_call(call.name) if recorder else None
            try:
                result = self._registry.execute(call, self._confirm)
            except Exception as exc:
                if recorder and tool_span:
                    recorder.finish_tool_call(tool_span, ok=False, error_type=type(exc).__name__)
                raise
            if recorder and tool_span:
                recorder.finish_tool_call(tool_span, ok=result.ok, error_type=None if result.ok else result.code)
            completed_outputs.append((call.call_id, result.to_json()))
        outputs = tuple(completed_outputs)
        continuation = outcome.continuation
    raise AgentLoopLimitError(f"Agent exceeded the maximum of {self._max_model_calls} model calls.")
```

Import `TraceRecorder` only for typing/runtime use; do not add any observability dependency to `ToolRegistry`.

- [ ] **Step 4: Run Agent and gateway regressions and commit**

Run: `uv run pytest tests/test_agent.py tests/test_openai_client.py tests/test_tool_registry.py -q`

Expected: PASS.

```bash
git add src/cdy_agent/agent.py tests/test_agent.py
git commit -m "Trace agent model and tool calls"
```

### Task 5: Append and query strict JSONL traces

**Files:**
- Create: `src/cdy_agent/observability/store.py`
- Create: `tests/test_trace_store.py`
- Modify: `src/cdy_agent/observability/__init__.py`

**Interfaces:**
- Produces: `TraceStoreError`, `TraceNotFoundError`.
- Produces: `TraceStore(workspace).append(record)`, `.list_traces() -> tuple[TraceRecord, ...]`, `.get(trace_id) -> TraceRecord`.

- [ ] **Step 1: Write failing persistence tests**

```python
# tests/test_trace_store.py
import json
from pathlib import Path

import pytest

from cdy_agent.observability.store import TraceNotFoundError, TraceStore, TraceStoreError
from test_observability_models import sample_trace


def test_empty_read_does_not_create_workspace_data(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    assert store.list_traces() == ()
    assert not (tmp_path / ".cdy-agent").exists()


def test_append_writes_one_json_line_and_lists_newest_first(tmp_path: Path) -> None:
    first = sample_trace()
    second_payload = first.to_dict()
    second_payload["trace_id"] = "0cebd5c2-7d4c-4655-a997-f31e05eb74a5"
    second_payload["started_at"] = "2026-07-20T09:30:00.000000Z"
    second = type(first).from_dict(second_payload)
    store = TraceStore(tmp_path)
    store.append(first)
    store.append(second)
    lines = (tmp_path / ".cdy-agent" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [record.trace_id for record in store.list_traces()] == [second.trace_id, first.trace_id]
    assert store.get(first.trace_id) == first


def test_corrupt_line_reports_line_number(tmp_path: Path) -> None:
    path = tmp_path / ".cdy-agent" / "traces.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps(sample_trace().to_dict()) + "\nnot-json\n", encoding="utf-8")
    with pytest.raises(TraceStoreError, match="line 2"):
        TraceStore(tmp_path).list_traces()


def test_get_requires_complete_existing_uuid(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    with pytest.raises(TraceStoreError, match="complete UUID"):
        store.get("52c809c6")
    with pytest.raises(TraceNotFoundError, match="not found"):
        store.get("52c809c6-6e55-4ff1-9220-e4f90a4f6774")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_trace_store.py -q`

Expected: FAIL because `observability.store` does not exist.

- [ ] **Step 3: Implement append-only strict storage**

```python
# src/cdy_agent/observability/store.py
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from .models import TraceRecord


class TraceStoreError(RuntimeError):
    pass


class TraceNotFoundError(TraceStoreError):
    pass


class TraceStore:
    def __init__(self, workspace: Path) -> None:
        self.path = workspace / ".cdy-agent" / "traces.jsonl"

    def append(self, record: TraceRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        except OSError as exc:
            raise TraceStoreError("Could not write trace data.") from exc

    def list_traces(self) -> tuple[TraceRecord, ...]:
        records = self._read_all()
        return tuple(sorted(records, key=lambda record: record.started_at, reverse=True))

    def get(self, trace_id: str) -> TraceRecord:
        try:
            if str(UUID(trace_id)) != trace_id:
                raise ValueError
        except ValueError:
            raise TraceStoreError("Trace ID must be a complete UUID.") from None
        for record in self._read_all():
            if record.trace_id == trace_id:
                return record
        raise TraceNotFoundError(f"Trace {trace_id} not found.")

    def _read_all(self) -> tuple[TraceRecord, ...]:
        if not self.path.exists():
            return ()
        records = []
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        records.append(TraceRecord.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise TraceStoreError(f"Invalid trace data on line {line_number}.") from exc
        except TraceStoreError:
            raise
        except OSError as exc:
            raise TraceStoreError("Could not read trace data.") from exc
        return tuple(records)
```

Export store types from `observability/__init__.py`.

- [ ] **Step 4: Run persistence tests and commit**

Run: `uv run pytest tests/test_trace_store.py tests/test_observability_models.py -q`

Expected: PASS.

```bash
git add src/cdy_agent/observability tests/test_trace_store.py
git commit -m "Persist workspace traces"
```

### Task 6: Integrate per-turn tracing and trace query CLI

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `TraceRecorder`, `TraceStore`, `resolve_pricing`, logging configuration.
- Produces: `traces list` and `traces show <complete-uuid>` Typer commands.
- Produces: private `_run_traced(agent, messages, recorder, store) -> str` that isolates trace finalization/persistence failures.

- [ ] **Step 1: Isolate observability environment in CLI tests**

Extend the autouse fixture:

```python
for name in (
    "CDY_AGENT_INPUT_COST_PER_MILLION",
    "CDY_AGENT_OUTPUT_COST_PER_MILLION",
    "CDY_AGENT_LOG_LEVEL",
):
    monkeypatch.delenv(name, raising=False)
```

- [ ] **Step 2: Write failing `ask` and `chat` lifecycle tests**

Update `FakeAgent.run` to accept the optional recorder, then exercise the real recorder and store around that fake Agent:

```python
def run(self, messages: Sequence[Message], recorder: object | None = None) -> str:
    self.calls.append(tuple(messages))
    if self.error is not None:
        raise self.error
    return next(self.replies)
```

```python
def test_ask_saves_one_successful_trace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent("reply"))
    result = runner.invoke(app, ["ask", "private prompt", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    records = TraceStore(tmp_path).list_traces()
    assert len(records) == 1
    assert records[0].command == "ask"
    assert records[0].session_id is None
    assert records[0].status == "succeeded"


def test_chat_saves_each_turn_with_same_session_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent(["one", "two"]))
    result = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="first\n\nsecond\n/exit\n")
    assert result.exit_code == 0
    records = TraceStore(tmp_path).list_traces()
    assert len(records) == 2
    assert records[0].session_id == records[1].session_id
    assert all(record.command == "chat" for record in records)


def test_failed_agent_still_saves_failed_trace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent(error=RuntimeError("private provider body")))
    result = runner.invoke(app, ["ask", "private prompt", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    records = TraceStore(tmp_path).list_traces()
    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].error_type == "RuntimeError"
    assert "private provider body" not in str(records[0].to_dict())


def test_trace_write_failure_warns_without_hiding_reply(monkeypatch, tmp_path) -> None:
    class FailingTraceStore:
        def __init__(self, workspace: Path) -> None:
            pass

        def append(self, record: TraceRecord) -> None:
            raise TraceStoreError("private path")

    monkeypatch.setattr(cli, "TraceStore", FailingTraceStore)
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent("visible reply"))
    result = runner.invoke(app, ["ask", "private prompt", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout == "visible reply\n"
    assert "Warning: Could not save trace." in result.stderr
    assert "private" not in result.stderr
```

Import `TraceRecord`, `TraceStore`, and `TraceStoreError` in `tests/test_cli.py`, then add:

```python
@pytest.mark.parametrize("user_input", ["\n/exit\n", "/quit\n", ""])
def test_chat_without_agent_turn_creates_no_trace(monkeypatch, tmp_path, user_input) -> None:
    agent = FakeAgent("unused")
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)
    result = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input=user_input)
    assert result.exit_code == 0
    assert agent.calls == []
    assert TraceStore(tmp_path).list_traces() == ()
```

- [ ] **Step 3: Write failing query and configuration tests**

```python
def test_traces_list_and_show_render_safe_metadata(monkeypatch, tmp_path) -> None:
    from test_observability_models import sample_trace

    record = sample_trace()
    monkeypatch.setattr(cli, "TraceStore", lambda workspace: FakeTraceStore([record]))
    listed = runner.invoke(app, ["traces", "list", "--workspace", str(tmp_path)])
    shown = runner.invoke(app, ["traces", "show", record.trace_id, "--workspace", str(tmp_path)])
    assert listed.exit_code == shown.exit_code == 0
    assert record.trace_id in listed.stdout
    assert "14 tokens" in listed.stdout
    assert "Model calls:" in shown.stdout
    assert "read_file" in shown.stdout
    assert "private prompt" not in listed.stdout + shown.stdout


def test_invalid_observability_configuration_fails_before_agent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CDY_AGENT_INPUT_COST_PER_MILLION", "1")
    created = []
    monkeypatch.setattr(cli, "_create_agent", lambda *args: created.append(True))
    result = runner.invoke(app, ["ask", "hello", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "configured together" in result.stderr
    assert created == []
```

- [ ] **Step 4: Run CLI tests to verify failure**

Run: `uv run pytest tests/test_cli.py -q`

Expected: FAIL because tracing and trace commands are not wired.

- [ ] **Step 5: Configure logging once and add safe traced execution**

Register the command group next to the existing groups and configure logging in the root callback:

```python
traces_app = typer.Typer(help="List and inspect saved call traces.")
app.add_typer(traces_app, name="traces")


@app.callback()
def main() -> None:
    """Run the CDY local personal AI assistant."""
    try:
        configure_structured_logging(resolve_log_level())
    except ValueError as exc:
        _fail_for_exception(exc)
```

Resolve `pricing = resolve_pricing()` after workspace/model/API validation but before `_create_agent` in `ask`, and once during `chat` startup before `_create_agent`. Invalid price configuration therefore uses the existing safe `ValueError` presentation and prevents an SDK call.

```python
def _run_traced(agent, messages, recorder: TraceRecorder, store: TraceStore) -> str:
    error = None
    try:
        return agent.run(messages, recorder)
    except Exception as exc:
        error = exc
        raise
    finally:
        try:
            store.append(recorder.finish(error))
        except (TraceStoreError, RuntimeError, ValueError, OSError):
            typer.echo("Warning: Could not save trace.", err=True)
```

Keep the catch exactly limited to the exception types shown above and never interpolate the caught exception. Persist the Agent trace before attempting `ConversationStore.append_turn`, preserving the design's Agent-success semantics for a later conversation-store failure.

The `ask` execution block becomes:

```python
active_model = resolve_model(model)
api_mode = resolve_api_mode()
pricing = resolve_pricing()
agent = _create_agent(active_model, api_mode, active_workspace)
conversation = Conversation()
conversation.append("user", normalized_prompt)
reply = _run_traced(
    agent,
    conversation.history,
    TraceRecorder("ask", active_model, api_mode, pricing=pricing),
    TraceStore(active_workspace),
)
```

Inside the valid-prompt branch of `chat`, replace the direct Agent call with:

```python
reply = _run_traced(
    agent,
    conversation.history,
    TraceRecorder(
        "chat", active_model, api_mode,
        session_id=session_id, pricing=pricing,
    ),
    TraceStore(active_workspace),
)
```

- [ ] **Step 6: Add trace rendering commands**

```python
@traces_app.command("list")
def list_traces(workspace: Annotated[Path | None, typer.Option(help="Workspace containing saved traces.")] = None) -> None:
    try:
        records = TraceStore(resolve_workspace(workspace or Path.cwd())).list_traces()
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not records:
        typer.echo("No saved traces.")
        return
    for record in records:
        tokens = "unknown tokens" if record.usage is None else f"{record.usage.total_tokens} tokens"
        cost = "unknown cost" if record.estimated_cost is None else f"{record.estimated_cost.total_cost} cost"
        typer.echo(f"{record.trace_id}  {record.started_at}  {record.status}  {record.command}  {record.model}  {record.duration_ms} ms  {tokens}  {cost}")


@traces_app.command("show")
def show_trace(trace_id: Annotated[str, typer.Argument(help="Complete UUID of the trace to show.")], workspace: Annotated[Path | None, typer.Option(help="Workspace containing saved traces.")] = None) -> None:
    try:
        record = TraceStore(resolve_workspace(workspace or Path.cwd())).get(trace_id)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    _render_trace(record)
```

Use the following renderer and add `TraceStoreError` to `REQUEST_ERRORS`:

```python
def _render_trace(record: TraceRecord) -> None:
    typer.echo(f"ID: {record.trace_id}")
    typer.echo(f"Started: {record.started_at}")
    typer.echo(f"Status: {record.status}")
    typer.echo(f"Command: {record.command}")
    typer.echo(f"Model: {record.model}")
    typer.echo(f"API mode: {record.api_mode}")
    typer.echo(f"Session: {record.session_id or '-'}")
    typer.echo(f"Duration: {record.duration_ms} ms")
    typer.echo(f"Error type: {record.error_type or '-'}")
    if record.usage is None:
        typer.echo("Usage: unknown")
    else:
        typer.echo(f"Usage: {record.usage.input_tokens} input, {record.usage.output_tokens} output, {record.usage.total_tokens} total")
    if record.estimated_cost is None:
        typer.echo("Estimated cost: unknown")
    else:
        typer.echo(f"Estimated cost: {record.estimated_cost.input_cost} input, {record.estimated_cost.output_cost} output, {record.estimated_cost.total_cost} total")
    typer.echo("Model calls:")
    for span in record.model_calls:
        tokens = "unknown tokens" if span.usage is None else f"{span.usage.total_tokens} tokens"
        typer.echo(f"  {span.sequence}. {span.status}, {span.duration_ms} ms, {tokens}, error={span.error_type or '-'}")
    typer.echo("Tool calls:")
    for span in record.tool_calls:
        typer.echo(f"  {span.sequence}. {span.tool_name}, {span.status}, {span.duration_ms} ms, error={span.error_type or '-'}")
```

The renderer receives only `TraceRecord`; never pass original request data to it.

- [ ] **Step 7: Run CLI and full regressions and commit**

Run: `uv run pytest tests/test_cli.py tests/test_trace_store.py tests/test_trace_recorder.py -q`

Expected: PASS.

Run: `uv run pytest -q`

Expected: PASS.

```bash
git add src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Expose workspace trace commands"
```

### Task 7: Document and verify the observability milestone

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`

**Interfaces:**
- Documents: exact variables, privacy boundary, trace path, query commands, and the remaining stage 8 work.

- [ ] **Step 1: Update user documentation**

Add a `调用轨迹与费用统计` section to `README.md` containing:

```powershell
$env:CDY_AGENT_INPUT_COST_PER_MILLION = "1.25"
$env:CDY_AGENT_OUTPUT_COST_PER_MILLION = "2.50"
$env:CDY_AGENT_LOG_LEVEL = "INFO"

uv run cdy-agent traces list --workspace .
uv run cdy-agent traces show <trace-id> --workspace .
```

State that prices apply to the selected provider/model, both price variables are optional but paired, trace files live at `<workspace>/.cdy-agent/traces.jsonl`, and prompts/replies/tool payloads are excluded. State that JSON logs go to stderr and default to `WARNING`.

- [ ] **Step 2: Update roadmap stage status precisely**

Replace the stage 8 paragraph with wording that says the observability slice is complete—structured logging, per-turn model/tool traces, Token usage, optional configured cost estimates, and trace queries—while configuration layering, streaming, and evaluation cases remain future stage 8 slices. Do not mark the whole stage complete.

- [ ] **Step 3: Run required verification**

Run: `uv run pytest`

Expected: all tests PASS.

Run: `uv run cdy-agent --help`

Expected: exit 0 and command list includes `ask`, `chat`, `sessions`, `memories`, and `traces`.

Run: `uv run cdy-agent ask --help`

Expected: exit 0 and existing ask arguments/options remain present.

Run: `uv run cdy-agent traces --help`

Expected: exit 0 and command list includes `list` and `show`.

Run: `uv build`

Expected: source distribution and wheel build successfully.

- [ ] **Step 4: Inspect privacy and repository scope**

Run: `rg -n "prompt|arguments|tool_outputs|output_text|API_KEY" src/cdy_agent/observability`

Expected: no persistence/logging fields for sensitive payloads; any matches are validation comments or forbidden-field tests only.

Run: `git status --short`

Expected: only files named in this plan are modified; pre-existing untracked `.idea/`, diagram assets, and `vllm_demo.py` remain untracked and untouched.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md
git commit -m "Document observability workflow"
```

## Final Review Checklist

- [ ] Every design requirement in `docs/superpowers/specs/2026-07-20-observability-design.md` maps to a task above.
- [ ] `FinalResponse` and `ToolCallResponse` expose the same optional `usage` field used by Agent instrumentation.
- [ ] No trace/log API accepts message content or tool payload data.
- [ ] Model failures, structured tool failures, unexpected tool errors, and loop limits retain completed spans.
- [ ] Conversation persistence failure does not retroactively change a successful Agent trace.
- [ ] Empty trace queries do not create `.cdy-agent`.
- [ ] Observability write failures never expose their exception messages or alter primary CLI output/exit behavior.
- [ ] Full pytest, all CLI help checks, and package build pass before completion is claimed.
