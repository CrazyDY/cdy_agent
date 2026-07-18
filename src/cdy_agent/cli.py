"""Command-line interface for CDY Agent."""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)

from .config import resolve_api_mode, resolve_model
from .conversation import Conversation
from .openai_client import (
    MissingAPIKeyError,
    generate_reply,
    generate_reply_for_messages,
)


app = typer.Typer(help="Run the CDY local personal AI assistant.")

REQUEST_ERRORS = (
    MissingAPIKeyError,
    AuthenticationError,
    APIConnectionError,
    RateLimitError,
    APIError,
    OpenAIError,
    ValueError,
    RuntimeError,
)


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _fail_for_exception(exc: Exception) -> NoReturn:
    """Render one supported request failure without exposing a traceback."""
    if isinstance(exc, (MissingAPIKeyError, AuthenticationError)):
        _fail("OpenAI authentication failed. Check OPENAI_API_KEY.")
    if isinstance(exc, APIConnectionError):
        _fail(
            "Unable to connect to OpenAI. "
            "Check OPENAI_BASE_URL and your network connection."
        )
    if isinstance(exc, RateLimitError):
        _fail("OpenAI rate limit reached. Try again later or check your quota.")
    if isinstance(exc, APIError):
        _fail(f"OpenAI request failed: {exc}")
    if isinstance(exc, OpenAIError):
        _fail(f"OpenAI client error: {exc}")
    _fail(str(exc))


@app.callback()
def main() -> None:
    """Run the CDY local personal AI assistant."""


@app.command()
def ask(
    prompt: Annotated[
        str,
        typer.Argument(help="The question or instruction to send."),
    ],
    model: Annotated[
        str | None,
        typer.Option(help="Model override for this request."),
    ] = None,
) -> None:
    """Send one prompt and print one model reply."""
    try:
        reply = generate_reply(
            prompt,
            model=resolve_model(model),
            api_mode=resolve_api_mode(),
        )
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)

    typer.echo(reply)


@app.command()
def chat(
    model: Annotated[
        str | None,
        typer.Option(help="Model override for this conversation."),
    ] = None,
) -> None:
    """Start an in-memory multi-turn conversation."""
    try:
        active_model = resolve_model(model)
        api_mode = resolve_api_mode()
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    conversation = Conversation()

    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            return

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            continue
        if normalized_prompt.lower() in {"/exit", "/quit"}:
            return

        conversation.append("user", normalized_prompt)
        try:
            reply = generate_reply_for_messages(
                conversation.history,
                model=active_model,
                api_mode=api_mode,
            )
        except REQUEST_ERRORS as exc:
            _fail_for_exception(exc)
        conversation.append("assistant", reply)
        typer.echo(f"Assistant: {reply}")
