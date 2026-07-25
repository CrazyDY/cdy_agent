from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .base import ToolResult
from .filesystem import resolve_workspace


APPROVAL_VERSION = 1
DATA_DIRECTORY = ".cdy-agent"
APPROVAL_FILENAME = "shell-approvals.json"
Replace = Callable[
    [
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ],
    None,
]


class ShellApprovalStore:
    def __init__(
        self,
        workspace: Path,
        replace: Replace = os.replace,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self._replace = replace

    def contains(self, argv: Sequence[str]) -> ToolResult:
        loaded = self._load()
        if not loaded.ok:
            return loaded
        command = list(argv)
        return ToolResult.success(command in loaded.data)

    def add(self, argv: Sequence[str]) -> ToolResult:
        command = list(argv)
        if not _valid_command(command):
            return ToolResult.failure(
                "invalid_arguments", "Approval argv must be non-empty strings."
            )
        loaded = self._load()
        if not loaded.ok:
            return loaded
        commands: list[list[str]] = []
        for item in loaded.data:
            normalized = list(item)
            if normalized not in commands:
                commands.append(normalized)
        if command not in commands:
            commands.append(command)
        return self._save(commands)

    def _load(self) -> ToolResult:
        target = self._target(create_directory=False)
        if isinstance(target, ToolResult):
            return target
        if target is None:
            return ToolResult.success([])
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except OSError:
            return ToolResult.failure(
                "approval_store_error", "Could not read Shell approvals."
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ToolResult.failure(
                "invalid_approval_store", "Stored Shell approvals are invalid."
            )
        if not _valid_document(document):
            return ToolResult.failure(
                "invalid_approval_store", "Stored Shell approvals are invalid."
            )
        return ToolResult.success(
            [list(command) for command in document["allowed_commands"]]
        )

    def _save(self, commands: list[list[str]]) -> ToolResult:
        document = {
            "version": APPROVAL_VERSION,
            "allowed_commands": commands,
        }
        if not _valid_document(document):
            return ToolResult.failure(
                "invalid_approval_store",
                "Refusing to write invalid Shell approvals.",
            )
        target = self._target(create_directory=True)
        if isinstance(target, ToolResult) or target is None:
            return target or ToolResult.failure(
                "approval_store_error", "Could not create Shell approval store."
            )
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{APPROVAL_FILENAME}.",
            )
            temporary = Path(raw_path)
            with os.fdopen(
                descriptor, "w", encoding="utf-8", newline="\n"
            ) as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            self._replace(temporary, target)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return ToolResult.failure(
                "approval_store_error", "Could not write Shell approvals."
            )
        return ToolResult.success({
            "path": str(target),
            "count": len(commands),
        })

    def _target(
        self, create_directory: bool
    ) -> Path | ToolResult | None:
        data_directory = self.workspace / DATA_DIRECTORY
        try:
            if not data_directory.exists() and not data_directory.is_symlink():
                if not create_directory:
                    return None
                data_directory.mkdir()
            resolved_directory = data_directory.resolve()
            resolved_directory.relative_to(self.workspace)
            if not resolved_directory.is_dir():
                return ToolResult.failure(
                    "approval_store_error",
                    "Shell approval path is not a directory.",
                )
            target = resolved_directory / APPROVAL_FILENAME
            if target.is_symlink() or target.exists():
                resolved_target = target.resolve()
                resolved_target.relative_to(self.workspace)
                if not resolved_target.is_file():
                    return ToolResult.failure(
                        "approval_store_error",
                        "Shell approval path is not a file.",
                    )
                return resolved_target
            return target
        except ValueError:
            return ToolResult.failure(
                "path_outside_workspace",
                "Shell approvals are outside the workspace.",
            )
        except OSError:
            return ToolResult.failure(
                "approval_store_error", "Could not access Shell approvals."
            )


def _valid_command(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _valid_document(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"version", "allowed_commands"}
        and type(value["version"]) is int
        and value["version"] == APPROVAL_VERSION
        and isinstance(value["allowed_commands"], list)
        and all(_valid_command(item) for item in value["allowed_commands"])
    )
