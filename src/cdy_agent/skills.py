"""Local skill discovery and execution.

A skill is a folder with a SKILL.md instruction file. Optional executable
scripts can be placed beside it and referenced by relative path in metadata.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skill:
    """A local skill loaded from a directory containing SKILL.md."""

    name: str
    description: str
    instructions: str
    path: Path
    command: list[str] | None = None

    @property
    def tool_name(self) -> str:
        return f"skill_{self.name.replace('-', '_')}"

    def run(self, task: str, timeout_seconds: int = 120) -> str:
        """Run this skill for a task.

        If the skill defines a command in its front matter, the command is
        executed with the task on stdin. Otherwise, the skill returns its
        instructions so the model can apply them directly.
        """
        if not self.command:
            return self.instructions

        command = list(self.command)
        executable = Path(command[0])
        if executable.parent != Path(".") or (self.path / executable).exists():
            command[0] = str((self.path / executable).resolve())
        completed = subprocess.run(
            command,
            input=task,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            cwd=self.path,
            check=False,
        )
        output = completed.stdout.strip()
        error = completed.stderr.strip()
        if completed.returncode != 0:
            return f"Skill {self.name} failed with exit code {completed.returncode}.\nSTDOUT:\n{output}\nSTDERR:\n{error}"
        return output or error or f"Skill {self.name} completed without output."


class SkillRegistry:
    """Discovers and exposes skills from one or more directories."""

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = roots or [Path("skills")]
        self._skills: dict[str, Skill] = {}

    def discover(self) -> dict[str, Skill]:
        self._skills.clear()
        for root in self.roots:
            if not root.exists():
                continue
            for skill_file in root.rglob("SKILL.md"):
                skill = self._load_skill(skill_file)
                self._skills[skill.tool_name] = skill
        return dict(self._skills)

    def get(self, tool_name: str) -> Skill:
        if not self._skills:
            self.discover()
        return self._skills[tool_name]

    def tool_schemas(self) -> list[dict[str, Any]]:
        if not self._skills:
            self.discover()
        return [
            {
                "type": "function",
                "name": skill.tool_name,
                "description": f"Use the local skill '{skill.name}': {skill.description}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The concrete task or input to pass to this skill.",
                        }
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
            }
            for skill in self._skills.values()
        ]

    def execute_tool(self, tool_name: str, arguments: str | dict[str, Any]) -> str:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        task = str(args.get("task", ""))
        return self.get(tool_name).run(task)

    def skill_prompt(self) -> str:
        if not self._skills:
            self.discover()
        if not self._skills:
            return "No local skills are currently installed."
        lines = ["Available local skills:"]
        for skill in self._skills.values():
            lines.append(f"- {skill.name}: {skill.description} (tool: {skill.tool_name})")
        return "\n".join(lines)

    @staticmethod
    def _load_skill(skill_file: Path) -> Skill:
        text = skill_file.read_text(encoding="utf-8")
        metadata: dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            _, meta_text, body = text.split("---", 2)
            metadata = _parse_front_matter(meta_text)
        name = str(metadata.get("name") or skill_file.parent.name)
        first_line = body.strip().splitlines()[0] if body.strip() else name
        description = str(metadata.get("description") or first_line)
        command = metadata.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
        return Skill(name=name, description=description, instructions=body.strip(), path=skill_file.parent, command=command)


def _parse_front_matter(meta_text: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for raw_line in meta_text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"\'')
    return metadata
