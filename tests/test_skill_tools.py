from __future__ import annotations

import cdy_agent.skills as skills_package
from cdy_agent.skills.tools import (
    ActivateSkillTool,
    ListSkillsTool,
    create_skill_tools,
)
from cdy_agent.tools.base import ToolResult


class FakeManager:
    def list_skills(self) -> dict[str, list[object]]:
        return {"skills": [], "diagnostics": []}

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


def test_management_tool_factory_has_stable_order_and_types() -> None:
    manager = FakeManager()

    tools = create_skill_tools(manager)

    assert tuple(type(tool) for tool in tools) == (ListSkillsTool, ActivateSkillTool)
    assert [tool.name for tool in tools] == ["list_skills", "activate_skill"]
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
