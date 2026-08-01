"""Map internal failures to stable records safe to return to the browser."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from openai import APIError

from cdy_agent.agent import AgentLoopLimitError
from cdy_agent.memory import (
    ConversationNotFoundError,
    ConversationStoreError,
    InvalidConversationStoreError,
)
from cdy_agent.openai_client import MissingAPIKeyError
from cdy_agent.run_control import AgentRunCancelled


@dataclass(frozen=True)
class SafeWebError:
    """A browser-safe domain error with its matching HTTP status."""

    code: str
    message: str
    retryable: bool
    status_code: int


def map_web_error(error: BaseException) -> SafeWebError:
    """Return a stable public error without exposing exception details."""
    if isinstance(error, ConversationNotFoundError):
        return SafeWebError(
            "conversation_not_found", "Conversation was not found.", False, 404
        )
    if isinstance(error, InvalidConversationStoreError):
        return SafeWebError(
            "invalid_conversation_store",
            "Conversation data is unavailable.",
            False,
            500,
        )
    if isinstance(error, ConversationStoreError):
        return SafeWebError(
            "conversation_store_error",
            "Conversation data could not be saved.",
            True,
            500,
        )
    if isinstance(error, AgentRunCancelled):
        return SafeWebError("turn_cancelled", "The turn was cancelled.", False, 409)
    if isinstance(error, AgentLoopLimitError):
        return SafeWebError(
            "model_call_limit", "The turn reached its model-call limit.", True, 503
        )
    if isinstance(error, (MissingAPIKeyError, APIError)):
        return SafeWebError(
            "provider_unavailable", "The model provider is unavailable.", True, 503
        )
    if isinstance(error, subprocess.TimeoutExpired):
        return SafeWebError("process_timeout", "A tool process timed out.", True, 504)
    if isinstance(error, (subprocess.SubprocessError, OSError)):
        return SafeWebError(
            "process_execution_failed", "A tool process could not run.", True, 502
        )
    return SafeWebError("internal_error", "The turn failed safely.", False, 500)
