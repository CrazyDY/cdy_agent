from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdy_agent.agent import Agent, AgentLoopLimitError
from cdy_agent.conversation import Message
from cdy_agent.openai_client import FinalResponse, ResponsesContinuation, ToolCallResponse
from cdy_agent.openai_client import ModelGateway
from cdy_agent.tools import create_builtin_registry
from cdy_agent.tools.base import ToolCall, ToolResult


class FakeGateway:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return next(self.outcomes)


class FakeRegistry:
    definitions = (
        {"type": "function", "name": "echo", "description": "", "parameters": {}},
    )

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def execute(self, call: ToolCall, confirm: object) -> ToolResult:
        self.calls.append(call)
        return ToolResult.success({"value": call.name})


def test_agent_returns_direct_response() -> None:
    gateway = FakeGateway([FinalResponse("done")])

    assert Agent(gateway, FakeRegistry(), lambda _: False).run(
        [Message("user", "hello")]
    ) == "done"


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
    ) == ("read_file", "write_file", "shell")


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
