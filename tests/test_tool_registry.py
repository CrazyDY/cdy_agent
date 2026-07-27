import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cdy_agent.tools import create_builtin_registry
from cdy_agent.tools.base import (
    ConfirmationDecision,
    ConfirmationRequest,
    PreparedToolExecution,
    ToolCall,
    ToolResult,
)
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
            return ToolResult.failure(
                "invalid_arguments",
                "text must be a string.",
            )
        return None

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
            return ToolResult.failure(
                "invalid_arguments",
                "text must be a string.",
            )
        return ToolResult.success({"text": arguments["text"]})


@dataclass
class DynamicEchoTool(EchoTool):
    confirmation_required: bool = True
    remembered: list[dict[str, Any]] = None  # type: ignore[assignment]
    remember_result: ToolResult = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.remembered = []
        self.remember_result = ToolResult.success({"remembered": True})

    def requires_confirmation_for(self, arguments: dict[str, Any]) -> bool:
        return self.confirmation_required

    def remember_approval(self, arguments: dict[str, Any]) -> ToolResult:
        self.remembered.append(dict(arguments))
        return self.remember_result


@dataclass
class PreparingEchoTool(EchoTool):
    events: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.events = []

    def prepare_execution(
        self,
        arguments: dict[str, Any],
    ) -> PreparedToolExecution | ToolResult:
        self.events.append("prepare")
        if set(arguments) != {"text"}:
            return ToolResult.failure(
                "invalid_arguments",
                "text is required.",
            )
        text = arguments["text"]
        return PreparedToolExecution(
            requires_confirmation=True,
            confirmation_description=f"Echo {text}.",
            remember_approval=lambda: (
                self.events.append("remember")
                or ToolResult.success({"remembered": True})
            ),
            execute=lambda: (
                self.events.append("execute") or ToolResult.success({"text": text})
            ),
        )

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        raise AssertionError("legacy preflight must not run")

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        raise AssertionError("legacy description must not run")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise AssertionError("legacy execute must not run")


def test_registry_exposes_function_definition_and_executes() -> None:
    registry = ToolRegistry([EchoTool()])
    result = registry.execute(
        ToolCall("call-1", "echo", '{"text":"hello"}'),
        confirm=lambda request: True,
    )
    assert registry.definitions == (
        {
            "type": "function",
            "name": "echo",
            "description": "Echo text.",
            "parameters": EchoTool().parameters,
        },
    )
    assert json.loads(result.to_json()) == {
        "ok": True,
        "data": {"text": "hello"},
    }


def test_registry_returns_structured_errors() -> None:
    registry = ToolRegistry([EchoTool()])
    missing = registry.execute(
        ToolCall("1", "missing", "{}"),
        lambda _: True,
    )
    malformed = registry.execute(
        ToolCall("2", "echo", "{"),
        lambda _: True,
    )
    not_an_object = registry.execute(
        ToolCall("3", "echo", "[]"),
        lambda _: True,
    )

    assert missing.code == "unknown_tool"
    assert malformed.code == "invalid_arguments"
    assert not_an_object.code == "invalid_arguments"


def test_registry_denies_confirmed_tool_without_executing() -> None:
    tool = EchoTool(requires_confirmation=True)
    requests: list[ConfirmationRequest] = []
    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        confirm=lambda request: requests.append(request) or False,
    )
    assert result.code == "approval_denied"
    assert requests[0].tool_name == "echo"


def test_registry_skips_callback_when_dynamic_tool_is_auto_approved() -> None:
    callbacks: list[ConfirmationRequest] = []
    tool = DynamicEchoTool(confirmation_required=False)

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: callbacks.append(request) or ConfirmationDecision.DENY,
    )

    assert result.ok
    assert callbacks == []


def test_registry_allows_once_without_persisting() -> None:
    tool = DynamicEchoTool()
    requests: list[ConfirmationRequest] = []

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: requests.append(request) or ConfirmationDecision.ALLOW_ONCE,
    )

    assert result.ok
    assert requests[0].allow_always is True
    assert tool.remembered == []


def test_registry_persists_before_execution() -> None:
    events: list[str] = []
    tool = DynamicEchoTool()
    tool.remember_approval = lambda arguments: (
        events.append("remember") or ToolResult.success({"remembered": True})
    )  # type: ignore[method-assign]
    tool.execute = lambda arguments: (
        events.append("execute") or ToolResult.success({"text": arguments["text"]})
    )  # type: ignore[method-assign]

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result.ok
    assert events == ["remember", "execute"]


def test_registry_does_not_execute_when_persistence_fails() -> None:
    executions: list[dict[str, Any]] = []
    tool = DynamicEchoTool()
    tool.remember_result = ToolResult.failure(
        "approval_store_error", "Could not save Shell approval."
    )
    tool.execute = lambda arguments: (
        executions.append(arguments) or ToolResult.success({})
    )  # type: ignore[method-assign]

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result.code == "approval_store_error"
    assert executions == []


def test_registry_rejects_always_for_tool_without_persistence_hook() -> None:
    result = ToolRegistry([EchoTool(requires_confirmation=True)]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result.code == "persistent_approval_not_supported"


def test_registry_uses_one_immutable_prepared_execution_context() -> None:
    tool = PreparingEchoTool()
    requests: list[ConfirmationRequest] = []

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: requests.append(request) or ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result == ToolResult.success({"text": "hello"})
    assert requests[0].description == "Echo hello."
    assert requests[0].allow_always is True
    assert tool.events == ["prepare", "remember", "execute"]


@pytest.mark.parametrize("failure_stage", ["description", "callback"])
def test_registry_cancels_and_preserves_confirmation_phase_exception(
    failure_stage: str,
) -> None:
    tool = EchoTool(requires_confirmation=True)
    original_error = RuntimeError(f"{failure_stage} failed")
    cancellations = 0

    def cancel() -> None:
        nonlocal cancellations
        cancellations += 1
        raise ValueError("cancellation failed")

    def confirmation_description(arguments: dict[str, Any]) -> str:
        if failure_stage == "description":
            raise original_error
        return "Echo text."

    def confirm(request: ConfirmationRequest) -> bool:
        if failure_stage == "callback":
            raise original_error
        return True

    tool.cancel = cancel  # type: ignore[attr-defined]
    tool.confirmation_description = (  # type: ignore[method-assign]
        confirmation_description
    )

    with pytest.raises(RuntimeError) as captured:
        ToolRegistry([tool]).execute(
            ToolCall("1", "echo", '{"text":"hello"}'),
            confirm,
        )

    assert captured.value is original_error
    assert cancellations == 1


def test_registry_preflights_before_confirmation() -> None:
    requests: list[ConfirmationRequest] = []
    result = ToolRegistry([EchoTool(requires_confirmation=True)]).execute(
        ToolCall("1", "echo", '{"text":1}'),
        lambda request: requests.append(request) or True,
    )
    assert result.code == "invalid_arguments"
    assert requests == []


def test_register_many_adds_valid_tools_in_order() -> None:
    registry = ToolRegistry([EchoTool(name="first")])

    result = registry.register_many(
        [
            EchoTool(name="second"),
            EchoTool(name="third"),
        ]
    )

    assert result == ToolResult.success({"names": ["second", "third"]})
    assert [item["name"] for item in registry.definitions] == [
        "first",
        "second",
        "third",
    ]


def test_register_many_is_atomic_on_name_conflict() -> None:
    original = EchoTool(name="existing")
    registry = ToolRegistry([original])
    original_definition = registry.definitions[0]
    replacement = EchoTool(name="existing")
    replacement.description = "Replacement behavior."
    replacement.execute = (  # type: ignore[method-assign]
        lambda arguments: ToolResult.success({"text": "replacement"})
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

    result = registry.register_many(
        [
            EchoTool(name="new"),
            EchoTool(name="new"),
        ]
    )

    assert result.code == "tool_name_conflict"
    assert [item["name"] for item in registry.definitions] == ["existing"]


@pytest.mark.parametrize(
    "name",
    ["", "Upper", "has-dash", "1starts_with_digit", "a" * 65],
)
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


def test_builtin_registry_exposes_tools_in_deterministic_order(
    tmp_path: Path,
) -> None:
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


def test_creating_builtin_registry_does_not_create_database(
    tmp_path: Path,
) -> None:
    create_builtin_registry(tmp_path)
    assert not (tmp_path / ".cdy-agent").exists()


def test_failed_tool_result_serializes_optional_structured_data() -> None:
    result = ToolResult.failure(
        "script_failed",
        "Script exited with return code 2.",
        {"returncode": 2, "stdout": "out", "stderr": "err"},
    )

    assert json.loads(result.to_json()) == {
        "ok": False,
        "error": {
            "code": "script_failed",
            "message": "Script exited with return code 2.",
            "data": {"returncode": 2, "stdout": "out", "stderr": "err"},
        },
    }


def test_failed_tool_result_omits_absent_structured_data() -> None:
    result = ToolResult.failure("failed", "No details.")

    assert json.loads(result.to_json()) == {
        "ok": False,
        "error": {"code": "failed", "message": "No details."},
    }
