from __future__ import annotations

import sqlite3
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


class ConversationStoreError(RuntimeError):
    """A conversation database operation failed safely."""


class ConversationNotFoundError(ConversationStoreError):
    """The requested conversation does not exist."""


class InvalidConversationStoreError(ConversationStoreError):
    """The database path, schema, or stored history is invalid."""


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
            self._require_readable_version(connection)
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
        new_file = not path.exists()
        connection: sqlite3.Connection | None = None
        failed = True
        try:
            connection = sqlite3.connect(path)
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
            failed = False
        except ConversationStoreError:
            if connection is not None:
                connection.rollback()
            raise
        except (sqlite3.Error, OSError) as error:
            if connection is not None:
                connection.rollback()
            raise ConversationStoreError(
                "Could not write conversation data."
            ) from error
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()
            if failed and new_file and path.exists():
                try:
                    path.unlink()
                except OSError as error:
                    raise ConversationStoreError(
                        "Could not remove incomplete database."
                    ) from error

    def _path(self, *, create: bool) -> Path | None:
        data_directory = self.workspace / DATA_DIRECTORY
        try:
            if not data_directory.exists() and not data_directory.is_symlink():
                if not create:
                    return None
                data_directory.mkdir()
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

    @staticmethod
    def _require_readable_version(connection: sqlite3.Connection) -> int:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {1, SCHEMA_VERSION}:
            raise InvalidConversationStoreError(
                "Conversation database schema version is not supported."
            )
        return version
