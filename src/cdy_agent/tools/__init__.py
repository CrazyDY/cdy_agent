from pathlib import Path

from .filesystem import ReadFileTool, WriteFileTool
from .registry import ToolRegistry
from .shell import ShellTool


def create_builtin_registry(workspace: Path) -> ToolRegistry:
    """Create the deterministic registry of built-in local tools."""
    return ToolRegistry([
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        ShellTool(workspace),
    ])


__all__ = ["create_builtin_registry"]
