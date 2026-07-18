import json
from dataclasses import dataclass
from typing import Any

from cdy_agent.tools.base import ConfirmationRequest, ToolCall, ToolResult
from cdy_agent.tools.registry import ToolRegistry


@dataclass
class EchoTool:
    name: str = "echo"
    description: str = "Echo text."
    parameters: dict[str, Any] = None  # type: ignore[assignment]
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "Echo text."

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
            return ToolResult.failure("invalid_arguments", "text must be a string.")
        return None

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
            return ToolResult.failure("invalid_arguments", "text must be a string.")
        return ToolResult.success({"text": arguments["text"]})


def test_registry_exposes_function_definition_and_executes() -> None:
    registry = ToolRegistry([EchoTool()])
    result = registry.execute(
        ToolCall("call-1", "echo", '{"text":"hello"}'),
        confirm=lambda request: True,
    )
    assert registry.definitions == ({
        "type": "function",
        "name": "echo",
        "description": "Echo text.",
        "parameters": EchoTool().parameters,
    },)
    assert json.loads(result.to_json()) == {
        "ok": True,
        "data": {"text": "hello"},
    }


def test_registry_returns_structured_errors() -> None:
    registry = ToolRegistry([EchoTool()])
    assert registry.execute(ToolCall("1", "missing", "{}"), lambda _: True).code == "unknown_tool"
    assert registry.execute(ToolCall("2", "echo", "{"), lambda _: True).code == "invalid_arguments"
    assert registry.execute(ToolCall("3", "echo", "[]"), lambda _: True).code == "invalid_arguments"


def test_registry_denies_confirmed_tool_without_executing() -> None:
    tool = EchoTool(requires_confirmation=True)
    requests: list[ConfirmationRequest] = []
    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        confirm=lambda request: requests.append(request) or False,
    )
    assert result.code == "approval_denied"
    assert requests[0].tool_name == "echo"


def test_registry_preflights_before_confirmation() -> None:
    requests: list[ConfirmationRequest] = []
    result = ToolRegistry([EchoTool(requires_confirmation=True)]).execute(
        ToolCall("1", "echo", '{"text":1}'), lambda request: requests.append(request) or True
    )
    assert result.code == "invalid_arguments"
    assert requests == []
