"""Core data models for skill metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Skill:
    """A capability package exposed to the agent as one or more tools."""

    name: str
    description: str
    tools: list[Any] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    enabled: bool = True
