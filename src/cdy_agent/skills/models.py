from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

ResourceCategory = Literal["scripts", "references", "assets"]


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    allowed_tools: str | None = None


@dataclass(frozen=True)
class _ResourceIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    metadata_changed_ns: int


@dataclass(frozen=True)
class SkillResource:
    category: ResourceCategory
    relative_path: str
    path: Path
    size: int
    _identity: _ResourceIdentity = field(repr=False)


@dataclass(frozen=True)
class DiscoveredSkill:
    metadata: SkillMetadata
    directory: Path
    instructions: str
    resources: tuple[SkillResource, ...]


@dataclass(frozen=True)
class SkillDiagnostic:
    entry: str
    code: str
    message: str


@dataclass(frozen=True)
class SkillDiscovery:
    skills: tuple[DiscoveredSkill, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
