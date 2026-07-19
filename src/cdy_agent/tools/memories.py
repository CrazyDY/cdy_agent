from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from cdy_agent.memory import (
    DuplicateMemoryError,
    InvalidMemoryError,
    MemoryConflictError,
    MemoryNotFoundError,
    PreparedCreate,
    PreparedDelete,
    PreparedUpdate,
    MemoryStore,
    MemoryStoreError,
    StoredMemory,
)

from .base import ToolResult


CONTENT_TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["content", "tags"],
    "additionalProperties": False,
}
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["query", "tags"],
    "additionalProperties": False,
}
UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_id": {"type": "string"},
        "content": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["memory_id", "content", "tags"],
    "additionalProperties": False,
}
ID_SCHEMA = {
    "type": "object",
    "properties": {"memory_id": {"type": "string"}},
    "required": ["memory_id"],
    "additionalProperties": False,
}


def _record_data(record: StoredMemory) -> dict[str, object]:
    return {
        "id": record.id,
        "content": record.content,
        "tags": list(record.tags),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _failure(error: MemoryStoreError) -> ToolResult:
    if isinstance(error, MemoryConflictError):
        return ToolResult.failure("memory_conflict", str(error))
    if isinstance(error, DuplicateMemoryError):
        return ToolResult.failure("duplicate_memory", str(error))
    if isinstance(error, MemoryNotFoundError):
        return ToolResult.failure("memory_not_found", str(error))
    if isinstance(error, InvalidMemoryError):
        return ToolResult.failure("invalid_arguments", str(error))
    return ToolResult.failure("memory_store_error", str(error))


def _invalid(message: str = "Arguments do not match the tool schema.") -> ToolResult:
    return ToolResult.failure("invalid_arguments", message)


def _valid_tags(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 10
        and all(isinstance(tag, str) for tag in value)
    )


def _validate_content_tags(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"content", "tags"}:
        return _invalid()
    if not isinstance(arguments["content"], str) or not _valid_tags(arguments["tags"]):
        return _invalid()
    return None


def _validate_search(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"query", "tags"}:
        return _invalid()
    if not (arguments["query"] is None or isinstance(arguments["query"], str)):
        return _invalid()
    if not _valid_tags(arguments["tags"]):
        return _invalid()
    return None


def _validate_update(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"memory_id", "content", "tags"}:
        return _invalid()
    if not isinstance(arguments["memory_id"], str):
        return _invalid()
    return _validate_content_tags(
        {"content": arguments["content"], "tags": arguments["tags"]}
    )


def _validate_id(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"memory_id"} or not isinstance(
        arguments.get("memory_id"), str
    ):
        return _invalid()
    return None


def _tags_description(tags: tuple[str, ...]) -> str:
    return f"[{', '.join(tags)}]"


@dataclass
class RememberMemoryTool:
    store: MemoryStore
    _prepared: PreparedCreate | None = field(
        init=False, default=None, repr=False
    )
    name: ClassVar[str] = "remember_memory"
    description: ClassVar[str] = (
        "Store a long-term memory only when the user explicitly asks to remember it."
    )
    parameters: ClassVar[dict[str, Any]] = CONTENT_TAGS_SCHEMA
    requires_confirmation: ClassVar[bool] = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        self._prepared = None
        invalid = _validate_content_tags(arguments)
        if invalid is not None:
            return invalid
        try:
            self._prepared = self.store.prepare_create(
                arguments["content"], arguments["tags"]
            )
        except MemoryStoreError as error:
            return _failure(error)
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        if self._prepared is None:
            raise MemoryStoreError("Memory operation was not prepared.")
        return (
            f"Remember long-term memory {self._prepared.memory_id} with tags "
            f"{_tags_description(self._prepared.draft.tags)}:\n"
            f"{self._prepared.draft.content}"
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_content_tags(arguments)
        if invalid is not None:
            return invalid
        try:
            if self._prepared is None:
                raise MemoryStoreError("Memory operation was not prepared.")
            return ToolResult.success(
                _record_data(self.store.commit_create(self._prepared))
            )
        except MemoryStoreError as error:
            return _failure(error)
        finally:
            self._prepared = None

    def cancel(self) -> None:
        self._prepared = None


@dataclass(frozen=True)
class SearchMemoriesTool:
    store: MemoryStore
    name: ClassVar[str] = "search_memories"
    description: ClassVar[str] = (
        "Search long-term memories only when the user explicitly asks to search them."
    )
    parameters: ClassVar[dict[str, Any]] = SEARCH_SCHEMA
    requires_confirmation: ClassVar[bool] = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_search(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "Search long-term memories."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_search(arguments)
        if invalid is not None:
            return invalid
        try:
            records = self.store.search(arguments["query"], arguments["tags"])
            return ToolResult.success([_record_data(record) for record in records])
        except MemoryStoreError as error:
            return _failure(error)


@dataclass
class UpdateMemoryTool:
    store: MemoryStore
    _prepared: PreparedUpdate | None = field(
        init=False, default=None, repr=False
    )
    name: ClassVar[str] = "update_memory"
    description: ClassVar[str] = (
        "Update a long-term memory only when the user explicitly asks to change it."
    )
    parameters: ClassVar[dict[str, Any]] = UPDATE_SCHEMA
    requires_confirmation: ClassVar[bool] = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        self._prepared = None
        invalid = _validate_update(arguments)
        if invalid is not None:
            return invalid
        try:
            self._prepared = self.store.prepare_update(
                arguments["memory_id"], arguments["content"], arguments["tags"]
            )
        except MemoryStoreError as error:
            return _failure(error)
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        if self._prepared is None:
            raise MemoryStoreError("Memory operation was not prepared.")
        current = self._prepared.before
        replacement = self._prepared.replacement
        return (
            f"Update long-term memory {current.id}.\n"
            f"Current:\nTags {_tags_description(current.tags)}\n{current.content}\n"
            f"Replacement:\nTags {_tags_description(replacement.tags)}\n"
            f"{replacement.content}"
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_update(arguments)
        if invalid is not None:
            return invalid
        try:
            if self._prepared is None:
                raise MemoryStoreError("Memory operation was not prepared.")
            record = self.store.commit_update(self._prepared)
            return ToolResult.success(_record_data(record))
        except MemoryStoreError as error:
            return _failure(error)
        finally:
            self._prepared = None

    def cancel(self) -> None:
        self._prepared = None


@dataclass
class ForgetMemoryTool:
    store: MemoryStore
    _prepared: PreparedDelete | None = field(
        init=False, default=None, repr=False
    )
    name: ClassVar[str] = "forget_memory"
    description: ClassVar[str] = (
        "Delete a long-term memory only when the user explicitly asks to forget it."
    )
    parameters: ClassVar[dict[str, Any]] = ID_SCHEMA
    requires_confirmation: ClassVar[bool] = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        self._prepared = None
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        try:
            self._prepared = self.store.prepare_delete(arguments["memory_id"])
        except MemoryStoreError as error:
            return _failure(error)
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        if self._prepared is None:
            raise MemoryStoreError("Memory operation was not prepared.")
        record = self._prepared.before
        return (
            f"Forget long-term memory {record.id} with tags "
            f"{_tags_description(record.tags)}:\n{record.content}\n"
            f"Created: {record.created_at}\nUpdated: {record.updated_at}"
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        try:
            if self._prepared is None:
                raise MemoryStoreError("Memory operation was not prepared.")
            self.store.commit_delete(self._prepared)
            return ToolResult.success(
                {"id": self._prepared.before.id, "deleted": True}
            )
        except MemoryStoreError as error:
            return _failure(error)
        finally:
            self._prepared = None

    def cancel(self) -> None:
        self._prepared = None
