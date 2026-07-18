from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cdy_agent.tools.base import ToolResult


MAX_READ_BYTES = 1024 * 1024


def resolve_workspace(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Invalid workspace directory: {path}.")
    return resolved


def _resolve_existing(workspace: Path, raw_path: object) -> Path | ToolResult:
    if not isinstance(raw_path, str) or not raw_path:
        return ToolResult.failure(
            "invalid_arguments", "path must be a non-empty string."
        )
    path = Path(raw_path)
    try:
        target = (
            (workspace / path).resolve() if not path.is_absolute() else path.resolve()
        )
    except OSError as error:
        return ToolResult.failure("file_error", f"Could not resolve path: {error}.")
    try:
        target.relative_to(workspace)
    except ValueError:
        return ToolResult.failure(
            "path_outside_workspace", "Path is outside the workspace."
        )
    return target


@dataclass
class ReadFileTool:
    workspace: Path
    name: str = "read_file"
    description: str = "Read a UTF-8 text file from the workspace."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        self.workspace = resolve_workspace(self.workspace)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Read file {arguments.get('path', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        if set(arguments) != {"path"}:
            return ToolResult.failure(
                "invalid_arguments", "Exactly one path argument is required."
            )
        target = _resolve_existing(self.workspace, arguments["path"])
        if isinstance(target, ToolResult):
            return target

        try:
            if not target.is_file():
                return ToolResult.failure("not_a_file", "Path is not a file.")
            with target.open("rb") as file:
                raw_content = file.read(MAX_READ_BYTES + 1)
        except OSError as error:
            return ToolResult.failure("file_error", f"Could not read file: {error}.")

        truncated = len(raw_content) > MAX_READ_BYTES
        content_bytes = raw_content[:MAX_READ_BYTES]
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            if truncated and error.reason == "unexpected end of data" and error.end == len(
                content_bytes
            ):
                content = content_bytes[: error.start].decode("utf-8")
            else:
                return ToolResult.failure(
                    "unsupported_encoding", "File must contain valid UTF-8 text."
                )

        return ToolResult.success(
            {
                "path": str(target),
                "content": content,
                "truncated": truncated,
            }
        )


def _validate_write_arguments(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) not in ({"path", "content"}, {"path", "content", "overwrite"}):
        return ToolResult.failure(
            "invalid_arguments",
            "path and content are required; overwrite is optional.",
        )
    if not isinstance(arguments["path"], str) or not arguments["path"]:
        return ToolResult.failure(
            "invalid_arguments", "path must be a non-empty string."
        )
    if not isinstance(arguments["content"], str):
        return ToolResult.failure("invalid_arguments", "content must be a string.")
    if "overwrite" in arguments and not isinstance(arguments["overwrite"], bool):
        return ToolResult.failure("invalid_arguments", "overwrite must be a boolean.")
    return None


def _resolve_write_target(workspace: Path, raw_path: str) -> Path | ToolResult:
    path = Path(raw_path)
    unresolved = workspace / path if not path.is_absolute() else path
    try:
        if unresolved.exists() or unresolved.is_symlink():
            target = unresolved.resolve()
        else:
            parent = unresolved.parent
            if not parent.exists() or not parent.is_dir():
                return ToolResult.failure(
                    "parent_not_found", "Parent directory does not exist."
                )
            target = parent.resolve() / unresolved.name
    except OSError as error:
        return ToolResult.failure("file_error", f"Could not resolve path: {error}.")

    try:
        target.relative_to(workspace)
    except ValueError:
        return ToolResult.failure(
            "path_outside_workspace", "Path is outside the workspace."
        )
    return target


@dataclass
class WriteFileTool:
    workspace: Path
    name: str = "write_file"
    description: str = "Write UTF-8 text to a file in the workspace."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = True

    def __post_init__(self) -> None:
        self.workspace = resolve_workspace(self.workspace)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        invalid = _validate_write_arguments(arguments)
        if invalid is not None:
            return "Invalid write_file arguments."
        target = _resolve_write_target(self.workspace, arguments["path"])
        if isinstance(target, ToolResult):
            return f"Cannot write file: {target.message}"
        operation = "Overwrite" if target.exists() else "Create"
        byte_count = len(arguments["content"].encode("utf-8"))
        return f"{operation} file {target} with {byte_count} bytes of UTF-8 text."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_write_arguments(arguments)
        if invalid is not None:
            return invalid
        target = _resolve_write_target(self.workspace, arguments["path"])
        if isinstance(target, ToolResult):
            return target

        if target.exists() and target.is_dir():
            return ToolResult.failure("not_a_file", "Path is not a file.")
        overwritten = target.exists()
        if overwritten and arguments.get("overwrite") is not True:
            return ToolResult.failure(
                "overwrite_not_allowed",
                "File already exists; set overwrite to true to replace it.",
            )

        content = arguments["content"]
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as error:
            return ToolResult.failure("file_error", f"Could not write file: {error}.")
        return ToolResult.success(
            {
                "path": str(target),
                "bytes": len(content.encode("utf-8")),
                "overwritten": overwritten,
            }
        )
