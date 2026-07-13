from pathlib import Path

from cdy_agent.skills import SkillRegistry


def test_discovers_skill_and_builds_tool_schema(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nUse this skill for demos.",
        encoding="utf-8",
    )

    registry = SkillRegistry([tmp_path / "skills"])

    schemas = registry.tool_schemas()

    assert schemas[0]["name"] == "skill_demo"
    assert "Demo skill" in schemas[0]["description"]


def test_instruction_only_skill_returns_instructions(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "plan"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Plan carefully.", encoding="utf-8")

    registry = SkillRegistry([tmp_path / "skills"])
    registry.discover()

    assert registry.execute_tool("skill_plan", {"task": "anything"}) == "Plan carefully."
