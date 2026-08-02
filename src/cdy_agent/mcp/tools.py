"""Agent-facing MCP management, context, and remote tool adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cdy_agent.run_control import RunControl
from cdy_agent.tools.base import PreparedToolExecution, ToolResult

from .manager import McpManager

_SERVER_SCHEMA = {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$"}


@dataclass
class ListMcpServersTool:
    manager: McpManager
    name: str = "list_mcp_servers"
    description: str = (
        "List configured MCP servers, connection status, and negotiated capabilities."
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return None if not arguments else _invalid("No arguments are accepted.")

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "List MCP servers."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or ToolResult.success(self.manager.list_servers())


@dataclass
class ConnectMcpServerTool:
    manager: McpManager
    name: str = "connect_mcp_server"
    description: str = (
        "Connect one configured MCP server after user approval and load its tools."
    )
    parameters: dict[str, Any] = field(default_factory=lambda: _server_parameters())
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_server(arguments, self.manager)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        description = self.manager.connection_description(
            str(arguments.get("server", ""))
        )
        return (
            description
            if isinstance(description, str)
            else "Connect the requested MCP server."
        )

    def prepare_execution(
        self, arguments: dict[str, Any]
    ) -> PreparedToolExecution | ToolResult:
        return self._prepare(arguments, None)

    def prepare_execution_with_control(
        self, arguments: dict[str, Any], run_control: RunControl
    ) -> PreparedToolExecution | ToolResult:
        return self._prepare(arguments, run_control)

    def _prepare(
        self, arguments: dict[str, Any], run_control: RunControl | None
    ) -> PreparedToolExecution | ToolResult:
        invalid = self.preflight(arguments)
        if invalid is not None:
            return invalid
        return self.manager.prepare_connection(arguments["server"], run_control)

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or self.manager.connect(arguments["server"])


@dataclass
class DisconnectMcpServerTool:
    manager: McpManager
    name: str = "disconnect_mcp_server"
    description: str = "Disconnect one MCP server and remove its dynamic tools."
    parameters: dict[str, Any] = field(default_factory=lambda: _server_parameters())
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_server(arguments, self.manager)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Disconnect MCP server {arguments.get('server', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or self.manager.disconnect(arguments["server"])


@dataclass
class _PagedMcpTool:
    manager: McpManager
    name: str
    description: str
    operation: str
    parameters: dict[str, Any] = field(default_factory=lambda: _paged_parameters())
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if set(arguments) - {"server", "cursor"}:
            return _invalid("server is required and cursor is optional.")
        invalid = _validate_server({"server": arguments.get("server")}, self.manager)
        cursor = arguments.get("cursor")
        if invalid is not None or (cursor is not None and not isinstance(cursor, str)):
            return _invalid("server is required and cursor must be text when provided.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Read {self.operation} from MCP server {arguments.get('server', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        if invalid:
            return invalid
        method = getattr(self.manager, self.operation)
        return method(arguments["server"], arguments.get("cursor"))


@dataclass
class ReadMcpResourceTool:
    manager: McpManager
    name: str = "read_mcp_resource"
    description: str = "Read one resource URI from a connected MCP server."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server": _SERVER_SCHEMA,
                "uri": {"type": "string", "minLength": 1},
            },
            "required": ["server", "uri"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if set(arguments) != {"server", "uri"}:
            return _invalid("server and uri are required.")
        invalid = _validate_server({"server": arguments.get("server")}, self.manager)
        if invalid or not isinstance(arguments.get("uri"), str) or not arguments["uri"]:
            return _invalid("server and non-empty uri are required.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Read one resource from MCP server {arguments.get('server', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or self.manager.read_resource(
            arguments["server"], arguments["uri"]
        )


@dataclass
class GetMcpPromptTool:
    manager: McpManager
    name: str = "get_mcp_prompt"
    description: str = "Get one prompt template result from a connected MCP server."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server": _SERVER_SCHEMA,
                "name": {"type": "string", "minLength": 1},
                "arguments": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["server", "name"],
            "additionalProperties": False,
        }
    )
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if set(arguments) - {"server", "name", "arguments"}:
            return _invalid("server and name are required; arguments is optional.")
        invalid = _validate_server({"server": arguments.get("server")}, self.manager)
        name = arguments.get("name")
        values = arguments.get("arguments")
        if (
            invalid
            or not isinstance(name, str)
            or not name
            or (
                values is not None
                and (
                    not isinstance(values, dict)
                    or any(
                        not isinstance(k, str) or not isinstance(v, str)
                        for k, v in values.items()
                    )
                )
            )
        ):
            return _invalid(
                "server and name are required; arguments values must be text."
            )
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        prompt = arguments.get("name", "")
        server = arguments.get("server", "")
        return f"Get prompt {prompt} from MCP server {server}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or self.manager.get_prompt(
            arguments["server"], arguments["name"], arguments.get("arguments")
        )


@dataclass
class RemoteMcpTool:
    manager: McpManager
    server: str
    remote_name: str
    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool = field(default=True, init=False)

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Call MCP tool {self.remote_name!r} on server {self.server!r}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self.manager.call_tool(self.server, self.remote_name, arguments)

    def execute_with_control(
        self, arguments: dict[str, Any], run_control: RunControl
    ) -> ToolResult:
        return self.manager.call_tool(
            self.server, self.remote_name, arguments, run_control
        )


def create_mcp_tools(manager: McpManager) -> tuple[Any, ...]:
    return (
        ListMcpServersTool(manager),
        ConnectMcpServerTool(manager),
        DisconnectMcpServerTool(manager),
        _PagedMcpTool(
            manager,
            "list_mcp_resources",
            "List resources from a connected MCP server.",
            "list_resources",
        ),
        _PagedMcpTool(
            manager,
            "list_mcp_resource_templates",
            "List resource templates from a connected MCP server.",
            "list_resource_templates",
        ),
        ReadMcpResourceTool(manager),
        _PagedMcpTool(
            manager,
            "list_mcp_prompts",
            "List prompts from a connected MCP server.",
            "list_prompts",
        ),
        GetMcpPromptTool(manager),
    )


def _validate_server(
    arguments: dict[str, Any], manager: McpManager
) -> ToolResult | None:
    if set(arguments) != {"server"} or not isinstance(arguments.get("server"), str):
        return _invalid("server is required with no additional arguments.")
    if manager.config.get(arguments["server"]) is None:
        return ToolResult.failure(
            "unknown_mcp_server", f"Unknown MCP server: {arguments['server']}."
        )
    return None


def _server_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"server": _SERVER_SCHEMA},
        "required": ["server"],
        "additionalProperties": False,
    }


def _paged_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"server": _SERVER_SCHEMA, "cursor": {"type": "string"}},
        "required": ["server"],
        "additionalProperties": False,
    }


def _invalid(message: str) -> ToolResult:
    return ToolResult.failure("invalid_arguments", message)
