from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from cdy_agent.memory import MemoryStore, MemoryStoreError
from cdy_agent.tools.base import ToolCall, ToolResult
from cdy_agent.tools.memories import (
    CONTENT_TAGS_SCHEMA,
    ID_SCHEMA,
    SEARCH_SCHEMA,
    UPDATE_SCHEMA,
    ForgetMemoryTool,
    RememberMemoryTool,
    SearchMemoriesTool,
    UpdateMemoryTool,
)
from cdy_agent.tools.registry import ToolRegistry


FIRST_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "99999999-9999-9999-9999-999999999999"
FIRST_TIME = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)


def fixed_store(tmp_path: Path) -> MemoryStore:
    ids = iter(
        (
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )
    )
    times = iter((FIRST_TIME, FIRST_TIME + timedelta(hours=1)))
    return MemoryStore(
        tmp_path, clock=lambda: next(times), id_factory=lambda: next(ids)
    )


def _call(tool_name: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall("call-1", tool_name, json.dumps(arguments))


def test_memory_tool_confirmation_policy(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    assert RememberMemoryTool(store).requires_confirmation is True
    assert SearchMemoriesTool(store).requires_confirmation is False
    assert UpdateMemoryTool(store).requires_confirmation is True
    assert ForgetMemoryTool(store).requires_confirmation is True


def test_memory_tool_definitions_are_exact(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    tools = [
        RememberMemoryTool(store),
        SearchMemoriesTool(store),
        UpdateMemoryTool(store),
        ForgetMemoryTool(store),
    ]
    expected = [
        {
            "type": "function",
            "name": "remember_memory",
            "description": (
                "Store a long-term memory only when the user explicitly asks to "
                "remember it."
            ),
            "parameters": CONTENT_TAGS_SCHEMA,
        },
        {
            "type": "function",
            "name": "search_memories",
            "description": (
                "Search long-term memories only when the user explicitly asks to "
                "search them."
            ),
            "parameters": SEARCH_SCHEMA,
        },
        {
            "type": "function",
            "name": "update_memory",
            "description": (
                "Update a long-term memory only when the user explicitly asks to "
                "change it."
            ),
            "parameters": UPDATE_SCHEMA,
        },
        {
            "type": "function",
            "name": "forget_memory",
            "description": (
                "Delete a long-term memory only when the user explicitly asks to "
                "forget it."
            ),
            "parameters": ID_SCHEMA,
        },
    ]
    assert ToolRegistry(tools).definitions == tuple(expected)


def test_memory_tool_schemas_are_closed(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    pairs = (
        (RememberMemoryTool(store), CONTENT_TAGS_SCHEMA),
        (SearchMemoriesTool(store), SEARCH_SCHEMA),
        (UpdateMemoryTool(store), UPDATE_SCHEMA),
        (ForgetMemoryTool(store), ID_SCHEMA),
    )
    for tool, expected in pairs:
        assert tool.parameters == expected
        assert tool.parameters["additionalProperties"] is False


def test_descriptions_require_explicit_user_request(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    tools = (
        RememberMemoryTool(store),
        SearchMemoriesTool(store),
        UpdateMemoryTool(store),
        ForgetMemoryTool(store),
    )
    for tool in tools:
        assert "only when the user explicitly asks" in tool.description.casefold()


def test_remember_preflight_rejects_duplicate_before_confirmation(
    tmp_path: Path,
) -> None:
    store = fixed_store(tmp_path)
    store.create("Use uv", ["python"])
    result = RememberMemoryTool(store).preflight(
        {"content": "Use uv", "tags": ["PYTHON"]}
    )
    assert result is not None
    assert (result.ok, result.code) == (False, "duplicate_memory")
    assert FIRST_ID in result.message


def test_search_executes_without_confirmation_and_returns_records(
    tmp_path: Path,
) -> None:
    store = fixed_store(tmp_path)
    record = store.create("Use uv", ["python"])
    result = SearchMemoriesTool(store).execute({"query": "uv", "tags": []})
    assert result.ok
    assert result.data == [
        {
            "id": record.id,
            "content": record.content,
            "tags": ["python"],
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    ]


@pytest.mark.parametrize(
    ("factory", "arguments"),
    [
        (RememberMemoryTool, {"content": "text"}),
        (RememberMemoryTool, {"content": "text", "tags": [], "extra": 1}),
        (RememberMemoryTool, {"content": 1, "tags": []}),
        (RememberMemoryTool, {"content": "text", "tags": "tag"}),
        (RememberMemoryTool, {"content": "text", "tags": ["x"] * 11}),
        (SearchMemoriesTool, {"query": "text"}),
        (SearchMemoriesTool, {"query": 1, "tags": []}),
        (SearchMemoriesTool, {"query": None, "tags": [1]}),
        (UpdateMemoryTool, {"memory_id": FIRST_ID, "content": "text"}),
        (
            UpdateMemoryTool,
            {"memory_id": 1, "content": "text", "tags": []},
        ),
        (ForgetMemoryTool, {}),
        (ForgetMemoryTool, {"memory_id": FIRST_ID, "extra": True}),
        (ForgetMemoryTool, {"memory_id": 1}),
    ],
)
def test_malformed_arguments_return_stable_error(
    factory: Callable[[MemoryStore], Any],
    arguments: dict[str, Any],
    tmp_path: Path,
) -> None:
    result = factory(fixed_store(tmp_path)).preflight(arguments)
    assert result is not None
    assert result.code == "invalid_arguments"


def test_update_confirmation_shows_complete_before_and_after(
    tmp_path: Path,
) -> None:
    store = fixed_store(tmp_path)
    original_content = "Original " + "o" * 500
    replacement_content = "Replacement " + "r" * 500
    store.create(original_content, ["FIRST", "second"])
    tool = UpdateMemoryTool(store)
    arguments = {
        "memory_id": FIRST_ID,
        "content": f"  {replacement_content}  ",
        "tags": ["FOURTH", "third"],
    }
    assert tool.preflight(arguments) is None
    description = tool.confirmation_description(arguments)
    assert FIRST_ID in description
    assert "Current:" in description and "Replacement:" in description
    assert original_content in description
    assert replacement_content in description
    for tag in ("first", "second", "third", "fourth"):
        assert tag in description


def test_forget_confirmation_shows_complete_record(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    content = "Do not truncate " + "x" * 700
    store.create(content, ["FIRST", "second"])
    tool = ForgetMemoryTool(store)
    arguments = {"memory_id": FIRST_ID}
    assert tool.preflight(arguments) is None
    description = tool.confirmation_description(arguments)
    assert FIRST_ID in description
    assert content in description
    assert "first" in description
    assert "second" in description


@pytest.mark.parametrize("operation", ["remember", "update", "forget"])
def test_declined_mutations_leave_store_unchanged(
    operation: str, tmp_path: Path
) -> None:
    store = fixed_store(tmp_path)
    original = store.create("Original", ["old"]) if operation != "remember" else None
    if operation == "remember":
        tool = RememberMemoryTool(store)
        arguments = {"content": "New", "tags": ["new"]}
    elif operation == "update":
        tool = UpdateMemoryTool(store)
        arguments = {
            "memory_id": FIRST_ID,
            "content": "Replacement",
            "tags": ["new"],
        }
    else:
        tool = ForgetMemoryTool(store)
        arguments = {"memory_id": FIRST_ID}

    result = ToolRegistry([tool]).execute(
        _call(tool.name, arguments), confirm=lambda _: False
    )

    assert result.code == "approval_denied"
    assert store.list_memories() == (() if original is None else (original,))


class FailingStore:
    def __getattr__(self, name: str) -> Callable[..., Any]:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise MemoryStoreError("safe")

        return fail


@pytest.mark.parametrize(
    ("tool", "method", "arguments"),
    [
        (RememberMemoryTool(FailingStore()), "preflight", {"content": "x", "tags": []}),
        (SearchMemoriesTool(FailingStore()), "execute", {"query": "x", "tags": []}),
        (
            UpdateMemoryTool(FailingStore()),
            "preflight",
            {"memory_id": FIRST_ID, "content": "x", "tags": []},
        ),
        (ForgetMemoryTool(FailingStore()), "preflight", {"memory_id": FIRST_ID}),
    ],
)
def test_store_failures_are_safe_and_stable(
    tool: Any, method: str, arguments: dict[str, Any]
) -> None:
    result = getattr(tool, method)(arguments)
    assert result == ToolResult.failure("memory_store_error", "safe")
    assert "traceback" not in result.message.casefold()


@pytest.mark.parametrize(
    ("tool_factory", "arguments"),
    [
        (
            UpdateMemoryTool,
            {"memory_id": MISSING_ID, "content": "new", "tags": []},
        ),
        (ForgetMemoryTool, {"memory_id": MISSING_ID}),
    ],
)
def test_missing_memory_returns_stable_error(
    tool_factory: Callable[[MemoryStore], Any],
    arguments: dict[str, Any],
    tmp_path: Path,
) -> None:
    result = tool_factory(fixed_store(tmp_path)).preflight(arguments)
    assert result is not None
    assert result == ToolResult.failure("memory_not_found", "Memory not found.")
