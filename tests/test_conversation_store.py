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
