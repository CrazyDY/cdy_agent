"""Manual skill registry for the V0.1 MVP."""

from __future__ import annotations

from collections.abc import Iterable

from cdy_agent.models import Skill
from cdy_agent.skills.notes.skill import get_skill as get_notes_skill
from cdy_agent.skills.todo.skill import get_skill as get_todo_skill


def load_skills() -> list[Skill]:
    """Load the enabled skills available to the agent.

    The first product milestone intentionally uses a manual registry so that
    every exposed capability is explicit and easy to audit. A manifest-driven
    auto-discovery registry can be added in a later milestone.
    """

    return [skill for skill in (get_todo_skill(), get_notes_skill()) if skill.enabled]


def load_tools(skills: Iterable[Skill] | None = None) -> list[object]:
    """Flatten registered skill tools into the format expected by Agents SDK."""

    selected_skills = list(skills) if skills is not None else load_skills()
    tools: list[object] = []
    for skill in selected_skills:
        tools.extend(skill.tools)
    return tools
