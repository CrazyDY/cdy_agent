from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from uuid import uuid4

from cdy_agent.tools.base import ConfirmationCallback, ConfirmationRequest, ToolResult
from cdy_agent.tools.registry import ToolRegistry

from .loader import InvalidSkillError, discover_skills, revalidate_tools_file
from .models import DiscoveredSkill

TOKEN_PATTERN = re.compile(r"[^\W_]+", re.IGNORECASE)
MAX_KEYWORDS = 8


class SkillManager:
    def __init__(
        self, workspace: Path, registry: ToolRegistry, confirm: ConfirmationCallback
    ) -> None:
        self.workspace = workspace.resolve()
        self.registry = registry
        self.confirm = confirm
        discovery = discover_skills(self.workspace)
        self._skills = {skill.metadata.name: skill for skill in discovery.skills}
        self._diagnostics = discovery.diagnostics
        self._active: dict[str, tuple[str, ...]] = {}

    def list_skills(self) -> dict[str, object]:
        return {
            "skills": [
                {
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "keywords": _keywords_for(skill),
                    "has_tools": skill.has_tools,
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
            score, matched_terms, reason = _score_skill(skill, normalized_query, terms)
            if score <= 0:
                continue
            matches.append(
                {
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "score": score,
                    "matched_terms": matched_terms,
                    "reason": reason,
                    "has_tools": skill.has_tools,
                    "active": skill.metadata.name in self._active,
                }
            )
        matches.sort(key=lambda item: (-item["score"], item["name"]))
        return {"query": normalized_query, "matches": matches[:limit]}

    def activate(self, name: str) -> ToolResult:
        skill = self._skills.get(name)
        if skill is None:
            if any(item.entry == name for item in self._diagnostics):
                return ToolResult.failure("invalid_skill", f"Skill '{name}' is invalid.")
            return ToolResult.failure("unknown_skill", f"Unknown Skill: {name}.")
        if name in self._active:
            return self._success(skill, "already_active", self._active[name])
        if skill.tools_path is None:
            self._active[name] = ()
            return self._success(skill, "activated", ())
        return self._activate_tools(skill)

    def _success(
        self, skill: DiscoveredSkill, status: str, names: tuple[str, ...]
    ) -> ToolResult:
        return ToolResult.success(
            {
                "name": skill.metadata.name,
                "status": status,
                "instructions": skill.instructions,
                "tools": list(names),
            }
        )

    def _activate_tools(self, skill: DiscoveredSkill) -> ToolResult:
        assert skill.tools_path is not None
        try:
            revalidate_tools_file(skill, self.workspace)
        except (InvalidSkillError, OSError):
            return ToolResult.failure(
                "invalid_skill",
                f"Skill '{skill.metadata.name}' changed or is invalid.",
            )
        request = ConfirmationRequest(
            "activate_skill",
            {"name": skill.metadata.name},
            f"Run Skill '{skill.metadata.name}' Python code from {skill.tools_path} "
            "with current user permissions.",
        )
        if not self.confirm(request):
            return ToolResult.failure(
                "approval_denied", "User declined this Skill activation."
            )
        try:
            revalidate_tools_file(skill, self.workspace)
        except (InvalidSkillError, OSError):
            return ToolResult.failure(
                "invalid_skill",
                f"Skill '{skill.metadata.name}' changed or is invalid.",
            )

        module_name = f"_cdy_agent_skill_{skill.metadata.name}_{uuid4().hex}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, skill.tools_path)
            if spec is None or spec.loader is None:
                return ToolResult.failure(
                    "load_failed", f"Could not load Skill '{skill.metadata.name}'."
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            factory = getattr(module, "create_tools", None)
            if not callable(factory):
                return ToolResult.failure(
                    "invalid_tools", "Skill must define create_tools(workspace)."
                )
            tools = factory(self.workspace)
        except Exception:
            return ToolResult.failure(
                "load_failed", f"Could not load Skill '{skill.metadata.name}'."
            )
        finally:
            sys.modules.pop(module_name, None)
        try:
            registered = self.registry.register_many(tools)
        except Exception:
            return ToolResult.failure(
                "invalid_tools", "Skill returned invalid tools."
            )
        if not registered.ok:
            return registered
        names = tuple(registered.data["names"])
        self._active[skill.metadata.name] = names
        return self._success(skill, "activated", names)


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
    text = f"{skill.metadata.name.replace('_', ' ')} {skill.metadata.description}"
    return list(_unique_ordered(_tokens(text))[:MAX_KEYWORDS])


def _contains(text: str, term: str) -> bool:
    return term in text.lower()


def _score_skill(
    skill: DiscoveredSkill, normalized_query: str, terms: tuple[str, ...]
) -> tuple[int, list[str], str]:
    name_text = skill.metadata.name.replace("_", " ").lower()
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
