from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cdy_agent.tools.base import ToolResult
from cdy_agent.tools.process import (
    MAX_OUTPUT_BYTES,
    limited_output,
    sanitized_environment,
)
from cdy_agent.tools.shell_approvals import ShellApprovalStore
from cdy_agent.tools.shell_policy import (
    MAX_TIMEOUT_SECONDS,
    ShellExecutionDecision,
    ShellExecutionPolicy,
)

# Backwards-compatible name for callers that imported the original constant.
MAX_OUTPUT_CHARS = MAX_OUTPUT_BYTES

Runner = Callable[..., subprocess.CompletedProcess[str]]


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

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        prepared = self.policy.prepare(arguments)
        argv = (
            arguments.get("argv", [])
            if isinstance(prepared, ToolResult)
            else list(prepared.argv)
        )
        return (
            f"Run command {argv!r} in workspace {self.workspace} "
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
        argv = list(prepared.argv)
        try:
            completed = self.runner(
                argv,
                cwd=self.workspace,
                shell=False,
                capture_output=True,
                text=True,
                env=sanitized_environment(),
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
