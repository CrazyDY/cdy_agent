from __future__ import annotations

import cdy_agent.skills as skills_package
from cdy_agent.skills.tools import (
    ActivateSkillTool,
    ListSkillsTool,
    SearchSkillsTool,
    create_skill_tools,
)
from cdy_agent.tools.base import ToolResult


class FakeManager:
    def list_skills(self) -> dict[str, list[object]]:
        return {"skills": [], "diagnostics": []}

    def search_skills(self, query: str, limit: int) -> dict[str, object]:
        return {"query": query, "limit": limit, "matches": []}

    def activate(self, name: str) -> ToolResult:
        return ToolResult.success({"name": name})


def test_list_skills_accepts_only_empty_arguments() -> None:
    tool = ListSkillsTool(FakeManager())

    assert tool.execute({}).data == {"skills": [], "diagnostics": []}
    assert tool.preflight({"extra": True}).code == "invalid_arguments"
    assert tool.requires_confirmation is False


def test_activate_skill_requires_exactly_one_valid_name() -> None:
    tool = ActivateSkillTool(FakeManager())

    assert tool.execute({"name": "research"}).data == {"name": "research"}
    assert tool.preflight({}).code == "invalid_arguments"
    assert tool.preflight({"name": 1}).code == "invalid_arguments"
    assert tool.preflight({"name": "research", "extra": True}).code == (
        "invalid_arguments"
    )
    assert tool.requires_confirmation is False


def test_search_skills_requires_query_and_accepts_optional_limit() -> None:
    tool = SearchSkillsTool(FakeManager())

    assert tool.execute({"query": "durable notes"}).data == {
        "query": "durable notes",
        "limit": 5,
        "matches": [],
    }
    assert tool.execute({"query": "durable notes", "limit": 3}).data == {
        "query": "durable notes",
        "limit": 3,
        "matches": [],
    }
    assert tool.preflight({}).code == "invalid_arguments"
    assert tool.preflight({"query": ""}).code == "invalid_arguments"
    assert tool.preflight({"query": "x", "limit": 0}).code == "invalid_arguments"
    assert tool.preflight({"query": "x", "limit": 11}).code == "invalid_arguments"
    assert tool.preflight({"query": "x", "extra": True}).code == (
        "invalid_arguments"
    )
    assert tool.requires_confirmation is False


def test_management_tools_expose_exact_schemas() -> None:
    manager = FakeManager()

    assert ListSkillsTool(manager).parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert ActivateSkillTool(manager).parameters == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    assert SearchSkillsTool(manager).parameters == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def test_management_tool_factory_has_stable_order_and_types() -> None:
    manager = FakeManager()

    tools = create_skill_tools(manager)

    assert tuple(type(tool) for tool in tools) == (
        ListSkillsTool,
        SearchSkillsTool,
        ActivateSkillTool,
    )
    assert [tool.name for tool in tools] == [
        "list_skills",
        "search_skills",
        "activate_skill",
    ]
    assert all(tool.manager is manager for tool in tools)


def test_skills_package_exports_only_public_management_api() -> None:
    assert skills_package.__all__ == ["SkillManager", "create_skill_tools"]


def test_activate_tool_preserves_manager_failure_identity() -> None:
    failure = ToolResult.failure("invalid_skill", "Skill changed.")

    class FailingManager(FakeManager):
        def activate(self, name: str) -> ToolResult:
            return failure

    result = ActivateSkillTool(FailingManager()).execute({"name": "research"})

    assert result is failure
