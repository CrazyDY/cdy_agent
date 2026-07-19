import os
from pathlib import Path
import sqlite3
import threading

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


def test_concurrent_first_writes_preserve_both_transactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    database = data / "cdy-agent.sqlite3"
    barrier = threading.Barrier(2)
    original_connect = sqlite3.connect

    def synchronized_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        barrier.wait(timeout=5)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        "cdy_agent.memory.database.sqlite3.connect", synchronized_connect
    )
    outcomes: list[BaseException | None] = []

    def write(session_id: str) -> None:
        try:
            with WorkspaceDatabase(tmp_path).write() as connection:
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?)",
                    (session_id, "created", "updated"),
                )
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append(None)

    threads = [
        threading.Thread(target=write, args=(session_id,), daemon=True)
        for session_id in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=8)

    assert all(not thread.is_alive() for thread in threads)
    assert outcomes == [None, None]
    assert database.is_file()
    with original_connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute(
            "SELECT id FROM sessions ORDER BY id"
        ).fetchall() == [("first",), ("second",)]


def test_failed_first_write_leaves_recoverable_version_zero_placeholder(
    tmp_path: Path,
) -> None:
    database = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"

    with pytest.raises(RuntimeError, match="stop"):
        with WorkspaceDatabase(tmp_path).write():
            raise RuntimeError("stop")

    assert database.is_file()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == []

    with WorkspaceDatabase(tmp_path).write():
        pass
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def test_read_treats_version_zero_placeholder_as_empty_without_mutating(
    tmp_path: Path,
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    database = data / "cdy-agent.sqlite3"
    with sqlite3.connect(database):
        pass

    with WorkspaceDatabase(tmp_path).read() as connection:
        assert connection is None

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == []


def test_version_zero_database_with_user_table_is_rejected(
    tmp_path: Path,
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    database = data / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unexpected (value TEXT)")

    with pytest.raises(InvalidConversationStoreError):
        with WorkspaceDatabase(tmp_path).read():
            pass
    with pytest.raises(InvalidConversationStoreError):
        with WorkspaceDatabase(tmp_path).write():
            pass
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'unexpected'"
        ).fetchone() == ("unexpected",)


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


@pytest.mark.parametrize(
    "schema",
    [
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        PRAGMA user_version = 1;
        """,
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
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
        """,
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
            PRIMARY KEY (session_id, sequence)
        );
        PRAGMA user_version = 1;
        """,
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
            session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL CHECK (length(trim(content)) > 0),
            PRIMARY KEY (session_id, sequence),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        PRAGMA user_version = 1;
        """,
    ],
    ids=["missing-table", "wrong-column", "missing-foreign-key", "missing-check"],
)
def test_read_and_migration_reject_malformed_v1_schema(
    tmp_path: Path, schema: str
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    database = data / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(schema)

    with pytest.raises(InvalidConversationStoreError):
        with WorkspaceDatabase(tmp_path).read():
            pass
    with pytest.raises(InvalidConversationStoreError):
        with WorkspaceDatabase(tmp_path).write():
            pass

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories'"
        ).fetchone() is None


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP TABLE memory_tags",
        "ALTER TABLE memories RENAME COLUMN content TO body",
        """
        PRAGMA foreign_keys = OFF;
        ALTER TABLE memory_tags RENAME TO old_memory_tags;
        CREATE TABLE memory_tags (
            memory_id TEXT NOT NULL,
            tag TEXT NOT NULL CHECK (length(trim(tag)) > 0),
            PRIMARY KEY (memory_id, tag)
        );
        DROP TABLE old_memory_tags;
        """,
        """
        ALTER TABLE memories RENAME TO old_memories;
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL CHECK (length(trim(content)) > 0),
            identity_hash TEXT NOT NULL CHECK (length(identity_hash) = 64),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO memories SELECT * FROM old_memories;
        DROP TABLE old_memories;
        """,
        """
        PRAGMA foreign_keys = OFF;
        ALTER TABLE memory_tags RENAME TO old_memory_tags;
        CREATE TABLE memory_tags (
            memory_id TEXT NOT NULL,
            tag TEXT NOT NULL CHECK (length(trim(tag)) > 0),
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
        );
        DROP TABLE old_memory_tags;
        """,
        """
        ALTER TABLE memories RENAME TO old_memories;
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            identity_hash TEXT NOT NULL UNIQUE CHECK (length(identity_hash) = 64),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO memories SELECT * FROM old_memories;
        DROP TABLE old_memories;
        """,
        "CREATE TABLE unexpected (value TEXT)",
        "CREATE TABLE sqliteEvil (value TEXT)",
    ],
    ids=[
        "missing-table",
        "wrong-column",
        "missing-foreign-key",
        "missing-identity-unique",
        "missing-primary-key",
        "missing-check",
        "unexpected-table",
        "sqlite-prefix-application-table",
    ],
)
def test_read_and_write_reject_malformed_v2_schema(
    tmp_path: Path, mutation: str
) -> None:
    with WorkspaceDatabase(tmp_path).write():
        pass
    database = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(mutation)

    with pytest.raises(InvalidConversationStoreError):
        with WorkspaceDatabase(tmp_path).read():
            pass
    with pytest.raises(InvalidConversationStoreError):
        with WorkspaceDatabase(tmp_path).write():
            pass
