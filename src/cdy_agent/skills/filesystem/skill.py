"""Filesystem skill metadata."""

from __future__ import annotations

from cdy_agent.models import Skill
from cdy_agent.skills.filesystem.tools import list_files, read_file, write_file


def get_skill() -> Skill:
    return Skill(
        name="filesystem",
        description="在工作区范围内列出、读取和写入文本文件。",
        tools=[list_files, read_file, write_file],
        permissions=["filesystem.read", "filesystem.write"],
    )
