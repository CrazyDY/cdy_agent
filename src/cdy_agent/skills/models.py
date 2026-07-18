from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str


@dataclass(frozen=True)
class DiscoveredSkill:
    metadata: SkillMetadata
    directory: Path
    instructions: str
    tools_path: Path | None

    @property
    def has_tools(self) -> bool:
        return self.tools_path is not None


@dataclass(frozen=True)
class SkillDiagnostic:
    entry: str
    code: str
    message: str


@dataclass(frozen=True)
class SkillDiscovery:
    skills: tuple[DiscoveredSkill, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
