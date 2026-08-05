"""Shared Agent runtime construction for local interfaces."""

from __future__ import annotations

from pathlib import Path

from .agent import Agent
from .mcp import McpManager, create_mcp_tools, load_mcp_config
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
    base_url: str | None = None,
) -> Agent:
    """Compose the model gateway, local tools, Skills, and Agent loop."""
    gateway = ModelGateway(model=model, api_mode=api_mode, base_url=base_url)
    registry = create_builtin_registry(workspace)
    manager = SkillManager(workspace)
    catalog = manager.list_skills()
    effective_prompt = _system_prompt_with_skills(system_prompt or "", catalog)
    registered = registry.register_many(create_skill_tools(manager))
    if not registered.ok:
        raise RuntimeError(registered.message or "Could not register Skill tools.")
    mcp_config = load_mcp_config(workspace)
    mcp_manager = McpManager(workspace, mcp_config, registry)
    if mcp_config.servers:
        registered = registry.register_many(create_mcp_tools(mcp_manager))
        if not registered.ok:
            raise RuntimeError(registered.message or "Could not register MCP tools.")
        effective_prompt = _system_prompt_with_mcp(effective_prompt, mcp_config)
    return Agent(
        gateway,
        registry,
        confirm,
        max_model_calls=max_model_calls,
        system_prompt=effective_prompt,
        close_callback=mcp_manager.close,
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


def _system_prompt_with_mcp(base_prompt: str, config: object) -> str:
    servers = getattr(config, "servers", ())
    if not servers:
        return base_prompt
    entries = "\n".join(
        f"- *{server.name}*: {server.description}" for server in servers
    )
    return (
        f"{base_prompt.rstrip()}\n\n"
        "**Configured MCP servers**:\n"
        f"{entries}\n\n"
        "Use list_mcp_servers and connect_mcp_server when an MCP server matches "
        "the task. Connecting and all remote MCP tool calls require approval."
    )
