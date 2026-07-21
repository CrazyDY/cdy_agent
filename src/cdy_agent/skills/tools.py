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
) -> tuple[ListSkillsTool, SearchSkillsTool, ActivateSkillTool]:
    return (
        ListSkillsTool(manager),
        SearchSkillsTool(manager),
        ActivateSkillTool(manager),
    )
