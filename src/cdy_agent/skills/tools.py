from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from cdy_agent.tools.base import ToolResult
from cdy_agent.tools.process import limited_output, sanitized_environment

from .loader import NAME_PATTERN
from .manager import SkillManager

MAX_RESOURCE_BYTES = 1024 * 1024
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 30
MAX_SCRIPT_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class _PreparedScript:
    name: str
    directory: Path
    script_path: Path
    argv: tuple[str, ...]
    timeout: int


@dataclass
class ListSkillsTool:
    manager: SkillManager
    name: str = "list_skills"
    description: str = "List workspace Skills available for optional activation."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if arguments:
            return ToolResult.failure(
                "invalid_arguments", "No arguments are accepted."
            )
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "List workspace Skills."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or ToolResult.success(self.manager.list_skills())


@dataclass
class SearchSkillsTool:
    manager: SkillManager
    name: str = "search_skills"
    description: str = (
        "Search workspace Skills by natural-language task description."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if set(arguments) - {"query", "limit"}:
            return ToolResult.failure(
                "invalid_arguments",
                "query is required and limit must be between 1 and 10.",
            )
        query = arguments.get("query")
        limit = arguments.get("limit", 5)
        if (
            not isinstance(query, str)
            or not query.strip()
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 10
        ):
            return ToolResult.failure(
                "invalid_arguments",
                "query is required and limit must be between 1 and 10.",
            )
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "Search workspace Skills."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        if invalid:
            return invalid
        return ToolResult.success(
            self.manager.search_skills(
                arguments["query"], arguments.get("limit", 5)
            )
        )


@dataclass
class ActivateSkillTool:
    manager: SkillManager
    name: str = "activate_skill"
    description: str = (
        "Activate one workspace Skill and receive its instructions and resources."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if (
            set(arguments) != {"name"}
            or not isinstance(arguments["name"], str)
            or NAME_PATTERN.fullmatch(arguments["name"]) is None
        ):
            return ToolResult.failure(
                "invalid_arguments", "name must be a valid Skill name."
            )
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Activate Skill {arguments.get('name', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or self.manager.activate(arguments["name"])


@dataclass
class ReadSkillResourceTool:
    manager: SkillManager
    name: str = "read_skill_resource"
    description: str = (
        "Read one text reference or asset from an activated workspace Skill."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["name", "path"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_resource_arguments(arguments)
        if invalid is not None:
            return invalid
        resource = self.manager.resolve_active_resource(
            arguments["name"],
            arguments["path"],
            ("references", "assets"),
        )
        return resource if isinstance(resource, ToolResult) else None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "Read one activated Skill resource."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_resource_arguments(arguments)
        if invalid is not None:
            return invalid
        resource = self.manager.resolve_active_resource(
            arguments["name"],
            arguments["path"],
            ("references", "assets"),
        )
        if isinstance(resource, ToolResult):
            return resource
        try:
            with resource.path.open("rb") as stream:
                content = stream.read(MAX_RESOURCE_BYTES + 1)
        except OSError:
            return ToolResult.failure(
                "resource_read_failed", "Could not read the Skill resource."
            )
        if len(content) > MAX_RESOURCE_BYTES:
            return ToolResult.failure(
                "resource_too_large",
                "Skill resource exceeds the 1 MiB read limit.",
            )
        payload: dict[str, object] = {
            "path": str(resource.path.resolve()),
            "relative_path": resource.relative_path,
            "size": len(content),
            "binary": True,
        }
        try:
            payload["content"] = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        else:
            payload["binary"] = False
        return ToolResult.success(payload)


@dataclass
class RunSkillScriptTool:
    manager: SkillManager
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    name: str = "run_skill_script"
    description: str = (
        "Run one script from an activated workspace Skill after confirmation."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "argv": {"type": "array", "items": {"type": "string"}},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                },
            },
            "required": ["name", "argv"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = field(default=True, init=False)

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        prepared = self._prepare(arguments)
        return prepared if isinstance(prepared, ToolResult) else None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        prepared = self._prepare(arguments)
        if isinstance(prepared, ToolResult):
            return (
                "Run the requested activated Skill script with current user "
                "permissions."
            )
        return (
            f"Run Skill '{prepared.name}' script {prepared.script_path} "
            f"with argv {list(prepared.argv)!r} in directory "
            f"{prepared.directory} with current user permissions."
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        prepared = self._prepare(arguments)
        if isinstance(prepared, ToolResult):
            return prepared
        argv = list(prepared.argv)
        try:
            completed = self.runner(
                argv,
                cwd=prepared.directory,
                shell=False,
                capture_output=True,
                text=True,
                env=sanitized_environment(),
                timeout=prepared.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(
                "script_timeout",
                f"Script timed out after {prepared.timeout} seconds.",
            )
        except OSError:
            return ToolResult.failure(
                "execution_error", "Could not execute the Skill script."
            )

        stdout, stdout_truncated = limited_output(completed.stdout)
        stderr, stderr_truncated = limited_output(completed.stderr)
        payload = {
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        if completed.returncode != 0:
            return ToolResult.failure(
                "script_failed",
                f"Script exited with return code {completed.returncode}.",
                payload,
            )
        return ToolResult.success(payload)

    def _prepare(
        self, arguments: dict[str, Any]
    ) -> _PreparedScript | ToolResult:
        validated = _validate_script_arguments(arguments)
        if isinstance(validated, ToolResult):
            return validated
        name, user_argv, timeout = validated
        resources = self.manager.active_resources(name, ("scripts",))
        if isinstance(resources, ToolResult):
            return resources
        resource_by_path = {
            resource.relative_path: resource for resource in resources
        }
        effective_argv: list[str] = []
        resolved_matches = []
        for argument in user_argv:
            if argument not in resource_by_path:
                effective_argv.append(argument)
                continue
            resolved = self.manager.resolve_active_resource(
                name, argument, ("scripts",)
            )
            if isinstance(resolved, ToolResult):
                return resolved
            resolved_matches.append(resolved)
            effective_argv.append(str(resolved.path.resolve()))
        if len(resolved_matches) != 1:
            return ToolResult.failure(
                "invalid_script_command",
                "argv must reference exactly one manifest script.",
            )
        script = resolved_matches[0]
        relative_parts = PurePosixPath(script.relative_path).parts
        directory = script.path.resolve()
        for _ in relative_parts:
            directory = directory.parent
        return _PreparedScript(
            name=name,
            directory=directory,
            script_path=script.path.resolve(),
            argv=tuple(effective_argv),
            timeout=timeout,
        )


def create_skill_tools(
    manager: SkillManager,
) -> tuple[
    ListSkillsTool,
    SearchSkillsTool,
    ActivateSkillTool,
    ReadSkillResourceTool,
    RunSkillScriptTool,
]:
    return (
        ListSkillsTool(manager),
        SearchSkillsTool(manager),
        ActivateSkillTool(manager),
        ReadSkillResourceTool(manager),
        RunSkillScriptTool(manager),
    )


def _validate_resource_arguments(
    arguments: dict[str, Any],
) -> ToolResult | None:
    if set(arguments) != {"name", "path"}:
        return ToolResult.failure(
            "invalid_arguments",
            "name and path are required with no additional arguments.",
        )
    name = arguments["name"]
    path = arguments["path"]
    if (
        not isinstance(name, str)
        or NAME_PATTERN.fullmatch(name) is None
        or not isinstance(path, str)
        or not path.strip()
    ):
        return ToolResult.failure(
            "invalid_arguments",
            "name must be a valid Skill name and path must be non-empty.",
        )
    return None


def _validate_script_arguments(
    arguments: dict[str, Any],
) -> tuple[str, list[str], int] | ToolResult:
    if set(arguments) not in (
        {"name", "argv"},
        {"name", "argv", "timeout_seconds"},
    ):
        return ToolResult.failure(
            "invalid_arguments",
            "name and argv are required; timeout_seconds is optional.",
        )
    name = arguments["name"]
    argv = arguments["argv"]
    timeout = arguments.get(
        "timeout_seconds", DEFAULT_SCRIPT_TIMEOUT_SECONDS
    )
    if (
        not isinstance(name, str)
        or NAME_PATTERN.fullmatch(name) is None
        or not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
    ):
        return ToolResult.failure(
            "invalid_arguments",
            "name must be valid and argv must be a non-empty list of strings.",
        )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= MAX_SCRIPT_TIMEOUT_SECONDS
    ):
        return ToolResult.failure(
            "invalid_arguments",
            "timeout_seconds must be an integer from 1 to 300.",
        )
    return name, argv, timeout
