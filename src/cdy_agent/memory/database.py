from __future__ import annotations

import sqlite3
import re
from collections.abc import Iterator
from contextlib import contextmanager
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

_V1_TABLES = {"sessions", "messages"}
_V2_TABLES = _V1_TABLES | {"memories", "memory_tags"}
_COLUMNS = {
    "sessions": (
        ("id", "TEXT", 0, 1),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "messages": (
        ("session_id", "TEXT", 1, 1),
        ("sequence", "INTEGER", 1, 2),
        ("role", "TEXT", 1, 0),
        ("content", "TEXT", 1, 0),
    ),
    "memories": (
        ("id", "TEXT", 0, 1),
        ("content", "TEXT", 1, 0),
        ("identity_hash", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "memory_tags": (
        ("memory_id", "TEXT", 1, 1),
        ("tag", "TEXT", 1, 2),
    ),
}
_FOREIGN_KEYS = {
    "sessions": (),
    "messages": (("session_id", "sessions", "id", "CASCADE"),),
    "memories": (),
    "memory_tags": (("memory_id", "memories", "id", "CASCADE"),),
}
_UNIQUE_INDEXES = {
    "sessions": (("id",),),
    "messages": (("session_id", "sequence"),),
    "memories": (("id",), ("identity_hash",)),
    "memory_tags": (("memory_id", "tag"),),
}
_CHECK_FRAGMENTS = {
    "sessions": (),
    "messages": (
        "check(rolein('user','assistant'))",
        "check(length(trim(content))>0)",
    ),
    "memories": (
        "check(length(trim(content))>0)",
        "check(length(identity_hash)=64)",
    ),
    "memory_tags": ("check(length(trim(tag))>0)",),
}


class ConversationStoreError(RuntimeError):
    """A conversation database operation failed safely."""


class ConversationNotFoundError(ConversationStoreError):
    """The requested conversation does not exist."""


class InvalidConversationStoreError(ConversationStoreError):
    """The database path, schema, or stored history is invalid."""


class _WorkspaceDatabaseWriteError(ConversationStoreError):
    """The shared database could not complete a write operation."""


class WorkspaceDatabase:
    def __init__(self, workspace: Path) -> None:
        self.workspace = resolve_workspace(workspace)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection | None]:
        connection: sqlite3.Connection | None = None
        try:
            path = self._path(create=False)
            if path is None:
                yield None
                return
            connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
            self._configure(connection)
            if self._require_readable_version(connection) is None:
                yield None
                return
            yield connection
        except ConversationStoreError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise InvalidConversationStoreError(
                "Could not read conversation data."
            ) from error
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        path = self._path(create=True)
        assert path is not None
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path)
            self._configure(connection)
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and not self._application_tables(connection):
                for statement in (*SESSION_STATEMENTS, *MEMORY_STATEMENTS):
                    connection.execute(statement)
            elif version == 1:
                self._validate_schema(connection, 1)
                for statement in MEMORY_STATEMENTS:
                    connection.execute(statement)
            elif version == SCHEMA_VERSION:
                self._validate_schema(connection, SCHEMA_VERSION)
            else:
                raise InvalidConversationStoreError(
                    "Conversation database schema version is not supported."
                )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._validate_schema(connection, SCHEMA_VERSION)
            yield connection
            connection.commit()
        except ConversationStoreError:
            if connection is not None:
                connection.rollback()
            raise
        except (sqlite3.Error, OSError) as error:
            if connection is not None:
                connection.rollback()
            raise _WorkspaceDatabaseWriteError(
                "Could not write conversation data."
            ) from error
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _path(self, *, create: bool) -> Path | None:
        data_directory = self.workspace / DATA_DIRECTORY
        try:
            if not data_directory.exists() and not data_directory.is_symlink():
                if not create:
                    return None
                data_directory.mkdir(exist_ok=True)
            if data_directory.is_symlink():
                raise InvalidConversationStoreError(
                    "Data path must not be a symbolic link."
                )
            resolved_directory = data_directory.resolve(strict=True)
            resolved_directory.relative_to(self.workspace)
            if not resolved_directory.is_dir():
                raise InvalidConversationStoreError(
                    "Data path is not a directory."
                )
            target = resolved_directory / DATABASE_FILENAME
            if not target.exists() and not target.is_symlink():
                return target if create else None
            if target.is_symlink():
                raise InvalidConversationStoreError(
                    "Database must not be a symbolic link."
                )
            resolved_target = target.resolve(strict=True)
            resolved_target.relative_to(self.workspace)
            if not resolved_target.is_file():
                raise InvalidConversationStoreError(
                    "Database is not a regular file."
                )
            return resolved_target
        except InvalidConversationStoreError:
            raise
        except (OSError, ValueError) as error:
            raise InvalidConversationStoreError("Data path is invalid.") from error

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def _require_readable_version(
        cls, connection: sqlite3.Connection
    ) -> int | None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and not cls._application_tables(connection):
            return None
        if version not in {1, SCHEMA_VERSION}:
            raise InvalidConversationStoreError(
                "Conversation database schema version is not supported."
            )
        cls._validate_schema(connection, version)
        return version

    @staticmethod
    def _application_tables(connection: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    @classmethod
    def _validate_schema(
        cls, connection: sqlite3.Connection, version: int
    ) -> None:
        expected_tables = _V1_TABLES if version == 1 else _V2_TABLES
        if cls._application_tables(connection) != expected_tables:
            cls._invalid_schema()
        for table in expected_tables:
            columns = tuple(
                (row[1], row[2].upper(), row[3], row[5])
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if columns != _COLUMNS[table]:
                cls._invalid_schema()

            foreign_keys = tuple(
                sorted(
                    (row[3], row[2], row[4], row[6].upper())
                    for row in connection.execute(
                        f"PRAGMA foreign_key_list({table})"
                    )
                )
            )
            if foreign_keys != _FOREIGN_KEYS[table]:
                cls._invalid_schema()

            unique_indexes: set[tuple[str, ...]] = set()
            for index in connection.execute(f"PRAGMA index_list({table})"):
                if index[2] != 1 or index[4] != 0:
                    continue
                unique_indexes.add(
                    tuple(
                        row[2]
                        for row in connection.execute(
                            f"PRAGMA index_info({cls._quote(index[1])})"
                        )
                    )
                )
            if not set(_UNIQUE_INDEXES[table]).issubset(unique_indexes):
                cls._invalid_schema()

            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if row is None or not isinstance(row[0], str):
                cls._invalid_schema()
            normalized_sql = re.sub(r"\s+", "", row[0].casefold())
            if any(
                fragment not in normalized_sql
                for fragment in _CHECK_FRAGMENTS[table]
            ):
                cls._invalid_schema()

    @staticmethod
    def _quote(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @staticmethod
    def _invalid_schema() -> None:
        raise InvalidConversationStoreError(
            "Conversation database schema is invalid."
        )
