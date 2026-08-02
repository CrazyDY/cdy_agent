"""Persistent MCP client sessions behind the synchronous Agent tool boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import ListenNotSupportedError, SubscriptionLost

from cdy_agent.run_control import AgentRunCancelled, RunControl
from cdy_agent.tools.base import PreparedToolExecution, ToolResult
from cdy_agent.tools.process import sanitized_environment
from cdy_agent.tools.registry import TOOL_NAME_PATTERN, ToolRegistry

from .config import McpConfig, McpServerConfig

MAX_SERVER_TOOLS = 64
MAX_TOTAL_TOOLS = 256
MAX_PAGES = 100
MAX_RESULT_BYTES = 1024 * 1024


@dataclass
class _Connection:
    config: McpServerConfig
    client: Client
    stack: AsyncExitStack
    listener: asyncio.Task[None] | None = None


class McpManager:
    """Own MCP connection lifecycles on one private asyncio event loop."""

    def __init__(
        self, workspace: Path, config: McpConfig, registry: ToolRegistry
    ) -> None:
        self.workspace = workspace.resolve()
        self.config = config
        self.registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._connections: dict[str, _Connection] = {}
        self._tool_names: dict[str, dict[str, str]] = {}
        self._errors: dict[str, str] = {}
        self._disconnected: set[str] = set()
        self._closed = False

    def list_servers(self) -> dict[str, object]:
        return {
            "servers": [
                {
                    "name": server.name,
                    "description": server.description,
                    "transport": server.transport,
                    "status": self._server_status(server.name),
                    "capabilities": sorted(self._capabilities(server.name)),
                    "error": self._errors.get(server.name),
                }
                for server in self.config.servers
            ]
        }

    def connection_description(self, name: str) -> str | ToolResult:
        prepared = self._prepare_connection_data(name)
        if isinstance(prepared, ToolResult):
            return prepared
        server, _environment, _headers = prepared
        return self._describe_connection(server)

    def prepare_connection(
        self, name: str, run_control: RunControl | None = None
    ) -> PreparedToolExecution | ToolResult:
        prepared = self._prepare_connection_data(name)
        if isinstance(prepared, ToolResult):
            return prepared
        server, environment, headers = prepared
        return PreparedToolExecution(
            requires_confirmation=True,
            confirmation_description=self._describe_connection(server),
            execute=lambda: self._connect_sync(
                server, environment, headers, run_control
            ),
        )

    def _describe_connection(self, server: McpServerConfig) -> str:
        if server.transport == "stdio":
            argv = [server.command or "", *server.args]
            variables = sorted(server.env_from)
            return (
                f"Start MCP server '{server.name}' with argv {argv!r} in directory "
                f"{self.workspace}; pass environment variables {variables}."
            )
        assert server.url is not None
        origin = _origin(server.url)
        return (
            f"Connect to MCP server '{server.name}' at {origin}; send headers "
            f"{sorted(server.headers_from)}."
        )

    def connect(self, name: str, run_control: RunControl | None = None) -> ToolResult:
        prepared = self._prepare_connection_data(name)
        if isinstance(prepared, ToolResult):
            return prepared
        server, environment, headers = prepared
        return self._connect_sync(server, environment, headers, run_control)

    def _connect_sync(
        self,
        server: McpServerConfig,
        environment: dict[str, str],
        headers: dict[str, str],
        run_control: RunControl | None,
    ) -> ToolResult:
        name = server.name
        if name in self._connections and name not in self._disconnected:
            return ToolResult.success(self._connection_payload(name))
        if name in self._connections:
            try:
                self._submit(self._disconnect(name), 10, run_control)
            except Exception:
                return ToolResult.failure(
                    "mcp_disconnect_failed",
                    "Could not clear the disconnected MCP session.",
                )
        try:
            return self._submit(
                self._connect(server, environment, headers),
                server.connect_timeout_seconds,
                run_control,
            )
        except AgentRunCancelled:
            raise
        except TimeoutError:
            return ToolResult.failure(
                "mcp_connect_timeout", "MCP connection timed out."
            )
        except Exception as exc:
            self._errors[name] = type(exc).__name__
            return ToolResult.failure(
                "mcp_connect_failed", "Could not connect to the MCP server."
            )

    def _prepare_connection_data(
        self, name: str
    ) -> tuple[McpServerConfig, dict[str, str], dict[str, str]] | ToolResult:
        server = self.config.get(name)
        if server is None:
            return ToolResult.failure(
                "unknown_mcp_server", f"Unknown MCP server: {name}."
            )
        try:
            environment = server.resolved_environment()
            headers = server.resolved_headers()
        except ValueError as exc:
            return ToolResult.failure("mcp_credentials_missing", str(exc))
        if server.transport == "stdio":
            assert server.command is not None
            resolved = shutil.which(
                server.command, path=sanitized_environment().get("PATH")
            )
            if resolved is None:
                return ToolResult.failure(
                    "mcp_executable_not_found",
                    f"Could not resolve MCP executable for server {name!r}.",
                )
            server = replace(server, command=str(Path(resolved).resolve()))
        return server, environment, headers

    def disconnect(self, name: str) -> ToolResult:
        if self.config.get(name) is None:
            return ToolResult.failure(
                "unknown_mcp_server", f"Unknown MCP server: {name}."
            )
        if name not in self._connections:
            return ToolResult.success({"name": name, "status": "disconnected"})
        try:
            self._submit(self._disconnect(name), 10, None)
        except Exception:
            return ToolResult.failure(
                "mcp_disconnect_failed", "Could not close the MCP server cleanly."
            )
        return ToolResult.success({"name": name, "status": "disconnected"})

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        run_control: RunControl | None = None,
    ) -> ToolResult:
        return self._request(
            server,
            lambda client: client.call_tool(tool, arguments),
            run_control,
            error_code="mcp_tool_failed",
            transform=_tool_result,
        )

    def list_resources(self, server: str, cursor: str | None) -> ToolResult:
        return self._request(
            server,
            lambda client: client.list_resources(cursor=cursor, cache_mode="refresh"),
            transform=_model_payload,
        )

    def list_resource_templates(self, server: str, cursor: str | None) -> ToolResult:
        return self._request(
            server,
            lambda client: client.list_resource_templates(
                cursor=cursor, cache_mode="refresh"
            ),
            transform=_model_payload,
        )

    def read_resource(self, server: str, uri: str) -> ToolResult:
        return self._request(
            server,
            lambda client: client.read_resource(uri, cache_mode="refresh"),
            transform=_model_payload,
        )

    def list_prompts(self, server: str, cursor: str | None) -> ToolResult:
        return self._request(
            server,
            lambda client: client.list_prompts(cursor=cursor, cache_mode="refresh"),
            transform=_model_payload,
        )

    def get_prompt(
        self, server: str, name: str, arguments: dict[str, str] | None
    ) -> ToolResult:
        return self._request(
            server,
            lambda client: client.get_prompt(name, arguments),
            transform=_model_payload,
        )

    def close(self) -> None:
        with self._start_lock:
            if self._closed:
                return
            self._closed = True
        if self._loop is not None:
            try:
                self._submit(self._disconnect_all(), 15, None, allow_closed=True)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _connect(
        self,
        server: McpServerConfig,
        environment: dict[str, str],
        headers: dict[str, str],
    ) -> ToolResult:
        if server.name in self._connections:
            return ToolResult.success(self._connection_payload(server.name))
        stack = AsyncExitStack()
        try:
            if server.transport == "stdio":
                errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                parameters = StdioServerParameters(
                    command=server.command or "",
                    args=list(server.args),
                    env=sanitized_environment() | environment,
                    cwd=self.workspace,
                )
                transport = stdio_client(parameters, errlog=errlog)
            else:
                http_client = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        headers=headers,
                        follow_redirects=False,
                        timeout=server.request_timeout_seconds,
                        trust_env=False,
                    )
                )
                transport = streamable_http_client(
                    server.url or "", http_client=http_client
                )
            client = Client(
                transport, read_timeout_seconds=server.request_timeout_seconds
            )
            await stack.enter_async_context(client)
            tools = await self._fetch_tools(client)
            adapters, mapping = self._build_adapters(server.name, tools)
            total = sum(len(names) for names in self._tool_names.values()) + len(
                mapping
            )
            if total > MAX_TOTAL_TOOLS:
                failure = ToolResult.failure(
                    "mcp_tool_limit",
                    f"MCP tools exceed the global limit of {MAX_TOTAL_TOOLS}.",
                )
                await stack.aclose()
                return failure
            registered = self.registry.replace_group(f"mcp:{server.name}", adapters)
            if not registered.ok:
                await stack.aclose()
                return registered
            connection = _Connection(server, client, stack)
            self._connections[server.name] = connection
            self._tool_names[server.name] = mapping
            self._errors.pop(server.name, None)
            self._disconnected.discard(server.name)
            connection.listener = asyncio.create_task(
                self._listen_for_tool_changes(server.name, client)
            )
            return ToolResult.success(self._connection_payload(server.name))
        except BaseException:
            await stack.aclose()
            raise

    async def _disconnect(self, name: str) -> None:
        connection = self._connections.pop(name, None)
        self._tool_names.pop(name, None)
        self._disconnected.discard(name)
        self.registry.remove_group(f"mcp:{name}")
        if connection is None:
            return
        if connection.listener is not None:
            connection.listener.cancel()
            await asyncio.gather(connection.listener, return_exceptions=True)
        await connection.stack.aclose()

    async def _disconnect_all(self) -> None:
        for name in tuple(self._connections):
            await self._disconnect(name)

    async def _fetch_tools(self, client: Client) -> tuple[Any, ...]:
        tools: list[Any] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            page = await client.list_tools(cursor=cursor, cache_mode="refresh")
            tools.extend(page.tools)
            if len(tools) > MAX_SERVER_TOOLS:
                raise ValueError(
                    f"MCP server exposes more than {MAX_SERVER_TOOLS} tools."
                )
            cursor = page.next_cursor
            if cursor is None:
                return tuple(tools)
        raise ValueError("MCP tool pagination exceeded the page limit.")

    def _build_adapters(
        self, server: str, tools: tuple[Any, ...]
    ) -> tuple[tuple[Any, ...], dict[str, str]]:
        from .tools import RemoteMcpTool

        used = set(self.registry_name_snapshot()) - set(
            self._tool_names.get(server, {})
        )
        adapters = []
        mapping: dict[str, str] = {}
        for tool in tools:
            original = tool.name
            local = _mapped_tool_name(server, original, used)
            used.add(local)
            parameters = tool.input_schema
            if (
                not isinstance(parameters, dict)
                or parameters.get("type", "object") != "object"
            ):
                raise ValueError("MCP tool input schema must describe an object.")
            description = tool.description or tool.title or f"Call MCP tool {original}."
            adapters.append(
                RemoteMcpTool(self, server, original, local, description, parameters)
            )
            mapping[local] = original
        return tuple(adapters), mapping

    def registry_name_snapshot(self) -> tuple[str, ...]:
        return tuple(definition["name"] for definition in self.registry.definitions)  # type: ignore[misc]

    async def _listen_for_tool_changes(self, server: str, client: Client) -> None:
        try:
            async with client.listen(tools_list_changed=True) as subscription:
                async for _event in subscription:
                    tools = await self._fetch_tools(client)
                    adapters, mapping = self._build_adapters(server, tools)
                    registered = self.registry.replace_group(f"mcp:{server}", adapters)
                    if registered.ok:
                        self._tool_names[server] = mapping
                        self._errors.pop(server, None)
                    else:
                        self._errors[server] = registered.code or "tool_refresh_failed"
        except asyncio.CancelledError:
            raise
        except ListenNotSupportedError:
            return
        except SubscriptionLost as exc:
            self._errors[server] = type(exc).__name__
            self._disconnected.add(server)
            self._tool_names.pop(server, None)
            self.registry.remove_group(f"mcp:{server}")
        except Exception as exc:
            self._errors[server] = type(exc).__name__

    def _request(
        self,
        server: str,
        operation: Any,
        run_control: RunControl | None = None,
        *,
        error_code: str = "mcp_request_failed",
        transform: Any = None,
    ) -> ToolResult:
        connection = self._connections.get(server)
        if connection is None or server in self._disconnected:
            return ToolResult.failure(
                "mcp_not_connected", f"MCP server {server!r} is not connected."
            )
        try:
            raw = self._submit(
                operation(connection.client),
                connection.config.request_timeout_seconds,
                run_control,
            )
            return (transform or _model_payload)(raw)
        except AgentRunCancelled:
            raise
        except TimeoutError:
            return ToolResult.failure("mcp_request_timeout", "MCP request timed out.")
        except Exception as exc:
            self._errors[server] = type(exc).__name__
            return ToolResult.failure(error_code, "MCP request failed.")

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._closed:
                raise RuntimeError("MCP manager is closed.")
            if self._loop is not None:
                return self._loop
            ready = threading.Event()

            def run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()
                loop.close()

            self._thread = threading.Thread(target=run, name="cdy-mcp", daemon=True)
            self._thread.start()
        ready.wait()
        assert self._loop is not None
        return self._loop

    def _submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
        timeout: int,
        run_control: RunControl | None,
        *,
        allow_closed: bool = False,
    ) -> Any:
        if allow_closed:
            loop = self._loop
            if loop is None:
                coroutine.close()
                return None
        else:
            loop = self._ensure_loop()
        assert loop is not None
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coroutine, loop)
        remaining = float(timeout)
        while remaining > 0:
            if run_control is not None:
                try:
                    run_control.raise_if_cancelled()
                except AgentRunCancelled:
                    future.cancel()
                    raise
            interval = min(0.1, remaining)
            try:
                return future.result(timeout=interval)
            except FutureTimeoutError:
                remaining -= interval
        future.cancel()
        raise TimeoutError

    def _connection_payload(self, name: str) -> dict[str, object]:
        connection = self._connections.get(name)
        client = connection.client if connection is not None else None
        info = getattr(client, "server_info", None)
        return {
            "name": name,
            "status": "connected",
            "protocol_version": getattr(client, "protocol_version", None),
            "server_info": _dump(info) if info is not None else None,
            "instructions": getattr(client, "instructions", None),
            "capabilities": sorted(self._capabilities(name)),
            "tools": [
                {"name": local, "original_name": original}
                for local, original in self._tool_names.get(name, {}).items()
            ],
        }

    def _capabilities(self, name: str) -> set[str]:
        connection = self._connections.get(name)
        capabilities = (
            getattr(connection.client, "server_capabilities", None)
            if connection
            else None
        )
        if capabilities is None:
            return set()
        return {
            item
            for item in ("tools", "resources", "prompts", "completions", "logging")
            if getattr(capabilities, item, None) is not None
        }

    def _server_status(self, name: str) -> str:
        if name in self._disconnected:
            return "disconnected"
        if name in self._connections:
            return "connected"
        return "configured"


def _mapped_tool_name(server: str, original: str, used: set[str]) -> str:
    preferred = f"mcp_{server}_{original}"
    if TOOL_NAME_PATTERN.fullmatch(preferred) is not None and preferred not in used:
        return preferred
    slug = re.sub(r"[^a-z0-9_]+", "_", original.lower()).strip("_") or "tool"
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
    prefix = f"mcp_{server}_"
    room = 64 - len(prefix) - len(digest) - 1
    candidate = f"{prefix}{slug[:room]}_{digest}"
    if candidate in used:
        digest = hashlib.sha256(f"{server}\0{original}".encode()).hexdigest()[:12]
        room = 64 - len(prefix) - len(digest) - 1
        candidate = f"{prefix}{slug[:room]}_{digest}"
    return candidate


def _tool_result(result: Any) -> ToolResult:
    payload = _dump(result)
    bounded = _bounded(payload)
    if isinstance(bounded, ToolResult):
        return bounded
    if bool(getattr(result, "is_error", False)):
        return ToolResult.failure(
            "mcp_tool_error", "MCP tool reported an error.", bounded
        )
    return ToolResult.success(bounded)


def _model_payload(result: Any) -> ToolResult:
    bounded = _bounded(_dump(result))
    return bounded if isinstance(bounded, ToolResult) else ToolResult.success(bounded)


def _bounded(payload: Any) -> Any | ToolResult:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_RESULT_BYTES:
        return ToolResult.failure(
            "mcp_result_too_large", "MCP result exceeds the 1 MiB output limit."
        )
    return payload


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"
