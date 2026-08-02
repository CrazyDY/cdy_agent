from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cdy_agent.mcp.config import McpConfig, McpServerConfig
from cdy_agent.mcp.manager import (
    MAX_RESULT_BYTES,
    McpManager,
    _model_payload,
    _tool_result,
)
from cdy_agent.tools.base import ConfirmationDecision, ToolCall
from cdy_agent.tools.registry import ToolRegistry


class _Result:
    def __init__(self, payload: dict[str, Any], *, is_error: bool = False) -> None:
        self.payload = payload
        self.is_error = is_error

    def model_dump(self, **kwargs: object) -> dict[str, Any]:
        return self.payload


class _EmptySubscription:
    async def __aenter__(self) -> _EmptySubscription:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def __aiter__(self) -> _EmptySubscription:
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


class _FakeClient:
    server_info = None
    protocol_version = "test-version"
    instructions = "Use the test tool."
    server_capabilities = SimpleNamespace(
        tools=object(),
        resources=object(),
        prompts=object(),
        completions=None,
        logging=None,
    )

    def __init__(self, transport: object, **kwargs: object) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def list_tools(self, **kwargs: object) -> object:
        tool = SimpleNamespace(
            name="Echo-Tool",
            title=None,
            description="Echo remotely.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
        return SimpleNamespace(tools=[tool], next_cursor=None)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _Result:
        self.calls.append((name, arguments))
        return _Result(
            {"content": [{"type": "text", "text": arguments["text"]}], "isError": False}
        )

    def listen(self, **kwargs: object) -> _EmptySubscription:
        return _EmptySubscription()


def test_connect_registers_namespaced_tool_and_requires_approval(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("cdy_agent.mcp.manager.Client", _FakeClient)
    registry = ToolRegistry([])
    config = McpConfig(
        (
            McpServerConfig(
                name="remote",
                description="Remote",
                transport="streamable_http",
                url="https://example.test/mcp",
            ),
        )
    )
    manager = McpManager(tmp_path, config, registry)
    try:
        connected = manager.connect("remote")
        assert connected.ok
        mapping = connected.data["tools"][0]
        assert mapping["original_name"] == "Echo-Tool"
        assert mapping["name"].startswith("mcp_remote_echo_tool_")

        denied = registry.execute(
            ToolCall("1", mapping["name"], '{"text":"hello"}'),
            lambda request: ConfirmationDecision.DENY,
        )
        assert denied.code == "approval_denied"

        allowed = registry.execute(
            ToolCall("2", mapping["name"], '{"text":"hello"}'),
            lambda request: ConfirmationDecision.ALLOW_ONCE,
        )
        assert allowed.ok
        assert allowed.data["content"][0]["text"] == "hello"

        assert manager.disconnect("remote").ok
        assert all(item["name"] != mapping["name"] for item in registry.definitions)
    finally:
        manager.close()


def test_close_is_idempotent_without_starting_loop(tmp_path: Path) -> None:
    manager = McpManager(tmp_path, McpConfig(), ToolRegistry([]))
    manager.close()
    manager.close()


def test_result_limit_and_remote_error_are_structured() -> None:
    too_large = _model_payload(_Result({"text": "x" * MAX_RESULT_BYTES}))
    assert too_large.code == "mcp_result_too_large"

    remote_error = _tool_result(_Result({"content": []}, is_error=True))
    assert remote_error.code == "mcp_tool_error"


def test_real_stdio_server_handshake_call_and_cleanup(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(
        """
from mcp.server import MCPServer

server = MCPServer("stdio-test")

@server.tool()
def echo(text: str) -> str:
    return text

@server.resource("test://greeting")
def greeting() -> str:
    return "hello resource"

@server.resource("test://users/{name}")
def user(name: str) -> str:
    return name

@server.prompt()
def greet(name: str) -> str:
    return f"Say hello to {name}."

server.run()
""",
        encoding="utf-8",
    )
    registry = ToolRegistry([])
    config = McpConfig(
        (
            McpServerConfig(
                name="local",
                description="Local test",
                transport="stdio",
                command=sys.executable,
                args=(str(script),),
                connect_timeout_seconds=20,
            ),
        )
    )
    manager = McpManager(tmp_path, config, registry)
    try:
        connected = manager.connect("local")
        assert connected.ok, connected
        name = connected.data["tools"][0]["name"]
        result = registry.execute(
            ToolCall("1", name, '{"text":"stdio works"}'),
            lambda request: ConfirmationDecision.ALLOW_ONCE,
        )
        assert result.ok
        assert result.data["structuredContent"] == {"result": "stdio works"}

        resources = manager.list_resources("local", None)
        assert resources.ok
        assert resources.data["resources"][0]["uri"] == "test://greeting"
        resource = manager.read_resource("local", "test://greeting")
        assert resource.ok
        assert resource.data["contents"][0]["text"] == "hello resource"
        templates = manager.list_resource_templates("local", None)
        assert templates.ok
        assert (
            templates.data["resourceTemplates"][0]["uriTemplate"]
            == "test://users/{name}"
        )

        prompts = manager.list_prompts("local", None)
        assert prompts.ok
        assert prompts.data["prompts"][0]["name"] == "greet"
        prompt = manager.get_prompt("local", "greet", {"name": "Ada"})
        assert prompt.ok
        assert prompt.data["messages"][0]["content"]["text"] == "Say hello to Ada."
    finally:
        manager.close()
