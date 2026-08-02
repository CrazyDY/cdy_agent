from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .base import ToolResult
from .filesystem import resolve_workspace
from .process import sanitized_environment
from .shell_approvals import ShellApprovalStore

DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30
GIT_GLOBAL_OPTIONS_WITH_VALUES = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--git-dir",
        "--namespace",
        "--work-tree",
    }
)
GIT_HARDENING = (
    "--no-pager",
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
)
GIT_DIFF_SAFETY = ("--no-ext-diff", "--no-textconv")
BUILTIN_COMMANDS = frozenset(
    {
        "pwd",
        "ls",
        "rg",
        "grep",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "git",
    }
)
PWD_OPTIONS = frozenset({"-L", "-P", "--logical", "--physical"})
LS_SHORT_OPTIONS = frozenset("aAlhRdF1rtS")
LS_LONG_OPTIONS = frozenset(
    {
        "--all",
        "--almost-all",
        "--human-readable",
        "--recursive",
        "--directory",
        "--classify",
        "--group-directories-first",
    }
)
RG_FLAGS = frozenset(
    {
        "-n",
        "--line-number",
        "-i",
        "--ignore-case",
        "-S",
        "--smart-case",
        "-F",
        "--fixed-strings",
        "-l",
        "--files-with-matches",
        "--files",
        "--hidden",
        "--no-ignore",
    }
)
RG_VALUE_OPTIONS = frozenset(
    {
        "-g",
        "--glob",
        "-t",
        "--type",
        "-T",
        "--type-not",
        "-A",
        "--after-context",
        "-B",
        "--before-context",
        "-C",
        "--context",
        "-e",
        "--regexp",
    }
)
GREP_FLAGS = frozenset(
    {
        "-n",
        "--line-number",
        "-i",
        "--ignore-case",
        "-F",
        "--fixed-strings",
        "-E",
        "--extended-regexp",
        "-l",
        "--files-with-matches",
        "-v",
        "--invert-match",
        "-s",
        "--no-messages",
    }
)
GREP_VALUE_OPTIONS = frozenset(
    {
        "-e",
        "--regexp",
        "-A",
        "--after-context",
        "-B",
        "--before-context",
        "-C",
        "--context",
    }
)
HEAD_TAIL_FLAGS = frozenset({"-q", "--quiet", "--silent", "-v", "--verbose"})
HEAD_TAIL_VALUE_OPTIONS = frozenset(
    {
        "-n",
        "--lines",
        "-c",
        "--bytes",
    }
)
WC_FLAGS = frozenset(
    {
        "-c",
        "--bytes",
        "-m",
        "--chars",
        "-l",
        "--lines",
        "-w",
        "--words",
        "-L",
        "--max-line-length",
    }
)
SORT_FLAGS = frozenset(
    {
        "-b",
        "--ignore-leading-blanks",
        "-f",
        "--ignore-case",
        "-n",
        "--numeric-sort",
        "-r",
        "--reverse",
        "-s",
        "--stable",
        "-u",
        "--unique",
    }
)
SORT_VALUE_OPTIONS = frozenset(
    {
        "-k",
        "--key",
        "-t",
        "--field-separator",
    }
)
UNIQ_FLAGS = frozenset(
    {
        "-c",
        "--count",
        "-d",
        "--repeated",
        "-D",
        "--all-repeated",
        "-i",
        "--ignore-case",
        "-u",
        "--unique",
    }
)
UNIQ_VALUE_OPTIONS = frozenset(
    {
        "-f",
        "--skip-fields",
        "-s",
        "--skip-chars",
        "-w",
        "--check-chars",
    }
)
GIT_STATUS_FLAGS = frozenset(
    {
        "-s",
        "--short",
        "-b",
        "--branch",
        "--porcelain",
        "--long",
        "-z",
        "--null",
        "-u",
        "--untracked-files",
        "--ignored",
        "--no-renames",
    }
)
GIT_DIFF_FLAGS = frozenset(
    {
        "--stat",
        "--numstat",
        "--shortstat",
        "--name-only",
        "--name-status",
        "--check",
        "--summary",
        "--patch",
        "-p",
        "-w",
        "--ignore-all-space",
        "--no-renames",
        "--cached",
        "--staged",
        "-U",
        "--unified",
    }
)
GIT_DIFF_ATTACHED_NUMERIC_OPTIONS = frozenset({"-U", "--unified"})
MACH_O_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }
)

ExecutableResolver = Callable[
    [str, Path, Mapping[str, str]],
    Path | None,
]
GitRepositoryProbe = Callable[
    [Path, Path, dict[str, str]],
    tuple[Path, Path, Path] | None,
]


class ShellExecutionDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REJECT = "reject"


@dataclass(frozen=True)
class PreparedShellCommand:
    argv: tuple[str, ...]
    timeout_seconds: int
    user_argv: tuple[str, ...] = field(repr=False)
    environment: tuple[tuple[str, str], ...] = field(
        repr=False,
        compare=False,
    )
    executable: Path | None = field(repr=False)
    trusted_system_executable: bool = field(repr=False)


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
        *,
        trusted_executable_roots: Sequence[Path] | None = None,
        executable_resolver: ExecutableResolver | None = None,
        git_repository_probe: GitRepositoryProbe | None = None,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        if approvals.workspace != self.workspace:
            raise ValueError(
                "Shell approval store workspace does not match policy workspace."
            )
        self.approvals = approvals
        if trusted_executable_roots is None:
            self._trusted_executable_roots = _system_executable_roots()
            self._require_system_ownership = True
        else:
            self._trusted_executable_roots = tuple(
                _resolve_trusted_root(path) for path in trusted_executable_roots
            )
            self._require_system_ownership = False
        self._executable_resolver = executable_resolver or _resolve_executable
        self._git_repository_probe = git_repository_probe or _probe_git_repository

    def prepare(
        self,
        arguments: dict[str, Any],
        environment: Mapping[str, str] | None = None,
    ) -> PreparedShellCommand | ToolResult:
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return validated
        user_argv, timeout = validated
        execution_environment = sanitized_environment(
            environment,
            scrub_git=user_argv[0] == "git",
        )
        return _prepare_command(
            user_argv,
            timeout,
            self.workspace,
            execution_environment,
            self._executable_resolver,
            self._trusted_executable_roots,
            self._require_system_ownership,
        )

    def classify(
        self,
        arguments: dict[str, Any],
        environment: Mapping[str, str] | None = None,
    ) -> ShellPolicyResult:
        prepared = self.prepare(arguments, environment)
        if isinstance(prepared, ToolResult):
            return ShellPolicyResult(
                ShellExecutionDecision.REJECT,
                failure=prepared,
            )
        return self.classify_prepared(prepared)

    def classify_prepared(
        self,
        prepared: PreparedShellCommand,
    ) -> ShellPolicyResult:
        if prepared.user_argv[0] in BUILTIN_COMMANDS and prepared.executable is None:
            return ShellPolicyResult(
                ShellExecutionDecision.REQUIRE_CONFIRMATION,
                command=prepared,
            )
        if prepared.trusted_system_executable and _is_safe_read_only(
            list(prepared.user_argv),
            self.workspace,
            prepared.executable,
            dict(prepared.environment),
            self._git_repository_probe,
        ):
            return ShellPolicyResult(
                ShellExecutionDecision.AUTO_APPROVE,
                command=prepared,
            )
        if prepared.executable is not None:
            allowed = self.approvals.contains(prepared.argv)
        else:
            allowed = ToolResult.success(False)
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
        return self.remember_prepared(prepared)

    def remember_prepared(
        self,
        prepared: PreparedShellCommand,
    ) -> ToolResult:
        if prepared.executable is None:
            return _unresolved_executable_failure(prepared)
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
                if option.startswith("--") and argument.startswith(f"{option}=")
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
            and all(character in combined_short_flags for character in argument[1:])
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
        value = argument[len(prefix) :]
        if value and value.isascii() and value.isdecimal():
            return option
    return None


def _has_option(arguments: list[str], names: frozenset[str]) -> bool:
    return any(
        argument in names
        or any(
            name.startswith("--") and argument.startswith(f"{name}=") for name in names
        )
        for argument in arguments
    )


def _paths_within_workspace(paths: list[str], workspace: Path) -> bool:
    for raw_path in paths:
        if raw_path == "-":
            return False
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        try:
            relative = candidate.resolve().relative_to(workspace)
        except (OSError, ValueError):
            return False
        if relative.parts and relative.parts[0] == ".cdy-agent":
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
    if _has_option(arguments, frozenset({"-L", "--dereference"})):
        return False
    parsed = _parse_options(
        arguments,
        LS_LONG_OPTIONS | frozenset(f"-{item}" for item in LS_SHORT_OPTIONS),
        combined_short_flags=LS_SHORT_OPTIONS,
    )
    if parsed is None:
        return False
    paths = list(parsed.positionals)
    if not _paths_within_workspace(paths, workspace):
        return False
    reveals_hidden = bool(
        parsed.seen & frozenset({"-a", "-A", "--all", "--almost-all"})
    )
    recursive = bool(parsed.seen & frozenset({"-R", "--recursive"}))
    return not (
        (reveals_hidden or recursive)
        and _paths_can_reach_machine_state(paths or ["."], workspace)
    )


def _safe_rg(arguments: list[str], workspace: Path) -> bool:
    if _has_option(arguments, frozenset({"--pre", "--hidden"})):
        return False
    parsed = _parse_options(
        arguments,
        RG_FLAGS,
        RG_VALUE_OPTIONS,
        combined_short_flags=frozenset("niSFl"),
    )
    if parsed is None:
        return False
    pattern_is_option = bool(parsed.seen & frozenset({"-e", "--regexp"}))
    paths = list(parsed.positionals)
    if "--files" not in parsed.seen and not pattern_is_option:
        if not paths:
            return False
        paths = paths[1:]
    return bool(paths) and _paths_within_workspace(paths, workspace)


def _safe_grep(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        GREP_FLAGS,
        GREP_VALUE_OPTIONS,
        combined_short_flags=frozenset("niFElvs"),
    )
    if parsed is None:
        return False
    pattern_is_option = bool(parsed.seen & frozenset({"-e", "--regexp"}))
    paths = list(parsed.positionals)
    if not pattern_is_option:
        if not paths:
            return False
        paths = paths[1:]
    return bool(paths) and _paths_within_workspace(paths, workspace)


def _safe_head_or_tail(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        HEAD_TAIL_FLAGS,
        HEAD_TAIL_VALUE_OPTIONS,
        combined_short_flags=frozenset("qv"),
    )
    return (
        parsed is not None
        and bool(parsed.positionals)
        and _paths_within_workspace(list(parsed.positionals), workspace)
    )


def _safe_wc(arguments: list[str], workspace: Path) -> bool:
    if _has_option(arguments, frozenset({"--files0-from"})):
        return False
    parsed = _parse_options(
        arguments,
        WC_FLAGS,
        combined_short_flags=frozenset("cmlwL"),
    )
    return (
        parsed is not None
        and bool(parsed.positionals)
        and _paths_within_workspace(list(parsed.positionals), workspace)
    )


def _safe_sort(arguments: list[str], workspace: Path) -> bool:
    dangerous = frozenset(
        {
            "-o",
            "--output",
            "--compress-program",
            "-T",
            "--temporary-directory",
        }
    )
    if _has_option(arguments, dangerous):
        return False
    parsed = _parse_options(
        arguments,
        SORT_FLAGS,
        SORT_VALUE_OPTIONS,
        combined_short_flags=frozenset("bfnrsu"),
    )
    return (
        parsed is not None
        and bool(parsed.positionals)
        and _paths_within_workspace(list(parsed.positionals), workspace)
    )


def _safe_uniq(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        UNIQ_FLAGS,
        UNIQ_VALUE_OPTIONS,
        combined_short_flags=frozenset("cdDiu"),
    )
    if parsed is None or len(parsed.positionals) != 1:
        return False
    return _paths_within_workspace(list(parsed.positionals), workspace)


def _safe_git(
    argv: list[str],
    workspace: Path,
    executable: Path,
    environment: dict[str, str],
    repository_probe: GitRepositoryProbe,
) -> bool:
    if len(argv) < 2 or argv[1] not in {"status", "diff"}:
        return False
    machine_state = workspace / ".cdy-agent"
    try:
        if machine_state.exists() or machine_state.is_symlink():
            return False
    except OSError:
        return False
    if not _git_metadata_within_workspace(workspace):
        return False
    arguments = argv[2:]
    if argv[1] == "status":
        parsed = _parse_options(arguments, GIT_STATUS_FLAGS)
    else:
        dangerous = frozenset(
            {
                "--output",
                "--ext-diff",
                "--textconv",
            }
        )
        if _has_option(arguments, dangerous):
            return False
        parsed = _parse_options(
            arguments,
            GIT_DIFF_FLAGS,
            attached_numeric_options=GIT_DIFF_ATTACHED_NUMERIC_OPTIONS,
        )
    return (
        parsed is not None
        and _paths_within_workspace(list(parsed.positionals), workspace)
        and _git_repository_paths_within_workspace(
            repository_probe(executable, workspace, environment),
            workspace,
        )
    )


def _is_safe_read_only(
    argv: list[str],
    workspace: Path,
    executable: Path | None,
    environment: dict[str, str],
    repository_probe: GitRepositoryProbe,
) -> bool:
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
        if executable is None:
            return False
        return _safe_git(
            argv,
            workspace,
            executable,
            environment,
            repository_probe,
        )
    return False


def _paths_can_reach_machine_state(
    paths: list[str],
    workspace: Path,
) -> bool:
    machine_state = workspace / ".cdy-agent"
    for raw_path in paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        try:
            resolved = candidate.resolve()
            machine_state.relative_to(resolved)
        except (OSError, ValueError):
            continue
        return True
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
    return _path_within_workspace(
        resolved_gitdir, workspace
    ) and _git_commondir_within_workspace(resolved_gitdir, workspace)


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
    value = lines[0][len(prefix) :].strip()
    return value or None


def _git_repository_paths_within_workspace(
    paths: tuple[Path, Path, Path] | None,
    workspace: Path,
) -> bool:
    if paths is None:
        return False
    for path in paths:
        candidate = path if path.is_absolute() else workspace / path
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(workspace)
        except (OSError, ValueError):
            return False
        if not resolved.is_dir():
            return False
    return True


def _probe_git_repository(
    executable: Path,
    workspace: Path,
    environment: dict[str, str],
) -> tuple[Path, Path, Path] | None:
    argv = [
        str(executable),
        *GIT_HARDENING,
        "rev-parse",
        "--path-format=absolute",
        "--git-dir",
        "--git-common-dir",
        "--show-toplevel",
    ]
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            shell=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=environment,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or len(completed.stdout) > 12288:
        return None
    lines = completed.stdout.splitlines()
    if len(lines) != 3 or any(not line for line in lines):
        return None
    return Path(lines[0]), Path(lines[1]), Path(lines[2])


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
        or any(not isinstance(element, str) or "\0" in element for element in argv)
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
            f"timeout_seconds must be an integer from 1 to {MAX_TIMEOUT_SECONDS}.",
        )
    return argv, timeout


def _prepare_command(
    argv: list[str],
    timeout: int,
    workspace: Path,
    environment: Mapping[str, str],
    executable_resolver: ExecutableResolver,
    trusted_executable_roots: tuple[Path, ...],
    require_system_ownership: bool,
) -> PreparedShellCommand:
    executable = executable_resolver(
        argv[0],
        workspace,
        environment,
    )
    effective_argv = _effective_argv(argv)
    if executable is not None:
        effective_argv[0] = str(executable)
    trusted = (
        argv[0] in BUILTIN_COMMANDS
        and executable is not None
        and _is_trusted_system_executable(
            executable,
            trusted_executable_roots,
            require_system_ownership,
        )
    )
    return PreparedShellCommand(
        tuple(effective_argv),
        timeout,
        tuple(argv),
        tuple(environment.items()),
        executable,
        trusted,
    )


def _resolve_executable(
    command: str,
    workspace: Path,
    environment: Mapping[str, str],
) -> Path | None:
    if _has_path_component(command):
        candidate = Path(command)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        return _resolve_executable_candidate(candidate)
    for directory in _execution_search_directories(
        workspace,
        environment,
    ):
        for name in _candidate_executable_names(command, environment):
            resolved = _resolve_executable_candidate(directory / name)
            if resolved is not None:
                return resolved
    return None


def _resolve_executable_candidate(candidate: Path) -> Path | None:
    try:
        executable = candidate.resolve(strict=True)
    except OSError:
        return None
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return None
    return executable


def _has_path_component(command: str) -> bool:
    return (
        Path(command).is_absolute()
        or os.sep in command
        or (os.altsep is not None and os.altsep in command)
    )


def _execution_search_directories(
    workspace: Path,
    environment: Mapping[str, str],
) -> tuple[Path, ...]:
    directories: list[Path] = []
    if os.name == "nt":
        directories.append(workspace)
    raw_path = environment.get("PATH", os.defpath)
    for raw_directory in raw_path.split(os.pathsep):
        directory = Path(raw_directory) if raw_directory else workspace
        if not directory.is_absolute():
            directory = workspace / directory
        if directory not in directories:
            directories.append(directory)
    return tuple(directories)


def _candidate_executable_names(
    command: str,
    environment: Mapping[str, str],
) -> tuple[str, ...]:
    if os.name != "nt" or Path(command).suffix:
        return (command,)
    raw_extensions = environment.get(
        "PATHEXT",
        ".COM;.EXE;.BAT;.CMD",
    )
    return tuple(
        f"{command}{extension}"
        for extension in raw_extensions.split(os.pathsep)
        if extension
    )


def _resolve_trusted_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Invalid trusted executable root: {path}.") from error
    if not resolved.is_dir():
        raise ValueError(f"Invalid trusted executable root: {path}.")
    return resolved


def _system_executable_roots() -> tuple[Path, ...]:
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend(_windows_system_directories())
    else:
        try:
            configured = os.confstr("CS_PATH")
        except (AttributeError, OSError, ValueError):
            configured = None
        raw_paths = configured.split(os.pathsep) if configured else ["/bin", "/usr/bin"]
        candidates.extend(Path(path) for path in raw_paths if path)
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _windows_system_directories() -> tuple[Path, ...]:
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return ()
    directories: list[Path] = []
    for function_name in (
        "GetSystemDirectoryW",
        "GetSystemWow64DirectoryW",
    ):
        try:
            function = getattr(kernel32, function_name)
            function.argtypes = (
                ctypes.POINTER(ctypes.c_wchar),
                ctypes.c_uint,
            )
            function.restype = ctypes.c_uint
            buffer = ctypes.create_unicode_buffer(32768)
            length = function(buffer, len(buffer))
        except (AttributeError, OSError, TypeError, ValueError):
            continue
        if 0 < length < len(buffer) and buffer.value:
            directories.append(Path(buffer.value))
    return tuple(directories)


def _is_trusted_system_executable(
    executable: Path,
    roots: tuple[Path, ...],
    require_system_ownership: bool,
) -> bool:
    root = next(
        (
            candidate
            for candidate in roots
            if _path_within_workspace(executable, candidate)
        ),
        None,
    )
    if root is None or not _has_native_executable_header(executable):
        return False
    if os.name == "nt":
        return executable.suffix.lower() in {".exe", ".com"}
    if not require_system_ownership:
        return True
    try:
        executable_stat = executable.stat()
        root_stat = root.stat()
    except OSError:
        return False
    writable_mask = stat.S_IWGRP | stat.S_IWOTH
    return (
        executable_stat.st_uid == 0
        and root_stat.st_uid == 0
        and not executable_stat.st_mode & writable_mask
        and not root_stat.st_mode & writable_mask
    )


def _has_native_executable_header(executable: Path) -> bool:
    try:
        with executable.open("rb") as file:
            header = file.read(4)
    except OSError:
        return False
    return header.startswith(b"MZ") or header == b"\x7fELF" or header in MACH_O_MAGICS


def _unresolved_executable_failure(
    prepared: PreparedShellCommand,
) -> ToolResult:
    return ToolResult.failure(
        "execution_error",
        f"Could not resolve executable: {prepared.user_argv[0]!r}.",
    )


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
    command_arguments = argv[command_index + 1 :]
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
    user_options = [argument for argument in options if argument not in GIT_DIFF_SAFETY]
    return [
        *user_options,
        *GIT_DIFF_SAFETY,
        *operands,
    ]
