from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cdy_agent.tools.base import PreparedToolExecution, ToolResult
from cdy_agent.tools.process import (
    MAX_OUTPUT_BYTES,
    limited_output,
)
from cdy_agent.tools.shell_approvals import ShellApprovalStore
from cdy_agent.tools.shell_policy import (
    MAX_TIMEOUT_SECONDS,
    PreparedShellCommand,
    ShellExecutionDecision,
    ShellExecutionPolicy,
)

# Backwards-compatible name for callers that imported the original constant.
MAX_OUTPUT_CHARS = MAX_OUTPUT_BYTES

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class _ShellAuthorizationContext:
    command: PreparedShellCommand
    decision: ShellExecutionDecision
    workspace: Path
    runner: Runner = field(repr=False, compare=False)
    policy: ShellExecutionPolicy = field(repr=False, compare=False)

    def remember(self) -> ToolResult:
        return self.policy.remember_prepared(self.command)

    def execute(self) -> ToolResult:
        return _run_prepared_shell_command(
            self.command,
            self.workspace,
            self.runner,
        )


@dataclass
class ShellTool:
    workspace: Path
    runner: Runner = subprocess.run
    policy: ShellExecutionPolicy | None = None
    name: str = field(default="shell", init=False)
    description: str = field(
        default=(
            "Run a command in the workspace. Proven read-only commands and "
            "persistently approved exact commands can run automatically."
        ),
        init=False,
    )
    parameters: dict[str, Any] = field(
        init=False,
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                },
            },
            "required": ["argv"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()
        if self.policy is None:
            approvals = ShellApprovalStore(self.workspace)
            self.policy = ShellExecutionPolicy(self.workspace, approvals)
        elif self.policy.workspace != self.workspace:
            raise ValueError(
                "Shell policy workspace does not match tool workspace."
            )

    def prepare_execution(
        self,
        arguments: dict[str, Any],
    ) -> PreparedToolExecution | ToolResult:
        policy = self.policy
        workspace = self.workspace
        runner = self.runner
        if policy is None or policy.workspace != workspace:
            return ToolResult.failure(
                "invalid_tool_execution",
                "Shell policy workspace does not match tool workspace.",
            )
        result = policy.classify(arguments)
        if result.failure is not None:
            return result.failure
        if result.command is None:
            return ToolResult.failure(
                "invalid_tool_execution",
                "Shell policy did not prepare a command.",
            )
        context = _ShellAuthorizationContext(
            result.command,
            result.decision,
            workspace,
            runner,
            policy,
        )
        return PreparedToolExecution(
            requires_confirmation=(
                context.decision
                is ShellExecutionDecision.REQUIRE_CONFIRMATION
            ),
            confirmation_description=self._describe(context.command),
            remember_approval=context.remember,
            execute=context.execute,
        )

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        prepared = self.policy.prepare(arguments)
        argv = (
            arguments.get("argv", [])
            if isinstance(prepared, ToolResult)
            else list(prepared.argv)
        )
        if isinstance(prepared, ToolResult):
            return (
                f"Run command {argv!r} in workspace {self.workspace} "
                "with current user permissions."
            )
        return self._describe(prepared)

    def _describe(self, prepared: PreparedShellCommand) -> str:
        return (
            f"Run command {list(prepared.argv)!r} "
            f"in workspace {self.workspace} "
            "with current user permissions."
        )

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        result = self.policy.classify(arguments)
        return result.failure

    def requires_confirmation_for(
        self, arguments: dict[str, Any]
    ) -> bool:
        result = self.policy.classify(arguments)
        return (
            result.decision
            is ShellExecutionDecision.REQUIRE_CONFIRMATION
        )

    def remember_approval(self, arguments: dict[str, Any]) -> ToolResult:
        return self.policy.remember(arguments)

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        prepared = self.policy.prepare(arguments)
        if isinstance(prepared, ToolResult):
            return prepared
        return self._execute_prepared(prepared)

    def _execute_prepared(
        self,
        prepared: PreparedShellCommand,
    ) -> ToolResult:
        return _run_prepared_shell_command(
            prepared,
            self.workspace,
            self.runner,
        )


def _run_prepared_shell_command(
    prepared: PreparedShellCommand,
    workspace: Path,
    runner: Runner,
) -> ToolResult:
    if prepared.executable is None:
        return ToolResult.failure(
            "execution_error",
            f"Could not resolve executable: "
            f"{prepared.user_argv[0]!r}.",
        )
    argv = list(prepared.argv)
    try:
        completed = runner(
            argv,
            cwd=workspace,
            shell=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=dict(prepared.environment),
            timeout=prepared.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(
            "command_timeout",
            f"Command timed out after "
            f"{prepared.timeout_seconds} seconds.",
        )
    except OSError as error:
        return ToolResult.failure(
            "execution_error", f"Could not execute command: {error}."
        )

    stdout, stdout_truncated = limited_output(completed.stdout)
    stderr, stderr_truncated = limited_output(completed.stderr)
    if completed.returncode != 0:
        return ToolResult.failure(
            "command_failed",
            f"Command exited with return code {completed.returncode}.",
        )
    return ToolResult.success(
        {
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
    )
