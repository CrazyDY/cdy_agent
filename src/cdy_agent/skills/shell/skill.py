"""Shell skill metadata."""

from __future__ import annotations

from cdy_agent.models import Skill
from cdy_agent.skills.shell.tools import run_bash


def get_skill() -> Skill:
    return Skill(
        name="shell",
        description="在工作区范围内执行 bash 命令并返回 stdout/stderr/exit code。",
        tools=[run_bash],
        permissions=["shell.execute"],
    )
