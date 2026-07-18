from __future__ import annotations

import json
import re
from collections.abc import Iterable

from .base import ConfirmationCallback, ConfirmationRequest, Tool, ToolCall, ToolResult

TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @property
    def definitions(self) -> tuple[dict[str, object], ...]:
        return tuple({
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        } for tool in self._tools.values())

    def register_many(self, tools: Iterable[Tool]) -> ToolResult:
        try:
            candidates = tuple(tools)
        except (TypeError, RuntimeError):
            return ToolResult.failure("invalid_tools", "Tool factory must return an iterable.")
        names: list[str] = []
        for tool in candidates:
            if not _valid_tool(tool):
                return ToolResult.failure("invalid_tools", "Skill returned an invalid tool.")
            names.append(tool.name)
        if len(names) != len(set(names)) or any(name in self._tools for name in names):
            return ToolResult.failure(
                "tool_name_conflict", "Tool name conflicts with an existing tool."
            )
        self._tools.update(zip(names, candidates))
        return ToolResult.success({"names": names})

    def execute(self, call: ToolCall, confirm: ConfirmationCallback) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.failure("unknown_tool", f"Unknown tool: {call.name}.")
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            return ToolResult.failure("invalid_arguments", "Arguments must be valid JSON.")
        if not isinstance(arguments, dict):
            return ToolResult.failure("invalid_arguments", "Arguments must be a JSON object.")
        invalid = tool.preflight(arguments)
        if invalid is not None:
            return invalid
        if tool.requires_confirmation:
            request = ConfirmationRequest(
                tool.name,
                arguments,
                tool.confirmation_description(arguments),
            )
            if not confirm(request):
                return ToolResult.failure("approval_denied", "User declined this tool call.")
        return tool.execute(arguments)


def _valid_tool(tool: object) -> bool:
    return (
        isinstance(getattr(tool, "name", None), str)
        and TOOL_NAME_PATTERN.fullmatch(tool.name) is not None
        and isinstance(getattr(tool, "description", None), str)
        and bool(tool.description)
        and isinstance(getattr(tool, "parameters", None), dict)
        and isinstance(getattr(tool, "requires_confirmation", None), bool)
        and callable(getattr(tool, "preflight", None))
        and callable(getattr(tool, "confirmation_description", None))
        and callable(getattr(tool, "execute", None))
    )
