from __future__ import annotations

from pathlib import Path

from cdy_agent.mcp.config import McpConfig, McpServerConfig
from cdy_agent.mcp.manager import McpManager
from cdy_agent.mcp.tools import create_mcp_tools
from cdy_agent.tools.registry import ToolRegistry


def test_management_tools_are_valid_and_deterministic(tmp_path: Path) -> None:
    config = McpConfig((McpServerConfig("demo", "Demo", "stdio", command="demo"),))
    registry = ToolRegistry([])
    manager = McpManager(tmp_path, config, registry)
    try:
        result = registry.register_many(create_mcp_tools(manager))
        assert result.ok
        assert [item["name"] for item in registry.definitions] == [
            "list_mcp_servers",
            "connect_mcp_server",
            "disconnect_mcp_server",
            "list_mcp_resources",
            "list_mcp_resource_templates",
            "read_mcp_resource",
            "list_mcp_prompts",
            "get_mcp_prompt",
        ]
    finally:
        manager.close()
