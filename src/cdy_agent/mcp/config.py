"""Strict workspace MCP client configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

MCP_CONFIG_RELATIVE_PATH = Path(".cdy-agent") / "mcp.yaml"
SERVER_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
ENVIRONMENT_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
MAX_SERVERS = 16
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60


class _McpConfigLoader(yaml.SafeLoader):
    pass


def _unique_mapping(
    loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False
) -> dict[object, object]:
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, "expected a mapping node", node.start_mark
        )
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_McpConfigLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    description: str
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env_from: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers_from: dict[str, str] = field(default_factory=dict)
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS

    def resolved_environment(self) -> dict[str, str]:
        """Resolve configured secret references without including values in errors."""
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for target, source in self.env_from.items():
            value = os.getenv(source)
            if value is None:
                missing.append(source)
            else:
                resolved[target] = value
        if missing:
            raise ValueError(
                "Missing MCP environment variable(s): "
                + ", ".join(sorted(missing))
                + "."
            )
        return resolved

    def resolved_headers(self) -> dict[str, str]:
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for header, source in self.headers_from.items():
            value = os.getenv(source)
            if value is None:
                missing.append(source)
            else:
                resolved[header] = value
        if missing:
            raise ValueError(
                "Missing MCP environment variable(s): "
                + ", ".join(sorted(missing))
                + "."
            )
        return resolved


@dataclass(frozen=True)
class McpConfig:
    servers: tuple[McpServerConfig, ...] = ()

    def get(self, name: str) -> McpServerConfig | None:
        return next((server for server in self.servers if server.name == name), None)


def load_mcp_config(workspace: Path) -> McpConfig:
    """Load optional MCP configuration without creating workspace state."""
    path = workspace / MCP_CONFIG_RELATIVE_PATH
    if not path.exists():
        return McpConfig()
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_McpConfigLoader)
    except OSError as exc:
        raise ValueError("Could not read MCP config.") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid MCP config YAML: {exc}") from None
    if not isinstance(raw, dict) or set(raw) != {"version", "servers"}:
        raise ValueError("MCP config must contain only version and servers.")
    if raw["version"] != 1:
        raise ValueError("MCP config version must be 1.")
    raw_servers = raw["servers"]
    if not isinstance(raw_servers, dict):
        raise ValueError("MCP config servers must be a mapping.")
    if len(raw_servers) > MAX_SERVERS:
        raise ValueError(f"MCP config supports at most {MAX_SERVERS} servers.")
    servers = tuple(_parse_server(name, value) for name, value in raw_servers.items())
    return McpConfig(servers)


def _parse_server(name: object, raw: object) -> McpServerConfig:
    if not isinstance(name, str) or SERVER_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("MCP server names must match [a-z][a-z0-9_]{0,31}.")
    if not isinstance(raw, dict):
        raise ValueError(f"MCP server {name!r} must be a mapping.")
    description = _required_text(raw.get("description"), f"servers.{name}.description")
    transport = raw.get("transport")
    common = {
        "description",
        "transport",
        "connect_timeout_seconds",
        "request_timeout_seconds",
    }
    connect_timeout = _timeout(raw.get("connect_timeout_seconds", 15), name)
    request_timeout = _timeout(raw.get("request_timeout_seconds", 60), name)
    if transport == "stdio":
        allowed = common | {"command", "args", "env_from"}
        _reject_unknown(raw, allowed, name)
        command = _required_text(raw.get("command"), f"servers.{name}.command")
        args = _string_list(raw.get("args", []), f"servers.{name}.args")
        env_from = _mapping(raw.get("env_from", {}), name, headers=False)
        return McpServerConfig(
            name=name,
            description=description,
            transport=transport,
            command=command,
            args=args,
            env_from=env_from,
            connect_timeout_seconds=connect_timeout,
            request_timeout_seconds=request_timeout,
        )
    if transport == "streamable_http":
        allowed = common | {"url", "headers_from"}
        _reject_unknown(raw, allowed, name)
        url = _validate_url(raw.get("url"), name)
        headers = _mapping(raw.get("headers_from", {}), name, headers=True)
        if urlsplit(url).scheme == "http" and headers:
            raise ValueError(
                "Loopback HTTP MCP servers cannot receive configured headers."
            )
        return McpServerConfig(
            name=name,
            description=description,
            transport=transport,
            url=url,
            headers_from=headers,
            connect_timeout_seconds=connect_timeout,
            request_timeout_seconds=request_timeout,
        )
    raise ValueError(f"MCP server {name!r} transport must be stdio or streamable_http.")


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"MCP config {field_name} must be non-empty text.")
    return value.strip()


def _timeout(value: object, server: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 300:
        raise ValueError(
            f"MCP server {server!r} timeouts must be between 1 and 300 seconds."
        )
    return value


def _string_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError(
            f"MCP config {field_name} must be a list of at most 128 strings."
        )
    if any(not isinstance(item, str) or "\x00" in item for item in value):
        raise ValueError(
            f"MCP config {field_name} must contain strings without NUL bytes."
        )
    return tuple(value)


def _mapping(value: object, server: str, *, headers: bool) -> dict[str, str]:
    label = "headers_from" if headers else "env_from"
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError(
            f"MCP server {server!r} {label} must be a mapping of at most 64 entries."
        )
    result: dict[str, str] = {}
    for target, source in value.items():
        target_pattern = HEADER_NAME_PATTERN if headers else ENVIRONMENT_NAME_PATTERN
        if (
            not isinstance(target, str)
            or target_pattern.fullmatch(target) is None
            or not isinstance(source, str)
            or ENVIRONMENT_NAME_PATTERN.fullmatch(source) is None
        ):
            raise ValueError(f"MCP server {server!r} has an invalid {label} entry.")
        result[target] = source
    return result


def _validate_url(value: object, server: str) -> str:
    url = _required_text(value, f"servers.{server}.url")
    parsed = urlsplit(url)
    try:
        parsed.port
    except ValueError:
        raise ValueError(f"MCP server {server!r} has an invalid URL port.") from None
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ValueError(f"MCP server {server!r} has an invalid URL.")
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname.lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return url
    raise ValueError("Remote MCP URLs must use HTTPS; HTTP is limited to loopback.")


def _reject_unknown(raw: dict[object, object], allowed: set[str], server: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        names = ", ".join(sorted(str(item) for item in unknown))
        raise ValueError(f"Unsupported MCP config key for {server!r}: {names}.")
