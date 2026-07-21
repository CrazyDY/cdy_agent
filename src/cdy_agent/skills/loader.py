from __future__ import annotations

import re
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from cdy_agent.tools.filesystem import resolve_workspace

from .models import DiscoveredSkill, SkillDiagnostic, SkillDiscovery, SkillMetadata


MAX_SKILL_BYTES = 256 * 1024
MAX_TOOLS_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{