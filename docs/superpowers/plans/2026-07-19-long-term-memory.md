# Long-Term Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicitly controlled, workspace-scoped long-term memory with SQLite keyword/tag retrieval, model tools, CLI management, and safe forgetting.

**Architecture:** Extract the existing SQLite path and schema lifecycle into a focused `WorkspaceDatabase`, while keeping `ConversationStore` and the new `MemoryStore` as separate domain stores over the same schema-v2 database. Register four memory tools in the existing tool loop and expose five Typer subcommands; no memory is loaded unless the user explicitly causes a search tool call or runs a read command.

**Tech Stack:** Python 3.10+, standard-library `sqlite3`, `hashlib`, `json`, and `uuid`; Typer; pytest; existing OpenAI-compatible Agent Tool Loop; uv/Hatchling.

## Global Constraints

- Keep application code under `src/cdy_agent/` and matching pytest files under `tests/`.
- Do not add runtime dependencies or a vector database.
- Store data only in `<workspace>/.cdy-agent/cdy-agent.sqlite3`; reject symlinks, path escapes, and non-regular database files.
- Long-term memory is workspace-scoped and is never automatically extracted, retrieved, or injected into model messages.
- `ask` and `chat` expose memory tools, but all model-side memory operations must be described as valid only after an explicit user request.
- Every add, update, and forget operation requires default-No confirmation; reads do not require confirmation.
- Content is trimmed, non-empty, and at most 8 KiB UTF-8; preserve internal whitespace.
- Tags are Unicode-casefolded, deduplicated, sorted, 1–50 characters each after normalization, with at most 10 per memory.
- Search queries are optional only when tags are supplied, at most 500 characters, casefolded, whitespace-tokenized, and matched with AND semantics; tag filters also use AND semantics.
- Search returns at most 20 records ordered by `updated_at` descending and ID ascending; list returns all matches in the same order.
- Reject only exact normalized content-and-tag duplicates; do not infer semantic similarity.
- Preserve all v1 conversation data during atomic migration to schema v2, and preserve the public `ConversationStore` API.
- Tests must use temporary workspaces, deterministic clocks/UUIDs, mocked model boundaries, and no network or real credentials.
- Preserve unrelated working-tree changes and never add credentials, `.idea`, generated images, caches, or model responses to a feature commit.

---

## File Map

- Create `src/cdy_agent/memory/database.py`: shared path validation, read/write connections, schema-v2 creation, and v1-to-v2 migration.
- Modify `src/cdy_agent/memory/sqlite.py`: retain only conversation domain behavior and delegate database lifecycle to `WorkspaceDatabase`.
- Create `src/cdy_agent/memory/long_term.py`: immutable memory records, validation, exact-duplicate identity, CRUD, listing, and keyword/tag search.
- Modify `src/cdy_agent/memory/__init__.py`: export conversation and long-term memory public types.
- Create `src/cdy_agent/tools/memories.py`: remember/search/update/forget tool adapters.
- Modify `src/cdy_agent/tools/__init__.py`: register one workspace `MemoryStore` and its four tools.
- Modify `src/cdy_agent/cli.py`: add the `memories` Typer group and five subcommands without staging the unrelated credential-bearing hunk.
- Create `tests/test_memory_database.py`: schema creation, migration, rollback, and path-boundary tests.
- Modify `tests/test_conversation_store.py`: regression coverage for conversation behavior over schema v2 and readable schema v1.
- Create `tests/test_memory_store.py`: validation, CRUD, duplicate identity, search, sorting, and rollback tests.
- Create `tests/test_memory_tools.py`: protocol definitions, preflight, confirmation, denial, and result conversion tests.
- Modify `tests/test_tool_registry.py`: deterministic built-in registration assertions if the current expected tool list is explicit.
- Modify `tests/test_cli.py`: CLI add/list/search/update/delete and registry integration tests.
- Modify `README.md`: document explicit memory control and commands.
- Modify `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`: mark stage 7 complete.

### Task 1: Shared SQLite lifecycle and schema-v2 migration

**Files:**
- Create: `src/cdy_agent/memory/database.py`
- Modify: `src/cdy_agent/memory/sqlite.py`
- Create: `tests/test_memory_database.py`
- Modify: `tests/test_conversation_store.py`

**Interfaces:**
- Produces: `WorkspaceDatabase(workspace: Path)`.
- Produces: `WorkspaceDatabase.read() -> ContextManager[sqlite3.Connection | None]`; it accepts schema versions 1 and 2 and never creates or migrates.
- Produces: `WorkspaceDatabase.write() -> ContextManager[sqlite3.Connection]`; it starts one transaction, creates v2 or migrates v1, commits on success, and rolls back on error.
- Produces: `SCHEMA_VERSION = 2`, `DATA_DIRECTORY`, and `DATABASE_FILENAME` in `memory.database`.
- Preserves: all `ConversationStore` constructor and public method signatures.

- [ ] **Step 1: Write failing schema and migration tests**

Create `tests/test_memory_database.py` with fixed v1 setup SQL and these concrete cases:

```python
from pathlib import Path
import sqlite3

import pytest

from cdy_agent.memory.database import WorkspaceDatabase


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


def test_read_empty_database_does_not_create_files(tmp_path: Path) -> None:
    with WorkspaceDatabase(tmp_path).read() as connection:
        assert connection is None
    assert not (tmp_path / ".cdy-agent").exists()


def test_first_write_creates_schema_version_two(tmp_path: Path) -> None:
    with WorkspaceDatabase(tmp_path).write() as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
    assert {"sessions", "messages", "memories", "memory_tags"} <= names
    path = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def test_write_migrates_v1_without_changing_conversations(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    path = data / "cdy-agent.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(V1_SCHEMA)
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("11111111-1111-1111-1111-111111111111", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            [
                ("11111111-1111-1111-1111-111111111111", 0, "user", "hello"),
                ("11111111-1111-1111-1111-111111111111", 1, "assistant", "hi"),
            ],
        )
    with WorkspaceDatabase(tmp_path).write():
        pass
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute("SELECT role, content FROM messages ORDER BY sequence").fetchall() == [
            ("user", "hello"), ("assistant", "hi")
        ]


def test_failed_write_rolls_back_schema_migration(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    path = data / "cdy-agent.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(V1_SCHEMA)
    with pytest.raises(RuntimeError, match="stop"):
        with WorkspaceDatabase(tmp_path).write():
            raise RuntimeError("stop")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories'"
        ).fetchone() is None
```

Add `test_read_rejects_symlinked_data_directory`, `test_read_rejects_symlinked_database`, and `test_read_rejects_non_regular_database` by creating those exact filesystem shapes under `tmp_path` and asserting `InvalidConversationStoreError` matches `symbolic link` or `regular file`. Add `test_read_rejects_corrupt_database` by writing `b"not sqlite"` to the database path and asserting the public error contains `read` but not `sqlite`. Add `test_read_rejects_unsupported_version` by creating the v1 tables, setting `PRAGMA user_version = 3`, and asserting `schema version is not supported`.

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `uv run pytest tests/test_memory_database.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'cdy_agent.memory.database'`.

- [ ] **Step 3: Implement `WorkspaceDatabase` and schema v2**

Create `src/cdy_agent/memory/database.py` with these exact public constants and schema lifecycle:

```python
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from cdy_agent.tools.filesystem import resolve_workspace


DATA_DIRECTORY = ".cdy-agent"
DATABASE_FILENAME = "cdy-agent.sqlite3"
SCHEMA_VERSION = 2

SESSION_STATEMENTS = (
    "CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE messages (session_id TEXT NOT NULL, sequence INTEGER NOT NULL, role TEXT NOT NULL CHECK (role IN ('user', 'assistant')), content TEXT NOT NULL CHECK (length(trim(content)) > 0), PRIMARY KEY (session_id, sequence), FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE)",
)
MEMORY_STATEMENTS = (
    "CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT NOT NULL CHECK (length(trim(content)) > 0), identity_hash TEXT NOT NULL UNIQUE CHECK (length(identity_hash) = 64), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE memory_tags (memory_id TEXT NOT NULL, tag TEXT NOT NULL CHECK (length(trim(tag)) > 0), PRIMARY KEY (memory_id, tag), FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE)",
)


class WorkspaceDatabase:
    def __init__(self, workspace: Path) -> None:
        self.workspace = resolve_workspace(workspace)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection | None]:
        path = self._path(create=False)
        if path is None:
            yield None
            return
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            self._configure(connection)
            self._require_readable_version(connection)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        path = self._path(create=True)
        assert path is not None
        new_file = not path.exists()
        connection = sqlite3.connect(path)
        try:
            self._configure(connection)
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if new_file:
                for statement in (*SESSION_STATEMENTS, *MEMORY_STATEMENTS):
                    connection.execute(statement)
            elif version == 1:
                for statement in MEMORY_STATEMENTS:
                    connection.execute(statement)
            elif version != SCHEMA_VERSION:
                raise InvalidConversationStoreError(
                    "Conversation database schema version is not supported."
                )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            connection.close()
            if new_file and path.exists():
                path.unlink()
            raise
        else:
            connection.close()

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _require_readable_version(connection: sqlite3.Connection) -> int:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {1, SCHEMA_VERSION}:
            raise InvalidConversationStoreError(
                "Conversation database schema version is not supported."
            )
        return version
```

Move `ConversationStoreError`, `ConversationNotFoundError`, and `InvalidConversationStoreError` into this file above `WorkspaceDatabase` so both stores share the same safe error family. Move the current `_database_path` body into `WorkspaceDatabase._path(create: bool)` unchanged except for domain-neutral message wording (`Data path...`, `Database...`). Wrap `sqlite3.Error`, `OSError`, and unlink failures in the shared safe error types; never expose SQL.

- [ ] **Step 4: Refactor `ConversationStore` onto the shared lifecycle**

In `src/cdy_agent/memory/sqlite.py`, import the constants, errors, and `WorkspaceDatabase`; remove local schema/path code; construct `self._database = WorkspaceDatabase(workspace)`. Replace read blocks with `self._database.read()` and accept both schema versions. Replace write/delete blocks with `self._database.write()` so v1 migrates before the existing DML. Keep `_validated_messages`, timestamp validation, record dataclasses, exception messages, and all public signatures unchanged.

Add this regression test to `tests/test_conversation_store.py`:

```python
def test_append_turn_migrates_v1_and_preserves_existing_history(tmp_path: Path) -> None:
    create_v1_conversation_database(tmp_path, session_id=SESSION_ID)
    store = make_store(tmp_path)
    store.append_turn(
        SESSION_ID,
        Message(role="user", content="second"),
        Message(role="assistant", content="reply"),
    )
    assert [message.content for message in store.load(SESSION_ID).messages] == [
        "first", "answer", "second", "reply"
    ]
```

Implement `create_v1_conversation_database` in the same test file using the v1 SQL shown in Step 1 and two valid initial messages.

- [ ] **Step 5: Run shared database and conversation tests**

Run: `uv run pytest tests/test_memory_database.py tests/test_conversation_store.py -v`

Expected: all tests pass, including all pre-existing conversation corruption and path-boundary tests.

- [ ] **Step 6: Commit the shared database slice**

```bash
git add src/cdy_agent/memory/database.py src/cdy_agent/memory/sqlite.py tests/test_memory_database.py tests/test_conversation_store.py
git commit -m "Add shared memory database migration"
```

### Task 2: Long-term memory validation and CRUD

**Files:**
- Create: `src/cdy_agent/memory/long_term.py`
- Modify: `src/cdy_agent/memory/__init__.py`
- Create: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `WorkspaceDatabase.read()` and `WorkspaceDatabase.write()` from Task 1.
- Produces: `StoredMemory(id: str, content: str, tags: tuple[str, ...], created_at: str, updated_at: str)`.
- Produces: `MemoryDraft(content: str, tags: tuple[str, ...], identity_hash: str)` for non-mutating preflight and confirmation rendering.
- Produces: `MemoryStore(workspace: Path, *, clock: Callable[[], datetime] = _now, id_factory: Callable[[], str] = _new_id)`.
- Produces: `prepare(content: str, tags: Sequence[str]) -> MemoryDraft` and `find_duplicate(draft: MemoryDraft, *, exclude_id: str | None = None) -> StoredMemory | None`, both non-mutating.
- Produces: `create(content: str, tags: Sequence[str]) -> StoredMemory`, `get(memory_id: str) -> StoredMemory`, `update(memory_id: str, content: str, tags: Sequence[str]) -> StoredMemory`, and `delete(memory_id: str) -> None`.
- Produces: `DuplicateMemoryError(existing_id: str)`, `MemoryNotFoundError`, `InvalidMemoryError`, and `MemoryStoreError`.

- [ ] **Step 1: Write failing CRUD, validation, and duplicate tests**

Create `tests/test_memory_store.py` with deterministic factories and the following core cases:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cdy_agent.memory import (
    DuplicateMemoryError,
    InvalidMemoryError,
    MemoryNotFoundError,
    MemoryStore,
)


FIRST_ID = "11111111-1111-1111-1111-111111111111"
SECOND_ID = "22222222-2222-2222-2222-222222222222"
FIRST_TIME = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
SECOND_TIME = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)


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
```

Add these named tests with direct assertions: `test_get_rejects_noncanonical_uuid` expects `InvalidMemoryError("Memory ID must be a complete UUID.")`; `test_create_rejects_naive_clock` expects `MemoryStoreError("Memory clock must be timezone-aware.")`; `test_update_duplicate_rolls_back_original` creates two records, attempts to replace the second with the first's draft, then asserts both original records are unchanged; `test_content_limit_counts_utf8_bytes` accepts `"é" * 4096` (8192 UTF-8 bytes) and rejects one additional character; `test_create_rejects_invalid_id_factory_value` returns `"short"` and expects the canonical UUID error. Corruption tests must update one stored column at a time (`updated_at`, `tag`, or `identity_hash`) through `sqlite3`, then assert `InvalidMemoryError("Stored memory data is invalid.")`. A rollback test must install a SQLite trigger that aborts `memory_tags` insertion, call `update`, assert `MemoryStoreError`, drop the trigger, and assert the old content and tags remain.

- [ ] **Step 2: Run the memory-store tests and verify failure**

Run: `uv run pytest tests/test_memory_store.py -v`

Expected: collection fails because `MemoryStore` and its error types are not exported.

- [ ] **Step 3: Implement immutable records and normalization**

Create `src/cdy_agent/memory/long_term.py` with these definitions and constants:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .database import InvalidConversationStoreError, WorkspaceDatabase


MAX_CONTENT_BYTES = 8 * 1024
MAX_TAGS = 10
MAX_TAG_CHARACTERS = 50
MAX_QUERY_CHARACTERS = 500
MAX_SEARCH_RESULTS = 20


class MemoryStoreError(RuntimeError):
    """A long-term memory operation failed safely."""


class InvalidMemoryError(MemoryStoreError):
    """Memory input or stored data is invalid."""


class MemoryNotFoundError(MemoryStoreError):
    """The requested memory does not exist."""


class DuplicateMemoryError(MemoryStoreError):
    def __init__(self, existing_id: str) -> None:
        super().__init__(f"Memory duplicates existing memory {existing_id}.")
        self.existing_id = existing_id


@dataclass(frozen=True)
class StoredMemory:
    id: str
    content: str
    tags: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MemoryDraft:
    content: str
    tags: tuple[str, ...]
    identity_hash: str


def _normalize_content(content: object) -> str:
    if not isinstance(content, str):
        raise InvalidMemoryError("Memory content must be UTF-8 text.")
    value = content.strip()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise InvalidMemoryError("Memory content must be UTF-8 text.") from error
    if not value:
        raise InvalidMemoryError("Memory content must not be empty.")
    if size > MAX_CONTENT_BYTES:
        raise InvalidMemoryError("Memory content must be at most 8 KiB.")
    return value


def _normalize_tags(tags: object) -> tuple[str, ...]:
    if not isinstance(tags, Sequence) or isinstance(tags, (str, bytes)):
        raise InvalidMemoryError("Memory tags must be a list of strings.")
    if len(tags) > MAX_TAGS:
        raise InvalidMemoryError("Memory must have at most 10 tags.")
    normalized: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise InvalidMemoryError("Each memory tag must be text.")
        value = tag.strip().casefold()
        if not value:
            raise InvalidMemoryError("Each memory tag must not be empty.")
        if len(value) > MAX_TAG_CHARACTERS:
            raise InvalidMemoryError("Each memory tag must be at most 50 characters.")
        normalized.add(value)
    return tuple(sorted(normalized))


def _identity(content: str, tags: tuple[str, ...]) -> str:
    payload = json.dumps([content, list(tags)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Implement `_canonical_uuid` with `UUID(value)` plus `str(parsed) == value`; `_timestamp` must reject naive datetimes and emit UTC microsecond ISO text ending in `Z`; `_require_timestamp` must parse only UTC `Z` strings; `_new_id` returns `str(uuid4())`; `_now` returns `datetime.now(timezone.utc)`. Each invalid input raises the exact memory-specific error asserted in Step 1.

- [ ] **Step 4: Implement transactional CRUD**

Implement `MemoryStore.prepare` by returning `MemoryDraft(_normalize_content(content), normalized_tags, _identity(...))`. Implement `find_duplicate` as a read-only lookup by `identity_hash`, verify the full content and tags to distinguish a theoretical hash collision, and honor `exclude_id`. Implement `create` so it inserts `memories` then sorted `memory_tags` in one `WorkspaceDatabase.write()` context; on the identity unique constraint, load the existing row and raise `DuplicateMemoryError(existing_id)`. `get` reads the memory row and ordered tags through `WorkspaceDatabase.read()`. `update` prepares first, verifies the target, rejects `find_duplicate(..., exclude_id=memory_id)`, updates the row, deletes old tags, inserts new tags, and returns the reconstructed record in one transaction. `delete` checks `rowcount == 1`; foreign-key cascade removes tags.

All row reconstruction must validate canonical UUID, content, identity hash, timestamps, tag normalization/order, and recomputed identity. Read methods convert `sqlite3.Error` to `MemoryStoreError("Could not read memory data.")`; create/update convert it to `MemoryStoreError("Could not write memory data.")`; delete converts it to `MemoryStoreError("Could not delete memory data.")`. Typed domain errors pass through unchanged.

Export all public record/store/error types from `src/cdy_agent/memory/__init__.py` without removing existing conversation exports.

- [ ] **Step 5: Run CRUD tests**

Run: `uv run pytest tests/test_memory_store.py -k 'not search and not list' -v`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the CRUD slice**

```bash
git add src/cdy_agent/memory/long_term.py src/cdy_agent/memory/__init__.py tests/test_memory_store.py
git commit -m "Add long-term memory storage"
```

### Task 3: Deterministic list and keyword/tag retrieval

**Files:**
- Modify: `src/cdy_agent/memory/long_term.py`
- Modify: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `StoredMemory` and normalization helpers from Task 2.
- Produces: `MemoryStore.list_memories(tags: Sequence[str] = ()) -> tuple[StoredMemory, ...]`.
- Produces: `MemoryStore.search(query: str | None = None, tags: Sequence[str] = ()) -> tuple[StoredMemory, ...]`.

- [ ] **Step 1: Add failing retrieval tests**

Append concrete tests covering Unicode, Chinese, AND matching, filters, limit, and sorting:

```python
def test_search_casefolds_unicode_and_matches_content_or_tags(tmp_path: Path) -> None:
    store = sequenced_store(tmp_path)
    first = store.create("Use STRASSE conventions", ["Python"])
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


def test_search_limits_newest_results_and_list_does_not(tmp_path: Path) -> None:
    store = store_with_21_sequential_memories(tmp_path)
    assert len(store.search("shared")) == 20
    assert len(store.list_memories()) == 21
    assert [item.updated_at for item in store.search("shared")] == sorted(
        (item.updated_at for item in store.search("shared")), reverse=True
    )
```

The helpers must use 21 canonical deterministic UUIDs and timezone-aware timestamps, not random values.

- [ ] **Step 2: Run retrieval tests and verify missing methods**

Run: `uv run pytest tests/test_memory_store.py -k 'search or list' -v`

Expected: failures report missing `MemoryStore.search` and `MemoryStore.list_memories`.

- [ ] **Step 3: Implement Python-side Unicode filtering**

Add `_all_records(connection) -> tuple[StoredMemory, ...]` to fetch all memory rows and their ordered tags, validate every record, then sort with:

```python
records.sort(key=lambda record: record.id)
records.sort(key=lambda record: record.updated_at, reverse=True)
```

Implement `list_memories` by normalizing filters, opening `WorkspaceDatabase.read()`, returning `()` for no database or schema v1, loading all records for v2, and retaining records whose tag set contains every requested tag.

Implement `search` with this exact predicate after validating query length and requiring a query or tag:

```python
terms = tuple(normalized_query.casefold().split()) if normalized_query else ()
haystack = record.content.casefold()
tag_haystack = record.tags
matches_terms = all(
    term in haystack or any(term in tag for tag in tag_haystack)
    for term in terms
)
matches_tags = set(normalized_tags).issubset(tag_haystack)
```

Return the first `MAX_SEARCH_RESULTS` matching already-sorted records. Treat schema v1 as an empty memory store without migrating it.

- [ ] **Step 4: Run all memory-store tests**

Run: `uv run pytest tests/test_memory_store.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit retrieval**

```bash
git add src/cdy_agent/memory/long_term.py tests/test_memory_store.py
git commit -m "Add explicit memory retrieval"
```

### Task 4: Model-callable memory tools

**Files:**
- Create: `src/cdy_agent/tools/memories.py`
- Modify: `src/cdy_agent/tools/__init__.py`
- Create: `tests/test_memory_tools.py`
- Modify: `tests/test_tool_registry.py`

**Interfaces:**
- Consumes: Task 2/3 `MemoryStore` CRUD, list, and search methods.
- Produces: `RememberMemoryTool`, `SearchMemoriesTool`, `UpdateMemoryTool`, and `ForgetMemoryTool` implementing the existing `Tool` protocol.
- Produces: tool names `remember_memory`, `search_memories`, `update_memory`, and `forget_memory`.

- [ ] **Step 1: Write failing tool-contract tests**

Create `tests/test_memory_tools.py` using a real `MemoryStore(tmp_path)` with deterministic factories. Assert exact definitions and behavior:

```python
def test_memory_tool_confirmation_policy(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    assert RememberMemoryTool(store).requires_confirmation is True
    assert SearchMemoriesTool(store).requires_confirmation is False
    assert UpdateMemoryTool(store).requires_confirmation is True
    assert ForgetMemoryTool(store).requires_confirmation is True


def test_remember_preflight_rejects_duplicate_before_confirmation(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    store.create("Use uv", ["python"])
    result = RememberMemoryTool(store).preflight({"content": "Use uv", "tags": ["PYTHON"]})
    assert result is not None
    assert (result.ok, result.code) == (False, "duplicate_memory")
    assert FIRST_ID in result.message


def test_search_executes_without_confirmation_and_returns_records(tmp_path: Path) -> None:
    store = fixed_store(tmp_path)
    record = store.create("Use uv", ["python"])
    result = SearchMemoriesTool(store).execute({"query": "uv", "tags": []})
    assert result.ok
    assert result.data == [{
        "id": record.id,
        "content": record.content,
        "tags": ["python"],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }]
```

Add `test_memory_tool_schemas_are_closed` to compare every `parameters` dict with the constants in Step 3 and assert `additionalProperties is False`. Add `test_descriptions_require_explicit_user_request` to casefold all four descriptions and assert `only when the user explicitly asks` is present. Parameterize malformed argument dictionaries and assert `invalid_arguments`. Add `test_update_confirmation_shows_complete_before_and_after` and `test_forget_confirmation_shows_complete_record`, asserting the UUID, untruncated content, and every tag. Execute remember/update/forget through a real `ToolRegistry` with `confirm=lambda _: False` and assert `approval_denied` plus unchanged store contents. Use a fake store whose methods raise `MemoryStoreError("safe")` and assert `memory_store_error` with no traceback text. Use a valid missing UUID for update/forget and assert `memory_not_found`.

- [ ] **Step 2: Run tool tests and verify failure**

Run: `uv run pytest tests/test_memory_tools.py -v`

Expected: collection fails because `cdy_agent.tools.memories` does not exist.

- [ ] **Step 3: Implement tool adapters**

Create `src/cdy_agent/tools/memories.py`. Each dataclass owns a `MemoryStore`, declares a strict object schema, and delegates all normalization/mutation to the store. Use these exact schemas:

```python
CONTENT_TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["content", "tags"],
    "additionalProperties": False,
}
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["query", "tags"],
    "additionalProperties": False,
}
UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_id": {"type": "string"},
        "content": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["memory_id", "content", "tags"],
    "additionalProperties": False,
}
ID_SCHEMA = {
    "type": "object",
    "properties": {"memory_id": {"type": "string"}},
    "required": ["memory_id"],
    "additionalProperties": False,
}
```

Use one `_record_data(record)` serializer returning only `id`, `content`, list-valued `tags`, `created_at`, and `updated_at`. Use one `_failure(error)` mapper preserving typed errors and never including tracebacks. Write preflight calls `MemoryStore.prepare(content, tags)`, `MemoryStore.get(memory_id)`, and `MemoryStore.find_duplicate(draft, exclude_id=memory_id)` exactly as defined in Task 2; it performs no mutation.

Confirmation descriptions must render normalized values, for example:

```text
Remember long-term memory with tags [python, tools]:
Use uv for Python projects.
```

and update must render `Current:` and `Replacement:` blocks. Do not truncate confirmation content.

- [ ] **Step 4: Register the four tools once per workspace**

Modify `create_builtin_registry` to construct one `MemoryStore(workspace)` and append the tools after Todo tools in this deterministic order:

```python
RememberMemoryTool(memory_store),
SearchMemoriesTool(memory_store),
UpdateMemoryTool(memory_store),
ForgetMemoryTool(memory_store),
```

Update explicit tool-name assertions in `tests/test_tool_registry.py` to include these four names in that order. Add a regression test that merely creating the registry does not create `.cdy-agent` or the SQLite database.

- [ ] **Step 5: Run tool and registry tests**

Run: `uv run pytest tests/test_memory_tools.py tests/test_tool_registry.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit model tools**

```bash
git add src/cdy_agent/tools/memories.py src/cdy_agent/tools/__init__.py tests/test_memory_tools.py tests/test_tool_registry.py
git commit -m "Add long-term memory tools"
```

### Task 5: CLI memory management

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `MemoryStore` and its typed errors from Tasks 2/3.
- Produces: `memories add`, `memories list`, `memories search`, `memories update`, and `memories delete` Typer commands.
- Preserves: existing `ask`, `chat`, and `sessions` signatures and error presentation.

- [ ] **Step 1: Add failing CLI tests with a fake memory store**

Extend `tests/test_cli.py` with `FakeMemoryStore` implementing the exact Task 2/3 interface, then add:

```python
def test_memories_add_defaults_to_no(tmp_path: Path, monkeypatch) -> None:
    store = FakeMemoryStore()
    monkeypatch.setattr(cli, "MemoryStore", lambda workspace: store)
    result = runner.invoke(
        app,
        ["memories", "add", "Use uv", "--tag", "Python", "--workspace", str(tmp_path)],
        input="\n",
    )
    assert result.exit_code == 0
    assert "Aborted." in result.output
    assert store.created == []


def test_memories_add_confirmed_creates_normalized_record(tmp_path: Path, monkeypatch) -> None:
    store = FakeMemoryStore()
    monkeypatch.setattr(cli, "MemoryStore", lambda workspace: store)
    result = runner.invoke(
        app,
        ["memories", "add", "Use uv", "--tag", "Python", "--workspace", str(tmp_path)],
        input="y\n",
    )
    assert result.exit_code == 0
    assert store.created == [("Use uv", ("Python",))]
    assert FIRST_ID in result.output


def test_memories_search_renders_full_records(tmp_path: Path, monkeypatch) -> None:
    store = FakeMemoryStore(records=(MEMORY_RECORD,))
    monkeypatch.setattr(cli, "MemoryStore", lambda workspace: store)
    result = runner.invoke(
        app,
        ["memories", "search", "uv", "--tag", "python", "--workspace", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert MEMORY_RECORD.id in result.output
    assert MEMORY_RECORD.content in result.output
    assert "python" in result.output
```

Add `test_memories_list_forwards_tags_and_renders_every_record`, `test_memories_list_empty`, and `test_memories_search_empty`, asserting the exact empty messages from Step 3. Add `test_memories_update_confirmed_replaces_complete_record` with `input="y\n"` and assert the fake received `(memory_id, content, tags)`. Add default-No and confirmed delete tests asserting zero/one delete calls. Parameterize EOF and `KeyboardInterrupt` confirmation callbacks and assert `Aborted.` with no mutation. Add invalid UUID and fake `MemoryStoreError("safe message")` tests asserting exit code 1, the safe message, and no `Traceback`. Assert every fake receives the resolved `tmp_path`. Parameterize `memories`, `memories add`, `memories list`, `memories search`, `memories update`, and `memories delete` with `--help`, expecting exit code 0.

- [ ] **Step 2: Run focused CLI tests and verify missing group**

Run: `uv run pytest tests/test_cli.py -k 'memories' -v`

Expected: failures report that the `memories` command does not exist or `cli.MemoryStore` is missing.

- [ ] **Step 3: Implement the Typer command group**

In `src/cdy_agent/cli.py`, import `MemoryStore`, `MemoryStoreError`, and `StoredMemory`; add `MemoryStoreError` to `REQUEST_ERRORS`; register:

```python
memories_app = typer.Typer(help="Manage explicit long-term memories.")
app.add_typer(memories_app, name="memories")
```

Implement the five commands with repeated `--tag` options typed as `list[str] | None`, `--workspace` matching the sessions commands, and complete UUID arguments. Before each write, call store validation/probe methods, render the exact normalized current/proposed record, and use `typer.confirm(..., default=False)`. Catch `EOFError`, `KeyboardInterrupt`, and `typer.Abort` as `Aborted.`. Read commands never confirm.

Use one renderer with stable multiline output:

```python
def _render_memory(record: StoredMemory) -> None:
    typer.echo(f"ID: {record.id}")
    typer.echo(f"Updated: {record.updated_at}")
    typer.echo(f"Tags: {', '.join(record.tags) if record.tags else '-'}")
    typer.echo("Content:")
    typer.echo(record.content)
```

Print a blank line between multiple records. Use `No saved memories.` for an empty list and `No matching memories.` for an empty search. On success print `Created memory <id>.`, `Updated memory <id>.`, or `Deleted memory <id>.` after the record/confirmation flow.

- [ ] **Step 4: Verify memory tools are available to both request modes without automatic injection**

Add CLI/agent regression tests that inspect the fake gateway call for both `ask` and `chat`:

```python
assert {tool["name"] for tool in gateway.calls[0]["tools"]} >= {
    "remember_memory", "search_memories", "update_memory", "forget_memory"
}
assert all(memory.content not in message.content for message in gateway.calls[0]["messages"])
```

Seed a memory in the fake/temporary store but make the model return a final response without a tool call. Assert no search method was called and no memory content entered messages.

- [ ] **Step 5: Run CLI and Agent regression tests**

Run: `uv run pytest tests/test_cli.py tests/test_agent.py -v`

Expected: all tests pass for `ask`, `chat`, `sessions`, tools, and the new `memories` group.

- [ ] **Step 6: Commit the CLI slice**

```bash
git add src/cdy_agent/cli.py tests/test_cli.py
git diff --cached --check
git commit -m "Add memory management commands"
```

### Task 6: Documentation, full verification, and stage completion

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`

**Interfaces:**
- Consumes: all user-visible commands and rules from Tasks 1–5.
- Produces: current-stage documentation and final stage-7 acceptance evidence.

- [ ] **Step 1: Update user documentation**

Replace README statements saying long-term memory is future work with text that distinguishes saved sessions from explicit memories. Add exact examples:

```powershell
uv run cdy-agent memories add "Python 项目统一使用 uv 管理依赖" --tag python --tag tooling --workspace .
uv run cdy-agent memories list --workspace .
uv run cdy-agent memories search "uv" --tag python --workspace .
uv run cdy-agent memories update <memory-id> --content "Python 项目统一使用 uv sync 管理依赖" --tag python --tag tooling --workspace .
uv run cdy-agent memories delete <memory-id> --workspace .
```

Document that add/update/delete default to No, IDs must be complete UUIDs, search is keyword/tag AND matching, memory is workspace-local, `ask` and `chat` only retrieve after explicit user requests, and no automatic extraction/injection occurs.

Update stage 7 in `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md` to say both persistent conversations and explicit long-term memory are delivered, while semantic/vector retrieval remains out of scope.

- [ ] **Step 2: Run the complete offline suite**

Run: `uv run pytest`

Expected: all tests pass with no network access and no real API key required.

- [ ] **Step 3: Run CLI help acceptance checks**

Run:

```bash
uv run cdy-agent --help
uv run cdy-agent memories --help
uv run cdy-agent memories add --help
uv run cdy-agent memories search --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
```

Expected: every command exits 0; root help lists `ask`, `chat`, `sessions`, and `memories`; memory help lists `add`, `list`, `search`, `update`, and `delete`.

- [ ] **Step 4: Build distributions**

Run: `uv build`

Expected: source and wheel distributions build successfully through Hatchling.

- [ ] **Step 5: Audit the worktree and tracked content for secrets/caches**

Run:

```bash
git diff --check
git diff --cached --check
git status --short
git grep -n "OPENAI_API_KEY.*=" -- ':!docs/superpowers/plans/2026-07-19-long-term-memory.md'
```

Expected: feature diffs have no whitespace errors; no newly tracked cache, model response, `.env`, `.idea`, generated image, or credential appears. The previously exposed provider credential must have been revoked even though it is no longer present in the working tree.

- [ ] **Step 6: Commit docs and stage completion**

```bash
git add README.md docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md
git commit -m "Document explicit long-term memory"
```

- [ ] **Step 7: Record final evidence**

Run:

```bash
git log -6 --oneline
git status --short
```

Expected: the six implementation commits are visible in order, only pre-existing user changes remain, and the final report includes exact pytest/build/help results plus the credential-rotation reminder.
