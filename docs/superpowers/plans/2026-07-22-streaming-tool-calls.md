# Streaming Tool Calls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute streamed Responses and Chat Completions tool calls without aborting or replaying the original request in non-streaming mode.

**Architecture:** `ModelGateway.stream()` will normalize provider-specific text and tool-call events into the existing `ModelResponse` union. `Agent.run_stream()` will consume that union in the same bounded tool loop as `Agent.run()`, while preserving incremental text callbacks and trace spans for every model and tool call.

**Tech Stack:** Python 3.10+, OpenAI Python SDK-compatible event objects, pytest, Typer CLI.

## Global Constraints

- Preserve `--stream` / `--no-stream`, `CDY_AGENT_STREAM`, and workspace configuration behavior.
- Keep OpenAI-compatible provider details in `src/cdy_agent/openai_client.py`.
- Keep model/tool orchestration in `src/cdy_agent/agent.py`.
- Tests must be offline and use fake SDK events.
- Do not automatically replay a streamed request through `ModelGateway.create()`.
- Keep the existing maximum of eight model calls unless explicitly configured otherwise.
- Do not stage `.idea/` or `debug_cli.py`.

---

### Task 1: Aggregate Chat Completions Tool-Call Deltas

**Files:**
- Modify: `tests/test_openai_client.py`
- Modify: `src/cdy_agent/openai_client.py`

**Interfaces:**
- Consumes: Chat stream chunks exposing `choice.delta.content`, `choice.delta.tool_calls`, and `choice.finish_reason`.
- Produces: `ModelGateway.stream(...) -> ModelResponse`.
- Produces: `_stream_chat_completion(...) -> ModelResponse`.

- [ ] **Step 1: Replace the unsupported-tool expectation with a failing fragmented Chat tool-call test**

Add a test that splits one call across chunks and verifies no tool JSON is sent to `on_text`:

```python
def test_chat_gateway_aggregates_streamed_tool_call_deltas() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="read_", arguments='{"pa'),
            )]),
            finish_reason=None,
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                index=0,
                id=None,
                function=SimpleNamespace(name="file", arguments='th":"a"}'),
            )]),
            finish_reason="tool_calls",
        )]),
    )
    chunks: list[str] = []

    outcome = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).stream((Message("user", "Read a"),), TOOL_DEFINITIONS, chunks.append)

    calls = (ToolCall("call-1", "read_file", '{"path":"a"}'),)
    assert outcome == openai_client.ToolCallResponse(
        calls,
        openai_client.ChatContinuation(calls, None, ()),
    )
    assert chunks == []
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_openai_client.py::test_chat_gateway_aggregates_streamed_tool_call_deltas -q`

Expected: FAIL because `_stream_chat_completion()` raises `StreamingToolCallUnsupported` on the first tool-call delta.

- [ ] **Step 3: Add failing coverage for interleaved parallel calls and malformed completion**

Add one test with indexes `1, 0, 1, 0` and assert returned calls are ordered by index `0, 1`. Add a parametrized malformed test for a negative/non-integer index, conflicting call IDs, missing final call ID, missing function name, and non-string argument fragments; every case must raise `RuntimeError("OpenAI returned an unsupported response.")`.

- [ ] **Step 4: Implement the minimal Chat delta accumulator**

In `src/cdy_agent/openai_client.py`:

```python
from dataclasses import dataclass, field


@dataclass
class _StreamedToolCall:
    call_id: str | None = None
    name_parts: list[str] = field(default_factory=list)
    argument_parts: list[str] = field(default_factory=list)
    final_arguments: str | None = None


def _unsupported_response() -> RuntimeError:
    return RuntimeError("OpenAI returned an unsupported response.")
```

Change `ModelGateway.stream`, `_stream_chat_completion`, and `_stream_response` return annotations from `FinalResponse` to `ModelResponse`. In `_stream_chat_completion`, keep `chunks` plus `tool_call_parts: dict[int, _StreamedToolCall]`. For each delta:

```python
for tool_delta in tool_calls:
    index = getattr(tool_delta, "index", None)
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise _unsupported_response()
    part = tool_call_parts.setdefault(index, _StreamedToolCall())
    _merge_chat_tool_delta(part, tool_delta)
```

Implement the merge helper exactly as follows:

```python
def _merge_chat_tool_delta(
    part: _StreamedToolCall, tool_delta: object
) -> None:
    call_id = getattr(tool_delta, "id", None)
    if call_id is not None:
        if not isinstance(call_id, str) or not call_id.strip():
            raise _unsupported_response()
        if part.call_id is not None and part.call_id != call_id:
            raise _unsupported_response()
        part.call_id = call_id

    function = getattr(tool_delta, "function", None)
    if function is None:
        return
    name = getattr(function, "name", None)
    arguments = getattr(function, "arguments", None)
    if name is not None:
        if not isinstance(name, str):
            raise _unsupported_response()
        part.name_parts.append(name)
    if arguments is not None:
        if not isinstance(arguments, str):
            raise _unsupported_response()
        part.argument_parts.append(arguments)
```

At stream completion, build calls in sorted index order using the existing validator:

```python
calls = tuple(
    _tool_call(
        part.call_id,
        "".join(part.name_parts),
        "".join(part.argument_parts),
    )
    for _, part in sorted(tool_call_parts.items())
)
if calls:
    history = tuple(request_messages[len(_message_dicts(messages)):])
    content = "".join(chunks) or None
    return ToolCallResponse(calls, ChatContinuation(calls, content, history))
return _final_response("".join(chunks))
```

- [ ] **Step 5: Run Chat streaming tests and verify GREEN**

Run: `uv run pytest tests/test_openai_client.py -k "chat and stream" -q`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the Chat aggregation task**

```powershell
git add -- src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Handle streamed chat tool calls"
```

### Task 2: Aggregate Responses Function-Call Events

**Files:**
- Modify: `tests/test_openai_client.py`
- Modify: `src/cdy_agent/openai_client.py`

**Interfaces:**
- Consumes: `response.created`, `response.output_item.added`, `response.function_call_arguments.delta`, `response.output_item.done`, and `response.completed` events.
- Produces: `ToolCallResponse(calls, ResponsesContinuation(response_id), usage)` when a streamed response requests tools.

- [ ] **Step 1: Write the failing Responses function-call stream test**

Replace `test_gateway_reports_streaming_tool_call_as_unsupported` with:

```python
def test_responses_gateway_aggregates_streamed_function_call() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created",
            response=SimpleNamespace(id="response-1"),
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="item-1",
            output_index=0,
            delta='{"path":',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="item-1",
            output_index=0,
            delta='"a"}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments='{"path":"a"}',
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-1",
                usage=SimpleNamespace(input_tokens=7, output_tokens=3),
            ),
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).stream((Message("user", "Read a"),), TOOL_DEFINITIONS, lambda _: None)

    assert outcome == openai_client.ToolCallResponse(
        (ToolCall("call-1", "read_file", '{"path":"a"}'),),
        openai_client.ResponsesContinuation("response-1"),
        TokenUsage(7, 3),
    )
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_openai_client.py::test_responses_gateway_aggregates_streamed_function_call -q`

Expected: FAIL because `_stream_response()` raises `StreamingToolCallUnsupported` at `response.output_item.added`.

- [ ] **Step 3: Add failing malformed-event coverage**

Add table-driven tests that feed `FakeStream` event tuples for these exact failures: a completed function call with no lifecycle response ID, two lifecycle events with different response IDs, an argument delta whose `item_id` differs from the added item's ID at the same `output_index`, a negative or non-integer `output_index`, and a completed item missing `call_id`, `name`, or string `arguments`. Every case asserts `RuntimeError("OpenAI returned an unsupported response.")`. Add this text-only usage regression:

```python
def test_responses_text_stream_captures_completed_usage() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(type="response.output_text.delta", delta="done"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-1",
                usage=SimpleNamespace(input_tokens=5, output_tokens=1),
            ),
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).stream((Message("user", "Hello"),), (), lambda _: None)

    assert outcome == openai_client.FinalResponse("done", TokenUsage(5, 1))
```

- [ ] **Step 4: Implement Responses event aggregation**

Maintain these values in `_stream_response`:

```python
response_id: str | None = None
usage: TokenUsage | None = None
tool_call_parts: dict[int, _StreamedToolCall] = {}
item_ids: dict[int, str] = {}
```

For lifecycle events carrying `event.response`, accept a non-empty response ID and reject conflicts. On `response.completed`, normalize usage with `_response_usage(response, "input_tokens", "output_tokens")`.

For `response.output_item.added` function-call items, validate `output_index`, store and cross-check `item.id`, and merge `call_id` and `name`. For `response.function_call_arguments.delta`, validate `output_index`, cross-check `item_id`, require a string delta, and append it to `argument_parts`. For `response.output_item.done`, merge the completed metadata and store its string `arguments` as `final_arguments`.

After iteration:

```python
calls = tuple(
    _tool_call(
        part.call_id,
        "".join(part.name_parts),
        part.final_arguments
        if part.final_arguments is not None
        else "".join(part.argument_parts),
    )
    for _, part in sorted(tool_call_parts.items())
)
if calls:
    if response_id is None:
        raise _unsupported_response()
    return ToolCallResponse(calls, ResponsesContinuation(response_id), usage)
return _final_response("".join(chunks), usage)
```

- [ ] **Step 5: Remove the obsolete exception type**

Delete `StreamingToolCallUnsupported` from `src/cdy_agent/openai_client.py` after all gateway paths return normal `ModelResponse` values. Remove corresponding imports from tests; `agent.py` is updated in Task 3.

- [ ] **Step 6: Run all gateway tests and verify GREEN**

Run: `uv run pytest tests/test_openai_client.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit the Responses aggregation task**

```powershell
git add -- src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Handle streamed Responses tool calls"
```

### Task 3: Execute Tools Inside the Streaming Agent Loop

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `src/cdy_agent/agent.py`

**Interfaces:**
- Consumes: `ModelGateway.stream(...) -> ModelResponse`.
- Produces: `Agent.run_stream(messages, on_text, recorder=None) -> str` with native tool execution and continuation.

- [ ] **Step 1: Update the fake gateway and write the failing no-replay test**

Change `FakeStreamingGateway` to accept per-call stream outcomes and chunks:

```python
class FakeStreamingGateway(FakeGateway):
    def __init__(
        self,
        stream_outcomes: Sequence[object],
        stream_chunks: Sequence[Sequence[str]] = (),
    ) -> None:
        super().__init__([])
        self.stream_outcomes = iter(stream_outcomes)
        self.stream_chunks = iter(stream_chunks)
        self.stream_calls: list[dict[str, object]] = []

    def stream(self, **kwargs: object) -> object:
        self.stream_calls.append(kwargs)
        outcome = next(self.stream_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        on_text = kwargs["on_text"]
        assert callable(on_text)
        for chunk in next(self.stream_chunks, ()):
            on_text(chunk)
        return outcome
```

Replace the fallback test with:

```python
def test_agent_executes_streamed_tool_call_without_non_streaming_replay() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    continuation = ResponsesContinuation("next")
    gateway = FakeStreamingGateway(
        [ToolCallResponse(calls, continuation), FinalResponse("done")],
        [(), ("do", "ne")],
    )
    registry = FakeRegistry()
    chunks: list[str] = []

    result = Agent(gateway, registry, lambda _: True).run_stream(
        [Message("user", "go")], chunks.append
    )

    assert result == "done"
    assert chunks == ["do", "ne"]
    assert gateway.calls == []
    assert registry.calls == list(calls)
    assert gateway.stream_calls[1]["continuation"] == continuation
    assert gateway.stream_calls[1]["tool_outputs"] == (
        ("1", ToolResult.success({"value": "echo"}).to_json()),
    )
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_agent.py::test_agent_executes_streamed_tool_call_without_non_streaming_replay -q`

Expected: FAIL because current `run_stream()` rejects a `ToolCallResponse` or uses the obsolete exception fallback.

- [ ] **Step 3: Add failing trace, provider-error, structured-tool-failure, and loop-limit tests**

```python
def test_streaming_agent_records_model_and_tool_spans() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeStreamingGateway([
        ToolCallResponse(
            calls, ResponsesContinuation("next"), TokenUsage(8, 2)
        ),
        FinalResponse("done", TokenUsage(4, 3)),
    ])
    recorder = SpyRecorder()

    assert Agent(gateway, FakeRegistry(), lambda _: True).run_stream(
        [Message("user", "secret")], lambda _: None, recorder
    ) == "done"
    assert recorder.events == [
        ("model", "start", 1),
        ("model", "finish", 1, TokenUsage(8, 2), None),
        ("tool", "start", 1, "echo"),
        ("tool", "finish", 1, True, None),
        ("model", "start", 2),
        ("model", "finish", 2, TokenUsage(4, 3), None),
    ]


def test_streaming_agent_records_provider_exception_and_reraises() -> None:
    provider_error = RuntimeError("provider secret")
    recorder = SpyRecorder()

    with pytest.raises(RuntimeError) as raised:
        Agent(
            FakeStreamingGateway([provider_error]),
            FakeRegistry(),
            lambda _: True,
        ).run_stream([Message("user", "hello")], lambda _: None, recorder)

    assert raised.value is provider_error
    assert recorder.events[-1][0:3] == ("model", "finish", 1)
    assert recorder.events[-1][-1] is provider_error


def test_streaming_agent_serializes_structured_tool_failure() -> None:
    registry = FakeRegistry()
    registry.result = ToolResult.failure("approval_denied", "secret detail")
    gateway = FakeStreamingGateway([
        ToolCallResponse(
            (ToolCall("1", "echo", "{}"),),
            ResponsesContinuation("next"),
        ),
        FinalResponse("done"),
    ])
    recorder = SpyRecorder()

    Agent(gateway, registry, lambda _: False).run_stream(
        [Message("user", "hello")], lambda _: None, recorder
    )

    assert gateway.stream_calls[1]["tool_outputs"] == (
        ("1", registry.result.to_json()),
    )
    assert ("tool", "finish", 1, False, "approval_denied") in recorder.events


def test_streaming_agent_stops_at_model_call_limit() -> None:
    outcome = ToolCallResponse(
        (ToolCall("1", "echo", "{}"),),
        ResponsesContinuation("next"),
    )
    gateway = FakeStreamingGateway([outcome, outcome])

    with pytest.raises(AgentLoopLimitError, match="maximum of 2"):
        Agent(
            gateway, FakeRegistry(), lambda _: True, max_model_calls=2
        ).run_stream([Message("user", "loop")], lambda _: None)

    assert len(gateway.stream_calls) == 2
    assert gateway.calls == []
```

- [ ] **Step 4: Implement the bounded streaming tool loop**

Remove `StreamingToolCallUnsupported` from the imports. Replace `run_stream()` with:

```python
def run_stream(
    self,
    messages: Sequence[Message],
    on_text: Callable[[str], None],
    recorder: TraceRecorder | None = None,
) -> str:
    if not messages:
        raise ValueError("Conversation history must not be empty.")

    continuation = None
    outputs: tuple[tuple[str, str], ...] = ()
    active_recorder = recorder
    for _ in range(self._max_model_calls):
        model_span = None
        if active_recorder is not None:
            try:
                model_span = active_recorder.start_model_call()
            except Exception:
                _invalidate_recorder(active_recorder)
                active_recorder = None
        try:
            outcome = self._gateway.stream(
                messages=self._messages_with_system_prompt(messages),
                tools=self._registry.definitions,
                on_text=on_text,
                continuation=continuation,
                tool_outputs=outputs,
            )
        except Exception as exc:
            if active_recorder is not None and model_span is not None:
                try:
                    active_recorder.finish_model_call(model_span, None, exc)
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            raise
        if active_recorder is not None and model_span is not None:
            try:
                active_recorder.finish_model_call(model_span, outcome.usage)
            except Exception:
                _invalidate_recorder(active_recorder)
                active_recorder = None
        if isinstance(outcome, FinalResponse):
            return outcome.text

        completed_outputs = []
        for call in outcome.calls:
            tool_span = None
            if active_recorder is not None:
                try:
                    tool_span = active_recorder.start_tool_call(call.name)
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            try:
                result = self._registry.execute(call, self._confirm)
            except Exception as exc:
                if active_recorder is not None and tool_span is not None:
                    try:
                        active_recorder.finish_tool_call(
                            tool_span,
                            ok=False,
                            error_type=type(exc).__name__,
                        )
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                raise
            if active_recorder is not None and tool_span is not None:
                try:
                    active_recorder.finish_tool_call(
                        tool_span,
                        ok=result.ok,
                        error_type=None if result.ok else result.code,
                    )
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            completed_outputs.append((call.call_id, result.to_json()))
        outputs = tuple(completed_outputs)
        continuation = outcome.continuation

    raise AgentLoopLimitError(
        f"Agent exceeded the maximum of {self._max_model_calls} model calls."
    )
```

- [ ] **Step 5: Run Agent tests and verify GREEN**

Run: `uv run pytest tests/test_agent.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the Agent loop task**

```powershell
git add -- src/cdy_agent/agent.py tests/test_agent.py
git commit -m "Run tools inside streaming agent loop"
```

### Task 4: Regression and CLI Verification

**Files:**
- Verify: `tests/test_cli.py`

**Interfaces:**
- Verifies the existing CLI callback and persistence contract without changing command options.

- [ ] **Step 1: Run focused streaming and CLI tests**

Run: `uv run pytest tests/test_openai_client.py tests/test_agent.py tests/test_cli.py -q`

Expected: all tests PASS. A failure blocks completion and starts a separate TDD correction before continuing this task.

- [ ] **Step 2: Run the complete offline suite**

Run: `uv run pytest`

Expected: all tests PASS with zero failures.

- [ ] **Step 3: Run CLI smoke checks**

Run: `uv run cdy-agent --help`

Expected: exit code 0 and the `evals`, `ask`, `chat`, and `config` commands remain visible.

Run: `uv run cdy-agent ask --help`

Expected: exit code 0 and `--stream / --no-stream` remains visible.

- [ ] **Step 4: Check the final diff and repository status**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only intentional tracked changes plus the pre-existing untracked `.idea/` and `debug_cli.py`.

- [ ] **Step 5: Commit any verification-only test correction**

Only when Step 1 required an additional CLI regression fix:

```powershell
git add -- src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Preserve CLI streaming behavior"
```
