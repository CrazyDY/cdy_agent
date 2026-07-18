from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from .base import ToolResult
from .personal_store import PersonalStore


MAX_TODO_CHARACTERS = 1000


def _new_id() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _utf8_encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _find(
    items: list[dict[str, Any]], todo_id: str
) -> dict[str, Any] | None:
    return next((item for item in items if item["id"] == todo_id), None)


def _validate_create(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"text"}:
        return ToolResult.failure("invalid_arguments", "text is required.")
    text = arguments["text"]
    if not isinstance(text, str):
        return ToolResult.failure(
            "invalid_arguments", "text must be 1 to 1000 characters."
        )
    trimmed = text.strip()
    if (
        not trimmed
        or len(trimmed) > MAX_TODO_CHARACTERS
        or not _utf8_encodable(trimmed)
    ):
        return ToolResult.failure(
            "invalid_arguments", "text must be 1 to 1000 characters."
        )
    return None


def _validate_id(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"todo_id"} or not _valid_id(
        arguments.get("todo_id")
    ):
        return ToolResult.failure(
            "invalid_arguments", "todo_id must be a canonical UUID."
        )
    return None


def _validate_empty(arguments: dict[str, Any]) -> ToolResult | None:
    if arguments:
        return ToolResult.failure(
            "invalid_arguments", "No arguments are accepted."
        )
    return None


@dataclass
class CreateTodoTool:
    store: PersonalStore
    id_factory: Callable[[], str] = _new_id
    now_factory: Callable[[], str] = _now
    name: str = "create_todo"
    description: str = "Create a persistent Todo in the workspace."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_create(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        if _validate_create(arguments) is not None:
            return "Invalid create_todo arguments."
        return f"Create Todo: {arguments['text'].strip()}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_create(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = {
            "id": self.id_factory(),
            "text": arguments["text"].strip(),
            "completed": False,
            "created_at": self.now_factory(),
            "completed_at": None,
        }
        items = [*loaded.data, todo]
        items.sort(key=lambda item: (item["created_at"], item["id"]))
        saved = self.store.save_todos(items)
        if not saved.ok:
            return saved
        return ToolResult.success(dict(todo))


@dataclass
class ListTodosTool:
    store: PersonalStore
    name: str = "list_todos"
    description: str = "List persistent Todos from the workspace."
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
        return "List Todos."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_empty(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        items = sorted(
            loaded.data, key=lambda item: (item["created_at"], item["id"])
        )
        return ToolResult.success([dict(item) for item in items])


@dataclass
class CompleteTodoTool:
    store: PersonalStore
    now_factory: Callable[[], str] = _now
    name: str = "complete_todo"
    description: str = "Mark one persistent Todo complete by ID."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"todo_id": {"type": "string"}},
            "required": ["todo_id"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = _find(loaded.data, arguments["todo_id"])
        if todo is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        if todo["completed"]:
            return ToolResult.failure(
                "todo_already_completed", "Todo is already completed."
            )
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        loaded = self.store.load_todos()
        todo = (
            _find(loaded.data, arguments.get("todo_id", ""))
            if loaded.ok
            else None
        )
        if todo is None:
            return "Complete Todo."
        return f"Complete Todo {todo['id']}: {todo['text']}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = _find(loaded.data, arguments["todo_id"])
        if todo is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        if todo["completed"]:
            return ToolResult.failure(
                "todo_already_completed", "Todo is already completed."
            )
        todo["completed"] = True
        todo["completed_at"] = self.now_factory()
        saved = self.store.save_todos(loaded.data)
        if not saved.ok:
            return saved
        return ToolResult.success(dict(todo))


@dataclass
class DeleteTodoTool:
    store: PersonalStore
    name: str = "delete_todo"
    description: str = "Delete one persistent Todo by ID."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"todo_id": {"type": "string"}},
            "required": ["todo_id"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        if _find(loaded.data, arguments["todo_id"]) is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        loaded = self.store.load_todos()
        todo = (
            _find(loaded.data, arguments.get("todo_id", ""))
            if loaded.ok
            else None
        )
        if todo is None:
            return "Delete Todo."
        return f"Delete Todo {todo['id']}: {todo['text']}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = _find(loaded.data, arguments["todo_id"])
        if todo is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        remaining = [
            item for item in loaded.data if item["id"] != todo["id"]
        ]
        saved = self.store.save_todos(remaining)
        if not saved.ok:
            return saved
        return ToolResult.success({"id": todo["id"], "deleted": True})
