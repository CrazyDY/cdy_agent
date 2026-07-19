import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cdy_agent.tools.base import ConfirmationRequest, ToolCall, ToolResult
from cdy_agent.tools import create_builtin_registry
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


def test_register_many_adds_valid_tools_in_order() -> None:
    registry = ToolRegistry([EchoTool(name="first")])

    result = registry.register_many([EchoTool(name="second"), EchoTool(name="third")])

    assert result == ToolResult.success({"names": ["second", "third"]})
    assert [item["name"] for item in registry.definitions] == ["first", "second", "third"]


def test_register_many_is_atomic_on_name_conflict() -> None:
    original = EchoTool(name="existing")
    registry = ToolRegistry([original])
    original_definition = registry.definitions[0]
    replacement = EchoTool(name="existing")
    replacement.description = "Replacement behavior."
    replacement.execute = lambda arguments: ToolResult.success(  # type: ignore[method-assign]
        {"text": "replacement"}
    )

    result = registry.register_many([EchoTool(name="new"), replacement])

    assert result.code == "tool_name_conflict"
    assert registry._tools["existing"] is original
    assert registry.definitions == (original_definition,)
    assert registry.execute(
        ToolCall("call", "existing", '{"text":"original"}'), lambda _: True
    ).data == {"text": "original"}


def test_register_many_rejects_invalid_tool_without_mutation() -> None:
    registry = ToolRegistry([EchoTool(name="existing")])
    invalid = EchoTool(name="new")
    invalid.parameters = []  # type: ignore[assignment]

    result = registry.register_many([invalid])

    assert result.code == "invalid_tools"
    assert [item["name"] for item in registry.definitions] == ["existing"]


def test_register_many_rejects_duplicate_candidates_atomically() -> None:
    registry = ToolRegistry([EchoTool(name="existing")])

    result = registry.register_many([EchoTool(name="new"), EchoTool(name="new")])

    assert result.code == "tool_name_conflict"
    assert [item["name"] for item in registry.definitions] == ["existing"]


@pytest.mark.parametrize("name", ["", "Upper", "has-dash", "1starts_with_digit", "a" * 65])
def test_register_many_rejects_invalid_tool_names(name: str) -> None:
    registry = ToolRegistry([EchoTool(name="existing")])

    result = registry.register_many([EchoTool(name=name)])

    assert result.code == "invalid_tools"
    assert [item["name"] for item in registry.definitions] == ["existing"]


@pytest.mark.parametrize(
    ("attribute", "invalid_value"),
    [
        ("description", ""),
        ("description", None),
        ("parameters", []),
        ("requires_confirmation", 0),
        ("preflight", None),
        ("confirmation_description", None),
        ("execute", None),
    ],
)
def test_register_many_prevalidates_the_complete_tool_contract(
    attribute: str, invalid_value: object
) -> None:
    registry = ToolRegistry([EchoTool(name="existing")])
    invalid = EchoTool(name="new")
    setattr(invalid, attribute, invalid_value)

    result = registry.register_many([invalid])

    assert result.code == "invalid_tools"
    assert [item["name"] for item in registry.definitions] == ["existing"]


@pytest.mark.parametrize("error_type", [TypeError, RuntimeError])
def test_register_many_handles_failure_while_materializing_iterable(
    error_type: type[Exception],
) -> None:
    registry = ToolRegistry([EchoTool(name="existing")])

    def broken_tools() -> Any:
        yield EchoTool(name="new")
        raise error_type("factory failed")

    result = registry.register_many(broken_tools())

    assert result.code == "invalid_tools"
    assert [item["name"] for item in registry.definitions] == ["existing"]


def test_builtin_registry_exposes_tools_in_deterministic_order(tmp_path: Path) -> None:
    assert [item["name"] for item in create_builtin_registry(tmp_path).definitions] == [
        "read_file",
        "write_file",
        "shell",
        "create_note",
        "list_notes",
        "get_note",
        "delete_note",
        "create_todo",
        "list_todos",
        "complete_todo",
        "delete_todo",
        "remember_memory",
        "search_memories",
        "update_memory",
        "forget_memory",
    ]


def test_creating_builtin_registry_does_not_create_database(tmp_path: Path) -> None:
    create_builtin_registry(tmp_path)
    assert not (tmp_path / ".cdy-agent").exists()
