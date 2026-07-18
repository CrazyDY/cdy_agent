# Persistent Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist complete `chat` turns in workspace-scoped SQLite storage and let users explicitly list, resume, and delete conversations.

**Architecture:** Keep `Conversation` as the in-memory message model and add a concrete `ConversationStore` under `memory/sqlite.py` for path validation, schema management, transactions, and queries. Typer commands orchestrate the store and Agent: a reply is displayed only after its user/assistant pair commits successfully, while `ask` remains stateless.

**Tech Stack:** Python 3.10+, standard-library `sqlite3`, `dataclasses`, `datetime`, `pathlib`, and `uuid`; existing Typer CLI, `Conversation`, Agent, OpenAI-compatible gateway, uv, and pytest.

## Global Constraints

- Persist only `chat`; `ask` must remain stateless.
- Store data at `<workspace>/.cdy-agent/cdy-agent.sqlite3`; read-only operations on an empty workspace must not create `.cdy-agent` or a database.
- Use only Python's standard-library SQLite support and do not add a runtime dependency, storage-provider abstraction, vector database, summarizer, or context-pruning policy.
- Persist exactly one normalized user message and one normalized assistant reply in a single transaction after a successful model response.
- Never persist an empty session, failed model turn, half-turn, unsupported role, blank message, or non-canonical session UUID.
- Use UTC ISO 8601 timestamps ending in `Z`, zero-based continuous message sequence numbers, and SQLite `PRAGMA user_version = 1`.
- Reject symlinked or non-regular `.cdy-agent`/database paths, resolved paths outside the workspace, corrupt history, and schema versions other than 1.
- `chat --resume` accepts a complete canonical UUID only; it never falls back to a new session.
- `sessions list` sorts by `updated_at` descending and displays the full ID, timestamp, message count, and first user message collapsed to one line and truncated to 80 characters with `…`.
- `sessions delete` uses a default-No confirmation and deletes messages through an SQLite foreign-key cascade.
- Tests use temporary workspaces, fake Agents/stores, and local SQLite only; they never use provider credentials, network access, or contributor data.
- Follow TDD for each task and keep every commit limited to the files named by that task.

---

## File Structure

- Create `src/cdy_agent/memory/__init__.py`: narrow exports for conversation persistence.
- Create `src/cdy_agent/memory/sqlite.py`: records, safe database path handling, schema initialization, validation, and `ConversationStore` operations.
- Create `tests/test_conversation_store.py`: storage behavior, corruption, transaction, and path-boundary tests.
- Modify `src/cdy_agent/cli.py`: persistent `chat`, explicit resume, and the `sessions` Typer command group.
- Modify `tests/test_cli.py`: chat persistence/resume and session-management CLI regressions.
- Modify `README.md`: persistent-session usage, location, lifecycle, and limits.
- Modify `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`: record the delivered first slice of phase 7 without marking long-term memory complete.

### Task 1: SQLite Conversation Store Happy Path

**Files:**
- Create: `src/cdy_agent/memory/__init__.py`
- Create: `src/cdy_agent/memory/sqlite.py`
- Create: `tests/test_conversation_store.py`

**Interfaces:**
- Consumes: `resolve_workspace(path: Path) -> Path` from `cdy_agent.tools.filesystem` and `Message(role: MessageRole, content: str)` from `cdy_agent.conversation`.
- Produces: `ConversationStore(workspace: Path, *, clock: Callable[[], datetime] = ...)`.
- Produces: `ConversationStore.append_turn(session_id: str, user: Message, assistant: Message) -> None`.
- Produces: `ConversationStore.load(session_id: str) -> StoredConversation`.
- Produces: `StoredConversation(id: str, created_at: str, updated_at: str, messages: tuple[Message, ...])`.
- Produces: `ConversationStoreError`, `ConversationNotFoundError`, and `InvalidConversationStoreError`.

- [ ] **Step 1: Write failing save/load and lazy-read tests**

```python
# tests/test_conversation_store.py
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from cdy_agent.conversation import Message
from cdy_agent.memory import ConversationNotFoundError, ConversationStore


FIXED_TIME = datetime(2026, 7, 18, 8, 30, tzinfo=timezone.utc)


def make_store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path, clock=lambda: FIXED_TIME)


def test_missing_store_read_does_not_create_files(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(ConversationNotFoundError, match="Conversation not found"):
        store.load("52c809c6-6e55-4ff1-9220-e4f90a4f6774")

    assert not (tmp_path / ".cdy-agent").exists()


def test_append_turn_creates_and_loads_conversation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"

    store.append_turn(
        session_id,
        Message("user", "Hello"),
        Message("assistant", "Hi there"),
    )

    stored = store.load(session_id)
    assert UUID(stored.id) == UUID(session_id)
    assert stored.created_at == "2026-07-18T08:30:00.000000Z"
    assert stored.updated_at == "2026-07-18T08:30:00.000000Z"
    assert stored.messages == (
        Message("user", "Hello"),
        Message("assistant", "Hi there"),
    )
    assert (tmp_path / ".cdy-agent" / "cdy-agent.sqlite3").is_file()


def test_append_turn_appends_two_ordered_messages(tmp_path: Path) -> None:
    times = iter(
        [
            datetime(2026, 7, 18, 8, 30, tzinfo=timezone.utc),
            datetime(2026, 7, 18, 8, 31, tzinfo=timezone.utc),
        ]
    )
    store = ConversationStore(tmp_path, clock=lambda: next(times))
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"

    store.append_turn(session_id, Message("user", "One"), Message("assistant", "1"))
    store.append_turn(session_id, Message("user", "Two"), Message("assistant", "2"))

    stored = store.load(session_id)
    assert stored.created_at == "2026-07-18T08:30:00.000000Z"
    assert stored.updated_at == "2026-07-18T08:31:00.000000Z"
    assert stored.messages == (
        Message("user", "One"),
        Message("assistant", "1"),
        Message("user", "Two"),
        Message("assistant", "2"),
    )
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `uv run pytest tests/test_conversation_store.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'cdy_agent.memory'`.

- [ ] **Step 3: Implement records, safe lazy paths, schema creation, append, and load**

```python
# src/cdy_agent/memory/sqlite.py
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from cdy_agent.conversation import Message
from cdy_agent.tools.filesystem import resolve_workspace


DATA_DIRECTORY = ".cdy-agent"
DATABASE_FILENAME = "cdy-agent.sqlite3"
SCHEMA_VERSION = 1
SCHEMA = """
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
"""


class ConversationStoreError(RuntimeError):
    """A conversation database operation failed safely."""


class ConversationNotFoundError(ConversationStoreError):
    """The requested conversation does not exist."""


class InvalidConversationStoreError(ConversationStoreError):
    """The database path, schema, or stored history is invalid."""


@dataclass(frozen=True)
class StoredConversation:
    id: str
    created_at: str
    updated_at: str
    messages: tuple[Message, ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ConversationStoreError("Conversation clock must be timezone-aware.")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ConversationStoreError("Session ID must be a complete UUID.") from error
    if str(parsed) != value:
        raise ConversationStoreError("Session ID must be a complete UUID.")
    return value


class ConversationStore:
    def __init__(
        self,
        workspace: Path,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self._clock = clock

    def append_turn(
        self,
        session_id: str,
        user: Message,
        assistant: Message,
    ) -> None:
        session_id = _canonical_uuid(session_id)
        if user.role != "user" or assistant.role != "assistant":
            raise ConversationStoreError(
                "A turn must contain one user message and one assistant message."
            )
        if not user.content.strip() or not assistant.content.strip():
            raise ConversationStoreError("Conversation messages must not be empty.")
        path = self._database_path(create=True)
        assert path is not None
        new_database = not path.exists()
        timestamp = _timestamp(self._clock())
        try:
            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                if new_database:
                    connection.executescript(SCHEMA)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                else:
                    self._require_schema(connection)
                row = connection.execute(
                    "SELECT COUNT(*), MIN(sequence), MAX(sequence) "
                    "FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                assert row is not None
                count, minimum, maximum = row
                if count == 0:
                    existing = connection.execute(
                        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    if existing is not None:
                        raise InvalidConversationStoreError(
                            "Stored conversation history is invalid."
                        )
                    connection.execute(
                        "INSERT INTO sessions(id, created_at, updated_at) VALUES (?, ?, ?)",
                        (session_id, timestamp, timestamp),
                    )
                    sequence = 0
                else:
                    if count % 2 or minimum != 0 or maximum != count - 1:
                        raise InvalidConversationStoreError(
                            "Stored conversation history is invalid."
                        )
                    sequence = count
                    connection.execute(
                        "UPDATE sessions SET updated_at = ? WHERE id = ?",
                        (timestamp, session_id),
                    )
                connection.executemany(
                    "INSERT INTO messages(session_id, sequence, role, content) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (session_id, sequence, user.role, user.content),
                        (session_id, sequence + 1, assistant.role, assistant.content),
                    ],
                )
        except ConversationStoreError:
            raise
        except sqlite3.Error as error:
            raise ConversationStoreError("Could not write conversation data.") from error

    def load(self, session_id: str) -> StoredConversation:
        session_id = _canonical_uuid(session_id)
        path = self._database_path(create=False)
        if path is None:
            raise ConversationNotFoundError("Conversation not found.")
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
                self._require_schema(connection)
                row = connection.execute(
                    "SELECT created_at, updated_at FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise ConversationNotFoundError("Conversation not found.")
                message_rows = connection.execute(
                    "SELECT sequence, role, content FROM messages "
                    "WHERE session_id = ? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
        except ConversationStoreError:
            raise
        except sqlite3.Error as error:
            raise InvalidConversationStoreError(
                "Could not read conversation data."
            ) from error
        messages = self._validated_messages(message_rows)
        return StoredConversation(session_id, row[0], row[1], messages)

    def _database_path(self, *, create: bool) -> Path | None:
        data_directory = self.workspace / DATA_DIRECTORY
        try:
            if not data_directory.exists() and not data_directory.is_symlink():
                if not create:
                    return None
                data_directory.mkdir()
            if data_directory.is_symlink():
                raise InvalidConversationStoreError(
                    "Conversation data path must not be a symbolic link."
                )
            resolved_directory = data_directory.resolve(strict=True)
            resolved_directory.relative_to(self.workspace)
            if not resolved_directory.is_dir():
                raise InvalidConversationStoreError(
                    "Conversation data path is not a directory."
                )
            target = resolved_directory / DATABASE_FILENAME
            if not target.exists() and not target.is_symlink():
                return target if create else None
            if target.is_symlink():
                raise InvalidConversationStoreError(
                    "Conversation database must not be a symbolic link."
                )
            resolved_target = target.resolve(strict=True)
            resolved_target.relative_to(self.workspace)
            if not resolved_target.is_file():
                raise InvalidConversationStoreError(
                    "Conversation database is not a regular file."
                )
            return resolved_target
        except InvalidConversationStoreError:
            raise
        except (OSError, ValueError) as error:
            raise InvalidConversationStoreError(
                "Conversation data path is invalid."
            ) from error

    @staticmethod
    def _require_schema(connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA user_version").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise InvalidConversationStoreError(
                "Conversation database schema version is not supported."
            )

    @staticmethod
    def _validated_messages(rows: list[tuple[object, object, object]]) -> tuple[Message, ...]:
        if not rows or len(rows) % 2:
            raise InvalidConversationStoreError("Stored conversation history is invalid.")
        messages: list[Message] = []
        for expected, (sequence, role, content) in enumerate(rows):
            expected_role = "user" if expected % 2 == 0 else "assistant"
            if sequence != expected or role != expected_role:
                raise InvalidConversationStoreError(
                    "Stored conversation history is invalid."
                )
            if not isinstance(content, str) or not content.strip():
                raise InvalidConversationStoreError(
                    "Stored conversation history is invalid."
                )
            messages.append(Message(role=expected_role, content=content))
        return tuple(messages)
```

```python
# src/cdy_agent/memory/__init__.py
from .sqlite import (
    ConversationNotFoundError,
    ConversationStore,
    ConversationStoreError,
    InvalidConversationStoreError,
    StoredConversation,
)

__all__ = [
    "ConversationNotFoundError",
    "ConversationStore",
    "ConversationStoreError",
    "InvalidConversationStoreError",
    "StoredConversation",
]
```

- [ ] **Step 4: Run focused tests and the existing conversation tests**

Run: `uv run pytest tests/test_conversation_store.py tests/test_conversation.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the storage happy path**

```bash
git add src/cdy_agent/memory tests/test_conversation_store.py
git commit -m "Add SQLite conversation store"
```

### Task 2: Store Listing, Deletion, Integrity, and Path Boundaries

**Files:**
- Modify: `src/cdy_agent/memory/__init__.py`
- Modify: `src/cdy_agent/memory/sqlite.py`
- Modify: `tests/test_conversation_store.py`

**Interfaces:**
- Consumes: Task 1's `ConversationStore`, validation helpers, and error types.
- Produces: `ConversationSummary(id: str, updated_at: str, message_count: int, preview: str)`.
- Produces: `ConversationStore.list_summaries() -> tuple[ConversationSummary, ...]`.
- Produces: `ConversationStore.delete(session_id: str) -> None`.

- [ ] **Step 1: Add failing list and delete tests**

```python
# append to tests/test_conversation_store.py
def test_list_summaries_is_lazy_sorted_and_truncates_preview(tmp_path: Path) -> None:
    empty_store = make_store(tmp_path)
    assert empty_store.list_summaries() == ()
    assert not (tmp_path / ".cdy-agent").exists()

    times = iter(
        [
            datetime(2026, 7, 18, 8, 30, tzinfo=timezone.utc),
            datetime(2026, 7, 18, 8, 31, tzinfo=timezone.utc),
        ]
    )
    store = ConversationStore(tmp_path, clock=lambda: next(times))
    first = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    second = "8eef1fd0-bcfa-45fe-99af-fbd6c9bd4027"
    store.append_turn(first, Message("user", "older"), Message("assistant", "a"))
    long_prompt = "word   " + "x" * 90 + "\nnext"
    store.append_turn(second, Message("user", long_prompt), Message("assistant", "b"))

    summaries = store.list_summaries()

    assert [item.id for item in summaries] == [second, first]
    assert summaries[0].message_count == 2
    assert summaries[0].preview == ("word " + "x" * 74 + "…")
    assert len(summaries[0].preview) == 80


def test_delete_removes_session_and_messages(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    store.append_turn(session_id, Message("user", "Hello"), Message("assistant", "Hi"))

    store.delete(session_id)

    with pytest.raises(ConversationNotFoundError):
        store.load(session_id)
    assert store.list_summaries() == ()


def test_delete_missing_session_does_not_create_store(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ConversationNotFoundError, match="Conversation not found"):
        store.delete("52c809c6-6e55-4ff1-9220-e4f90a4f6774")
    assert not (tmp_path / ".cdy-agent").exists()
```

- [ ] **Step 2: Run list/delete tests to verify RED**

Run: `uv run pytest tests/test_conversation_store.py -k 'list_summaries or delete' -v`

Expected: failures report missing `list_summaries` and `delete` attributes.

- [ ] **Step 3: Implement summary records, list, delete, and timestamp validation**

```python
# add beside StoredConversation in src/cdy_agent/memory/sqlite.py
@dataclass(frozen=True)
class ConversationSummary:
    id: str
    updated_at: str
    message_count: int
    preview: str


def _require_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InvalidConversationStoreError("Stored conversation timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise InvalidConversationStoreError(
            "Stored conversation timestamp is invalid."
        ) from error
    if parsed.tzinfo != timezone.utc:
        raise InvalidConversationStoreError("Stored conversation timestamp is invalid.")
    return value


def _preview(content: str) -> str:
    collapsed = " ".join(content.split())
    return collapsed if len(collapsed) <= 80 else collapsed[:79] + "…"
```

```python
# add these public methods to ConversationStore
    def list_summaries(self) -> tuple[ConversationSummary, ...]:
        path = self._database_path(create=False)
        if path is None:
            return ()
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
                self._require_schema(connection)
                rows = connection.execute(
                    "SELECT id, created_at, updated_at FROM sessions "
                    "ORDER BY updated_at DESC, id ASC"
                ).fetchall()
                summaries: list[ConversationSummary] = []
                for session_id, created_at, updated_at in rows:
                    _canonical_uuid(session_id)
                    _require_timestamp(created_at)
                    message_rows = connection.execute(
                        "SELECT sequence, role, content FROM messages "
                        "WHERE session_id = ? ORDER BY sequence",
                        (session_id,),
                    ).fetchall()
                    messages = self._validated_messages(message_rows)
                    summaries.append(
                        ConversationSummary(
                            id=session_id,
                            updated_at=_require_timestamp(updated_at),
                            message_count=len(messages),
                            preview=_preview(messages[0].content),
                        )
                    )
                return tuple(summaries)
        except ConversationStoreError:
            raise
        except sqlite3.Error as error:
            raise InvalidConversationStoreError(
                "Could not read conversation data."
            ) from error

    def delete(self, session_id: str) -> None:
        session_id = _canonical_uuid(session_id)
        path = self._database_path(create=False)
        if path is None:
            raise ConversationNotFoundError("Conversation not found.")
        try:
            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                self._require_schema(connection)
                cursor = connection.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                if cursor.rowcount != 1:
                    raise ConversationNotFoundError("Conversation not found.")
        except ConversationStoreError:
            raise
        except sqlite3.Error as error:
            raise ConversationStoreError("Could not delete conversation data.") from error
```

```python
# add ConversationSummary to src/cdy_agent/memory/__init__.py imports and __all__
from .sqlite import ConversationSummary

__all__.append("ConversationSummary")
```

- [ ] **Step 4: Add failing corruption, atomicity, UUID, and symlink tests**

```python
# append imports and tests to tests/test_conversation_store.py
import os
import sqlite3

from cdy_agent.memory import ConversationStoreError, InvalidConversationStoreError


@pytest.mark.parametrize(
    "session_id",
    ["not-a-uuid", "52C809C6-6E55-4FF1-9220-E4F90A4F6774", "52c809c6-6e55-4ff1-9220-e4f90a4f6774extra"],
)
def test_rejects_noncanonical_session_ids(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(ConversationStoreError, match="complete UUID"):
        make_store(tmp_path).load(session_id)


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    database = data / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(InvalidConversationStoreError, match="schema version"):
        make_store(tmp_path).list_summaries()


def test_corrupt_message_order_is_rejected_and_append_is_atomic(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    store.append_turn(session_id, Message("user", "One"), Message("assistant", "1"))
    database = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE messages SET role = 'user' WHERE session_id = ? AND sequence = 1",
            (session_id,),
        )

    with pytest.raises(InvalidConversationStoreError, match="history is invalid"):
        store.load(session_id)
    with pytest.raises((InvalidConversationStoreError, ConversationStoreError)):
        store.append_turn(
            session_id, Message("user", "Two"), Message("assistant", "2")
        )
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert count == 2


def test_symlinked_data_directory_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    os.symlink(outside, tmp_path / ".cdy-agent", target_is_directory=True)

    with pytest.raises(InvalidConversationStoreError, match="symbolic link"):
        make_store(tmp_path).list_summaries()


def test_symlinked_or_nonregular_database_is_rejected(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    outside_database = tmp_path / "outside.sqlite3"
    outside_database.touch()
    os.symlink(outside_database, data / "cdy-agent.sqlite3")
    with pytest.raises(InvalidConversationStoreError, match="symbolic link"):
        make_store(tmp_path).list_summaries()

    (data / "cdy-agent.sqlite3").unlink()
    (data / "cdy-agent.sqlite3").mkdir()
    with pytest.raises(InvalidConversationStoreError, match="regular file"):
        make_store(tmp_path).list_summaries()


def test_non_sqlite_database_is_rejected(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    (data / "cdy-agent.sqlite3").write_text("not sqlite", encoding="utf-8")

    with pytest.raises(InvalidConversationStoreError, match="read conversation"):
        make_store(tmp_path).list_summaries()
```

- [ ] **Step 5: Tighten append and load validation until the boundary tests pass**

Before calculating the next sequence in `append_turn`, fetch all existing messages and call `_validated_messages`; in `load`, validate both stored timestamps:

```python
# replace append_turn's COUNT/MIN/MAX query branch with this logic
                existing_session = connection.execute(
                    "SELECT created_at, updated_at FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                message_rows = connection.execute(
                    "SELECT sequence, role, content FROM messages "
                    "WHERE session_id = ? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
                if existing_session is None:
                    if message_rows:
                        raise InvalidConversationStoreError(
                            "Stored conversation history is invalid."
                        )
                    connection.execute(
                        "INSERT INTO sessions(id, created_at, updated_at) VALUES (?, ?, ?)",
                        (session_id, timestamp, timestamp),
                    )
                    sequence = 0
                else:
                    _require_timestamp(existing_session[0])
                    _require_timestamp(existing_session[1])
                    existing_messages = self._validated_messages(message_rows)
                    sequence = len(existing_messages)
                    connection.execute(
                        "UPDATE sessions SET updated_at = ? WHERE id = ?",
                        (timestamp, session_id),
                    )
```

```python
# in load, immediately after the session row existence check
                created_at = _require_timestamp(row[0])
                updated_at = _require_timestamp(row[1])

# construct the record with validated values
        return StoredConversation(session_id, created_at, updated_at, messages)
```

- [ ] **Step 6: Run the complete storage suite**

Run: `uv run pytest tests/test_conversation_store.py -v`

Expected: all storage tests pass, including lazy reads, ordering, corruption, rollback, and symlink rejection.

- [ ] **Step 7: Commit the completed storage boundary**

```bash
git add src/cdy_agent/memory tests/test_conversation_store.py
git commit -m "Complete conversation storage lifecycle"
```

### Task 3: Persist and Resume Chat Sessions

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ConversationStore`, `StoredConversation`, `ConversationStoreError`, `Conversation.append`, and `Agent.run`.
- Produces: `chat --resume SESSION_ID` and atomic save-before-display behavior.
- Preserves: `_create_agent(model: str, api_mode: str, workspace: Path) -> Agent` and the stateless `ask` flow.

- [ ] **Step 1: Add fake-store support and failing new/resumed chat tests**

```python
# add imports near the top of tests/test_cli.py
from datetime import datetime, timezone

from cdy_agent.memory import ConversationNotFoundError, StoredConversation


class FakeConversationStore:
    def __init__(self, stored: StoredConversation | None = None) -> None:
        self.stored = stored
        self.loads: list[str] = []
        self.appended: list[tuple[str, Message, Message]] = []

    def load(self, session_id: str) -> StoredConversation:
        self.loads.append(session_id)
        assert self.stored is not None
        return self.stored

    def append_turn(
        self, session_id: str, user: Message, assistant: Message
    ) -> None:
        self.appended.append((session_id, user, assistant))
```

```python
# append to tests/test_cli.py
def test_chat_persists_each_complete_turn_before_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent(["First reply", "Second reply"])
    store = FakeConversationStore()
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="First\nSecond\n/exit\n"
    )

    assert result.exit_code == 0
    assert len(store.appended) == 2
    session_id = store.appended[0][0]
    assert store.appended == [
        (session_id, Message("user", "First"), Message("assistant", "First reply")),
        (session_id, Message("user", "Second"), Message("assistant", "Second reply")),
    ]
    assert store.loads == []
    assert result.stdout.index("Assistant: First reply") >= 0


def test_chat_resume_loads_history_before_first_agent_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    stored = StoredConversation(
        session_id,
        "2026-07-18T08:30:00.000000Z",
        "2026-07-18T08:30:00.000000Z",
        (Message("user", "Old"), Message("assistant", "History")),
    )
    store = FakeConversationStore(stored)
    agent = FakeAgent("New reply")
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app,
        ["chat", "--resume", session_id, "--workspace", str(tmp_path)],
        input="Continue\n/exit\n",
    )

    assert result.exit_code == 0
    assert store.loads == [session_id]
    assert agent.calls == [
        (
            Message("user", "Old"),
            Message("assistant", "History"),
            Message("user", "Continue"),
        )
    ]
    assert store.appended == [
        (session_id, Message("user", "Continue"), Message("assistant", "New reply"))
    ]
```

- [ ] **Step 2: Run the focused CLI tests to verify RED**

Run: `uv run pytest tests/test_cli.py -k 'persists_each_complete_turn or resume_loads_history' -v`

Expected: failures report that `cli.ConversationStore` or the `--resume` option is missing.

- [ ] **Step 3: Wire the store into `chat` without changing `ask`**

```python
# add imports to src/cdy_agent/cli.py and extend the existing conversation import
from uuid import uuid4

from .conversation import Conversation, Message
from .memory import ConversationStore, ConversationStoreError
```

Add `ConversationStoreError` to `REQUEST_ERRORS`, then replace `chat` with:

```python
@app.command()
def chat(
    model: Annotated[
        str | None,
        typer.Option(help="Model override for this conversation."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Directory available to local tools."),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option(help="Resume a saved conversation by its complete ID."),
    ] = None,
) -> None:
    """Start a new conversation or explicitly resume a saved one."""
    try:
        active_model = resolve_model(model)
        api_mode = resolve_api_mode()
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = ConversationStore(active_workspace)
        agent = _create_agent(active_model, api_mode, active_workspace)
        conversation = Conversation()
        if resume is None:
            session_id = str(uuid4())
        else:
            stored = store.load(resume)
            session_id = stored.id
            for message in stored.messages:
                conversation.append(message.role, message.content)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)

    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            return

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            continue
        if normalized_prompt.lower() in {"/exit", "/quit"}:
            return

        user_message = conversation.append("user", normalized_prompt)
        try:
            reply = agent.run(conversation.history)
            assistant_message = Message(role="assistant", content=reply.strip())
            store.append_turn(session_id, user_message, assistant_message)
        except REQUEST_ERRORS as exc:
            _fail_for_exception(exc)
        conversation.append(assistant_message.role, assistant_message.content)
        typer.echo(f"Assistant: {assistant_message.content}")
```

- [ ] **Step 4: Add failure and empty-session regression tests**

```python
# append to tests/test_cli.py
def test_chat_model_failure_and_immediate_exit_do_not_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FakeConversationStore()
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda *args: FakeAgent(error=AgentLoopLimitError("model loop exhausted")),
    )

    failed = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="Hello\n")
    exited = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="/exit\n")

    assert failed.exit_code == 1
    assert exited.exit_code == 0
    assert store.appended == []


def test_chat_store_failure_does_not_display_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FailingStore(FakeConversationStore):
        def append_turn(self, session_id: str, user: Message, assistant: Message) -> None:
            raise cli.ConversationStoreError("Could not write conversation data.")

    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: FailingStore())
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent("Unsaved reply"))

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="Hello\n"
    )

    assert result.exit_code == 1
    assert "Could not write conversation data" in result.stderr
    assert "Assistant: Unsaved reply" not in result.stdout


def test_chat_later_model_failure_keeps_only_prior_complete_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FailSecondAgent(FakeAgent):
        def run(self, messages: Sequence[Message]) -> str:
            self.calls.append(tuple(messages))
            if len(self.calls) == 2:
                raise AgentLoopLimitError("second turn failed")
            return "First reply"

    store = FakeConversationStore()
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FailSecondAgent())

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="First\nSecond\n"
    )

    assert result.exit_code == 1
    assert len(store.appended) == 1
    assert store.appended[0][1:] == (
        Message("user", "First"),
        Message("assistant", "First reply"),
    )


def test_resume_failure_happens_before_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class MissingStore(FakeConversationStore):
        def load(self, session_id: str) -> StoredConversation:
            raise ConversationNotFoundError("Conversation not found.")

    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: MissingStore())
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent())

    result = runner.invoke(
        app,
        [
            "chat",
            "--resume",
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Conversation not found" in result.stderr
    assert "You:" not in result.stdout


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_resumed_history_is_api_mode_neutral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api_mode: str
) -> None:
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    stored = StoredConversation(
        session_id,
        "2026-07-18T08:30:00.000000Z",
        "2026-07-18T08:30:00.000000Z",
        (Message("user", "Old"), Message("assistant", "History")),
    )
    store = FakeConversationStore(stored)
    seen_modes: list[str] = []
    agent = FakeAgent("Reply")
    monkeypatch.setenv("CDY_AGENT_API_MODE", api_mode)
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, mode, workspace: seen_modes.append(mode) or agent,
    )

    result = runner.invoke(
        app,
        ["chat", "--resume", session_id, "--workspace", str(tmp_path)],
        input="New\n/exit\n",
    )

    assert result.exit_code == 0
    assert seen_modes == [api_mode]
    assert agent.calls[0][:2] == stored.messages


def test_ask_does_not_construct_conversation_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent("Reply"))
    monkeypatch.setattr(
        cli,
        "ConversationStore",
        lambda workspace: pytest.fail("ask must remain stateless"),
    )

    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == "Reply\n"
```

- [ ] **Step 5: Run all conversation and CLI tests**

Run: `uv run pytest tests/test_conversation.py tests/test_conversation_store.py tests/test_cli.py -v`

Expected: all tests pass; existing `ask`, tool confirmation, and in-memory history assertions remain green.

- [ ] **Step 6: Commit persistent chat integration**

```bash
git add src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Persist and resume chat sessions"
```

### Task 4: Session List and Delete Commands

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ConversationStore.list_summaries()`, `ConversationStore.delete()`, and `ConversationSummary`.
- Produces: `cdy-agent sessions list --workspace PATH`.
- Produces: `cdy-agent sessions delete SESSION_ID --workspace PATH` with default-No confirmation.

- [ ] **Step 1: Add failing session-list CLI tests**

```python
# append import to tests/test_cli.py
from cdy_agent.memory import ConversationSummary


# append tests to tests/test_cli.py
def test_sessions_list_shows_empty_store_without_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FakeConversationStore()
    store.list_summaries = lambda: ()  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    result = runner.invoke(
        app, ["sessions", "list", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert result.stdout == "No saved conversations.\n"


def test_sessions_list_renders_ordered_summary_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FakeConversationStore()
    store.list_summaries = lambda: (  # type: ignore[attr-defined]
        ConversationSummary(
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "2026-07-18T08:30:00.000000Z",
            4,
            "First question",
        ),
    )
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    result = runner.invoke(
        app, ["sessions", "list", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert "52c809c6-6e55-4ff1-9220-e4f90a4f6774" in result.stdout
    assert "2026-07-18T08:30:00.000000Z" in result.stdout
    assert "4 messages" in result.stdout
    assert "First question" in result.stdout
```

- [ ] **Step 2: Run list tests to verify RED**

Run: `uv run pytest tests/test_cli.py -k 'sessions_list' -v`

Expected: command invocation fails because the `sessions` group is not registered.

- [ ] **Step 3: Register the command group and implement listing**

```python
# near app construction in src/cdy_agent/cli.py
app = typer.Typer(help="Run the CDY local personal AI assistant.")
sessions_app = typer.Typer(help="List and delete saved conversations.")
app.add_typer(sessions_app, name="sessions")
```

```python
# add command to src/cdy_agent/cli.py
@sessions_app.command("list")
def list_sessions(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved conversations."),
    ] = None,
) -> None:
    """List saved conversations, newest first."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        summaries = ConversationStore(active_workspace).list_summaries()
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not summaries:
        typer.echo("No saved conversations.")
        return
    for summary in summaries:
        typer.echo(
            f"{summary.id}  {summary.updated_at}  "
            f"{summary.message_count} messages  {summary.preview}"
        )
```

- [ ] **Step 4: Add failing delete confirmation tests**

```python
# append to tests/test_cli.py
def test_sessions_delete_defaults_to_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deleted: list[str] = []
    store = FakeConversationStore()
    store.delete = deleted.append  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"

    result = runner.invoke(
        app,
        ["sessions", "delete", session_id, "--workspace", str(tmp_path)],
        input="\n",
    )

    assert result.exit_code == 0
    assert session_id in result.stdout
    assert "Aborted" in result.stdout
    assert deleted == []


def test_sessions_delete_confirmed_calls_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deleted: list[str] = []
    store = FakeConversationStore()
    store.delete = deleted.append  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"

    result = runner.invoke(
        app,
        ["sessions", "delete", session_id, "--workspace", str(tmp_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert deleted == [session_id]
    assert result.stdout.endswith(f"Deleted conversation {session_id}.\n")
```

- [ ] **Step 5: Implement default-No deletion**

```python
# add command to src/cdy_agent/cli.py
@sessions_app.command("delete")
def delete_session(
    session_id: Annotated[
        str,
        typer.Argument(help="Complete ID of the conversation to delete."),
    ],
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved conversations."),
    ] = None,
) -> None:
    """Delete one saved conversation after confirmation."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = ConversationStore(active_workspace)
        approved = typer.confirm(
            f"Delete conversation {session_id}?", default=False
        )
        if not approved:
            typer.echo("Aborted.")
            return
        store.delete(session_id)
    except (KeyboardInterrupt, typer.Abort):
        typer.echo("Aborted.")
        return
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    typer.echo(f"Deleted conversation {session_id}.")
```

- [ ] **Step 6: Add invalid workspace and missing-session error regressions**

```python
# append to tests/test_cli.py
def test_sessions_commands_report_errors_without_tracebacks(tmp_path: Path) -> None:
    missing_workspace = runner.invoke(
        app, ["sessions", "list", "--workspace", str(tmp_path / "missing")]
    )
    missing_session = runner.invoke(
        app,
        [
            "sessions",
            "delete",
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "--workspace",
            str(tmp_path),
        ],
        input="y\n",
    )

    assert missing_workspace.exit_code == 1
    assert "workspace" in missing_workspace.stderr.lower()
    assert missing_session.exit_code == 1
    assert "Conversation not found" in missing_session.stderr
    assert "Traceback" not in missing_session.stderr
```

- [ ] **Step 7: Run all CLI tests and help smoke tests**

Run: `uv run pytest tests/test_cli.py -v`

Expected: all CLI tests pass.

Run: `uv run cdy-agent sessions --help`

Expected: exit 0 and output lists `list` and `delete`.

- [ ] **Step 8: Commit session management commands**

```bash
git add src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Add session management commands"
```

### Task 5: User Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`

**Interfaces:**
- Consumes: all user-visible behavior delivered by Tasks 1–4.
- Produces: accurate usage, storage, failure, and phase-status documentation.

- [ ] **Step 1: Update README current-stage and usage text**

Replace the sentence saying persistent sessions are future work with text stating that `chat` conversations are persisted per workspace while long-term memory remains future work. Add these exact examples to the usage section:

```powershell
# 开始并持久化一个新会话
uv run cdy-agent chat --workspace .

# 查看会话，再用完整 ID 恢复或删除
uv run cdy-agent sessions list --workspace .
uv run cdy-agent chat --resume 52c809c6-6e55-4ff1-9220-e4f90a4f6774 --workspace .
uv run cdy-agent sessions delete 52c809c6-6e55-4ff1-9220-e4f90a4f6774 --workspace .
```

Add a “持久化会话” subsection that states:

```markdown
### 持久化会话

`chat` 只在模型成功回复后保存完整的用户/助手轮次。直接退出、模型失败或保存失败不会留下空会话或半个轮次；保存失败的助手回复不会显示。

会话数据库位于 `<workspace>/.cdy-agent/cdy-agent.sqlite3`。`sessions list` 不会为了空结果创建数据库。恢复和删除必须使用完整会话 ID，删除操作默认拒绝并需要用户确认。

`ask` 仍然是无状态命令。首版不提供自动恢复、重命名、搜索、导出、分页、摘要或长期记忆。
```

- [ ] **Step 2: Mark only the persistent-conversation slice delivered in the roadmap**

Replace phase 7's current paragraph with:

```markdown
### 7. 持久化与记忆

本阶段的第一个子阶段已经交付 workspace 范围的 SQLite 会话持久化。`chat` 只原子保存成功完成的轮次，用户可以显式列出、恢复和删除会话；`ask` 继续保持无状态。长期记忆、检索和遗忘尚未开始，后续仍要求由用户明确控制，且不引入向量数据库。
```

- [ ] **Step 3: Run the full offline test suite**

Run: `uv run pytest`

Expected: all tests pass with no network access.

- [ ] **Step 4: Verify every documented CLI boundary**

Run: `uv run cdy-agent --help`

Expected: exit 0 and output lists `ask`, `chat`, and `sessions`.

Run: `uv run cdy-agent chat --help`

Expected: exit 0 and output includes `--resume`, `--model`, and `--workspace`.

Run: `uv run cdy-agent sessions --help`

Expected: exit 0 and output lists `list` and `delete`.

Run: `uv run cdy-agent ask --help`

Expected: exit 0 and output remains the one-shot stateless interface.

- [ ] **Step 5: Build source and wheel distributions**

Run: `uv build`

Expected: exit 0 and both source and wheel artifacts are created under `dist/`.

- [ ] **Step 6: Inspect the final scope and commit documentation**

Run: `git status --short`

Expected: only `README.md` and `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md` are modified; ignored `.cdy-agent/`, caches, and build artifacts are not staged.

```bash
git add README.md docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md
git commit -m "Document persistent conversations"
```
