from pathlib import Path
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from cdy_agent.agent import Agent, AgentLoopLimitError
from cdy_agent.conversation import Message
from cdy_agent.openai_client import (
    FinalResponse,
    ResponsesContinuation,
    ToolCallResponse,
)
from cdy_agent.openai_client import ModelGateway
from cdy_agent.observability import TokenUsage
from cdy_agent.skills import SkillManager, create_skill_tools
from cdy_agent.tools import create_builtin_registry
from cdy_agent.tools.base import ToolCall, ToolResult
from cdy_agent.tools.registry import ToolRegistry


class FakeGateway:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


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


class FakeRegistry:
    definitions = (
        {"type": "function", "name": "echo", "description": "", "parameters": {}},
    )

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
        self.result = ToolResult.success({"value": "echo"})

    def execute(self, call: ToolCall, confirm: object) -> ToolResult:
        self.calls.append(call)
        if self.result.ok:
            return ToolResult.success({"value": call.name})
        return self.result


class SpyRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []
        self.model_sequence = 0
        self.tool_sequence = 0
        self.invalidations = 0

    @property
    def healthy(self) -> bool:
        return self.invalidations == 0

    def invalidate(self) -> None:
        self.invalidations += 1

    def start_model_call(self) -> int:
        self.model_sequence += 1
        self.events.append(("model", "start", self.model_sequence))
        return self.model_sequence

    def finish_model_call(
        self,
        token: int,
        usage: TokenUsage | None,
        error: Exception | None = None,
    ) -> None:
        self.events.append(("model", "finish", token, usage, error))

    def start_tool_call(self, tool_name: str) -> int:
        self.tool_sequence += 1
        self.events.append(("tool", "start", self.tool_sequence, tool_name))
        return self.tool_sequence

    def finish_tool_call(
        self,
        token: int,
        *,
        ok: bool,
        error_type: str | None = None,
    ) -> None:
        self.events.append(("tool", "finish", token, ok, error_type))


class FailingRecorder(SpyRecorder):
    def __init__(
        self, operation: str, *, invalidate_raises: bool = False
    ) -> None:
        super().__init__()
        self.operation = operation
        self.invalidate_raises = invalidate_raises
        self.attempts: list[str] = []

    def invalidate(self) -> None:
        super().invalidate()
        if self.invalidate_raises:
            raise RuntimeError("private invalidation failure")

    def _fail(self, operation: str) -> None:
        self.attempts.append(operation)
        if operation == self.operation:
            raise RuntimeError("recorder secret")

    def start_model_call(self) -> int:
        self._fail("start_model")
        return super().start_model_call()

    def finish_model_call(
        self,
        token: int,
        usage: TokenUsage | None,
        error: Exception | None = None,
    ) -> None:
        self._fail("finish_model")
        super().finish_model_call(token, usage, error)

    def start_tool_call(self, tool_name: str) -> int:
        self._fail("start_tool")
        return super().start_tool_call(tool_name)

    def finish_tool_call(
        self,
        token: int,
        *,
        ok: bool,
        error_type: str | None = None,
    ) -> None:
        self._fail("finish_tool")
        if self.operation == "reject_bad_code" and error_type == "bad-code":
            raise ValueError("Tool error type must be a stable identifier.")
        super().finish_tool_call(token, ok=ok, error_type=error_type)


def test_agent_records_model_and_tool_spans() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeGateway([
        ToolCallResponse(calls, ResponsesContinuation("next"), TokenUsage(8, 2)),
        FinalResponse("done", TokenUsage(4, 3)),
    ])
    recorder = SpyRecorder()

    assert Agent(gateway, FakeRegistry(), lambda _: True).run(
        [Message("user", "secret")], recorder
    ) == "done"
    assert recorder.events == [
        ("model", "start", 1),
        ("model", "finish", 1, TokenUsage(8, 2), None),
        ("tool", "start", 1, "echo"),
        ("tool", "finish", 1, True, None),
        ("model", "start", 2),
        ("model", "finish", 2, TokenUsage(4, 3), None),
    ]


def test_agent_records_model_exception_and_reraises() -> None:
    gateway = FakeGateway([RuntimeError("provider secret")])
    recorder = SpyRecorder()

    with pytest.raises(RuntimeError, match="provider secret"):
        Agent(gateway, FakeRegistry(), lambda _: True).run(
            [Message("user", "hello")], recorder
        )

    assert recorder.events[-1][0:3] == ("model", "finish", 1)
    assert isinstance(recorder.events[-1][-1], RuntimeError)


def test_agent_marks_structured_tool_failure() -> None:
    registry = FakeRegistry()
    registry.result = ToolResult.failure("approval_denied", "secret detail")
    recorder = SpyRecorder()
    gateway = FakeGateway([
        ToolCallResponse(
            (ToolCall("1", "echo", "{}"),),
            ResponsesContinuation("next"),
        ),
        FinalResponse("done"),
    ])

    Agent(gateway, registry, lambda _: False).run(
        [Message("user", "hello")], recorder
    )

    assert ("tool", "finish", 1, False, "approval_denied") in recorder.events


def test_agent_records_unexpected_tool_exception_and_reraises() -> None:
    class ExplodingRegistry(FakeRegistry):
        def execute(self, call: ToolCall, confirm: object) -> ToolResult:
            self.calls.append(call)
            raise RuntimeError("tool secret")

    recorder = SpyRecorder()
    gateway = FakeGateway([
        ToolCallResponse(
            (ToolCall("1", "echo", "{}"),),
            ResponsesContinuation("next"),
        ),
    ])

    with pytest.raises(RuntimeError, match="tool secret"):
        Agent(gateway, ExplodingRegistry(), lambda _: True).run(
            [Message("user", "hello")], recorder
        )

    assert recorder.events[-1] == (
        "tool", "finish", 1, False, "RuntimeError",
    )


def test_agent_disables_tracing_when_model_span_start_fails() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeGateway([
        ToolCallResponse(calls, ResponsesContinuation("next")),
        FinalResponse("done"),
    ])
    recorder = FailingRecorder("start_model")

    assert Agent(gateway, FakeRegistry(), lambda _: True).run(
        [Message("user", "hello")], recorder
    ) == "done"
    assert recorder.attempts == ["start_model"]
    assert recorder.invalidations == 1


def test_agent_disables_tracing_when_model_span_finish_fails_after_success() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeGateway([
        ToolCallResponse(calls, ResponsesContinuation("next")),
        FinalResponse("done"),
    ])
    recorder = FailingRecorder("finish_model")

    assert Agent(gateway, FakeRegistry(), lambda _: True).run(
        [Message("user", "hello")], recorder
    ) == "done"
    assert recorder.attempts == ["start_model", "finish_model"]
    assert recorder.invalidations == 1


def test_model_finish_failure_does_not_mask_provider_exception() -> None:
    provider_error = RuntimeError("provider secret")
    recorder = FailingRecorder("finish_model")

    with pytest.raises(RuntimeError) as raised:
        Agent(
            FakeGateway([provider_error]), FakeRegistry(), lambda _: True
        ).run([Message("user", "hello")], recorder)

    assert raised.value is provider_error
    assert recorder.attempts == ["start_model", "finish_model"]
    assert recorder.invalidations == 1


def test_agent_disables_tracing_when_tool_span_start_fails() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeGateway([
        ToolCallResponse(calls, ResponsesContinuation("next")),
        FinalResponse("done"),
    ])
    recorder = FailingRecorder("start_tool")

    assert Agent(gateway, FakeRegistry(), lambda _: True).run(
        [Message("user", "hello")], recorder
    ) == "done"
    assert recorder.attempts == ["start_model", "finish_model", "start_tool"]
    assert recorder.invalidations == 1


@pytest.mark.parametrize(
    ("result", "recorder_failure"),
    [
        (ToolResult.success({"value": "echo"}), "finish_tool"),
        (ToolResult.failure("approval_denied", "secret detail"), "finish_tool"),
        (ToolResult.failure("bad-code", "secret detail"), "reject_bad_code"),
    ],
)
def test_tool_finish_failure_preserves_structured_result(
    result: ToolResult, recorder_failure: str
) -> None:
    registry = FakeRegistry()
    registry.result = result
    gateway = FakeGateway([
        ToolCallResponse(
            (ToolCall("1", "echo", "{}"),), ResponsesContinuation("next")
        ),
        FinalResponse("done"),
    ])
    recorder = FailingRecorder(recorder_failure)

    assert Agent(gateway, registry, lambda _: True).run(
        [Message("user", "hello")], recorder
    ) == "done"
    assert gateway.calls[1]["tool_outputs"] == (("1", result.to_json()),)
    assert recorder.attempts == [
        "start_model", "finish_model", "start_tool", "finish_tool",
    ]
    assert recorder.invalidations == 1


def test_tool_finish_failure_does_not_mask_tool_exception() -> None:
    tool_error = LookupError("tool secret")

    class ExplodingRegistry(FakeRegistry):
        def execute(self, call: ToolCall, confirm: object) -> ToolResult:
            raise tool_error

    recorder = FailingRecorder("finish_tool")
    gateway = FakeGateway([
        ToolCallResponse(
            (ToolCall("1", "echo", "{}"),), ResponsesContinuation("next")
        ),
    ])

    with pytest.raises(LookupError) as raised:
        Agent(gateway, ExplodingRegistry(), lambda _: True).run(
            [Message("user", "hello")], recorder
        )
    assert raised.value is tool_error
    assert recorder.attempts == [
        "start_model", "finish_model", "start_tool", "finish_tool",
    ]
    assert recorder.invalidations == 1


def test_recorder_invalidation_failure_does_not_replace_primary_result() -> None:
    recorder = FailingRecorder("start_model", invalidate_raises=True)

    assert Agent(
        FakeGateway([FinalResponse("done")]), FakeRegistry(), lambda _: True
    ).run([Message("user", "hello")], recorder) == "done"
    assert recorder.invalidations == 1


def test_agent_returns_direct_response() -> None:
    gateway = FakeGateway([FinalResponse("done")])

    assert Agent(gateway, FakeRegistry(), lambda _: False).run(
        [Message("user", "hello")]
    ) == "done"


def test_agent_streams_direct_response_chunks() -> None:
    gateway = FakeStreamingGateway([FinalResponse("Hello")], [("Hel", "lo")])
    chunks: list[str] = []

    result = Agent(gateway, FakeRegistry(), lambda _: False).run_stream(
        [Message("user", "hello")], chunks.append
    )

    assert result == "Hello"
    assert chunks == ["Hel", "lo"]
    assert gateway.stream_calls[0]["messages"] == (Message("user", "hello"),)


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


def test_agent_prepends_initialized_system_prompt_to_model_calls() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    gateway = FakeGateway([
        ToolCallResponse(calls, ResponsesContinuation("next")),
        FinalResponse("done"),
    ])

    assert Agent(
        gateway,
        FakeRegistry(),
        lambda _: True,
        system_prompt="  Use local tools carefully.  ",
    ).run([Message("user", "go")]) == "done"

    assert gateway.calls[0]["messages"] == (
        Message("system", "Use local tools carefully."),
        Message("user", "go"),
    )
    assert gateway.calls[1]["messages"] == (
        Message("system", "Use local tools carefully."),
        Message("user", "go"),
    )


def test_agent_executes_batch_and_continues() -> None:
    calls = (ToolCall("1", "a", "{}"), ToolCall("2", "b", "{}"))
    continuation = ResponsesContinuation("next")
    gateway = FakeGateway([
        ToolCallResponse(calls, continuation),
        FinalResponse("done"),
    ])
    registry = FakeRegistry()

    assert Agent(gateway, registry, lambda _: True).run(
        [Message("user", "go")]
    ) == "done"
    assert registry.calls == list(calls)
    assert gateway.calls[1]["continuation"] is continuation
    assert gateway.calls[1]["tool_outputs"] == (
        ("1", ToolResult.success({"value": "a"}).to_json()),
        ("2", ToolResult.success({"value": "b"}).to_json()),
    )


def test_agent_stops_after_eight_model_calls() -> None:
    calls = (ToolCall("1", "echo", "{}"),)
    outcomes = [
        ToolCallResponse(calls, ResponsesContinuation(str(index)))
        for index in range(8)
    ]
    gateway = FakeGateway(outcomes + [FinalResponse("too late")])

    with pytest.raises(AgentLoopLimitError, match="maximum of 8 model calls"):
        Agent(gateway, FakeRegistry(), lambda _: True).run([Message("user", "go")])

    assert len(gateway.calls) == 8


def test_agent_rejects_empty_history() -> None:
    with pytest.raises(ValueError, match="history must not be empty"):
        Agent(FakeGateway([]), FakeRegistry(), lambda _: True).run([])


def test_agent_rejects_invalid_model_call_limit() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Agent(FakeGateway([]), FakeRegistry(), lambda _: True, max_model_calls=0)


def test_builtin_registry_has_deterministic_order(tmp_path: Path) -> None:
    assert tuple(
        definition["name"]
        for definition in create_builtin_registry(tmp_path).definitions
    ) == (
        "read_file", "write_file", "shell",
        "create_note", "list_notes", "get_note", "delete_note",
        "create_todo", "list_todos", "complete_todo", "delete_todo",
        "remember_memory", "search_memories", "update_memory", "forget_memory",
    )


def test_builtin_personal_tools_share_one_store(tmp_path: Path) -> None:
    registry = create_builtin_registry(tmp_path)
    personal_tool_names = (
        "create_note", "list_notes", "get_note", "delete_note",
        "create_todo", "list_todos", "complete_todo", "delete_todo",
    )

    stores = [registry._tools[name].store for name in personal_tool_names]

    assert all(store is stores[0] for store in stores)


def test_agent_passes_registry_definitions_to_real_gateway(tmp_path: Path) -> None:
    class FakeResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(output_text="done", output=[])

    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    gateway = ModelGateway(model="test-model", api_mode="responses", client=client)
    registry = create_builtin_registry(tmp_path)

    assert Agent(gateway, registry, lambda _: True).run(
        [Message("user", "hello")]
    ) == "done"
    assert responses.calls[0]["tools"] == list(registry.definitions)


def test_agent_keeps_fixed_skill_definitions_after_activation(
    tmp_path: Path,
) -> None:
    directory = tmp_path / ".cdy-agent" / "skills" / "research-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        (
            "---\n"
            "name: research-skill\n"
            "description: Research workspace information.\n"
            "---\n\n"
            "# Research\n"
        ),
        encoding="utf-8",
    )
    registry = create_builtin_registry(tmp_path)
    registered = registry.register_many(
        create_skill_tools(SkillManager(tmp_path))
    )
    assert registered.ok
    gateway = FakeGateway(
        [
            ToolCallResponse(
                (
                    ToolCall(
                        "activate-1",
                        "activate_skill",
                        '{"name":"research-skill"}',
                    ),
                ),
                ResponsesContinuation("next"),
            ),
            FinalResponse("done"),
        ]
    )

    result = Agent(
        gateway,
        registry,
        lambda request: pytest.fail(
            f"Activation requested confirmation: {request}"
        ),
    ).run([Message("user", "research this workspace")])

    assert result == "done"
    assert gateway.calls[0]["tools"] == gateway.calls[1]["tools"]
    assert {
        definition["name"] for definition in gateway.calls[0]["tools"]
    } >= {
        "list_skills",
        "search_skills",
        "activate_skill",
        "read_skill_resource",
        "run_skill_script",
    }
