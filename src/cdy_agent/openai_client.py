"""Thin OpenAI Responses API boundary."""

from __future__ import annotations

import os

from openai import OpenAI


class MissingAPIKeyError(RuntimeError):
    """Raised when the default OpenAI client has no configured API key."""


def generate_reply(
    prompt: str,
    *,
    model: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one non-empty text reply for a user prompt."""
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("Prompt must not be empty.")

    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise MissingAPIKeyError("OPENAI_API_KEY is required.")
        active_client = OpenAI()
    else:
        active_client = client
    response = active_client.responses.create(
        model=model,
        input=normalized_prompt,
    )
    output_text = response.output_text
    if not output_text or not output_text.strip():
        raise RuntimeError("OpenAI returned an empty response.")

    return output_text
