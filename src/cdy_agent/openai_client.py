"""Thin OpenAI-compatible Responses and Chat Completions boundary."""

from __future__ import annotations

import os
from collections.abc import Sequence

from openai import OpenAI

from .conversation import Message


class MissingAPIKeyError(RuntimeError):
    """Raised when the default OpenAI client has no configured API key."""


def generate_reply(
    prompt: str,
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one non-empty text reply for a user prompt."""
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("Prompt must not be empty.")

    return generate_reply_for_messages(
        (Message(role="user", content=normalized_prompt),),
        model=model,
        api_mode=api_mode,
        client=client,
    )


def generate_reply_for_messages(
    messages: Sequence[Message],
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one reply from a complete, ordered message history."""
    if not messages:
        raise ValueError("Conversation history must not be empty.")
    if api_mode not in {"responses", "chat_completions"}:
        raise ValueError(f"Unsupported API mode: {api_mode!r}.")

    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise MissingAPIKeyError("OPENAI_API_KEY is required.")
        active_client = OpenAI()
    else:
        active_client = client

    request_messages = [
        {"role": message.role, "content": message.content}
        for message in messages
    ]
    if api_mode == "responses":
        response = active_client.responses.create(
            model=model,
            input=request_messages,
        )
        output_text = response.output_text
    else:
        response = active_client.chat.completions.create(
            model=model,
            messages=request_messages,
        )
        try:
            output_text = response.choices[0].message.content
        except (AttributeError, IndexError):
            output_text = None

    if not isinstance(output_text, str) or not output_text.strip():
        raise RuntimeError("OpenAI returned an empty response.")

    return output_text
