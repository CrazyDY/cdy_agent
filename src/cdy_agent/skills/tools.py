from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cdy_agent.tools.base import ToolResult

from .loader import NAME_PATTERN
from .manager import SkillManager


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
class ActivateSkillTool:
    manager: SkillManager
    name: str = "activate_skill"
    description: str = (
        "Activate one workspace Skill and receive its instructions and tools."
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


def create_skill_tools(
    manager: SkillManager,
) -> tuple[ListSkillsTool, ActivateSkillTool]:
    return ListSkillsTool(manager), ActivateSkillTool(manager)
