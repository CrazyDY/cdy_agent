from cdy_agent.skill_registry import load_skills, load_tools


def test_load_skills_includes_mvp_skills():
    skill_names = {skill.name for skill in load_skills()}

    assert skill_names == {"todo", "notes"}


def test_load_tools_flattens_registered_skill_tools():
    tools = load_tools()

    assert len(tools) == 6
