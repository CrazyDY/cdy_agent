from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from uuid import UUID

import pytest

from cdy_agent.memory import (
    DuplicateMemoryError,
    InvalidMemoryError,
    MemoryConflictError,
    MemoryNotFoundError,
    PreparedCreate,
    PreparedDelete,
    PreparedUpdate,
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


def sequenced_store(tmp_path: Path) -> MemoryStore:
    ids = iter(str(UUID(int=value)) for value in range(1, 100))
    times = iter(FIRST_TIME + timedelta(minutes=value) for value in range(100))
    return MemoryStore(
        tmp_path, clock=lambda: next(times), id_factory=lambda: next(ids)
    )


def store_with_21_sequential_memories(tmp_path: Path) -> MemoryStore:
    store = sequenced_store(tmp_path)
    for value in range(21):
        store.create(f"shared memory {value}", ["shared"])
    return store


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
    with pytest.raises(
        MemoryStoreError, match=r"^Memory clock must be timezone-aware\.$"
    ):
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


def test_identity_hash_uses_cdymem1_length_prefixed_fixed_vector(
    tmp_path: Path,
) -> None:
    draft = MemoryStore(tmp_path).prepare("line 1\n雪", ["标签", "a,b"])

    assert draft.tags == ("a,b", "标签")
    assert draft.identity_hash == (
        "dd55a46a1d395d55ce6b7bbfda0c1e26351a1cd7969bf49b2e4afd042be014ef"
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("a,b", ["c"]), ("a", ["b,c"])),
        (("line\none", ["标签"]), ("line", ["one", "标签"])),
        (("雪", ["é"]), ("雪é", [])),
    ],
)
def test_identity_hash_distinguishes_ambiguous_text_and_tag_boundaries(
    tmp_path: Path,
    first: tuple[str, list[str]],
    second: tuple[str, list[str]],
) -> None:
    store = MemoryStore(tmp_path)
    assert store.prepare(*first).identity_hash != store.prepare(*second).identity_hash


def test_prepare_create_allocates_uuid_and_commit_uses_exact_snapshot(
    tmp_path: Path,
) -> None:
    store = MemoryStore(
        tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: FIRST_ID
    )

    prepared = store.prepare_create("  Remember this  ", [" B ", "a"])

    assert prepared == PreparedCreate(
        FIRST_ID,
        store.prepare("Remember this", ["a", "b"]),
    )
    assert not (tmp_path / ".cdy-agent").exists()
    created = store.commit_create(prepared)
    assert created.id == prepared.memory_id == FIRST_ID
    assert (created.content, created.tags) == (
        prepared.draft.content,
        prepared.draft.tags,
    )


def test_prepared_update_conflicts_with_newer_two_store_record(
    tmp_path: Path,
) -> None:
    times = iter((FIRST_TIME, SECOND_TIME))
    store_a = MemoryStore(
        tmp_path, clock=lambda: next(times), id_factory=lambda: FIRST_ID
    )
    store_b = MemoryStore(tmp_path, clock=lambda: FIRST_TIME + timedelta(hours=3))
    original = store_a.create("original", ["old"])
    prepared = store_a.prepare_update(FIRST_ID, "approved", ["new"])
    newer = store_b.update(FIRST_ID, "newer", ["other"])

    assert prepared == PreparedUpdate(
        original, store_a.prepare("approved", ["new"])
    )
    with pytest.raises(MemoryConflictError, match="changed after confirmation"):
        store_a.commit_update(prepared)
    assert store_a.get(FIRST_ID) == newer


def test_prepared_delete_conflicts_with_newer_two_store_record(
    tmp_path: Path,
) -> None:
    store_a = MemoryStore(
        tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: FIRST_ID
    )
    store_b = MemoryStore(tmp_path, clock=lambda: SECOND_TIME)
    original = store_a.create("original", ["old"])
    prepared = store_a.prepare_delete(FIRST_ID)
    newer = store_b.update(FIRST_ID, "newer", ["other"])

    assert prepared == PreparedDelete(original)
    with pytest.raises(MemoryConflictError, match="changed after confirmation"):
        store_a.commit_delete(prepared)
    assert store_a.get(FIRST_ID) == newer


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


def test_search_casefolds_unicode_and_matches_content_or_tags(
    tmp_path: Path,
) -> None:
    store = sequenced_store(tmp_path)
    first = store.create("Use STRAẞE conventions", ["Python"])
    store.create("unrelated", ["other"])
    assert store.search("strasse python") == (first,)


def test_search_matches_chinese_query_as_one_term(tmp_path: Path) -> None:
    store = sequenced_store(tmp_path)
    record = store.create("项目统一使用 uv 管理依赖", ["工具"])
    assert store.search("使用 uv") == (record,)


def test_search_and_tag_filters_require_every_value(tmp_path: Path) -> None:
    store = sequenced_store(tmp_path)
    exact = store.create("alpha beta", ["one", "two"])
    store.create("alpha only", ["one"])
    assert store.search("alpha beta", ["ONE", "two"]) == (exact,)


def test_search_requires_query_or_tags(tmp_path: Path) -> None:
    with pytest.raises(InvalidMemoryError, match="query or tags"):
        MemoryStore(tmp_path).search()


def test_search_rejects_explicit_whitespace_query_with_tags(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidMemoryError, match="query"):
        MemoryStore(tmp_path).search("   ", ["x"])


def test_search_rejects_query_over_character_limit(tmp_path: Path) -> None:
    with pytest.raises(InvalidMemoryError, match="500 characters"):
        MemoryStore(tmp_path).search("x" * 501)


def test_search_limits_newest_results_and_list_does_not(tmp_path: Path) -> None:
    store = store_with_21_sequential_memories(tmp_path)
    results = store.search("shared")
    assert len(results) == 20
    assert len(store.list_memories()) == 21
    assert [item.updated_at for item in results] == sorted(
        (item.updated_at for item in results), reverse=True
    )


def test_list_filters_all_tags_and_sorts_equal_timestamps_by_id(
    tmp_path: Path,
) -> None:
    ids = iter((SECOND_ID, FIRST_ID))
    store = MemoryStore(
        tmp_path, clock=lambda: FIRST_TIME, id_factory=lambda: next(ids)
    )
    second = store.create("second", ["ONE", "two"])
    first = store.create("first", ["one", "two"])
    assert store.list_memories([" One ", "TWO"]) == (first, second)


def test_retrieval_treats_empty_and_v1_stores_as_empty_without_writes(
    tmp_path: Path,
) -> None:
    empty_workspace = tmp_path / "empty"
    empty_workspace.mkdir()
    assert MemoryStore(empty_workspace).list_memories() == ()
    assert MemoryStore(empty_workspace).search(tags=["tag"]) == ()
    assert not (empty_workspace / ".cdy-agent").exists()

    v1_workspace = tmp_path / "v1"
    v1_workspace.mkdir()
    database = _create_v1_database(v1_workspace)
    store = MemoryStore(v1_workspace)
    assert store.list_memories() == ()
    assert store.search(tags=["tag"]) == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories'"
        ).fetchone() is None
