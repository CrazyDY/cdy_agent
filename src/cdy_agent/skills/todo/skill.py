"""Todo skill metadata."""

from __future__ import annotations

from cdy_agent.models import Skill
from cdy_agent.skills.todo.tools import add_todo, complete_todo, list_todos


def get_skill() -> Skill:
    return Skill(
        name="todo",
        description="管理待办事项：添加、查看、完成事务。",
        tools=[add_todo, list_todos, complete_todo],
        permissions=["todo.read", "todo.write"],
    )
