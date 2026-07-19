from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from cdy_agent.memory import (
    DuplicateMemoryError,
    InvalidMemoryError,
    MemoryNotFoundError,
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


@dataclass(frozen=True)
class RememberMemoryTool:
    store: MemoryStore
    name: ClassVar[str] = "remember_memory"
    description: ClassVar[str] = (
        "Store a long-term memory only when the user explicitly asks to remember it."
    )
    parameters: ClassVar[dict[str, Any]] = CONTENT_TAGS_SCHEMA
    requires_confirmation: ClassVar[bool] = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_content_tags(arguments)
        if invalid is not None:
            return invalid
        try:
            draft = self.store.prepare(arguments["content"], arguments["tags"])
            duplicate = self.store.find_duplicate(draft)
        except MemoryStoreError as error:
            return _failure(error)
        if duplicate is not None:
            return _failure(DuplicateMemoryError(duplicate.id))
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        draft = self.store.prepare(arguments["content"], arguments["tags"])
        return (
            f"Remember long-term memory with tags {_tags_description(draft.tags)}:\n"
            f"{draft.content}"
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_content_tags(arguments)
        if invalid is not None:
            return invalid
        try:
            return ToolResult.success(
                _record_data(self.store.create(arguments["content"], arguments["tags"]))
            )
        except MemoryStoreError as error:
            return _failure(error)


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


@dataclass(frozen=True)
class UpdateMemoryTool:
    store: MemoryStore
    name: ClassVar[str] = "update_memory"
    description: ClassVar[str] = (
        "Update a long-term memory only when the user explicitly asks to change it."
    )
    parameters: ClassVar[dict[str, Any]] = UPDATE_SCHEMA
    requires_confirmation: ClassVar[bool] = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_update(arguments)
        if invalid is not None:
            return invalid
        try:
            draft = self.store.prepare(arguments["content"], arguments["tags"])
            self.store.get(arguments["memory_id"])
            duplicate = self.store.find_duplicate(
                draft, exclude_id=arguments["memory_id"]
            )
        except MemoryStoreError as error:
            return _failure(error)
        if duplicate is not None:
            return _failure(DuplicateMemoryError(duplicate.id))
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        current = self.store.get(arguments["memory_id"])
        replacement = self.store.prepare(arguments["content"], arguments["tags"])
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
            record = self.store.update(
                arguments["memory_id"], arguments["content"], arguments["tags"]
            )
            return ToolResult.success(_record_data(record))
        except MemoryStoreError as error:
            return _failure(error)


@dataclass(frozen=True)
class ForgetMemoryTool:
    store: MemoryStore
    name: ClassVar[str] = "forget_memory"
    description: ClassVar[str] = (
        "Delete a long-term memory only when the user explicitly asks to forget it."
    )
    parameters: ClassVar[dict[str, Any]] = ID_SCHEMA
    requires_confirmation: ClassVar[bool] = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        try:
            self.store.get(arguments["memory_id"])
        except MemoryStoreError as error:
            return _failure(error)
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        record = self.store.get(arguments["memory_id"])
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
            self.store.delete(arguments["memory_id"])
            return ToolResult.success(
                {"id": arguments["memory_id"], "deleted": True}
            )
        except MemoryStoreError as error:
            return _failure(error)
