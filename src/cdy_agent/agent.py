from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .conversation import Message
from .observability import TraceRecorder
from .openai_client import FinalResponse
from .tools.base import ConfirmationCallback


class AgentLoopLimitError(RuntimeError):
    """Raised when an agent does not finish within its model-call budget."""


class Agent:
    """Run a bounded, API-neutral model and tool interaction loop."""

    def __init__(
        self,
        gateway: Any,
        registry: Any,
        confirm: ConfirmationCallback,
        max_model_calls: int = 8,
    ) -> None:
        if max_model_calls < 1:
            raise ValueError("max_model_calls must be at least 1.")
        self._gateway = gateway
        self._registry = registry
        self._confirm = confirm
        self._max_model_calls = max_model_calls

    def run(
        self,
        messages: Sequence[Message],
        recorder: TraceRecorder | None = None,
    ) -> str:
        if not messages:
            raise ValueError("Conversation history must not be empty.")

        continuation = None
        outputs: tuple[tuple[str, str], ...] = ()
        for _ in range(self._max_model_calls):
            model_span = recorder.start_model_call() if recorder else None
            try:
                outcome = self._gateway.create(
                    messages=messages,
                    tools=self._registry.definitions,
                    continuation=continuation,
                    tool_outputs=outputs,
                )
            except Exception as exc:
                if recorder is not None and model_span is not None:
                    recorder.finish_model_call(model_span, None, exc)
                raise
            if recorder is not None and model_span is not None:
                recorder.finish_model_call(model_span, outcome.usage)
            if isinstance(outcome, FinalResponse):
                return outcome.text
            completed_outputs = []
            for call in outcome.calls:
                tool_span = recorder.start_tool_call(call.name) if recorder else None
                try:
                    result = self._registry.execute(call, self._confirm)
                except Exception as exc:
                    if recorder is not None and tool_span is not None:
                        recorder.finish_tool_call(
                            tool_span,
                            ok=False,
                            error_type=type(exc).__name__,
                        )
                    raise
                if recorder is not None and tool_span is not None:
                    recorder.finish_tool_call(
                        tool_span,
                        ok=result.ok,
                        error_type=None if result.ok else result.code,
                    )
                completed_outputs.append((call.call_id, result.to_json()))
            outputs = tuple(completed_outputs)
            continuation = outcome.continuation

        raise AgentLoopLimitError(
            f"Agent exceeded the maximum of {self._max_model_calls} model calls."
        )
