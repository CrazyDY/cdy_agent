from __future__ import annotations

from pathlib import Path

from cdy_agent import runtime
from cdy_agent.tools.base import ToolResult


def test_runtime_registers_skill_tools_and_catalog(
    monkeypatch, tmp_path: Path
) -> None:
    """Omitting Skill composition would hide available Skills from the model."""
    gateway = object()
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
    monkeypatch.setattr(runtime, "ModelGateway", lambda **kwargs: gateway)
    monkeypatch.setattr(runtime, "create_builtin_registry", lambda path: registry)
    monkeypatch.setattr(runtime, "SkillManager", lambda path: manager)
    monkeypatch.setattr(runtime, "create_skill_tools", lambda built_manager: skill_tools)

    agent = runtime.create_agent_runtime(
        model="test-model",
        api_mode="responses",
        workspace=tmp_path,
        confirm=lambda request: False,
        system_prompt="Base prompt",
    )

    assert agent._gateway is gateway
    assert agent._registry is registry
    assert registry.registered == [skill_tools]
    assert agent._system_message is not None
    assert agent._system_message.content == (
        "Base prompt\n\n"
        "**Available workspace Skills**:\n"
        "- *pdf*: Read PDFs\n\n"
        "When a Skill matches the task, activate it with activate_skill and "
        "follow its instructions."
    )
