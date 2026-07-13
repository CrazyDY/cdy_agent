"""Notes skill metadata."""

from __future__ import annotations

from cdy_agent.models import Skill
from cdy_agent.skills.notes.tools import create_note, list_notes, search_notes


def get_skill() -> Skill:
    return Skill(
        name="notes",
        description="记录和检索笔记、知识片段与事务背景。",
        tools=[create_note, list_notes, search_notes],
        permissions=["notes.read", "notes.write"],
    )
