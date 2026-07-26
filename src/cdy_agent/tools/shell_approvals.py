from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class _PathRecord:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "_PathRecord":
        return cls(
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def same_identity(self, other: "_PathRecord") -> bool:
        return (
            self.device == other.device
            and self.inode == other.inode
        )


@dataclass(frozen=True)
class _StorePathState:
    workspace: _PathRecord
    data_directory: _PathRecord
    target: _PathRecord | None


class ShellApprovalStore:
    def __init__(
        self,
        workspace: Path,
        replace: Replace = os.replace,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        workspace_stat = self.workspace.lstat()
        if not _is_safe_directory_stat(workspace_stat):
            raise ValueError(
                "Shell approval workspace is not a safe directory."
            )
        self._workspace_identity = _PathRecord.from_stat(workspace_stat)
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
                "invalid_arguments",
                "Approval argv must be non-empty strings.",
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
        state = self._capture_state(create_directory=False)
        if isinstance(state, ToolResult):
            return state
        if state is None or state.target is None:
            return ToolResult.success([])
        target = self._target
        try:
            descriptor = os.open(
                target,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "r", encoding="utf-8") as file:
                opened = _PathRecord.from_stat(os.fstat(file.fileno()))
                if not opened.same_identity(state.target):
                    return _store_changed_failure()
                content = file.read()
        except OSError:
            return ToolResult.failure(
                "approval_store_error",
                "Could not read Shell approvals.",
            )
        except UnicodeDecodeError:
            return _invalid_store_failure()
        after = self._capture_state(create_directory=False)
        if (
            not isinstance(after, _StorePathState)
            or not _same_store_state(state, after)
        ):
            return _store_changed_failure()
        try:
            document = json.loads(
                content,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (json.JSONDecodeError, ValueError):
            return _invalid_store_failure()
        if not _valid_document(document):
            return _invalid_store_failure()
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
        state = self._capture_state(create_directory=True)
        if not isinstance(state, _StorePathState):
            return state or ToolResult.failure(
                "approval_store_error",
                "Could not create Shell approval store.",
            )
        temporary: Path | None = None
        temporary_record: _PathRecord | None = None
        descriptor: int | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                dir=self._data_directory,
                prefix=f".{APPROVAL_FILENAME}.",
            )
            temporary = Path(raw_path)
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as file:
                descriptor = None
                json.dump(
                    document,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
                temporary_record = _PathRecord.from_stat(
                    os.fstat(file.fileno())
                )
            if not self._ready_to_replace(
                state,
                temporary,
                temporary_record,
            ):
                return _store_changed_failure()
            self._replace(temporary, self._target)
            after = self._capture_state(create_directory=False)
            if (
                not isinstance(after, _StorePathState)
                or after.target is None
                or not _same_parent_identity(state, after)
                or not after.target.same_identity(temporary_record)
            ):
                return _store_changed_failure()
        except OSError:
            return ToolResult.failure(
                "approval_store_error",
                "Could not write Shell approvals.",
            )
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None and temporary_record is not None:
                self._safe_unlink_temporary(
                    temporary,
                    temporary_record,
                    state,
                )
        return ToolResult.success({
            "path": str(self._target),
            "count": len(commands),
        })

    @property
    def _data_directory(self) -> Path:
        return self.workspace / DATA_DIRECTORY

    @property
    def _target(self) -> Path:
        return self._data_directory / APPROVAL_FILENAME

    def _capture_state(
        self,
        create_directory: bool,
    ) -> _StorePathState | ToolResult | None:
        workspace_record = self._safe_workspace_record()
        if isinstance(workspace_record, ToolResult):
            return workspace_record
        data_directory = self._data_directory
        try:
            data_stat = data_directory.lstat()
        except FileNotFoundError:
            if not create_directory:
                return None
            try:
                data_directory.mkdir()
            except FileExistsError:
                pass
            except OSError:
                return ToolResult.failure(
                    "approval_store_error",
                    "Could not create Shell approval directory.",
                )
            return self._capture_state(create_directory=False)
        except OSError:
            return ToolResult.failure(
                "approval_store_error",
                "Could not access Shell approval directory.",
            )
        if not _is_safe_directory_stat(data_stat):
            return self._unsafe_path_failure(data_directory)
        directory_record = _PathRecord.from_stat(data_stat)
        try:
            target_stat = self._target.lstat()
        except FileNotFoundError:
            target_record = None
        except OSError:
            return ToolResult.failure(
                "approval_store_error",
                "Could not access Shell approvals.",
            )
        else:
            if not _is_safe_regular_file_stat(target_stat):
                return self._unsafe_path_failure(self._target)
            target_record = _PathRecord.from_stat(target_stat)
        state = _StorePathState(
            workspace_record,
            directory_record,
            target_record,
        )
        return (
            state
            if self._state_is_current(state)
            else _store_changed_failure()
        )

    def _safe_workspace_record(self) -> _PathRecord | ToolResult:
        try:
            workspace_stat = self.workspace.lstat()
        except OSError:
            return _store_changed_failure()
        if not _is_safe_directory_stat(workspace_stat):
            return self._unsafe_path_failure(self.workspace)
        current = _PathRecord.from_stat(workspace_stat)
        if not current.same_identity(self._workspace_identity):
            return _store_changed_failure()
        return current

    def _state_is_current(self, state: _StorePathState) -> bool:
        try:
            workspace = _PathRecord.from_stat(self.workspace.lstat())
            data_directory = _PathRecord.from_stat(
                self._data_directory.lstat()
            )
            target = _optional_regular_file_record(self._target)
        except OSError:
            return False
        return (
            _same_record_version(state.workspace, workspace)
            and state.data_directory.same_identity(data_directory)
            and _same_optional_record_version(state.target, target)
        )

    def _ready_to_replace(
        self,
        state: _StorePathState,
        temporary: Path,
        temporary_record: _PathRecord | None,
    ) -> bool:
        if temporary_record is None:
            return False
        current = self._capture_state(create_directory=False)
        if (
            not isinstance(current, _StorePathState)
            or not _same_store_state(state, current)
        ):
            return False
        try:
            temp_stat = temporary.lstat()
        except OSError:
            return False
        return (
            _is_safe_regular_file_stat(temp_stat)
            and _PathRecord.from_stat(temp_stat).same_identity(
                temporary_record
            )
        )

    def _safe_unlink_temporary(
        self,
        temporary: Path,
        temporary_record: _PathRecord,
        original_state: _StorePathState,
    ) -> None:
        current = self._capture_state(create_directory=False)
        if (
            not isinstance(current, _StorePathState)
            or not _same_parent_identity(original_state, current)
        ):
            return
        try:
            temp_stat = temporary.lstat()
        except FileNotFoundError:
            return
        except OSError:
            return
        if (
            _is_safe_regular_file_stat(temp_stat)
            and _PathRecord.from_stat(temp_stat).same_identity(
                temporary_record
            )
        ):
            try:
                temporary.unlink()
            except OSError:
                pass

    def _unsafe_path_failure(self, path: Path) -> ToolResult:
        try:
            path.resolve(strict=False).relative_to(self.workspace)
        except (OSError, ValueError):
            return ToolResult.failure(
                "path_outside_workspace",
                "Shell approvals are outside the workspace.",
            )
        return ToolResult.failure(
            "approval_store_error",
            "Shell approval path must not be a link or reparse point.",
        )


def _is_link_or_reparse(value: os.stat_result) -> bool:
    reparse_flag = getattr(
        stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        0,
    )
    attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISLNK(value.st_mode) or bool(
        attributes & reparse_flag
    )


def _is_safe_directory_stat(value: os.stat_result) -> bool:
    return stat.S_ISDIR(value.st_mode) and not _is_link_or_reparse(value)


def _is_safe_regular_file_stat(value: os.stat_result) -> bool:
    return stat.S_ISREG(value.st_mode) and not _is_link_or_reparse(value)


def _optional_regular_file_record(path: Path) -> _PathRecord | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if not _is_safe_regular_file_stat(value):
        raise OSError("Unsafe Shell approval target.")
    return _PathRecord.from_stat(value)


def _same_record_version(
    first: _PathRecord,
    second: _PathRecord,
) -> bool:
    return first == second


def _same_optional_record_version(
    first: _PathRecord | None,
    second: _PathRecord | None,
) -> bool:
    return first == second


def _same_parent_identity(
    first: _StorePathState,
    second: _StorePathState,
) -> bool:
    return (
        first.workspace.same_identity(second.workspace)
        and first.data_directory.same_identity(second.data_directory)
    )


def _same_store_state(
    first: _StorePathState,
    second: _StorePathState,
) -> bool:
    return (
        _same_parent_identity(first, second)
        and _same_optional_record_version(first.target, second.target)
    )


def _store_changed_failure() -> ToolResult:
    return ToolResult.failure(
        "approval_store_error",
        "Shell approval path changed during access.",
    )


def _invalid_store_failure() -> ToolResult:
    return ToolResult.failure(
        "invalid_approval_store",
        "Stored Shell approvals are invalid.",
    )


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Duplicate JSON key.")
        document[key] = value
    return document


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
