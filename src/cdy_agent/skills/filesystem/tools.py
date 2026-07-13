"""Filesystem skill tools scoped to the current workspace."""

from __future__ import annotations

from pathlib import Path

from cdy_agent.openai_sdk import function_tool

WORKSPACE_ROOT = Path.cwd().resolve()
MAX_READ_BYTES = 64_000


def _resolve_workspace_path(path: str) -> Path:
    target = (WORKSPACE_ROOT / path).resolve()
    if target != WORKSPACE_ROOT and WORKSPACE_ROOT not in target.parents:
        raise ValueError(f"Path is outside workspace: {path}")
    return target


@function_tool
def list_files(path: str = ".", recursive: bool = False, max_entries: int = 200) -> list[str]:
    """List files and directories under the workspace.

    Args:
        path: Relative workspace path to list.
        recursive: Whether to recursively list descendants.
        max_entries: Maximum number of returned entries.
    """

    target = _resolve_workspace_path(path)
    if not target.exists():
        return [f"Path does not exist: {path}"]
    if target.is_file():
        return [target.relative_to(WORKSPACE_ROOT).as_posix()]

    iterator = target.rglob("*") if recursive else target.iterdir()
    entries: list[str] = []
    for entry in sorted(iterator):
        entries.append(entry.relative_to(WORKSPACE_ROOT).as_posix())
        if len(entries) >= max_entries:
            break
    return entries


@function_tool
def read_file(path: str, start_line: int = 1, max_lines: int = 200) -> dict:
    """Read a UTF-8 text file from the workspace.

    Args:
        path: Relative workspace file path.
        start_line: 1-based first line to return.
        max_lines: Maximum number of lines to return.
    """

    target = _resolve_workspace_path(path)
    if not target.exists():
        return {"error": f"File does not exist: {path}"}
    if not target.is_file():
        return {"error": f"Path is not a file: {path}"}
    if target.stat().st_size > MAX_READ_BYTES:
        return {"error": f"File is too large to read with this tool: {path}"}

    lines = target.read_text(encoding="utf-8").splitlines()
    first = max(start_line, 1) - 1
    selected = lines[first : first + max_lines]
    return {
        "path": target.relative_to(WORKSPACE_ROOT).as_posix(),
        "start_line": first + 1,
        "end_line": first + len(selected),
        "content": "\n".join(selected),
    }


@function_tool
def write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """Write a UTF-8 text file inside the workspace.

    Args:
        path: Relative workspace file path.
        content: Full file content to write.
        overwrite: Whether an existing file may be replaced.
    """

    target = _resolve_workspace_path(path)
    if target.exists() and not overwrite:
        return {"error": f"File already exists; set overwrite=true to replace it: {path}"}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": target.relative_to(WORKSPACE_ROOT).as_posix(), "bytes_written": len(content.encode("utf-8"))}
