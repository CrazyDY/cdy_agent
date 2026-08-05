from __future__ import annotations

from pathlib import Path

from cdy_agent import runtime
from cdy_agent.tools.base import ToolResult


def test_runtime_registers_skill_tools_and_catalog(monkeypatch, tmp_path: Path) -> None:
    """Omitting Skill composition would hide available Skills from the model."""
    gateway = object()
    gateway_options: list[dict[str, object]] = []
    skill_tools = object()

    class RecordingRegistry:
        def __init__(self) -> None:
            self.registered: list[object] = []

        def register_many(self, tools: object) -> ToolResult:
            self.registered.append(tools)
            return ToolResult.success({})

    class FakeSkillManager:
        def list_skills(self) -> dict[str, object]:
            return {"skills": [{"name": "pdf", "description": "Read PDFs"}]}

    registry = RecordingRegistry()
    manager = FakeSkillManager()
    def create_gateway(**kwargs: object) -> object:
        gateway_options.append(kwargs)
        return gateway

    monkeypatch.setattr(runtime, "ModelGateway", create_gateway)
    monkeypatch.setattr(runtime, "create_builtin_registry", lambda path: registry)
    monkeypatch.setattr(runtime, "SkillManager", lambda path: manager)
    monkeypatch.setattr(
        runtime, "create_skill_tools", lambda built_manager: skill_tools
    )

    agent = runtime.create_agent_runtime(
        model="test-model",
        api_mode="responses",
        workspace=tmp_path,
        confirm=lambda request: False,
        max_model_calls=13,
        system_prompt="Base prompt",
        base_url="https://provider.example/v1",
    )

    assert agent._gateway is gateway
    assert gateway_options == [
        {
            "model": "test-model",
            "api_mode": "responses",
            "base_url": "https://provider.example/v1",
        }
    ]
    assert agent._registry is registry
    assert agent._max_model_calls == 13
    assert registry.registered == [skill_tools]
    assert agent._system_message is not None
    assert agent._system_message.content == (
        "Base prompt\n\n"
        "**Available workspace Skills**:\n"
        "- *pdf*: Read PDFs\n\n"
        "When a Skill matches the task, activate it with activate_skill and "
        "follow its instructions."
    )


def test_runtime_registers_mcp_management_tools_when_configured(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime, "ModelGateway", lambda **kwargs: object())
    directory = tmp_path / ".cdy-agent"
    directory.mkdir()
    (directory / "mcp.yaml").write_text(
        """
version: 1
servers:
  demo:
    description: Demo MCP server
    transport: stdio
    command: demo
""",
        encoding="utf-8",
    )
    agent = runtime.create_agent_runtime(
        model="test-model",
        api_mode="responses",
        workspace=tmp_path,
        confirm=lambda request: False,
        max_model_calls=2,
        system_prompt="Base",
    )
    try:
        names = [definition["name"] for definition in agent._registry.definitions]
        assert "connect_mcp_server" in names
        assert "get_mcp_prompt" in names
        assert "Configured MCP servers" in agent._system_message.content
    finally:
        agent.close()
