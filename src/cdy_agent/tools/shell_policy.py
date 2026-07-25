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
        or any(not isinstance(element, str) for element in argv)
    ):
        return ToolResult.failure(
            "invalid_arguments", "argv must be a non-empty list of strings."
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
    if argv[0] != "git" or len(argv) < 2:
        return list(argv)
    prefix = [
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        argv[1],
    ]
    user_arguments = [
        argument
        for argument in argv[2:]
        if argument not in {"--no-ext-diff", "--no-textconv"}
    ]
    if argv[1] != "diff":
        return [*prefix, *user_arguments]
    safety = ["--no-ext-diff", "--no-textconv"]
    try:
        separator = user_arguments.index("--")
    except ValueError:
        return [*prefix, *user_arguments, *safety]
    return [
        *prefix,
        *user_arguments[:separator],
        *safety,
        *user_arguments[separator:],
    ]
