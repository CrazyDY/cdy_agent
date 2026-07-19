from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from cdy_agent.memory import (
    DuplicateMemoryError,
    InvalidMemoryError,
    MemoryNotFoundError,
    MemoryStore,
    MemoryStoreError,
)


FIRST_ID = "11111111-1111-1111-1111-111111111111"
SECOND_ID = "22222222-2222-2222-2222-222222222222"
FIRST_TIME = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
SECOND_TIME = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)

V1_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE messages (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    PRIMARY KEY (session_id, sequence),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
PRAGMA user_version = 1;
"""


class _BrokenTimezoneConversion(datetime):
    def astimezone(self, tz=None):
        raise OSError("conversion failed")


def _database(tmp_path: Path) -> Path:
    return tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"


def _create_v1_database(tmp_path: Path) -> Path:
    database = _database(tmp_path)
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript(V1_SCHEMA)
    return database


def test_create_normalizes_content_tags_and_timestamps(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: FIRST_ID)
    record = store.create("  Use uv for Python\nprojects.  ", [" Python ", "TOOLS", "python"])
    assert record.id == FIRST_ID
    assert record.content == "Use uv for Python\nprojects."
    assert record.tags == ("python", "tools")
    assert record.created_at == "2026-07-19T01:00:00.000000Z"
    assert store.get(FIRST_ID) == record


@pytest.mark.parametrize(
    ("content", "tags", "message"),
    [
        ("   ", [], "content"),
        ("x" * 8193, [], "8 KiB"),
        ("valid", ["x"] * 11, "10 tags"),
        ("valid", [" "], "tag"),
        ("valid", ["x" * 51], "50 characters"),
    ],
)
def test_create_rejects_invalid_memory(content, tags, message, tmp_path: Path) -> None:
    with pytest.raises(InvalidMemoryError, match=message):
        MemoryStore(tmp_path).create(content, tags)


def test_exact_duplicate_reports_existing_id(tmp_path: Path) -> None:
    ids = iter((FIRST_ID, SECOND_ID))
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: next(ids))
    store.create("Remember this", ["B", "a"])
    with pytest.raises(DuplicateMemoryError) as caught:
        store.create("Remember this", ["A", "b"])
    assert caught.value.existing_id == FIRST_ID


def test_update_replaces_content_and_tags_but_preserves_identity(tmp_path: Path) -> None:
    times = iter((FIRST_TIME, SECOND_TIME))
    store = MemoryStore(tmp_path, clock=lambda: next(times), id_factory=lambda: FIRST_ID)
    original = store.create("old", ["before"])
    updated = store.update(FIRST_ID, "new", ["AFTER"])
    assert updated.id == original.id
    assert updated.created_at == original.created_at
    assert updated.updated_at == "2026-07-19T02:00:00.000000Z"
    assert (updated.content, updated.tags) == ("new", ("after",))


def test_delete_removes_memory_and_tags(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: FIRST_ID)
    store.create("remove", ["tag"])
    store.delete(FIRST_ID)
    with pytest.raises(MemoryNotFoundError, match="Memory not found"):
        store.get(FIRST_ID)
    with sqlite3.connect(_database(tmp_path)) as connection:
        assert connection.execute("SELECT * FROM memory_tags").fetchall() == []


def test_get_rejects_noncanonical_uuid(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidMemoryError, match=r"^Memory ID must be a complete UUID\.$"
    ):
        MemoryStore(tmp_path).get("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA")


def test_create_rejects_naive_clock(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME.replace(tzinfo=None))
    with pytest.raises(MemoryStoreError, match=r"^Memory clock is invalid\.$"):
        store.create("valid", [])


def test_create_rejects_failed_timezone_conversion(tmp_path: Path) -> None:
    broken = _BrokenTimezoneConversion(
        2026, 7, 19, 1, 0, tzinfo=timezone.utc
    )
    store = MemoryStore(tmp_path, clock=lambda: broken)
    with pytest.raises(MemoryStoreError, match=r"^Memory clock is invalid\.$"):
        store.create("valid", [])


def test_create_rejects_tag_that_is_not_utf8(tmp_path: Path) -> None:
    with pytest.raises(
        InvalidMemoryError, match=r"^Each memory tag must be UTF-8 text\.$"
    ):
        MemoryStore(tmp_path).create("valid", ["\ud800"])


def test_update_duplicate_rolls_back_original(tmp_path: Path) -> None:
    ids = iter((FIRST_ID, SECOND_ID))
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: next(ids))
    first = store.create("first", ["same"])
    second = store.create("second", ["other"])
    with pytest.raises(DuplicateMemoryError) as caught:
        store.update(SECOND_ID, first.content, first.tags)
    assert caught.value.existing_id == FIRST_ID
    assert store.get(FIRST_ID) == first
    assert store.get(SECOND_ID) == second


def test_content_limit_counts_utf8_bytes(tmp_path: Path) -> None:
    ids = iter((FIRST_ID, SECOND_ID))
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: next(ids))
    assert store.create("é" * 4096, []).content == "é" * 4096
    with pytest.raises(InvalidMemoryError, match="8 KiB"):
        store.create("é" * 4097, [])


def test_create_rejects_invalid_id_factory_value(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, id_factory=lambda: "short")
    with pytest.raises(
        InvalidMemoryError, match=r"^Memory ID must be a complete UUID\.$"
    ):
        store.create("valid", [])


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        ("UPDATE memories SET updated_at = ?", ("2026-07-19T01:00:00+00:00",)),
        ("UPDATE memory_tags SET tag = ?", (" INVALID ",)),
        ("UPDATE memories SET identity_hash = ?", ("0" * 64,)),
    ],
)
def test_get_rejects_corrupt_stored_data(
    statement: str, parameters: tuple[str], tmp_path: Path
) -> None:
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: FIRST_ID)
    store.create("valid", ["tag"])
    with sqlite3.connect(_database(tmp_path)) as connection:
        connection.execute(statement, parameters)
    with pytest.raises(
        InvalidMemoryError, match=r"^Stored memory data is invalid\.$"
    ):
        store.get(FIRST_ID)


def test_failed_tag_update_rolls_back_content_and_tags(tmp_path: Path) -> None:
    times = iter((FIRST_TIME, SECOND_TIME))
    store = MemoryStore(tmp_path, clock=lambda: next(times), id_factory=lambda: FIRST_ID)
    original = store.create("old", ["before"])
    with sqlite3.connect(_database(tmp_path)) as connection:
        connection.execute(
            "CREATE TRIGGER reject_memory_tags BEFORE INSERT ON memory_tags "
            "BEGIN SELECT RAISE(ABORT, 'stop'); END"
        )
    with pytest.raises(MemoryStoreError, match="Could not write memory data"):
        store.update(FIRST_ID, "new", ["after"])
    with sqlite3.connect(_database(tmp_path)) as connection:
        connection.execute("DROP TRIGGER reject_memory_tags")
    assert store.get(FIRST_ID) == original


def test_prepare_and_find_duplicate_are_non_mutating(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: FIRST_ID)
    draft = store.prepare("  Remember  ", [" B ", "a"])
    assert draft.content == "Remember"
    assert draft.tags == ("a", "b")
    assert len(draft.identity_hash) == 64
    assert store.find_duplicate(draft) is None
    assert not (tmp_path / ".cdy-agent").exists()
    existing = store.create(draft.content, draft.tags)
    assert store.find_duplicate(draft) == existing
    assert store.find_duplicate(draft, exclude_id=FIRST_ID) is None


def test_find_duplicate_treats_v1_database_as_empty(tmp_path: Path) -> None:
    database = _create_v1_database(tmp_path)
    store = MemoryStore(tmp_path)
    draft = store.prepare("remember", ["tag"])
    assert store.find_duplicate(draft) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories'"
        ).fetchone() is None


def test_get_treats_v1_database_as_empty(tmp_path: Path) -> None:
    database = _create_v1_database(tmp_path)
    with pytest.raises(MemoryNotFoundError, match=r"^Memory not found\.$"):
        MemoryStore(tmp_path).get(FIRST_ID)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories'"
        ).fetchone() is None
