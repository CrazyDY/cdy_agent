import json
from pathlib import Path

import pytest

from cdy_agent.tools.personal_store import PersonalStore


NOTE = {
    "id": "00000000-0000-4000-8000-000000000001",
    "title": "Plan",
    "content": "Ship phase five",
    "created_at": "2026-07-18T02:00:00Z",
}
TODO = {
    "id": "00000000-0000-4000-8000-000000000002",
    "text": "Write tests",
    "completed": False,
    "created_at": "2026-07-18T02:01:00Z",
    "completed_at": None,
}


def test_empty_store_reads_without_creating_data_directory(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path)

    assert store.load_notes().data == []
    assert store.load_todos().data == []
    assert not (tmp_path / ".cdy-agent").exists()


def test_store_persists_versioned_note_and_todo_documents(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path)

    assert store.save_notes([NOTE]).ok
    assert store.save_todos([TODO]).ok
    assert PersonalStore(tmp_path).load_notes().data == [NOTE]
    assert PersonalStore(tmp_path).load_todos().data == [TODO]
    assert json.loads((tmp_path / ".cdy-agent/notes.json").read_text()) == {
        "version": 1,
        "items": [NOTE],
    }


@pytest.mark.parametrize(
    ("save_method", "filename", "item"),
    [
        ("save_notes", "notes.json", NOTE),
        ("save_todos", "todos.json", TODO),
    ],
)
@pytest.mark.parametrize(
    "invalid_content",
    [
        b"{",
        b"\xff",
        b'{"version": 2, "items": []}',
        b'{"version": 1, "items": {}}',
    ],
    ids=["malformed-json", "non-utf8", "unknown-version", "invalid-structure"],
)
def test_save_refuses_to_overwrite_invalid_existing_store(
    tmp_path: Path,
    save_method: str,
    filename: str,
    item: dict[str, object],
    invalid_content: bytes,
) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    target = data_directory / filename
    target.write_bytes(invalid_content)

    result = getattr(PersonalStore(tmp_path), save_method)([item])

    assert not result.ok
    assert result.code == "invalid_store"
    assert target.read_bytes() == invalid_content


def test_load_rejects_json_note_content_with_lone_surrogate(tmp_path: Path) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    target = data_directory / "notes.json"
    target.write_text(
        json.dumps({"version": 1, "items": [{**NOTE, "content": "\ud800"}]}),
        encoding="ascii",
    )

    result = PersonalStore(tmp_path).load_notes()

    assert result.code == "invalid_store"


def test_load_read_error_returns_store_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    target = data_directory / "notes.json"
    target.write_text(json.dumps({"version": 1, "items": [NOTE]}), encoding="utf-8")
    original_read_text = Path.read_text

    def fail_target_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == target:
            raise OSError("read failed")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target_read)

    result = PersonalStore(tmp_path).load_notes()

    assert result.code == "store_error"


def test_save_refuses_to_overwrite_lone_surrogate_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    target = data_directory / "notes.json"
    original = json.dumps(
        {"version": 1, "items": [{**NOTE, "content": "\ud800"}]}
    ).encode("ascii")
    target.write_bytes(original)

    result = PersonalStore(tmp_path).save_notes([NOTE])

    assert result.code == "invalid_store"
    assert target.read_bytes() == original


def test_save_existing_read_error_preserves_original_and_creates_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    target = data_directory / "notes.json"
    original = json.dumps({"version": 1, "items": [NOTE]}).encode("utf-8")
    target.write_bytes(original)
    original_read_text = Path.read_text

    def fail_target_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == target:
            raise OSError("read failed")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target_read)

    result = PersonalStore(tmp_path).save_notes([])

    assert result.code == "store_error"
    assert target.read_bytes() == original
    assert list(data_directory.glob(".notes.json.*")) == []


@pytest.mark.parametrize("version", [True, 1.0])
def test_store_version_requires_integer_one(
    tmp_path: Path, version: object
) -> None:
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    (data_directory / "notes.json").write_text(
        json.dumps({"version": version, "items": [NOTE]}),
        encoding="utf-8",
    )

    result = PersonalStore(tmp_path).load_notes()

    assert not result.ok
    assert result.code == "invalid_store"


def test_temporary_cleanup_failure_returns_store_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(*_arguments: object) -> None:
        raise OSError("replace failed")

    def fail_unlink(*_arguments: object, **_keywords: object) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    result = PersonalStore(tmp_path, replace=fail_replace).save_notes([NOTE])

    assert not result.ok
    assert result.code == "store_error"


@pytest.mark.parametrize(
    ("save_method", "load_method", "item"),
    [
        ("save_notes", "load_notes", NOTE),
        ("save_todos", "load_todos", TODO),
    ],
)
def test_each_load_returns_fresh_lists_and_item_dictionaries(
    tmp_path: Path,
    save_method: str,
    load_method: str,
    item: dict[str, object],
) -> None:
    store = PersonalStore(tmp_path)
    assert getattr(store, save_method)([item]).ok

    first = getattr(store, load_method)().data
    second = getattr(store, load_method)().data

    assert first == second == [item]
    assert first is not second
    assert first[0] is not second[0]


@pytest.mark.parametrize(
    "document",
    [
        {"version": 2, "items": []},
        {"version": 1, "items": [], "extra": True},
        {"version": 1, "items": [{**NOTE, "extra": True}]},
        {"version": 1, "items": [NOTE, NOTE]},
        {"version": 1, "items": [{**TODO, "completed": True}]},
    ],
)
def test_store_rejects_invalid_documents(tmp_path: Path, document: object) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    filename = (
        "todos.json"
        if isinstance(document, dict)
        and document.get("items")
        and "text" in document["items"][0]
        else "notes.json"
    )
    (data / filename).write_text(json.dumps(document), encoding="utf-8")

    store = PersonalStore(tmp_path)
    result = store.load_todos() if filename == "todos.json" else store.load_notes()

    assert result.code == "invalid_store"


@pytest.mark.parametrize(
    ("save_method", "load_method", "item"),
    [
        (
            "save_notes",
            "load_notes",
            {**NOTE, "title": f" {'x' * 200} "},
        ),
        (
            "save_todos",
            "load_todos",
            {**TODO, "text": f" {'x' * 1000} "},
        ),
    ],
)
def test_store_validates_trimmed_text_without_altering_stored_data(
    tmp_path: Path,
    save_method: str,
    load_method: str,
    item: dict[str, object],
) -> None:
    store = PersonalStore(tmp_path)

    assert getattr(store, save_method)([item]).ok
    assert getattr(store, load_method)().data == [item]


def test_store_rejects_data_directory_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-store"
    outside.mkdir()
    try:
        (tmp_path / ".cdy-agent").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("platform cannot create symlinks")

    assert PersonalStore(tmp_path).load_notes().code == "path_outside_workspace"


def test_store_rejects_data_file_symlink_escape_and_non_utf8(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-notes.json"
    outside.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")
    try:
        (data / "notes.json").symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("platform cannot create symlinks")
    assert PersonalStore(tmp_path).load_notes().code == "path_outside_workspace"

    (data / "notes.json").unlink()
    (data / "notes.json").write_bytes(b"\xff")
    assert PersonalStore(tmp_path).load_notes().code == "invalid_store"


def test_failed_atomic_replace_preserves_original_and_removes_temp_file(
    tmp_path: Path,
) -> None:
    store = PersonalStore(tmp_path)
    assert store.save_notes([NOTE]).ok
    original = (tmp_path / ".cdy-agent/notes.json").read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    result = PersonalStore(tmp_path, replace=fail_replace).save_notes([])

    assert result.code == "store_error"
    assert (tmp_path / ".cdy-agent/notes.json").read_bytes() == original
    assert list((tmp_path / ".cdy-agent").glob(".notes.json.*")) == []
