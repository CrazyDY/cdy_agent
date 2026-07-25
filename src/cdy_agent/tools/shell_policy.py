from __future__ import annotations

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
        return PreparedShellCommand(
            tuple(_effective_argv(user_argv)),
            timeout,
        )

    def classify(self, arguments: dict[str, Any]) -> ShellPolicyResult:
        prepared = self.prepare(arguments)
        if isinstance(prepared, ToolResult):
            return ShellPolicyResult(
                ShellExecutionDecision.REJECT,
                failure=prepared,
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
