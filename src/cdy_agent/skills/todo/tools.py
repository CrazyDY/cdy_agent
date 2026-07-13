"""Todo skill tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import count

from cdy_agent.openai_sdk import function_tool


@dataclass
class TodoItem:
    id: int
    title: str
    due_date: str | None = None
    priority: str | None = None
    done: bool = False


_todo_ids = count(1)
_todos: list[TodoItem] = []


def reset_todos() -> None:
    """Clear the in-memory todo store. Intended for tests and local demos."""

    global _todo_ids
    _todo_ids = count(1)
    _todos.clear()


@function_tool
def add_todo(title: str, due_date: str | None = None, priority: str | None = None) -> dict:
    """Add a todo item.

    Args:
        title: The todo title.
        due_date: Optional due date, such as 2026-07-15 or next Friday.
        priority: Optional priority, such as low, medium, high, or urgent.
    """

    item = TodoItem(id=next(_todo_ids), title=title, due_date=due_date, priority=priority)
    _todos.append(item)
    return asdict(item)


@function_tool
def list_todos(include_done: bool = False) -> list[dict]:
    """List todo items.

    Args:
        include_done: Whether completed todos should be returned.
    """

    return [asdict(item) for item in _todos if include_done or not item.done]


@function_tool
def complete_todo(todo_id: int) -> dict:
    """Mark a todo item as completed.

    Args:
        todo_id: The numeric id of the todo item to complete.
    """

    for item in _todos:
        if item.id == todo_id:
            item.done = True
            return asdict(item)
    return {"error": f"Todo item {todo_id} was not found."}
