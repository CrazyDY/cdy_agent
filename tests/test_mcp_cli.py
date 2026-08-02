from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cdy_agent import cli
from cdy_agent.cli import app
from cdy_agent.tools.base import PreparedToolExecution, ToolResult

runner = CliRunner()


def _write_config(workspace: Path) -> None:
    directory = workspace / ".cdy-agent"
    directory.mkdir()
    (directory / "mcp.yaml").write_text(
        """
version: 1
servers:
  demo:
    description: Demo server
    transport: stdio
    command: demo-command
    env_from: {TOKEN: MCP_DEMO_TOKEN}
""",
        encoding="utf-8",
    )


def test_mcp_servers_lists_only_safe_configuration(tmp_path: Path) -> None:
    _write_config(tmp_path)
    result = runner.invoke(app, ["mcp", "servers", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert "demo  stdio  credentials=MCP_DEMO_TOKEN" in result.stdout
    assert "demo-command" not in result.stdout


def test_mcp_check_confirms_connects_and_closes(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    events: list[str] = []

    class FakeManager:
        def __init__(self, *args: object) -> None:
            events.append("create")

        def prepare_connection(self, name: str) -> PreparedToolExecution:
            return PreparedToolExecution(
                True,
                "Start the demo MCP server.",
                lambda: (
                    events.append("connect")
                    or ToolResult.success(
                        {
                            "name": name,
                            "status": "connected",
                            "protocol_version": "test",
                            "capabilities": ["tools"],
                            "tools": [{"name": "mcp_demo_echo"}],
                        }
                    )
                ),
            )

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(cli, "McpManager", FakeManager)
    result = runner.invoke(
        app, ["mcp", "check", "demo", "--workspace", str(tmp_path)], input="y\n"
    )
    assert result.exit_code == 0
    assert "protocol_version: test" in result.stdout
    assert events == ["create", "connect", "close"]


def test_mcp_check_denial_never_connects(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    events: list[str] = []

    class FakeManager:
        def __init__(self, *args: object) -> None:
            pass

        def prepare_connection(self, name: str) -> PreparedToolExecution:
            return PreparedToolExecution(
                True,
                "Start the demo MCP server.",
                lambda: events.append("connect") or ToolResult.success({}),
            )

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(cli, "McpManager", FakeManager)
    result = runner.invoke(
        app, ["mcp", "check", "demo", "--workspace", str(tmp_path)], input="n\n"
    )
    assert result.exit_code == 1
    assert "declined" in result.stderr
    assert events == ["close"]
