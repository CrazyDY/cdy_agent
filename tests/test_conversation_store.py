import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from cdy_agent.conversation import Message
from cdy_agent.memory import (
    ConversationNotFoundError,
    ConversationStore,
    ConversationStoreError,
    InvalidConversationStoreError,
)


FIXED_TIME = datetime(2026, 7, 18, 8, 30, tzinfo=timezone.utc)
SESSION_ID = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"


def create_v1_conversation_database(tmp_path: Path, *, session_id: str) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    database = data / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
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
        )
        timestamp = "2026-07-18T08:00:00.000000Z"
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (session_id, timestamp, timestamp),
        )
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            [
                (session_id, 0, "user", "first"),
                (session_id, 1, "assistant", "answer"),
            ],
        )


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


def test_append_turn_migrates_v1_and_preserves_existing_history(
    tmp_path: Path,
) -> None:
    create_v1_conversation_database(tmp_path, session_id=SESSION_ID)
    store = make_store(tmp_path)
    store.append_turn(
        SESSION_ID,
        Message(role="user", content="second"),
        Message(role="assistant", content="reply"),
    )
    assert [message.content for message in store.load(SESSION_ID).messages] == [
        "first",
        "answer",
        "second",
        "reply",
    ]


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


def test_list_summaries_sorts_mixed_timestamp_precisions_chronologically(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    older = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    newer = "8eef1fd0-bcfa-45fe-99af-fbd6c9bd4027"
    store.append_turn(older, Message("user", "older"), Message("assistant", "a"))
    store.append_turn(newer, Message("user", "newer"), Message("assistant", "b"))
    database = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            ("2026-07-18T08:30:00Z", older),
        )
        connection.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            ("2026-07-18T08:30:00.900000Z", newer),
        )

    assert [item.id for item in store.list_summaries()] == [newer, older]


def test_delete_removes_session_and_messages(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    store.append_turn(session_id, Message("user", "Hello"), Message("assistant", "Hi"))

    store.delete(session_id)

    with pytest.raises(ConversationNotFoundError):
        store.load(session_id)
    assert store.list_summaries() == ()
    database = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        message_count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert message_count == 0


def test_delete_missing_session_does_not_create_store(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ConversationNotFoundError, match="Conversation not found"):
        store.delete("52c809c6-6e55-4ff1-9220-e4f90a4f6774")
    assert not (tmp_path / ".cdy-agent").exists()


@pytest.mark.parametrize(
    "session_id",
    [
        "not-a-uuid",
        "52C809C6-6E55-4FF1-9220-E4F90A4F6774",
        "52c809c6-6e55-4ff1-9220-e4f90a4f6774extra",
    ],
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


def test_corrupt_message_order_is_rejected_and_append_is_atomic(
    tmp_path: Path,
) -> None:
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
