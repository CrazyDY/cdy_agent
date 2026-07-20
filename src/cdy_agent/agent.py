from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .conversation import Message
from .observability import TraceRecorder
from .openai_client import FinalResponse
from .tools.base import ConfirmationCallback


class AgentLoopLimitError(RuntimeError):
    """Raised when an agent does not finish within its model-call budget."""


def _invalidate_recorder(recorder: TraceRecorder) -> None:
    """Mark a broken recorder unusable without affecting the Agent result."""
    try:
        recorder.invalidate()
    except Exception:
        pass


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
        active_recorder = recorder
        for _ in range(self._max_model_calls):
            model_span = None
            if active_recorder is not None:
                try:
                    model_span = active_recorder.start_model_call()
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            try:
                outcome = self._gateway.create(
                    messages=messages,
                    tools=self._registry.definitions,
                    continuation=continuation,
                    tool_outputs=outputs,
                )
            except Exception as exc:
                if active_recorder is not None and model_span is not None:
                    try:
                        active_recorder.finish_model_call(
                            model_span, None, exc
                        )
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                raise
            if active_recorder is not None and model_span is not None:
                try:
                    active_recorder.finish_model_call(model_span, outcome.usage)
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            if isinstance(outcome, FinalResponse):
                return outcome.text
            completed_outputs = []
            for call in outcome.calls:
                tool_span = None
                if active_recorder is not None:
                    try:
                        tool_span = active_recorder.start_tool_call(call.name)
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                try:
                    result = self._registry.execute(call, self._confirm)
                except Exception as exc:
                    if active_recorder is not None and tool_span is not None:
                        try:
                            active_recorder.finish_tool_call(
                                tool_span,
                                ok=False,
                                error_type=type(exc).__name__,
                            )
                        except Exception:
                            _invalidate_recorder(active_recorder)
                            active_recorder = None
                    raise
                if active_recorder is not None and tool_span is not None:
                    try:
                        active_recorder.finish_tool_call(
                            tool_span,
                            ok=result.ok,
                            error_type=None if result.ok else result.code,
                        )
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                completed_outputs.append((call.call_id, result.to_json()))
            outputs = tuple(completed_outputs)
            continuation = outcome.continuation

        raise AgentLoopLimitError(
            f"Agent exceeded the maximum of {self._max_model_calls} model calls."
        )
