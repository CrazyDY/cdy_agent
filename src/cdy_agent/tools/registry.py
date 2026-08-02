from __future__ import annotations

import json
import re
from collections.abc import Iterable
from copy import deepcopy
from threading import RLock

from ..run_control import AgentRunCancelled, RunControl
from .base import (
    ConfirmationCallback,
    ConfirmationDecision,
    ConfirmationRequest,
    PreparedToolExecution,
    Tool,
    ToolCall,
    ToolResult,
)

TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._groups: dict[str, set[str]] = {}
        self._lock = RLock()

    @property
    def definitions(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in self._tools.values()
            )

    def register_many(self, tools: Iterable[Tool]) -> ToolResult:
        try:
            candidates = tuple(tools)
        except (TypeError, RuntimeError):
            return ToolResult.failure(
                "invalid_tools",
                "Tool factory must return an iterable.",
            )
        names: list[str] = []
        for tool in candidates:
            if not _valid_tool(tool):
                return ToolResult.failure(
                    "invalid_tools",
                    "Skill returned an invalid tool.",
                )
            names.append(tool.name)
        with self._lock:
            if len(names) != len(set(names)) or any(
                name in self._tools for name in names
            ):
                return ToolResult.failure(
                    "tool_name_conflict",
                    "Tool name conflicts with an existing tool.",
                )
            self._tools.update(zip(names, candidates))
        return ToolResult.success({"names": names})

    def replace_group(self, group: str, tools: Iterable[Tool]) -> ToolResult:
        """Atomically replace one dynamic group without disturbing built-ins."""
        try:
            candidates = tuple(tools)
        except (TypeError, RuntimeError):
            return ToolResult.failure(
                "invalid_tools", "Tool factory must return an iterable."
            )
        names = [getattr(tool, "name", None) for tool in candidates]
        if any(not _valid_tool(tool) for tool in candidates):
            return ToolResult.failure(
                "invalid_tools", "Dynamic source returned an invalid tool."
            )
        if len(names) != len(set(names)):
            return ToolResult.failure(
                "tool_name_conflict", "Tool names conflict within the dynamic group."
            )
        with self._lock:
            old_names = self._groups.get(group, set())
            conflicts = set(names) & (set(self._tools) - old_names)
            if conflicts:
                return ToolResult.failure(
                    "tool_name_conflict", "Tool name conflicts with an existing tool."
                )
            updated = dict(self._tools)
            for name in old_names:
                updated.pop(name, None)
            updated.update(zip(names, candidates))
            self._tools = updated
            self._groups[group] = set(names)
        return ToolResult.success({"names": names})

    def remove_group(self, group: str) -> None:
        with self._lock:
            names = self._groups.pop(group, set())
            if not names:
                return
            self._tools = {
                name: tool for name, tool in self._tools.items() if name not in names
            }

    def execute(
        self,
        call: ToolCall,
        confirm: ConfirmationCallback,
        *,
        run_control: RunControl | None = None,
    ) -> ToolResult:
        if run_control is not None:
            run_control.raise_if_cancelled()
        with self._lock:
            tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.failure(
                "unknown_tool",
                f"Unknown tool: {call.name}.",
            )
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            return ToolResult.failure(
                "invalid_arguments",
                "Arguments must be valid JSON.",
            )
        if not isinstance(arguments, dict):
            return ToolResult.failure(
                "invalid_arguments",
                "Arguments must be a JSON object.",
            )
        prepare_with_control = getattr(tool, "prepare_execution_with_control", None)
        prepare_execution = getattr(tool, "prepare_execution", None)
        if run_control is not None and callable(prepare_with_control):
            prepared = prepare_with_control(arguments, run_control)
            has_prepared_execution = True
        elif callable(prepare_execution):
            prepared = prepare_execution(arguments)
            has_prepared_execution = True
        else:
            prepared = None
            has_prepared_execution = False
        if has_prepared_execution:
            if isinstance(prepared, ToolResult):
                _raise_if_cancelled(run_control)
                return prepared
            if not isinstance(prepared, PreparedToolExecution):
                _raise_if_cancelled(run_control)
                return ToolResult.failure(
                    "invalid_tool_execution",
                    "Tool returned an invalid prepared execution.",
                )
            result = _execute_prepared(
                tool,
                arguments,
                prepared,
                confirm,
                run_control,
            )
            _raise_if_cancelled(run_control)
            return result
        invalid = tool.preflight(arguments)
        if invalid is not None:
            _raise_if_cancelled(run_control)
            return invalid
        _raise_if_cancelled(run_control)
        if _confirmation_required(tool, arguments):
            remember = getattr(tool, "remember_approval", None)
            try:
                request = ConfirmationRequest(
                    tool.name,
                    deepcopy(arguments),
                    tool.confirmation_description(arguments),
                    allow_always=callable(remember),
                )
                _raise_if_cancelled(run_control)
                decision = _normalize_decision(confirm(request))
                _raise_if_cancelled(run_control)
            except AgentRunCancelled:
                _cancel_after_confirmation_exception(tool)
                raise
            except BaseException:
                _cancel_after_confirmation_exception(tool)
                raise
            if decision is ConfirmationDecision.DENY:
                _cancel_tool(tool)
                _raise_if_cancelled(run_control)
                return ToolResult.failure(
                    "approval_denied",
                    "User declined this tool call.",
                )
            if decision is ConfirmationDecision.ALLOW_ALWAYS:
                if not callable(remember):
                    _cancel_tool(tool)
                    _raise_if_cancelled(run_control)
                    return ToolResult.failure(
                        "persistent_approval_not_supported",
                        "This tool does not support persistent approval.",
                    )
                remembered = remember(arguments)
                if not remembered.ok:
                    _cancel_tool(tool)
                    _raise_if_cancelled(run_control)
                    return remembered
        _raise_if_cancelled(run_control)
        execute_with_control = getattr(tool, "execute_with_control", None)
        if run_control is not None and callable(execute_with_control):
            result = execute_with_control(arguments, run_control)
        else:
            result = tool.execute(arguments)
        _raise_if_cancelled(run_control)
        return result


def _execute_prepared(
    tool: Tool,
    arguments: dict[str, object],
    prepared: PreparedToolExecution,
    confirm: ConfirmationCallback,
    run_control: RunControl | None,
) -> ToolResult:
    _raise_if_cancelled(run_control)
    if prepared.requires_confirmation:
        try:
            request = ConfirmationRequest(
                tool.name,
                deepcopy(arguments),
                prepared.confirmation_description,
                allow_always=prepared.remember_approval is not None,
            )
            _raise_if_cancelled(run_control)
            decision = _normalize_decision(confirm(request))
            _raise_if_cancelled(run_control)
        except AgentRunCancelled:
            _cancel_after_confirmation_exception(tool)
            raise
        except BaseException:
            _cancel_after_confirmation_exception(tool)
            raise
        if decision is ConfirmationDecision.DENY:
            _cancel_tool(tool)
            _raise_if_cancelled(run_control)
            return ToolResult.failure(
                "approval_denied",
                "User declined this tool call.",
            )
        if decision is ConfirmationDecision.ALLOW_ALWAYS:
            if prepared.remember_approval is None:
                _cancel_tool(tool)
                _raise_if_cancelled(run_control)
                return ToolResult.failure(
                    "persistent_approval_not_supported",
                    "This tool does not support persistent approval.",
                )
            remembered = prepared.remember_approval()
            if not remembered.ok:
                _cancel_tool(tool)
                _raise_if_cancelled(run_control)
                return remembered
    _raise_if_cancelled(run_control)
    return prepared.execute()


def _confirmation_required(tool: Tool, arguments: dict[str, object]) -> bool:
    dynamic = getattr(tool, "requires_confirmation_for", None)
    if callable(dynamic):
        return bool(dynamic(arguments))
    return tool.requires_confirmation


def _normalize_decision(
    decision: bool | ConfirmationDecision,
) -> ConfirmationDecision:
    if isinstance(decision, ConfirmationDecision):
        return decision
    return ConfirmationDecision.ALLOW_ONCE if decision else ConfirmationDecision.DENY


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


def _cancel_tool(tool: object) -> None:
    cancel = getattr(tool, "cancel", None)
    if callable(cancel):
        cancel()


def _cancel_after_confirmation_exception(tool: object) -> None:
    try:
        _cancel_tool(tool)
    except BaseException:
        pass


def _raise_if_cancelled(run_control: RunControl | None) -> None:
    if run_control is not None:
        run_control.raise_if_cancelled()
