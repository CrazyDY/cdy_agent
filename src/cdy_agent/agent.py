from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .conversation import Message
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

    def run(self, messages: Sequence[Message]) -> str:
        if not messages:
            raise ValueError("Conversation history must not be empty.")

        continuation = None
        outputs: tuple[tuple[str, str], ...] = ()
        for _ in range(self._max_model_calls):
            outcome = self._gateway.create(
                messages=messages,
                tools=self._registry.definitions,
                continuation=continuation,
                tool_outputs=outputs,
            )
            if isinstance(outcome, FinalResponse):
                return outcome.text
            outputs = tuple(
                (call.call_id, self._registry.execute(call, self._confirm).to_json())
                for call in outcome.calls
            )
            continuation = outcome.continuation

        raise AgentLoopLimitError(
            f"Agent exceeded the maximum of {self._max_model_calls} model calls."
        )
