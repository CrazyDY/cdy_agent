from __future__ import annotations

import re
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
            and not any(
                (arg.startswith("--ext") and arg != "--no-ext-diff")
                or (arg.startswith("--textc") and arg != "--no-textconv")
                for arg in argv[2:]
            )
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
    if command == "sed" and not _sed_arguments_are_safe(argv[1:]):
        return False
    return True


def _sed_arguments_are_safe(arguments: list[str]) -> bool:
    if any("\n" in argument or "\r" in argument for argument in arguments):
        return False
    scripts: list[str] = []
    index = 0
    inline_script = False
    safe_flags = {"-n", "--quiet", "--silent", "-E", "-r", "--regexp-extended"}
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-e", "--expression"} and index + 1 < len(arguments):
            scripts.append(arguments[index + 1])
            inline_script = True
            index += 2
            continue
        if argument.startswith("--expression="):
            scripts.append(argument.split("=", 1)[1])
            inline_script = True
        elif argument.startswith("-e") and argument != "-e":
            scripts.append(argument[2:])
            inline_script = True
        elif argument in safe_flags:
            pass
        elif argument.startswith("-"):
            return False
        elif not inline_script and not scripts:
            scripts.append(argument)
        index += 1
    return bool(scripts) and all(_sed_script_is_safe(script) for script in scripts)


def _sed_script_is_safe(script: str) -> bool:
    if ";" in script or "{" in script or "}" in script or "!" in script:
        return False
    command = re.sub(
        r"^\s*(?:(?:\d+|\$)(?:,(?:\d+|\$))?)?\s*", "", script
    )
    if command in {"p", "d", "q", "="}:
        return True
    return _sed_substitution_is_safe(command)


def _sed_substitution_is_safe(command: str) -> bool:
    if len(command) < 2 or command[1].isalnum() or command[1].isspace():
        return False
    delimiter = command[1]
    delimiter_count = 0
    escaped = False
    for index, character in enumerate(command[2:], start=2):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == delimiter:
            delimiter_count += 1
            if delimiter_count == 2:
                flags = command[index + 1:].strip()
                return all(flag in "gpIiMm0123456789" for flag in flags)
    return False


def _effective_argv(argv: list[str]) -> list[str]:
    if argv[0] == "rg":
        return ["rg", "--no-config", *argv[1:]]
    if argv[0] == "git":
        prefix = ["git", "--no-pager", "-c", "core.fsmonitor=false", argv[1]]
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
    return list(argv)


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
        validated = _validate_arguments(arguments)
        argv = (
            arguments.get("argv", [])
            if isinstance(validated, ToolResult)
            else _effective_argv(validated[0])
        )
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
        user_argv, timeout = validated
        if not _command_is_allowed(user_argv):
            return ToolResult.failure(
                "command_not_allowed", "Command is not in the allowlist."
            )

        argv = _effective_argv(user_argv)
        try:
            completed = self.runner(
                argv,
                cwd=self.workspace,
                shell=False,
                capture_output=True,
                text=True,
                env=sanitized_environment(),
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
