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
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TokenUsage":
        if not isinstance(value, dict) or set(value) != {
            "input_tokens",
            "output_tokens",
            "total_tokens",
        }:
            raise ValueError("usage has an invalid shape.")
        usage = cls(
            _non_negative_int(value["input_tokens"], "input_tokens"),
            _non_negative_int(value["output_tokens"], "output_tokens"),
        )
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
        if any(
            not value.is_finite() or value < 0
            for value in (self.input_cost, self.output_cost)
        ):
            raise ValueError("estimated costs must be finite and non-negative.")

    @property
    def total_cost(self) -> Decimal:
        return self.input_cost + self.output_cost

    def to_dict(self) -> dict[str, str]:
        return {
            "input_cost": _decimal_text(self.input_cost),
            "output_cost": _decimal_text(self.output_cost),
            "total_cost": _decimal_text(self.total_cost),
        }

    @classmethod
    def from_dict(cls, value: object) -> "EstimatedCost":
        if not isinstance(value, dict) or set(value) != {
            "input_cost",
            "output_cost",
            "total_cost",
        }:
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
        return {
            "span_id": self.span_id,
            "sequence": self.sequence,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_type": self.error_type,
            "usage": None if self.usage is None else self.usage.to_dict(),
        }


@dataclass(frozen=True)
class ToolCallSpan:
    span_id: str
    sequence: int
    tool_name: str
    duration_ms: int
    status: str
    error_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "sequence": self.sequence,
            "tool_name": self.tool_name,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_type": self.error_type,
        }


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
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "command": self.command,
            "status": self.status,
            "model": self.model,
            "api_mode": self.api_mode,
            "session_id": self.session_id,
            "error_type": self.error_type,
            "usage": None if self.usage is None else self.usage.to_dict(),
            "estimated_cost": (
                None if self.estimated_cost is None else self.estimated_cost.to_dict()
            ),
            "model_calls": [span.to_dict() for span in self.model_calls],
            "tool_calls": [span.to_dict() for span in self.tool_calls],
        }

    @classmethod
    def from_dict(cls, value: object) -> "TraceRecord":
        payload = _exact_dict(
            value,
            {
                "schema_version",
                "trace_id",
                "started_at",
                "duration_ms",
                "command",
                "status",
                "model",
                "api_mode",
                "session_id",
                "error_type",
                "usage",
                "estimated_cost",
                "model_calls",
                "tool_calls",
            },
            "trace",
        )
        if payload["schema_version"] != 1:
            raise ValueError("Unsupported trace schema version.")
        started_at = _utc_timestamp(payload["started_at"])
        command = _choice(payload["command"], {"ask", "chat"}, "command")
        status = _choice(payload["status"], {"succeeded", "failed"}, "status")
        model = _non_empty_string(payload["model"], "model")
        api_mode = _choice(
            payload["api_mode"], {"responses", "chat_completions"}, "api_mode"
        )
        session_id = (
            None
            if payload["session_id"] is None
            else _uuid(payload["session_id"], "session_id")
        )
        if (command == "ask") != (session_id is None):
            raise ValueError("session_id does not match command.")
        error_type = _error_type(payload["error_type"], status)
        usage = (
            None if payload["usage"] is None else TokenUsage.from_dict(payload["usage"])
        )
        cost = (
            None
            if payload["estimated_cost"] is None
            else EstimatedCost.from_dict(payload["estimated_cost"])
        )
        if usage is None and cost is not None:
            raise ValueError("estimated_cost requires usage.")
        if not isinstance(payload["model_calls"], list) or not isinstance(
            payload["tool_calls"], list
        ):
            raise ValueError("trace spans must be arrays.")
        model_calls = tuple(_model_span(item) for item in payload["model_calls"])
        tool_calls = tuple(_tool_span(item) for item in payload["tool_calls"])
        if [span.sequence for span in model_calls] != list(
            range(1, len(model_calls) + 1)
        ):
            raise ValueError("model call sequence is invalid.")
        if [span.sequence for span in tool_calls] != list(
            range(1, len(tool_calls) + 1)
        ):
            raise ValueError("tool call sequence is invalid.")
        known = [span.usage for span in model_calls if span.usage is not None]
        aggregate = (
            None
            if not known
            else TokenUsage(
                sum(item.input_tokens for item in known),
                sum(item.output_tokens for item in known),
            )
        )
        if usage != aggregate:
            raise ValueError("trace usage is inconsistent with model calls.")
        return cls(
            1,
            _uuid(payload["trace_id"], "trace_id"),
            started_at,
            _non_negative_int(payload["duration_ms"], "duration_ms"),
            command,
            status,
            model,
            api_mode,
            session_id,
            error_type,
            usage,
            cost,
            model_calls,
            tool_calls,
        )


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
    payload = _exact_dict(
        value,
        {"span_id", "sequence", "duration_ms", "status", "error_type", "usage"},
        "model span",
    )
    status = _choice(payload["status"], {"succeeded", "failed"}, "status")
    usage = (
        None if payload["usage"] is None else TokenUsage.from_dict(payload["usage"])
    )
    return ModelCallSpan(
        _uuid(payload["span_id"], "span_id"),
        _positive_int(payload["sequence"], "sequence"),
        _non_negative_int(payload["duration_ms"], "duration_ms"),
        status,
        _error_type(payload["error_type"], status),
        usage,
    )


def _tool_span(value: object) -> ToolCallSpan:
    payload = _exact_dict(
        value,
        {"span_id", "sequence", "tool_name", "duration_ms", "status", "error_type"},
        "tool span",
    )
    status = _choice(payload["status"], {"succeeded", "failed"}, "status")
    return ToolCallSpan(
        _uuid(payload["span_id"], "span_id"),
        _positive_int(payload["sequence"], "sequence"),
        _non_empty_string(payload["tool_name"], "tool_name"),
        _non_negative_int(payload["duration_ms"], "duration_ms"),
        status,
        _error_type(payload["error_type"], status),
    )


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result == 0:
        raise ValueError(f"{field} must be positive.")
    return result
