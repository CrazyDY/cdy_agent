from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cdy_agent.tools.base import ToolResult


ALLOWED_COMMANDS = frozenset(
    {
        "pwd",
        "ls",
        "find",
        "rg",
        "grep",
        "sed",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
    }
)
ALLOWED_GIT_SUBCOMMANDS = frozenset({"status", "diff"})
MAX_OUTPUT_BYTES = 64 * 1024
# Backwards-compatible name for callers that imported the original constant.
MAX_OUTPUT_CHARS = MAX_OUTPUT_BYTES
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _validate_arguments(
    arguments: dict[str, Any],
) -> tuple[list[str], int] | ToolResult:
    if set(arguments) not in ({"argv"}, {"argv", "timeout_seconds"}):
        return ToolResult.failure(
            "invalid_arguments", "argv is required; timeout_seconds is optional."
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
            f"timeout_seconds must be an integer from 1 to {MAX_TIMEOUT_SECONDS}.",
        )
    return argv, timeout


def _command_is_allowed(argv: list[str]) -> bool:
    command = argv[0]
    if "/" in command or "\\" in command:
        return False
    if command == "git":
        return (
            len(argv) >= 2
            and argv[1] in ALLOWED_GIT_SUBCOMMANDS
            and not any(arg in {"--ext-diff", "--textconv"} for arg in argv[2:])
        )
    if command not in ALLOWED_COMMANDS:
        return False
    if command == "find" and any(
        arg in {"-exec", "-execdir", "-ok", "-okdir"} for arg in argv[1:]
    ):
        return False
    if command == "rg" and any(
        arg == "--pre" or arg.startswith("--pre=") for arg in argv[1:]
    ):
        return False
    if command == "sed" and _sed_can_execute(argv[1:]):
        return False
    return True


def _sed_can_execute(arguments: list[str]) -> bool:
    scripts: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-e", "--expression"} and index + 1 < len(arguments):
            scripts.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith("--expression="):
            scripts.append(argument.split("=", 1)[1])
        elif argument.startswith("-e") and argument != "-e":
            scripts.append(argument[2:])
        elif not argument.startswith("-") and not scripts:
            scripts.append(argument)
        index += 1
    return any(
        re.search(r"(?:^|[;}])\s*(?:\d+(?:,\d+)?\s*)?e(?:\s|$)", script)
        is not None
        for script in scripts
    )


def _limited_output(output: str) -> tuple[str, bool]:
    encoded = output.encode("utf-8")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return output, False
    limited = encoded[:MAX_OUTPUT_BYTES]
    return limited.decode("utf-8", errors="ignore"), True


@dataclass
class ShellTool:
    workspace: Path
    runner: Runner = subprocess.run
    name: str = field(default="shell", init=False)
    description: str = field(
        default="Run an allowlisted command in the workspace.", init=False
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

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        argv = arguments.get("argv", [])
        return f"Run command {argv!r} in workspace {self.workspace}."

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return validated
        argv, _ = validated
        if not _command_is_allowed(argv):
            return ToolResult.failure(
                "command_not_allowed", "Command is not in the allowlist."
            )
        return None

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return validated
        argv, timeout = validated
        if not _command_is_allowed(argv):
            return ToolResult.failure(
                "command_not_allowed", "Command is not in the allowlist."
            )

        try:
            completed = self.runner(
                argv,
                cwd=self.workspace,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(
                "command_timeout", f"Command timed out after {timeout} seconds."
            )
        except OSError as error:
            return ToolResult.failure(
                "execution_error", f"Could not execute command: {error}."
            )

        stdout, stdout_truncated = _limited_output(completed.stdout)
        stderr, stderr_truncated = _limited_output(completed.stderr)
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
