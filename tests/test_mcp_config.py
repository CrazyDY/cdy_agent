from __future__ import annotations

from pathlib import Path

import pytest

from cdy_agent.mcp.config import McpConfig, load_mcp_config


def _write(workspace: Path, content: str) -> None:
    directory = workspace / ".cdy-agent"
    directory.mkdir()
    (directory / "mcp.yaml").write_text(content, encoding="utf-8")


def test_missing_mcp_config_is_empty(tmp_path: Path) -> None:
    assert load_mcp_config(tmp_path) == McpConfig()


def test_loads_strict_stdio_and_http_servers(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
version: 1
servers:
  local:
    description: Local tools
    transport: stdio
    command: python
    args: [server.py]
    env_from:
      TOKEN: SOURCE_TOKEN
  remote:
    description: Remote tools
    transport: streamable_http
    url: https://example.test/mcp
    headers_from:
      Authorization: MCP_AUTH
""",
    )

    config = load_mcp_config(tmp_path)

    assert [server.name for server in config.servers] == ["local", "remote"]
    assert config.servers[0].args == ("server.py",)
    assert config.servers[1].headers_from == {"Authorization": "MCP_AUTH"}


@pytest.mark.parametrize(
    "body, message",
    [
        ("version: 2\nservers: {}\n", "version must be 1"),
        (
            "version: 1\nservers:\n  Bad-Name:\n    description: x\n    transport: stdio\n    command: x\n",
            "server names",
        ),
        (
            "version: 1\nservers:\n  x:\n    description: x\n    transport: streamable_http\n    url: http://example.test/mcp\n",
            "must use HTTPS",
        ),
        (
            "version: 1\nservers:\n  x:\n    description: x\n    transport: streamable_http\n    url: http://127.0.0.1/mcp\n    headers_from: {Authorization: TOKEN}\n",
            "cannot receive configured headers",
        ),
        (
            "version: 1\nservers:\n  x:\n    description: x\n    transport: stdio\n    command: x\n    surprise: true\n",
            "Unsupported MCP config key",
        ),
        (
            "version: 1\nservers:\n  x:\n    description: one\n    description: two\n    transport: stdio\n    command: x\n",
            "duplicate key",
        ),
    ],
)
def test_rejects_unsafe_or_unknown_configuration(
    tmp_path: Path, body: str, message: str
) -> None:
    _write(tmp_path, body)
    with pytest.raises(ValueError, match=message):
        load_mcp_config(tmp_path)


def test_missing_secret_error_names_variable_but_not_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_TEST_SECRET", raising=False)
    _write(
        tmp_path,
        """
version: 1
servers:
  x:
    description: x
    transport: stdio
    command: x
    env_from: {TOKEN: MCP_TEST_SECRET}
""",
    )
    server = load_mcp_config(tmp_path).servers[0]
    with pytest.raises(ValueError, match="MCP_TEST_SECRET"):
        server.resolved_environment()
