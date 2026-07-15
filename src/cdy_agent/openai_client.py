"""Thin OpenAI Responses API boundary."""

from __future__ import annotations

from openai import OpenAI


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

    active_client = client if client is not None else OpenAI()
    response = active_client.responses.create(
        model=model,
        input=normalized_prompt,
    )
    output_text = response.output_text
    if not output_text or not output_text.strip():
        raise RuntimeError("OpenAI returned an empty response.")

    return output_text
