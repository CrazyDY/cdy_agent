from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .base import ToolResult
from .filesystem import resolve_workspace
from .shell_approvals import ShellApprovalStore

DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30
GIT_GLOBAL_OPTIONS_WITH_VALUES = frozenset({
    "-C",
    "-c",
    "--config-env",
    "--git-dir",
    "--namespace",
    "--work-tree",
})
GIT_HARDENING = (
    "--no-pager",
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
)
GIT_DIFF_SAFETY = ("--no-ext-diff", "--no-textconv")
SAFE_COMMANDS = frozenset({
    "pwd", "ls", "rg", "grep", "head", "tail", "wc", "sort", "uniq",
})
BUILTIN_COMMANDS = SAFE_COMMANDS | frozenset({"git"})
PWD_OPTIONS = frozenset({"-L", "-P", "--logical", "--physical"})
LS_SHORT_OPTIONS = frozenset("aAlhRdF1rtS")
LS_LONG_OPTIONS = frozenset({
    "--all", "--almost-all", "--human-readable", "--recursive",
    "--directory", "--classify", "--group-directories-first",
})
RG_FLAGS = frozenset({
    "-n", "--line-number", "-i", "--ignore-case", "-S", "--smart-case",
    "-F", "--fixed-strings", "-l", "--files-with-matches", "--files",
    "--hidden", "--no-ignore",
})
RG_VALUE_OPTIONS = frozenset({
    "-g", "--glob", "-t", "--type", "-T", "--type-not",
    "-A", "--after-context", "-B", "--before-context",
    "-C", "--context", "-e", "--regexp",
})
GREP_FLAGS = frozenset({
    "-n", "--line-number", "-i", "--ignore-case", "-F", "--fixed-strings",
    "-E", "--extended-regexp", "-l", "--files-with-matches",
    "-v", "--invert-match", "-s", "--no-messages",
})
GREP_VALUE_OPTIONS = frozenset({
    "-e", "--regexp", "-A", "--after-context", "-B", "--before-context",
    "-C", "--context",
})
HEAD_TAIL_FLAGS = frozenset({"-q", "--quiet", "--silent", "-v", "--verbose"})
HEAD_TAIL_VALUE_OPTIONS = frozenset({
    "-n", "--lines", "-c", "--bytes",
})
WC_FLAGS = frozenset({
    "-c", "--bytes", "-m", "--chars", "-l", "--lines",
    "-w", "--words", "-L", "--max-line-length",
})
SORT_FLAGS = frozenset({
    "-b", "--ignore-leading-blanks", "-f", "--ignore-case",
    "-n", "--numeric-sort", "-r", "--reverse", "-s", "--stable",
    "-u", "--unique",
})
SORT_VALUE_OPTIONS = frozenset({
    "-k", "--key", "-t", "--field-separator",
})
UNIQ_FLAGS = frozenset({
    "-c", "--count", "-d", "--repeated", "-D", "--all-repeated",
    "-i", "--ignore-case", "-u", "--unique",
})
UNIQ_VALUE_OPTIONS = frozenset({
    "-f", "--skip-fields", "-s", "--skip-chars", "-w", "--check-chars",
})
GIT_STATUS_FLAGS = frozenset({
    "-s", "--short", "-b", "--branch", "--porcelain",
    "--long", "-z", "--null", "-u", "--untracked-files",
    "--ignored", "--no-renames",
})
GIT_DIFF_FLAGS = frozenset({
    "--stat", "--numstat", "--shortstat", "--name-only", "--name-status",
    "--check", "--summary", "--patch", "-p",
    "-w", "--ignore-all-space", "--no-renames", "--cached", "--staged",
    "-U", "--unified",
})
GIT_DIFF_ATTACHED_NUMERIC_OPTIONS = frozenset({"-U", "--unified"})


class ShellExecutionDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REJECT = "reject"


@dataclass(frozen=True)
class PreparedShellCommand:
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class ShellPolicyResult:
    decision: ShellExecutionDecision
    command: PreparedShellCommand | None = None
    failure: ToolResult | None = None


@dataclass(frozen=True)
class ParsedOptions:
    positionals: tuple[str, ...]
    seen: frozenset[str]


class ShellExecutionPolicy:
    def __init__(
        self,
        workspace: Path,
        approvals: ShellApprovalStore,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self.approvals = approvals

    def prepare(
        self, arguments: dict[str, Any]
    ) -> PreparedShellCommand | ToolResult:
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return validated
        user_argv, timeout = validated
        prepared, _ = _prepare_command(
            user_argv,
            timeout,
        )
        return prepared

    def classify(self, arguments: dict[str, Any]) -> ShellPolicyResult:
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return ShellPolicyResult(
                ShellExecutionDecision.REJECT,
                failure=validated,
            )
        user_argv, timeout = validated
        prepared, executable = _prepare_command(
            user_argv,
            timeout,
        )
        if user_argv[0] in BUILTIN_COMMANDS and executable is None:
            return ShellPolicyResult(
                ShellExecutionDecision.REQUIRE_CONFIRMATION,
                command=prepared,
            )
        if (
            executable is not None
            and not _path_within_workspace(executable, self.workspace)
            and _is_safe_read_only(user_argv, self.workspace)
        ):
            return ShellPolicyResult(
                ShellExecutionDecision.AUTO_APPROVE,
                command=prepared,
            )
        allowed = self.approvals.contains(prepared.argv)
        if allowed.ok and allowed.data is True:
            return ShellPolicyResult(
                ShellExecutionDecision.AUTO_APPROVE,
                command=prepared,
            )
        return ShellPolicyResult(
            ShellExecutionDecision.REQUIRE_CONFIRMATION,
            command=prepared,
        )

    def remember(self, arguments: dict[str, Any]) -> ToolResult:
        prepared = self.prepare(arguments)
        if isinstance(prepared, ToolResult):
            return prepared
        return self.approvals.add(prepared.argv)


def _parse_options(
    arguments: list[str],
    flags: frozenset[str],
    value_options: frozenset[str] = frozenset(),
    combined_short_flags: frozenset[str] = frozenset(),
    attached_numeric_options: frozenset[str] = frozenset(),
) -> ParsedOptions | None:
    positionals: list[str] = []
    seen: set[str] = set()
    options_finished = False
    dash_positionals_allowed = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if options_finished:
            if (
                not dash_positionals_allowed
                and argument != "-"
                and argument.startswith("-")
            ):
                return None
            positionals.append(argument)
            index += 1
            continue
        if argument == "-" or not argument.startswith("-"):
            positionals.append(argument)
            options_finished = True
            index += 1
            continue
        if argument == "--":
            options_finished = True
            dash_positionals_allowed = True
            index += 1
            continue
        if argument in flags:
            seen.add(argument)
            index += 1
            continue
        if argument in value_options:
            if index + 1 >= len(arguments):
                return None
            seen.add(argument)
            index += 2
            continue
        matched_value = next(
            (
                option
                for option in value_options
                if option.startswith("--")
                and argument.startswith(f"{option}=")
            ),
            None,
        )
        if matched_value is not None:
            seen.add(matched_value)
            index += 1
            continue
        matched_numeric = _match_attached_numeric_option(
            argument,
            attached_numeric_options,
        )
        if matched_numeric is not None:
            seen.add(matched_numeric)
            index += 1
            continue
        if (
            argument.startswith("-")
            and not argument.startswith("--")
            and len(argument) > 2
            and all(
                character in combined_short_flags
                for character in argument[1:]
            )
        ):
            seen.update(f"-{character}" for character in argument[1:])
            index += 1
            continue
        return None
    return ParsedOptions(tuple(positionals), frozenset(seen))


def _match_attached_numeric_option(
    argument: str,
    options: frozenset[str],
) -> str | None:
    for option in options:
        prefix = f"{option}=" if option.startswith("--") else option
        if not argument.startswith(prefix):
            continue
        value = argument[len(prefix):]
        if value and value.isascii() and value.isdecimal():
            return option
    return None


def _has_option(
    arguments: list[str], names: frozenset[str]
) -> bool:
    return any(
        argument in names
        or any(
            name.startswith("--") and argument.startswith(f"{name}=")
            for name in names
        )
        for argument in arguments
    )


def _paths_within_workspace(paths: list[str], workspace: Path) -> bool:
    for raw_path in paths:
        if raw_path == "-":
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        try:
            candidate.resolve().relative_to(workspace)
        except (OSError, ValueError):
            return False
    return True


def _path_within_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def _safe_pwd(arguments: list[str]) -> bool:
    parsed = _parse_options(arguments, PWD_OPTIONS)
    return parsed is not None and not parsed.positionals


def _safe_ls(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        LS_LONG_OPTIONS | frozenset(f"-{item}" for item in LS_SHORT_OPTIONS),
        combined_short_flags=LS_SHORT_OPTIONS,
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_rg(arguments: list[str], workspace: Path) -> bool:
    if _has_option(arguments, frozenset({"--pre"})):
        return False
    parsed = _parse_options(
        arguments,
        RG_FLAGS,
        RG_VALUE_OPTIONS,
        combined_short_flags=frozenset("niSFl"),
    )
    if parsed is None:
        return False
    pattern_is_option = bool(
        parsed.seen & frozenset({"-e", "--regexp"})
    )
    paths = list(parsed.positionals)
    if "--files" not in parsed.seen and not pattern_is_option:
        if not paths:
            return False
        paths = paths[1:]
    return _paths_within_workspace(paths, workspace)


def _safe_grep(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        GREP_FLAGS,
        GREP_VALUE_OPTIONS,
        combined_short_flags=frozenset("niFElvs"),
    )
    if parsed is None:
        return False
    pattern_is_option = bool(
        parsed.seen & frozenset({"-e", "--regexp"})
    )
    paths = list(parsed.positionals)
    if not pattern_is_option:
        if not paths:
            return False
        paths = paths[1:]
    return _paths_within_workspace(paths, workspace)


def _safe_head_or_tail(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        HEAD_TAIL_FLAGS,
        HEAD_TAIL_VALUE_OPTIONS,
        combined_short_flags=frozenset("qv"),
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_wc(arguments: list[str], workspace: Path) -> bool:
    if _has_option(arguments, frozenset({"--files0-from"})):
        return False
    parsed = _parse_options(
        arguments,
        WC_FLAGS,
        combined_short_flags=frozenset("cmlwL"),
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_sort(arguments: list[str], workspace: Path) -> bool:
    dangerous = frozenset({
        "-o", "--output", "--compress-program",
        "-T", "--temporary-directory",
    })
    if _has_option(arguments, dangerous):
        return False
    parsed = _parse_options(
        arguments,
        SORT_FLAGS,
        SORT_VALUE_OPTIONS,
        combined_short_flags=frozenset("bfnrsu"),
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_uniq(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        UNIQ_FLAGS,
        UNIQ_VALUE_OPTIONS,
        combined_short_flags=frozenset("cdDiu"),
    )
    if parsed is None or len(parsed.positionals) > 1:
        return False
    return _paths_within_workspace(list(parsed.positionals), workspace)


def _safe_git(argv: list[str], workspace: Path) -> bool:
    if len(argv) < 2 or argv[1] not in {"status", "diff"}:
        return False
    if not _git_metadata_within_workspace(workspace):
        return False
    arguments = argv[2:]
    if argv[1] == "status":
        parsed = _parse_options(arguments, GIT_STATUS_FLAGS)
    else:
        dangerous = frozenset({
            "--output", "--ext-diff", "--textconv",
        })
        if _has_option(arguments, dangerous):
            return False
        parsed = _parse_options(
            arguments,
            GIT_DIFF_FLAGS,
            attached_numeric_options=GIT_DIFF_ATTACHED_NUMERIC_OPTIONS,
        )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _is_safe_read_only(argv: list[str], workspace: Path) -> bool:
    command = argv[0]
    if command == "pwd":
        return _safe_pwd(argv[1:])
    if command == "ls":
        return _safe_ls(argv[1:], workspace)
    if command == "rg":
        return _safe_rg(argv[1:], workspace)
    if command == "grep":
        return _safe_grep(argv[1:], workspace)
    if command in {"head", "tail"}:
        return _safe_head_or_tail(argv[1:], workspace)
    if command == "wc":
        return _safe_wc(argv[1:], workspace)
    if command == "sort":
        return _safe_sort(argv[1:], workspace)
    if command == "uniq":
        return _safe_uniq(argv[1:], workspace)
    if command == "git":
        return _safe_git(argv, workspace)
    return False


def _git_metadata_within_workspace(workspace: Path) -> bool:
    for directory in (workspace, *workspace.parents):
        marker = directory / ".git"
        try:
            marker_exists = marker.exists() or marker.is_symlink()
        except OSError:
            return False
        if not marker_exists:
            continue
        if directory != workspace:
            return False
        return _git_marker_within_workspace(marker, workspace)
    return True


def _git_marker_within_workspace(marker: Path, workspace: Path) -> bool:
    try:
        resolved_marker = marker.resolve()
        if not _path_within_workspace(resolved_marker, workspace):
            return False
        if resolved_marker.is_dir():
            return _git_commondir_within_workspace(
                resolved_marker,
                workspace,
            )
        if not resolved_marker.is_file():
            return False
        gitdir_value = _read_git_metadata_pointer(
            resolved_marker,
            "gitdir: ",
        )
        if gitdir_value is None:
            return False
        gitdir = Path(gitdir_value)
        if not gitdir.is_absolute():
            gitdir = resolved_marker.parent / gitdir
        resolved_gitdir = gitdir.resolve()
    except OSError:
        return False
    return (
        _path_within_workspace(resolved_gitdir, workspace)
        and _git_commondir_within_workspace(resolved_gitdir, workspace)
    )


def _git_commondir_within_workspace(
    gitdir: Path,
    workspace: Path,
) -> bool:
    marker = gitdir / "commondir"
    try:
        if not marker.exists() and not marker.is_symlink():
            return True
        resolved_marker = marker.resolve()
        if not _path_within_workspace(resolved_marker, workspace):
            return False
        value = _read_git_metadata_pointer(resolved_marker, "")
        if value is None:
            return False
        commondir = Path(value)
        if not commondir.is_absolute():
            commondir = gitdir / commondir
        resolved_commondir = commondir.resolve()
    except OSError:
        return False
    return _path_within_workspace(resolved_commondir, workspace)


def _read_git_metadata_pointer(
    path: Path,
    prefix: str,
) -> str | None:
    try:
        if path.stat().st_size > 4096:
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) != 1 or not lines[0].startswith(prefix):
        return None
    value = lines[0][len(prefix):].strip()
    return value or None


def _validate_arguments(
    arguments: dict[str, Any],
) -> tuple[list[str], int] | ToolResult:
    if set(arguments) not in ({"argv"}, {"argv", "timeout_seconds"}):
        return ToolResult.failure(
            "invalid_arguments",
            "argv is required; timeout_seconds is optional.",
        )
    argv = arguments["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(
            not isinstance(element, str) or "\0" in element
            for element in argv
        )
    ):
        return ToolResult.failure(
            "invalid_arguments",
            "argv must be a non-empty list of strings without NUL characters.",
        )
    timeout = arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
    ):
        return ToolResult.failure(
            "invalid_arguments",
            f"timeout_seconds must be an integer from 1 to "
            f"{MAX_TIMEOUT_SECONDS}.",
        )
    return argv, timeout


def _prepare_command(
    argv: list[str],
    timeout: int,
) -> tuple[PreparedShellCommand, Path | None]:
    executable = _resolve_builtin_executable(argv[0])
    effective_argv = _effective_argv(argv)
    if executable is not None:
        effective_argv[0] = str(executable)
    return (
        PreparedShellCommand(tuple(effective_argv), timeout),
        executable,
    )


def _resolve_builtin_executable(command: str) -> Path | None:
    if command not in BUILTIN_COMMANDS:
        return None
    found = shutil.which(command)
    if found is None:
        return None
    try:
        executable = Path(found).resolve(strict=True)
    except OSError:
        return None
    if not executable.is_file():
        return None
    return executable


def _effective_argv(argv: list[str]) -> list[str]:
    if argv[0] == "rg":
        return ["rg", "--no-config", *argv[1:]]
    if argv[0] != "git":
        return list(argv)
    return _effective_git_argv(argv)


def _effective_git_argv(argv: list[str]) -> list[str]:
    command_index = _git_command_index(argv)
    hardening_index = _git_hardening_index(argv, command_index)
    prefix = [
        *argv[:hardening_index],
        *GIT_HARDENING,
        *argv[hardening_index:command_index],
    ]
    if command_index is None:
        return prefix
    command = argv[command_index]
    command_arguments = argv[command_index + 1:]
    if command != "diff":
        return [*prefix, command, *command_arguments]
    return [
        *prefix,
        command,
        *_effective_git_diff_arguments(command_arguments),
    ]


def _git_command_index(argv: list[str]) -> int | None:
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            return index + 1 if index + 1 < len(argv) else None
        if argument in GIT_GLOBAL_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return index
    return None


def _git_hardening_index(
    argv: list[str],
    command_index: int | None,
) -> int:
    if command_index is not None:
        if argv[command_index - 1] == "--":
            return command_index - 1
        return command_index
    if len(argv) > 1 and argv[-1] in {*GIT_GLOBAL_OPTIONS_WITH_VALUES, "--"}:
        return len(argv) - 1
    return len(argv)


def _effective_git_diff_arguments(arguments: list[str]) -> list[str]:
    try:
        separator = arguments.index("--")
    except ValueError:
        options = arguments
        operands: list[str] = []
    else:
        options = arguments[:separator]
        operands = arguments[separator:]
    user_options = [
        argument
        for argument in options
        if argument not in GIT_DIFF_SAFETY
    ]
    return [
        *user_options,
        *GIT_DIFF_SAFETY,
        *operands,
    ]
