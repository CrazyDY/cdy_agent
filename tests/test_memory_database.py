import os
from pathlib import Path
import sqlite3

import pytest

from cdy_agent.memory.database import WorkspaceDatabase
from cdy_agent.memory import InvalidConversationStoreError


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
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
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
            (
                "11111111-1111-1111-1111-111111111111",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            [
                ("11111111-1111-1111-1111-111111111111", 0, "user", "hello"),
                (
                    "11111111-1111-1111-1111-111111111111",
                    1,
                    "assistant",
                    "hi",
                ),
            ],
        )
    with WorkspaceDatabase(tmp_path).write():
        pass
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute(
            "SELECT role, content FROM messages ORDER BY sequence"
        ).fetchall() == [("user", "hello"), ("assistant", "hi")]


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


def test_read_rejects_symlinked_data_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    os.symlink(outside, tmp_path / ".cdy-agent", target_is_directory=True)

    with pytest.raises(InvalidConversationStoreError, match="symbolic link"):
        with WorkspaceDatabase(tmp_path).read():
            pass


def test_read_rejects_symlinked_database(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    outside = tmp_path / "outside.sqlite3"
    outside.touch()
    os.symlink(outside, data / "cdy-agent.sqlite3")

    with pytest.raises(InvalidConversationStoreError, match="symbolic link"):
        with WorkspaceDatabase(tmp_path).read():
            pass


def test_read_rejects_non_regular_database(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    (data / "cdy-agent.sqlite3").mkdir()

    with pytest.raises(InvalidConversationStoreError, match="regular file"):
        with WorkspaceDatabase(tmp_path).read():
            pass


def test_read_rejects_corrupt_database(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    (data / "cdy-agent.sqlite3").write_bytes(b"not sqlite")

    with pytest.raises(InvalidConversationStoreError) as exc_info:
        with WorkspaceDatabase(tmp_path).read():
            pass
    assert "read" in str(exc_info.value).lower()
    assert "sqlite" not in str(exc_info.value).lower()


def test_read_rejects_unsupported_version(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    path = data / "cdy-agent.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(V1_SCHEMA)
        connection.execute("PRAGMA user_version = 3")

    with pytest.raises(
        InvalidConversationStoreError, match="schema version is not supported"
    ):
        with WorkspaceDatabase(tmp_path).read():
            pass
