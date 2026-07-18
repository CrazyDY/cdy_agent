from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from .base import ToolResult
from .filesystem import resolve_workspace


STORE_VERSION = 1
DATA_DIRECTORY = ".cdy-agent"


class PersonalStore:
    def __init__(
        self,
        workspace: Path,
        replace: Callable[
            [
                str | bytes | os.PathLike[str] | os.PathLike[bytes],
                str | bytes | os.PathLike[str] | os.PathLike[bytes],
            ],
            None,
        ] = os.replace,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self._replace = replace

    def load_notes(self) -> ToolResult:
        return self._load("notes.json", _validate_notes)

    def save_notes(self, items: list[dict[str, Any]]) -> ToolResult:
        return self._save("notes.json", items, _validate_notes)

    def load_todos(self) -> ToolResult:
        return self._load("todos.json", _validate_todos)

    def save_todos(self, items: list[dict[str, Any]]) -> ToolResult:
        return self._save("todos.json", items, _validate_todos)

    def _load(self, filename: str, validator: Callable[[object], bool]) -> ToolResult:
        target = self._target(filename, create_directory=False)
        if isinstance(target, ToolResult):
            return target
        if target is None:
            return ToolResult.success([])
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return ToolResult.failure("invalid_store", "Stored personal data is invalid.")
        if not validator(document):
            return ToolResult.failure("invalid_store", "Stored personal data is invalid.")
        return ToolResult.success([dict(item) for item in document["items"]])

    def _save(
        self,
        filename: str,
        items: list[dict[str, Any]],
        validator: Callable[[object], bool],
    ) -> ToolResult:
        document = {"version": STORE_VERSION, "items": items}
        if not validator(document):
            return ToolResult.failure(
                "invalid_store", "Refusing to write invalid personal data."
            )
        target = self._target(filename, create_directory=True)
        if isinstance(target, ToolResult) or target is None:
            return target or ToolResult.failure(
                "store_error", "Could not create data store."
            )
        if target.exists():
            try:
                existing_document = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return ToolResult.failure(
                    "invalid_store", "Stored personal data is invalid."
                )
            if not validator(existing_document):
                return ToolResult.failure(
                    "invalid_store", "Stored personal data is invalid."
                )
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                dir=target.parent, prefix=f".{filename}."
            )
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
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
            return ToolResult.failure("store_error", "Could not write personal data.")
        return ToolResult.success({"path": str(target), "count": len(items)})

    def _target(self, filename: str, create_directory: bool) -> Path | ToolResult | None:
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
                    "store_error", "Personal data path is not a directory."
                )
            target = resolved_directory / filename
            if target.is_symlink() or target.exists():
                resolved_target = target.resolve()
                resolved_target.relative_to(self.workspace)
                if not resolved_target.is_file():
                    return ToolResult.failure(
                        "store_error", "Personal data path is not a file."
                    )
                return resolved_target
            return target
        except ValueError:
            return ToolResult.failure(
                "path_outside_workspace", "Personal data is outside the workspace."
            )
        except OSError:
            return ToolResult.failure(
                "store_error", "Could not access personal data."
            )


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc


def _valid_document(
    document: object,
    item_validator: Callable[[object], bool],
) -> bool:
    if not isinstance(document, dict) or set(document) != {"version", "items"}:
        return False
    if (
        type(document["version"]) is not int
        or document["version"] != STORE_VERSION
        or not isinstance(document["items"], list)
    ):
        return False
    items = document["items"]
    if not all(item_validator(item) for item in items):
        return False
    identifiers = [item["id"] for item in items]
    return len(identifiers) == len(set(identifiers))


def _valid_note(item: object) -> bool:
    if not isinstance(item, dict) or set(item) != {
        "id",
        "title",
        "content",
        "created_at",
    }:
        return False
    title = item["title"]
    content = item["content"]
    trimmed_title = title.strip() if isinstance(title, str) else ""
    if not isinstance(content, str):
        return False
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return (
        _is_uuid(item["id"])
        and isinstance(title, str)
        and bool(trimmed_title)
        and len(trimmed_title) <= 200
        and content_size <= 64 * 1024
        and _is_utc_timestamp(item["created_at"])
    )


def _valid_todo(item: object) -> bool:
    if not isinstance(item, dict) or set(item) != {
        "id",
        "text",
        "completed",
        "created_at",
        "completed_at",
    }:
        return False
    text = item["text"]
    completed = item["completed"]
    completed_at = item["completed_at"]
    trimmed_text = text.strip() if isinstance(text, str) else ""
    completion_is_valid = (
        _is_utc_timestamp(completed_at) if completed else completed_at is None
    )
    return (
        _is_uuid(item["id"])
        and isinstance(text, str)
        and bool(trimmed_text)
        and len(trimmed_text) <= 1000
        and type(completed) is bool
        and _is_utc_timestamp(item["created_at"])
        and completion_is_valid
    )


def _validate_notes(document: object) -> bool:
    return _valid_document(document, _valid_note)


def _validate_todos(document: object) -> bool:
    return _valid_document(document, _valid_todo)
