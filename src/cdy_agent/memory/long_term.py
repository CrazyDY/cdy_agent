from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .database import (
    ConversationStoreError,
    InvalidConversationStoreError,
    WorkspaceDatabase,
)


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
            raise InvalidMemoryError(
                "Each memory tag must be at most 50 characters."
            )
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidMemoryError(
                "Each memory tag must be UTF-8 text."
            ) from error
        normalized.add(value)
    return tuple(sorted(normalized))


def _normalize_query(query: object) -> str | None:
    if query is None:
        return None
    if not isinstance(query, str):
        raise InvalidMemoryError("Memory search query must be text.")
    value = query.strip()
    if len(value) > MAX_QUERY_CHARACTERS:
        raise InvalidMemoryError(
            "Memory search query must be at most 500 characters."
        )
    return value or None


def _identity(content: str, tags: tuple[str, ...]) -> str:
    payload = json.dumps(
        [content, list(tags)], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidMemoryError("Memory ID must be a complete UUID.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        raise InvalidMemoryError("Memory ID must be a complete UUID.") from error
    if str(parsed) != value:
        raise InvalidMemoryError("Memory ID must be a complete UUID.")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MemoryStoreError("Memory clock must be timezone-aware.")
    try:
        offset = value.utcoffset()
        if offset is None:
            raise ValueError("clock has no UTC offset")
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
    except (OSError, OverflowError, TypeError, ValueError) as error:
        raise MemoryStoreError("Memory clock is invalid.") from error


def _require_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InvalidMemoryError("Stored memory data is invalid.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        if _timestamp(parsed) != value:
            raise ValueError("timestamp is not canonical")
    except (ValueError, MemoryStoreError) as error:
        raise InvalidMemoryError("Stored memory data is invalid.") from error
    return value


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(
        self,
        workspace: Path,
        *,
        clock: Callable[[], datetime] = _now,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self._database = WorkspaceDatabase(workspace)
        self._clock = clock
        self._id_factory = id_factory

    def prepare(self, content: str, tags: Sequence[str]) -> MemoryDraft:
        normalized_content = _normalize_content(content)
        normalized_tags = _normalize_tags(tags)
        return MemoryDraft(
            normalized_content,
            normalized_tags,
            _identity(normalized_content, normalized_tags),
        )

    def find_duplicate(
        self, draft: MemoryDraft, *, exclude_id: str | None = None
    ) -> StoredMemory | None:
        self._require_draft(draft)
        excluded = _canonical_uuid(exclude_id) if exclude_id is not None else None
        try:
            with self._database.read() as connection:
                if connection is None:
                    return None
                if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                    return None
                return self._find_duplicate(connection, draft, excluded)
        except MemoryStoreError:
            raise
        except (sqlite3.Error, InvalidConversationStoreError) as error:
            raise MemoryStoreError("Could not read memory data.") from error

    def create(self, content: str, tags: Sequence[str]) -> StoredMemory:
        draft = self.prepare(content, tags)
        memory_id = _canonical_uuid(self._id_factory())
        created_at = _timestamp(self._clock())
        try:
            with self._database.write() as connection:
                try:
                    connection.execute(
                        "INSERT INTO memories "
                        "(id, content, identity_hash, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            memory_id,
                            draft.content,
                            draft.identity_hash,
                            created_at,
                            created_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    duplicate = self._find_duplicate(connection, draft, None)
                    if duplicate is not None:
                        raise DuplicateMemoryError(duplicate.id)
                    raise
                self._insert_tags(connection, memory_id, draft.tags)
                return StoredMemory(
                    memory_id,
                    draft.content,
                    draft.tags,
                    created_at,
                    created_at,
                )
        except MemoryStoreError:
            raise
        except (sqlite3.Error, ConversationStoreError) as error:
            raise MemoryStoreError("Could not write memory data.") from error

    def get(self, memory_id: str) -> StoredMemory:
        canonical_id = _canonical_uuid(memory_id)
        try:
            with self._database.read() as connection:
                if connection is None:
                    raise MemoryNotFoundError("Memory not found.")
                if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                    raise MemoryNotFoundError("Memory not found.")
                record = self._load(connection, canonical_id)
                if record is None:
                    raise MemoryNotFoundError("Memory not found.")
                return record
        except MemoryStoreError:
            raise
        except (sqlite3.Error, InvalidConversationStoreError) as error:
            raise MemoryStoreError("Could not read memory data.") from error

    def list_memories(
        self, tags: Sequence[str] = ()
    ) -> tuple[StoredMemory, ...]:
        normalized_tags = _normalize_tags(tags)
        try:
            with self._database.read() as connection:
                if connection is None:
                    return ()
                if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                    return ()
                return tuple(
                    record
                    for record in self._all_records(connection)
                    if set(normalized_tags).issubset(record.tags)
                )
        except MemoryStoreError:
            raise
        except (sqlite3.Error, InvalidConversationStoreError) as error:
            raise MemoryStoreError("Could not read memory data.") from error

    def search(
        self, query: str | None = None, tags: Sequence[str] = ()
    ) -> tuple[StoredMemory, ...]:
        normalized_query = _normalize_query(query)
        normalized_tags = _normalize_tags(tags)
        if normalized_query is None and not normalized_tags:
            raise InvalidMemoryError("Memory search requires query or tags.")
        terms = (
            tuple(normalized_query.casefold().split())
            if normalized_query
            else ()
        )
        try:
            with self._database.read() as connection:
                if connection is None:
                    return ()
                if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                    return ()
                matches: list[StoredMemory] = []
                for record in self._all_records(connection):
                    haystack = record.content.casefold()
                    tag_haystack = record.tags
                    matches_terms = all(
                        term in haystack or any(term in tag for tag in tag_haystack)
                        for term in terms
                    )
                    matches_tags = set(normalized_tags).issubset(tag_haystack)
                    if matches_terms and matches_tags:
                        matches.append(record)
                        if len(matches) == MAX_SEARCH_RESULTS:
                            break
                return tuple(matches)
        except MemoryStoreError:
            raise
        except (sqlite3.Error, InvalidConversationStoreError) as error:
            raise MemoryStoreError("Could not read memory data.") from error

    def update(
        self, memory_id: str, content: str, tags: Sequence[str]
    ) -> StoredMemory:
        canonical_id = _canonical_uuid(memory_id)
        draft = self.prepare(content, tags)
        try:
            with self._database.write() as connection:
                original = self._load(connection, canonical_id)
                if original is None:
                    raise MemoryNotFoundError("Memory not found.")
                duplicate = self._find_duplicate(connection, draft, canonical_id)
                if duplicate is not None:
                    raise DuplicateMemoryError(duplicate.id)
                updated_at = _timestamp(self._clock())
                connection.execute(
                    "UPDATE memories SET content = ?, identity_hash = ?, "
                    "updated_at = ? WHERE id = ?",
                    (draft.content, draft.identity_hash, updated_at, canonical_id),
                )
                connection.execute(
                    "DELETE FROM memory_tags WHERE memory_id = ?", (canonical_id,)
                )
                self._insert_tags(connection, canonical_id, draft.tags)
                return StoredMemory(
                    canonical_id,
                    draft.content,
                    draft.tags,
                    original.created_at,
                    updated_at,
                )
        except MemoryStoreError:
            raise
        except (sqlite3.Error, ConversationStoreError) as error:
            raise MemoryStoreError("Could not write memory data.") from error

    def delete(self, memory_id: str) -> None:
        canonical_id = _canonical_uuid(memory_id)
        try:
            with self._database.write() as connection:
                cursor = connection.execute(
                    "DELETE FROM memories WHERE id = ?", (canonical_id,)
                )
                if cursor.rowcount != 1:
                    raise MemoryNotFoundError("Memory not found.")
        except MemoryStoreError:
            raise
        except (sqlite3.Error, ConversationStoreError) as error:
            raise MemoryStoreError("Could not delete memory data.") from error

    @staticmethod
    def _require_draft(draft: object) -> None:
        if not isinstance(draft, MemoryDraft):
            raise InvalidMemoryError("Memory draft is invalid.")
        try:
            valid = (
                _normalize_content(draft.content) == draft.content
                and _normalize_tags(draft.tags) == draft.tags
                and _identity(draft.content, draft.tags) == draft.identity_hash
            )
        except InvalidMemoryError as error:
            raise InvalidMemoryError("Memory draft is invalid.") from error
        if not valid:
            raise InvalidMemoryError("Memory draft is invalid.")

    @staticmethod
    def _insert_tags(
        connection: sqlite3.Connection, memory_id: str, tags: tuple[str, ...]
    ) -> None:
        connection.executemany(
            "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            ((memory_id, tag) for tag in tags),
        )

    def _find_duplicate(
        self,
        connection: sqlite3.Connection,
        draft: MemoryDraft,
        exclude_id: str | None,
    ) -> StoredMemory | None:
        parameters: list[str] = [draft.identity_hash]
        query = "SELECT id FROM memories WHERE identity_hash = ?"
        if exclude_id is not None:
            query += " AND id != ?"
            parameters.append(exclude_id)
        row = connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        record = self._load(connection, row[0])
        assert record is not None
        if record.content == draft.content and record.tags == draft.tags:
            return record
        return None

    def _all_records(
        self, connection: sqlite3.Connection
    ) -> tuple[StoredMemory, ...]:
        records = []
        for row in connection.execute("SELECT id FROM memories"):
            record = self._load(connection, row[0])
            assert record is not None
            records.append(record)
        records.sort(key=lambda record: record.id)
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return tuple(records)

    @staticmethod
    def _load(
        connection: sqlite3.Connection, memory_id: str
    ) -> StoredMemory | None:
        row = connection.execute(
            "SELECT id, content, identity_hash, created_at, updated_at "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        tags = tuple(
            item[0]
            for item in connection.execute(
                "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
                (memory_id,),
            )
        )
        try:
            canonical_id = _canonical_uuid(row[0])
            content = _normalize_content(row[1])
            normalized_tags = _normalize_tags(tags)
            created_at = _require_timestamp(row[3])
            updated_at = _require_timestamp(row[4])
            valid = (
                content == row[1]
                and normalized_tags == tags
                and isinstance(row[2], str)
                and _identity(content, normalized_tags) == row[2]
            )
        except InvalidMemoryError as error:
            raise InvalidMemoryError("Stored memory data is invalid.") from error
        if not valid:
            raise InvalidMemoryError("Stored memory data is invalid.")
        return StoredMemory(
            canonical_id, content, normalized_tags, created_at, updated_at
        )
