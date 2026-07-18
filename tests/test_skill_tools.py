from __future__ import annotations

from cdy_agent.skills.tools import ActivateSkillTool, ListSkillsTool
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
