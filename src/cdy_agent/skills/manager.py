from __future__ import annotations

import re
from pathlib import Path

from cdy_agent.tools.base import ToolResult

from .loader import (
    InvalidSkillError,
    discover_skills,
    revalidate_resource,
    revalidate_skill,
)
from .models import (
    DiscoveredSkill,
    ResourceCategory,
    SkillMetadata,
    SkillResource,
)

TOKEN_PATTERN = re.compile(r"[^\W_]+", re.IGNORECASE)
MAX_KEYWORDS = 8
RESOURCE_CATEGORIES = ("scripts", "references", "assets")


class SkillManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        discovery = discover_skills(self.workspace)
        self._skills = {
            skill.metadata.name: skill for skill in discovery.skills
        }
        self._diagnostics = discovery.diagnostics
        self._active: set[str] = set()

    def list_skills(self) -> dict[str, object]:
        return {
            "skills": [
                {
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "keywords": _keywords_for(skill),
                    "resource_counts": _resource_counts(skill),
                    "active": skill.metadata.name in self._active,
                }
                for skill in self._skills.values()
            ],
            "diagnostics": [
                {"entry": item.entry, "code": item.code, "message": item.message}
                for item in self._diagnostics
            ],
        }

    def search_skills(self, query: str, limit: int = 5) -> dict[str, object]:
        normalized_query = query.strip()
        terms = _tokens(normalized_query)
        if not normalized_query or not terms:
            return {"query": normalized_query, "matches": []}

        matches = []
        for skill in self._skills.values():
            score, matched_terms, reason = _score_skill(
                skill, normalized_query, terms
            )
            if score <= 0:
                continue
            matches.append(
                {
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "score": score,
                    "matched_terms": matched_terms,
                    "reason": reason,
                    "resource_counts": _resource_counts(skill),
                    "active": skill.metadata.name in self._active,
                }
            )
        matches.sort(key=lambda item: (-item["score"], item["name"]))
        return {"query": normalized_query, "matches": matches[:limit]}

    def activate(self, name: str) -> ToolResult:
        skill = self._skill_or_failure(name)
        if isinstance(skill, ToolResult):
            return skill
        if name in self._active:
            return ToolResult.success(
                _activation_payload(skill, "already_active")
            )
        try:
            revalidate_skill(skill, self.workspace)
        except (InvalidSkillError, OSError):
            return ToolResult.failure(
                "invalid_skill",
                f"Skill '{skill.metadata.name}' changed or is invalid.",
            )
        self._active.add(name)
        return ToolResult.success(_activation_payload(skill, "activated"))

    def active_resources(
        self,
        name: str,
        categories: tuple[ResourceCategory, ...],
    ) -> tuple[SkillResource, ...] | ToolResult:
        skill = self._skill_or_failure(name)
        if isinstance(skill, ToolResult):
            return skill
        if name not in self._active:
            return ToolResult.failure(
                "skill_not_active", f"Skill '{name}' is not active."
            )
        return tuple(
            resource
            for resource in skill.resources
            if resource.category in categories
        )

    def resolve_active_resource(
        self,
        name: str,
        path: str,
        categories: tuple[ResourceCategory, ...],
    ) -> SkillResource | ToolResult:
        skill = self._skill_or_failure(name)
        if isinstance(skill, ToolResult):
            return skill
        if name not in self._active:
            return ToolResult.failure(
                "skill_not_active", f"Skill '{name}' is not active."
            )
        resource = next(
            (
                item
                for item in skill.resources
                if item.relative_path == path
            ),
            None,
        )
        if resource is None:
            return ToolResult.failure(
                "unknown_resource",
                f"Unknown resource for Skill '{name}': {path}.",
            )
        if resource.category not in categories:
            return ToolResult.failure(
                "wrong_resource_category",
                f"Resource '{path}' is not in an allowed category.",
            )
        try:
            revalidate_resource(
                skill,
                resource,
                self.workspace,
                expected_category=resource.category,
            )
        except (InvalidSkillError, OSError):
            return ToolResult.failure(
                "invalid_resource",
                f"Resource '{path}' changed or is invalid.",
            )
        return resource

    def _skill_or_failure(self, name: str) -> DiscoveredSkill | ToolResult:
        skill = self._skills.get(name)
        if skill is not None:
            return skill
        if any(item.entry == name for item in self._diagnostics):
            return ToolResult.failure(
                "invalid_skill", f"Skill '{name}' is invalid."
            )
        return ToolResult.failure(
            "unknown_skill", f"Unknown Skill: {name}."
        )


def _resource_counts(skill: DiscoveredSkill) -> dict[str, int]:
    return {
        category: sum(
            resource.category == category for resource in skill.resources
        )
        for category in RESOURCE_CATEGORIES
    }


def _metadata_payload(metadata: SkillMetadata) -> dict[str, object]:
    return {
        "name": metadata.name,
        "description": metadata.description,
        "license": metadata.license,
        "compatibility": metadata.compatibility,
        "metadata": dict(metadata.metadata),
        "allowed-tools": metadata.allowed_tools,
    }


def _resource_payload(resource: SkillResource) -> dict[str, object]:
    return {
        "category": resource.category,
        "path": resource.relative_path,
        "size": resource.size,
    }


def _activation_payload(
    skill: DiscoveredSkill, status: str
) -> dict[str, object]:
    return {
        "name": skill.metadata.name,
        "status": status,
        "instructions": skill.instructions,
        "metadata": _metadata_payload(skill.metadata),
        "directory": str(skill.directory),
        "relative_paths": "Resolve resource paths from the Skill directory.",
        "resources": [
            _resource_payload(resource) for resource in skill.resources
        ],
    }


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in TOKEN_PATTERN.finditer(text))


def _unique_ordered(values: tuple[str, ...]) -> tuple[str, ...]:
    seen = set()
    unique = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


def _keywords_for(skill: DiscoveredSkill) -> list[str]:
    text = (
        f"{skill.metadata.name.replace('-', ' ')} "
        f"{skill.metadata.description}"
    )
    return list(_unique_ordered(_tokens(text))[:MAX_KEYWORDS])


def _contains(text: str, term: str) -> bool:
    return term in text.lower()


def _score_skill(
    skill: DiscoveredSkill, normalized_query: str, terms: tuple[str, ...]
) -> tuple[int, list[str], str]:
    name_text = skill.metadata.name.replace("-", " ").lower()
    description_text = skill.metadata.description.lower()
    instruction_text = skill.instructions[:4000].lower()
    phrase = normalized_query.lower()
    score = 0
    matched: list[str] = []
    reasons: list[str] = []

    if phrase and phrase in name_text:
        score += 90
        reasons.append("name phrase")
    if phrase and phrase in description_text:
        score += 60
        reasons.append("description phrase")
    if phrase and phrase in instruction_text:
        score += 30
        reasons.append("instruction phrase")

    for term in _unique_ordered(terms):
        term_score = 0
        if _contains(name_text, term):
            term_score += 20
        if _contains(description_text, term):
            term_score += 10
        if _contains(instruction_text, term):
            term_score += 3
        if term_score:
            score += term_score
            matched.append(term)

    if matched and not reasons:
        reasons.append("term match")
    return score, matched, ", ".join(reasons)
