import json
from pathlib import Path

import pytest

from cdy_agent.tools.base import ToolCall
from cdy_agent.tools.notes import (
    CreateNoteTool,
    DeleteNoteTool,
    GetNoteTool,
    ListNotesTool,
)
from cdy_agent.tools.personal_store import PersonalStore
from cdy_agent.tools.registry import ToolRegistry


NOTE_ID = "00000000-0000-4000-8000-000000000010"
OTHER_NOTE_ID = "00000000-0000-4000-8000-000000000011"
NOW = "2026-07-18T03:00:00Z"


def build_tools(tmp_path: Path):
    store = PersonalStore(tmp_path)
    return (
        store,
        CreateNoteTool(store, id_factory=lambda: NOTE_ID, now_factory=lambda: NOW),
        ListNotesTool(store),
        GetNoteTool(store),
        DeleteNoteTool(store),
    )


def test_note_tool_schemas_and_confirmation_flags(tmp_path: Path) -> None:
    _, create, list_notes, get, delete = build_tools(tmp_path)

    assert [tool.name for tool in (create, list_notes, get, delete)] == [
        "create_note",
        "list_notes",
        "get_note",
        "delete_note",
    ]
    assert [
        tool.requires_confirmation for tool in (create, list_notes, get, delete)
    ] == [True, False, False, True]
    assert [tool.parameters for tool in (create, list_notes, get, delete)] == [
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["title", "content"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
            "additionalProperties": False,
        },
    ]


def test_note_lifecycle_and_list_omits_content(tmp_path: Path) -> None:
    store, create, list_notes, get, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, list_notes, get, delete])
    confirmations = []

    created = registry.execute(
        ToolCall("1", "create_note", '{"title":" Plan ","content":"Details"}'),
        lambda request: confirmations.append(request) or True,
    )

    assert created.data["id"] == NOTE_ID
    assert created.data["title"] == "Plan"
    assert confirmations[0].description == (
        "Create note 'Plan' with 7 bytes of UTF-8 text."
    )
    assert list_notes.execute({}).data == [
        {"id": NOTE_ID, "title": "Plan", "created_at": NOW}
    ]
    assert "content" not in list_notes.execute({}).data[0]
    assert get.execute({"note_id": NOTE_ID}).data["content"] == "Details"
    assert delete.execute({"note_id": NOTE_ID}).ok
    assert store.load_notes().data == []


def test_note_validation_and_missing_delete_happen_before_confirmation(
    tmp_path: Path,
) -> None:
    _, create, _, _, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, delete])
    confirmations = []

    cases = [
        (
            ToolCall("1", "create_note", '{"title":" ","content":"x"}'),
            "invalid_arguments",
        ),
        (
            ToolCall("2", "create_note", '{"title":"x","content":1}'),
            "invalid_arguments",
        ),
        (
            ToolCall(
                "3", "create_note", '{"title":"x","content":"x","extra":1}'
            ),
            "invalid_arguments",
        ),
        (ToolCall("4", "delete_note", '{"note_id":"bad"}'), "invalid_arguments"),
        (
            ToolCall(
                "5",
                "delete_note",
                '{"note_id":"00000000-0000-4000-8000-000000000099"}',
            ),
            "note_not_found",
        ),
        (
            ToolCall(
                "6",
                "create_note",
                json.dumps({"title": "\ud800", "content": "x"}),
            ),
            "invalid_arguments",
        ),
    ]

    for call, code in cases:
        assert (
            registry.execute(
                call, lambda request: confirmations.append(request) or True
            ).code
            == code
        )
    assert confirmations == []
    assert not (tmp_path / ".cdy-agent").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"title": "x" * 201, "content": "x"},
        {"title": "x", "content": "\ud800"},
        {"title": "x", "content": "你" * 21846},
    ],
)
def test_create_rejects_invalid_text_without_writing(
    tmp_path: Path, arguments: dict[str, str]
) -> None:
    store, create, _, _, _ = build_tools(tmp_path)

    result = create.execute(arguments)

    assert result.code == "invalid_arguments"
    assert store.load_notes().data == []
    assert not (tmp_path / ".cdy-agent").exists()


def test_create_accepts_exactly_64_kib_of_utf8_content(tmp_path: Path) -> None:
    _, create, _, _, _ = build_tools(tmp_path)
    content = "你" * 21845 + "a"

    result = create.execute({"title": "Boundary", "content": content})

    assert len(content.encode("utf-8")) == 64 * 1024
    assert result.ok
    assert result.data["content"] == content


def test_declined_note_mutations_do_not_create_or_change_store(tmp_path: Path) -> None:
    store, create, _, _, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, delete])

    denied_create = registry.execute(
        ToolCall("1", "create_note", '{"title":"Plan","content":"Details"}'),
        lambda _: False,
    )

    assert denied_create.code == "approval_denied"
    assert not (tmp_path / ".cdy-agent").exists()
    assert create.execute({"title": "Plan", "content": "Details"}).ok

    delete_confirmations = []
    denied_delete = registry.execute(
        ToolCall("2", "delete_note", f'{{"note_id":"{NOTE_ID}"}}'),
        lambda request: delete_confirmations.append(request) or False,
    )

    assert denied_delete.code == "approval_denied"
    assert delete_confirmations[0].description == (
        f"Delete note {NOTE_ID} titled 'Plan'."
    )
    assert len(store.load_notes().data) == 1


def test_note_tools_propagate_load_errors(tmp_path: Path) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    (data_directory / "notes.json").write_text("{", encoding="utf-8")
    _, create, list_notes, get, delete = build_tools(tmp_path)

    results = [
        create.execute({"title": "Plan", "content": "Details"}),
        list_notes.execute({}),
        get.execute({"note_id": NOTE_ID}),
        delete.preflight({"note_id": NOTE_ID}),
        delete.execute({"note_id": NOTE_ID}),
    ]

    assert all(
        result is not None and result.code == "invalid_store" for result in results
    )


def test_create_note_store_failure_happens_before_confirmation(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    (data_directory / "notes.json").write_text("{", encoding="utf-8")
    _, create, _, _, _ = build_tools(tmp_path)
    confirmations = []

    result = ToolRegistry([create]).execute(
        ToolCall("1", "create_note", '{"title":"Plan","content":"Details"}'),
        lambda request: confirmations.append(request) or True,
    )

    assert result.code == "invalid_store"
    assert confirmations == []


def test_create_note_preflight_does_not_create_empty_store(tmp_path: Path) -> None:
    _, create, _, _, _ = build_tools(tmp_path)

    assert create.preflight({"title": "Plan", "content": "Details"}) is None
    assert not (tmp_path / ".cdy-agent").exists()


def test_note_mutations_propagate_save_errors_and_preserve_store(
    tmp_path: Path,
) -> None:
    store, create, _, _, _ = build_tools(tmp_path)
    assert create.execute({"title": "Plan", "content": "Details"}).ok
    original = store.load_notes().data

    def fail_replace(*_arguments: object) -> None:
        raise OSError("replace failed")

    failing_store = PersonalStore(tmp_path, replace=fail_replace)
    failing_create = CreateNoteTool(
        failing_store,
        id_factory=lambda: OTHER_NOTE_ID,
        now_factory=lambda: NOW,
    )
    failing_delete = DeleteNoteTool(failing_store)

    assert (
        failing_create.execute({"title": "Other", "content": "x"}).code
        == "store_error"
    )
    assert failing_delete.execute({"note_id": NOTE_ID}).code == "store_error"
    assert store.load_notes().data == original


def test_mutations_reload_store_after_confirmation(tmp_path: Path) -> None:
    store, create, _, _, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, delete])
    other_note = {
        "id": OTHER_NOTE_ID,
        "title": "Concurrent",
        "content": "Keep this",
        "created_at": NOW,
    }

    created = registry.execute(
        ToolCall("1", "create_note", '{"title":"Plan","content":"Details"}'),
        lambda _: store.save_notes([other_note]).ok,
    )

    assert created.ok
    assert [note["id"] for note in store.load_notes().data] == [NOTE_ID, OTHER_NOTE_ID]

    replacement = {**other_note, "title": "Changed during confirmation"}
    deleted = registry.execute(
        ToolCall("2", "delete_note", f'{{"note_id":"{NOTE_ID}"}}'),
        lambda _: store.save_notes([created.data, replacement]).ok,
    )

    assert deleted.data == {"id": NOTE_ID, "deleted": True}
    assert store.load_notes().data == [replacement]


def test_list_notes_sorts_by_created_at_then_id(tmp_path: Path) -> None:
    store, _, list_notes, _, _ = build_tools(tmp_path)
    later_note = {
        "id": NOTE_ID,
        "title": "Later",
        "content": "later",
        "created_at": "2026-07-18T04:00:00Z",
    }
    same_time_higher_id = {
        "id": OTHER_NOTE_ID,
        "title": "Second",
        "content": "second",
        "created_at": NOW,
    }
    same_time_lower_id = {
        "id": "00000000-0000-4000-8000-000000000001",
        "title": "First",
        "content": "first",
        "created_at": NOW,
    }
    assert store.save_notes([later_note, same_time_higher_id, same_time_lower_id]).ok

    result = list_notes.execute({})

    assert [note["id"] for note in result.data] == [
        same_time_lower_id["id"],
        same_time_higher_id["id"],
        later_note["id"],
    ]
