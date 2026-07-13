"""Small compatibility layer around the OpenAI Agents SDK.

The production path uses ``agents`` from the OpenAI Agents SDK. A lightweight
local fallback keeps unit tests runnable in constrained environments where the
package cannot be installed.
"""

from __future__ import annotations

import inspect
import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any, Callable


if importlib.util.find_spec("agents") is not None:
    _agents = importlib.import_module("agents")
    Agent = _agents.Agent
    Runner = _agents.Runner
    function_tool = _agents.function_tool
else:

    @dataclass
    class Agent:
        """Fallback Agent shape used only when Agents SDK is unavailable."""

        name: str
        instructions: str
        tools: list[Any]

    @dataclass
    class _RunResult:
        final_output: str

    class Runner:
        """Fallback runner that explains how to install the real SDK."""

        @staticmethod
        async def run(agent: Agent, prompt: str) -> _RunResult:
            return _RunResult(
                final_output=(
                    "OpenAI Agents SDK is not installed. "
                    "Run `pip install -e '.[dev]'` or `pip install openai-agents` "
                    "in an environment with package index access, then retry."
                )
            )

    class _FallbackFunctionTool:
        def __init__(self, func: Callable[..., Any]):
            self.func = func
            self.name = func.__name__
            self.description = inspect.getdoc(func) or ""

        async def on_invoke_tool(self, context: Any, input: dict[str, Any] | None = None) -> Any:
            return self.func(**(input or {}))

    def function_tool(func: Callable[..., Any]) -> _FallbackFunctionTool:
        """Fallback decorator compatible with the subset used in tests."""

        return _FallbackFunctionTool(func)
