from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from cdy_agent.conversation import Message
from cdy_agent.memory.database import (
    ConversationNotFoundError,
    ConversationStoreError,
    InvalidConversationStoreError,
    WorkspaceDatabase,
    _WorkspaceDatabaseWriteError,
)


@dataclass(frozen=True)
class StoredConversation:
    id: str
    created_at: str
    updated_at: str
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    updated_at: str
    message_count: int
    preview: str


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


class ConversationStore:
    def __init__(
        self,
        workspace: Path,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._database = WorkspaceDatabase(workspace)
        self.workspace = self._database.workspace
        self._clock = clock

    def append_turn(
        self,
        session_id: str,
        user: Message,
        assistant: Message,
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> ConversationSummary:
        session_id = _canonical_uuid(session_id)
        if user.role != "user" or assistant.role != "assistant":
            raise ConversationStoreError(
                "A turn must contain one user message and one assistant message."
            )
        if not user.content.strip() or not assistant.content.strip():
            raise ConversationStoreError("Conversation messages must not be empty.")
        timestamp = _timestamp(self._clock())
        previous_updated_at: str | None = None
        sequence = 0
        with self._database.write() as connection:
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
                preview = _preview(user.content)
            else:
                _require_timestamp(existing_session[0])
                previous_updated_at = _require_timestamp(existing_session[1])
                existing_messages = self._validated_messages(message_rows)
                sequence = len(existing_messages)
                preview = _preview(existing_messages[0].content)
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
            summary = ConversationSummary(
                id=session_id,
                updated_at=timestamp,
                message_count=sequence + 2,
                preview=preview,
            )
            if cancellation_check is not None:
                cancellation_check()
        if cancellation_check is not None:
            try:
                cancellation_check()
            except BaseException:
                self._rollback_appended_turn(
                    summary,
                    user,
                    assistant,
                    previous_updated_at=previous_updated_at,
                )
                raise
        return summary

    def _rollback_appended_turn(
        self,
        summary: ConversationSummary,
        user: Message,
        assistant: Message,
        *,
        previous_updated_at: str | None,
    ) -> None:
        """Compensate one exact committed append without touching older history."""
        first_sequence = summary.message_count - 2
        with self._database.write() as connection:
            session = connection.execute(
                "SELECT updated_at FROM sessions WHERE id = ?", (summary.id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT sequence, role, content FROM messages "
                "WHERE session_id = ? ORDER BY sequence",
                (summary.id,),
            ).fetchall()
            expected_tail = [
                (first_sequence, user.role, user.content),
                (first_sequence + 1, assistant.role, assistant.content),
            ]
            if (
                session is None
                or session[0] != summary.updated_at
                or len(rows) != summary.message_count
                or rows[-2:] != expected_tail
            ):
                raise ConversationStoreError(
                    "Could not safely roll back the cancelled conversation turn."
                )
            if previous_updated_at is None:
                connection.execute("DELETE FROM sessions WHERE id = ?", (summary.id,))
            else:
                connection.execute(
                    "DELETE FROM messages WHERE session_id = ? AND sequence >= ?",
                    (summary.id, first_sequence),
                )
                connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (previous_updated_at, summary.id),
                )

    def load(self, session_id: str) -> StoredConversation:
        session_id = _canonical_uuid(session_id)
        with self._database.read() as connection:
            if connection is None:
                raise ConversationNotFoundError("Conversation not found.")
            row = connection.execute(
                "SELECT created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise ConversationNotFoundError("Conversation not found.")
            created_at = _require_timestamp(row[0])
            updated_at = _require_timestamp(row[1])
            message_rows = connection.execute(
                "SELECT sequence, role, content FROM messages "
                "WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        messages = self._validated_messages(message_rows)
        return StoredConversation(session_id, created_at, updated_at, messages)

    def list_summaries(self) -> tuple[ConversationSummary, ...]:
        with self._database.read() as connection:
            if connection is None:
                return ()
            rows = connection.execute(
                "SELECT id, created_at, updated_at FROM sessions ORDER BY id ASC"
            ).fetchall()
            summaries: list[tuple[datetime, ConversationSummary]] = []
            for session_id, created_at, updated_at in rows:
                _canonical_uuid(session_id)
                _require_timestamp(created_at)
                validated_updated_at = _require_timestamp(updated_at)
                message_rows = connection.execute(
                    "SELECT sequence, role, content FROM messages "
                    "WHERE session_id = ? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
                messages = self._validated_messages(message_rows)
                summaries.append(
                    (
                        datetime.fromisoformat(validated_updated_at[:-1] + "+00:00"),
                        ConversationSummary(
                            id=session_id,
                            updated_at=validated_updated_at,
                            message_count=len(messages),
                            preview=_preview(messages[0].content),
                        ),
                    )
                )
            summaries.sort(key=lambda item: item[0], reverse=True)
            return tuple(summary for _, summary in summaries)

    def delete(self, session_id: str) -> None:
        session_id = _canonical_uuid(session_id)
        with self._database.read() as connection:
            if connection is None:
                raise ConversationNotFoundError("Conversation not found.")
        try:
            with self._database.write() as connection:
                cursor = connection.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                if cursor.rowcount != 1:
                    raise ConversationNotFoundError("Conversation not found.")
        except _WorkspaceDatabaseWriteError as error:
            raise ConversationStoreError(
                "Could not delete conversation data."
            ) from error

    @staticmethod
    def _validated_messages(
        rows: list[tuple[object, object, object]],
    ) -> tuple[Message, ...]:
        if not rows or len(rows) % 2:
            raise InvalidConversationStoreError(
                "Stored conversation history is invalid."
            )
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
