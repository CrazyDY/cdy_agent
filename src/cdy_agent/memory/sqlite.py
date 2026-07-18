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
    def _validated_messages(
        rows: list[tuple[object, object, object]],
    ) -> tuple[Message, ...]:
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
