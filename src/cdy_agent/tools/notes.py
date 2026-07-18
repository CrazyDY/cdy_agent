from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from .base import ToolResult
from .personal_store import PersonalStore


MAX_TITLE_CHARACTERS = 200
MAX_CONTENT_BYTES = 64 * 1024


def _new_id() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _utf8_size(value: str) -> int | None:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _find(
    items: list[dict[str, Any]], note_id: str
) -> dict[str, Any] | None:
    return next((item for item in items if item["id"] == note_id), None)


def _validate_create(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"title", "content"}:
        return ToolResult.failure(
            "invalid_arguments", "title and content are required."
        )
    title, content = arguments["title"], arguments["content"]
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title.strip()) > MAX_TITLE_CHARACTERS
        or _utf8_size(title) is None
    ):
        return ToolResult.failure(
            "invalid_arguments", "title must be 1 to 200 characters."
        )
    if not isinstance(content, str):
        return ToolResult.failure(
            "invalid_arguments", "content must be at most 64 KiB of UTF-8 text."
        )
    size = _utf8_size(content)
    if size is None or size > MAX_CONTENT_BYTES:
        return ToolResult.failure(
            "invalid_arguments", "content must be at most 64 KiB of UTF-8 text."
        )
    return None


def _validate_id(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"note_id"} or not _valid_id(arguments.get("note_id")):
        return ToolResult.failure(
            "invalid_arguments", "note_id must be a canonical UUID."
        )
    return None


def _validate_empty(arguments: dict[str, Any]) -> ToolResult | None:
    if arguments:
        return ToolResult.failure("invalid_arguments", "No arguments are accepted.")
    return None


@dataclass
class CreateNoteTool:
    store: PersonalStore
    id_factory: Callable[[], str] = _new_id
    now_factory: Callable[[], str] = _now
    name: str = "create_note"
    description: str = "Create a persistent note in the workspace."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["title", "content"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_create(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        return None if loaded.ok else loaded

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        if _validate_create(arguments) is not None:
            return "Invalid create_note arguments."
        size = _utf8_size(arguments["content"])
        return (
            f"Create note '{arguments['title'].strip()}' with {size} bytes "
            "of UTF-8 text."
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_create(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        note = {
            "id": self.id_factory(),
            "title": arguments["title"].strip(),
            "content": arguments["content"],
            "created_at": self.now_factory(),
        }
        items = [*loaded.data, note]
        items.sort(key=lambda item: (item["created_at"], item["id"]))
        saved = self.store.save_notes(items)
        return ToolResult.success(dict(note)) if saved.ok else saved


@dataclass
class ListNotesTool:
    store: PersonalStore
    name: str = "list_notes"
    description: str = "List persistent note summaries from the workspace."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_empty(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "List notes."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_empty(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        items = sorted(
            loaded.data, key=lambda item: (item["created_at"], item["id"])
        )
        return ToolResult.success(
            [
                {key: item[key] for key in ("id", "title", "created_at")}
                for item in items
            ]
        )


@dataclass
class GetNoteTool:
    store: PersonalStore
    name: str = "get_note"
    description: str = "Get one persistent note by ID."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_id(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Get note {arguments.get('note_id', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        note = _find(loaded.data, arguments["note_id"])
        if note is None:
            return ToolResult.failure("note_not_found", "Note was not found.")
        return ToolResult.success(dict(note))


@dataclass
class DeleteNoteTool:
    store: PersonalStore
    name: str = "delete_note"
    description: str = "Delete one persistent note by ID."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        if _find(loaded.data, arguments["note_id"]) is None:
            return ToolResult.failure("note_not_found", "Note was not found.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        loaded = self.store.load_notes()
        note = (
            _find(loaded.data, arguments.get("note_id", "")) if loaded.ok else None
        )
        if note is None:
            return "Delete note."
        return f"Delete note {note['id']} titled '{note['title']}'."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        note = _find(loaded.data, arguments["note_id"])
        if note is None:
            return ToolResult.failure("note_not_found", "Note was not found.")
        saved = self.store.save_notes(
            [item for item in loaded.data if item["id"] != note["id"]]
        )
        if not saved.ok:
            return saved
        return ToolResult.success({"id": note["id"], "deleted": True})
