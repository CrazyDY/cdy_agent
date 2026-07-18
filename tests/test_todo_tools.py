from pathlib import Path
from uuid import UUID

import pytest

from cdy_agent.tools.base import ToolCall
from cdy_agent.tools.personal_store import PersonalStore
from cdy_agent.tools.registry import ToolRegistry
from cdy_agent.tools.todos import (
    CompleteTodoTool,
    CreateTodoTool,
    DeleteTodoTool,
    ListTodosTool,
)


TODO_ID = "00000000-0000-4000-8000-000000000020"
OTHER_TODO_ID = "00000000-0000-4000-8000-000000000021"
CREATED = "2026-07-18T04:00:00Z"
COMPLETED = "2026-07-18T05:00:00Z"


def build_tools(tmp_path: Path):
    store = PersonalStore(tmp_path)
    return (
        store,
        CreateTodoTool(
            store, id_factory=lambda: TODO_ID, now_factory=lambda: CREATED
        ),
        ListTodosTool(store),
        CompleteTodoTool(store, now_factory=lambda: COMPLETED),
        DeleteTodoTool(store),
    )


def test_todo_tool_schemas_and_confirmation_flags(tmp_path: Path) -> None:
    _, create, list_todos, complete, delete = build_tools(tmp_path)

    assert [tool.name for tool in (create, list_todos, complete, delete)] == [
        "create_todo",
        "list_todos",
        "complete_todo",
        "delete_todo",
    ]
    assert [
        tool.requires_confirmation
        for tool in (create, list_todos, complete, delete)
    ] == [True, False, True, True]
    assert create.parameters["additionalProperties"] is False
    assert list_todos.parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_todo_lifecycle(tmp_path: Path) -> None:
    store, create, list_todos, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, list_todos, complete, delete])
    confirmations = []

    created = registry.execute(
        ToolCall("1", "create_todo", '{"text":" Write tests "}'),
        lambda request: confirmations.append(request) or True,
    )

    assert created.data == {
        "id": TODO_ID,
        "text": "Write tests",
        "completed": False,
        "created_at": CREATED,
        "completed_at": None,
    }
    assert confirmations[0].description == "Create Todo: Write tests."
    assert list_todos.execute({}).data == [created.data]

    finished = registry.execute(
        ToolCall("2", "complete_todo", f'{{"todo_id":"{TODO_ID}"}}'),
        lambda request: confirmations.append(request) or True,
    )
    assert finished.data == {
        **created.data,
        "completed": True,
        "completed_at": COMPLETED,
    }
    assert confirmations[1].description == (
        f"Complete Todo {TODO_ID}: Write tests."
    )

    deleted = registry.execute(
        ToolCall("3", "delete_todo", f'{{"todo_id":"{TODO_ID}"}}'),
        lambda request: confirmations.append(request) or True,
    )
    assert deleted.data == {"id": TODO_ID, "deleted": True}
    assert confirmations[2].description == f"Delete Todo {TODO_ID}: Write tests."
    assert store.load_todos().data == []


def test_todo_preflight_rejects_invalid_missing_and_completed_items(
    tmp_path: Path,
) -> None:
    _, create, _, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, complete, delete])
    confirmations = []
    missing = "00000000-0000-4000-8000-000000000099"

    cases = [
        (ToolCall("1", "create_todo", '{"text":" "}'), "invalid_arguments"),
        (
            ToolCall("2", "create_todo", '{"text":"x","extra":1}'),
            "invalid_arguments",
        ),
        (ToolCall("3", "create_todo", '{"text":1}'), "invalid_arguments"),
        (ToolCall("4", "complete_todo", '{"todo_id":"bad"}'), "invalid_arguments"),
        (
            ToolCall(
                "5",
                "complete_todo",
                '{"todo_id":"00000000-0000-4000-8000-000000000020","extra":1}',
            ),
            "invalid_arguments",
        ),
        (
            ToolCall("6", "delete_todo", f'{{"todo_id":"{missing}"}}'),
            "todo_not_found",
        ),
    ]
    for call, code in cases:
        result = registry.execute(
            call, lambda request: confirmations.append(request) or True
        )
        assert result.code == code

    assert confirmations == []
    assert not (tmp_path / ".cdy-agent").exists()

    assert create.execute({"text": "x"}).ok
    assert complete.execute({"todo_id": TODO_ID}).ok
    assert (
        complete.preflight({"todo_id": TODO_ID}).code
        == "todo_already_completed"
    )
    assert complete.execute({"todo_id": TODO_ID}).code == "todo_already_completed"


@pytest.mark.parametrize(
    "text",
    ["x" * 1001, "\ud800"],
)
def test_create_rejects_invalid_text_without_writing(
    tmp_path: Path, text: str
) -> None:
    store, create, _, _, _ = build_tools(tmp_path)

    result = create.execute({"text": text})

    assert result.code == "invalid_arguments"
    assert store.load_todos().data == []
    assert not (tmp_path / ".cdy-agent").exists()


def test_create_accepts_trimmed_1000_character_utf8_text(tmp_path: Path) -> None:
    _, create, _, _, _ = build_tools(tmp_path)
    text = " " + "\u4f60" * 1000 + " "

    result = create.execute({"text": text})

    assert len(text.strip()) == 1000
    assert result.ok
    assert result.data["text"] == text.strip()


def test_declined_todo_mutations_do_not_create_or_change_store(
    tmp_path: Path,
) -> None:
    store, create, _, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, complete, delete])

    denied_create = registry.execute(
        ToolCall("1", "create_todo", '{"text":"Write tests"}'),
        lambda _: False,
    )
    assert denied_create.code == "approval_denied"
    assert not (tmp_path / ".cdy-agent").exists()

    assert create.execute({"text": "Write tests"}).ok
    denied_complete = registry.execute(
        ToolCall("2", "complete_todo", f'{{"todo_id":"{TODO_ID}"}}'),
        lambda _: False,
    )
    denied_delete = registry.execute(
        ToolCall("3", "delete_todo", f'{{"todo_id":"{TODO_ID}"}}'),
        lambda _: False,
    )
    assert denied_complete.code == "approval_denied"
    assert denied_delete.code == "approval_denied"
    assert store.load_todos().data[0]["completed"] is False


def test_todo_tools_propagate_load_errors(tmp_path: Path) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    (data_directory / "todos.json").write_text("{", encoding="utf-8")
    _, create, list_todos, complete, delete = build_tools(tmp_path)

    results = [
        create.execute({"text": "Write tests"}),
        list_todos.execute({}),
        complete.preflight({"todo_id": TODO_ID}),
        complete.execute({"todo_id": TODO_ID}),
        delete.preflight({"todo_id": TODO_ID}),
        delete.execute({"todo_id": TODO_ID}),
    ]

    assert all(
        result is not None and result.code == "invalid_store"
        for result in results
    )


def test_todo_mutations_propagate_save_errors_and_preserve_store(
    tmp_path: Path,
) -> None:
    store, create, _, _, _ = build_tools(tmp_path)
    assert create.execute({"text": "Write tests"}).ok
    original = store.load_todos().data

    def fail_replace(*_arguments: object) -> None:
        raise OSError("replace failed")

    failing_store = PersonalStore(tmp_path, replace=fail_replace)
    failing_create = CreateTodoTool(
        failing_store,
        id_factory=lambda: OTHER_TODO_ID,
        now_factory=lambda: CREATED,
    )
    failing_complete = CompleteTodoTool(
        failing_store, now_factory=lambda: COMPLETED
    )
    failing_delete = DeleteTodoTool(failing_store)

    assert failing_create.execute({"text": "Other"}).code == "store_error"
    assert failing_complete.execute({"todo_id": TODO_ID}).code == "store_error"
    assert failing_delete.execute({"todo_id": TODO_ID}).code == "store_error"
    assert store.load_todos().data == original


def test_mutations_reload_store_after_confirmation(tmp_path: Path) -> None:
    store, create, _, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, complete, delete])
    other_todo = {
        "id": OTHER_TODO_ID,
        "text": "Concurrent",
        "completed": False,
        "created_at": CREATED,
        "completed_at": None,
    }

    created = registry.execute(
        ToolCall("1", "create_todo", '{"text":"Write tests"}'),
        lambda _: store.save_todos([other_todo]).ok,
    )
    assert created.ok
    assert [todo["id"] for todo in store.load_todos().data] == [
        TODO_ID,
        OTHER_TODO_ID,
    ]

    replacement = {**other_todo, "text": "Changed during confirmation"}
    finished = registry.execute(
        ToolCall("2", "complete_todo", f'{{"todo_id":"{TODO_ID}"}}'),
        lambda _: store.save_todos([created.data, replacement]).ok,
    )
    assert finished.ok
    assert store.load_todos().data == [finished.data, replacement]

    deleted = registry.execute(
        ToolCall("3", "delete_todo", f'{{"todo_id":"{TODO_ID}"}}'),
        lambda _: store.save_todos([finished.data, other_todo]).ok,
    )
    assert deleted.data == {"id": TODO_ID, "deleted": True}
    assert store.load_todos().data == [other_todo]


def test_list_todos_sorts_by_created_at_then_id(tmp_path: Path) -> None:
    store, _, list_todos, _, _ = build_tools(tmp_path)
    later = {
        "id": TODO_ID,
        "text": "Later",
        "completed": False,
        "created_at": "2026-07-18T05:00:00Z",
        "completed_at": None,
    }
    same_time_higher_id = {
        "id": OTHER_TODO_ID,
        "text": "Second",
        "completed": False,
        "created_at": CREATED,
        "completed_at": None,
    }
    same_time_lower_id = {
        "id": "00000000-0000-4000-8000-000000000001",
        "text": "First",
        "completed": False,
        "created_at": CREATED,
        "completed_at": None,
    }
    assert store.save_todos([later, same_time_higher_id, same_time_lower_id]).ok

    result = list_todos.execute({})

    assert [todo["id"] for todo in result.data] == [
        same_time_lower_id["id"],
        same_time_higher_id["id"],
        later["id"],
    ]


def test_default_factories_produce_canonical_id_and_utc_timestamps(
    tmp_path: Path,
) -> None:
    store = PersonalStore(tmp_path)
    created = CreateTodoTool(store).execute({"text": "Write tests"})

    assert created.ok
    assert str(UUID(created.data["id"])) == created.data["id"]
    assert created.data["created_at"].endswith("Z")

    finished = CompleteTodoTool(store).execute({"todo_id": created.data["id"]})
    assert finished.ok
    assert finished.data["completed_at"].endswith("Z")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (
            lambda store: CreateTodoTool(
                store,
                id_factory=lambda: "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                now_factory=lambda: CREATED,
            ),
            {"text": "Write tests"},
        ),
        (
            lambda store: CreateTodoTool(
                store,
                id_factory=lambda: TODO_ID,
                now_factory=lambda: "2026-07-18T12:00:00+08:00",
            ),
            {"text": "Write tests"},
        ),
    ],
)
def test_create_does_not_persist_noncanonical_generated_values(
    tmp_path: Path, tool, arguments: dict[str, str]
) -> None:
    store = PersonalStore(tmp_path)

    result = tool(store).execute(arguments)

    assert result.code == "invalid_store"
    assert store.load_todos().data == []


def test_complete_does_not_persist_non_utc_completion_time(tmp_path: Path) -> None:
    store, create, _, _, _ = build_tools(tmp_path)
    assert create.execute({"text": "Write tests"}).ok
    complete = CompleteTodoTool(
        store, now_factory=lambda: "2026-07-18T13:00:00+08:00"
    )

    result = complete.execute({"todo_id": TODO_ID})

    assert result.code == "invalid_store"
    assert store.load_todos().data[0]["completed"] is False
