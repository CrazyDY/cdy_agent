from pathlib import Path

from .filesystem import ReadFileTool, WriteFileTool
from .notes import CreateNoteTool, DeleteNoteTool, GetNoteTool, ListNotesTool
from .personal_store import PersonalStore
from .registry import ToolRegistry
from .shell import ShellTool
from .todos import CompleteTodoTool, CreateTodoTool, DeleteTodoTool, ListTodosTool


def create_builtin_registry(workspace: Path) -> ToolRegistry:
    """Create the deterministic registry of built-in local tools."""
    store = PersonalStore(workspace)
    return ToolRegistry([
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        ShellTool(workspace),
        CreateNoteTool(store),
        ListNotesTool(store),
        GetNoteTool(store),
        DeleteNoteTool(store),
        CreateTodoTool(store),
        ListTodosTool(store),
        CompleteTodoTool(store),
        DeleteTodoTool(store),
    ])


__all__ = ["create_builtin_registry"]
