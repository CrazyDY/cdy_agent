"""Shared Agent runtime construction for local interfaces."""

from __future__ import annotations

from pathlib import Path

from .agent import Agent
from .openai_client import ModelGateway
from .skills import SkillManager, create_skill_tools
from .tools import create_builtin_registry
from .tools.base import ConfirmationCallback


def create_agent_runtime(
    *,
    model: str,
    api_mode: str,
    workspace: Path,
    confirm: ConfirmationCallback,
    max_model_calls: int,
    system_prompt: str | None = None,
) -> Agent:
    """Compose the model gateway, local tools, Skills, and Agent loop."""
    gateway = ModelGateway(model=model, api_mode=api_mode)
    registry = create_builtin_registry(workspace)
    manager = SkillManager(workspace)
    catalog = manager.list_skills()
    effective_prompt = _system_prompt_with_skills(system_prompt or "", catalog)
    registered = registry.register_many(create_skill_tools(manager))
    if not registered.ok:
        raise RuntimeError(registered.message or "Could not register Skill tools.")
    return Agent(
        gateway,
        registry,
        confirm,
        max_model_calls=max_model_calls,
        system_prompt=effective_prompt,
    )


def _system_prompt_with_skills(base_prompt: str, catalog: dict[str, object]) -> str:
    """Append a concise workspace Skill catalog when Skills are available."""
    raw_skills = catalog.get("skills")
    if not isinstance(raw_skills, list):
        return base_prompt

    entries = []
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            continue
        name = raw_skill.get("name")
        description = raw_skill.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            continue
        if not name.strip() or not description.strip():
            continue
        entries.append(f"- *{name.strip()}*: {description.strip()}")
    if not entries:
        return base_prompt

    skill_catalog = "\n".join(entries)
    return (
        f"{base_prompt.rstrip()}\n\n"
        "**Available workspace Skills**:\n"
        f"{skill_catalog}\n\n"
        "When a Skill matches the task, activate it with activate_skill and "
        "follow its instructions."
    )
