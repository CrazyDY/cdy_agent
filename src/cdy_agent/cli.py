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

from .config import resolve_model
from .openai_client import generate_reply


app = typer.Typer(help="Run the CDY local personal AI assistant.")


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


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
        reply = generate_reply(prompt, model=resolve_model(model))
    except AuthenticationError:
        _fail("OpenAI authentication failed. Check OPENAI_API_KEY.")
    except APIConnectionError:
        _fail(
            "Unable to connect to OpenAI. "
            "Check OPENAI_BASE_URL and your network connection."
        )
    except RateLimitError:
        _fail("OpenAI rate limit reached. Try again later or check your quota.")
    except APIError as exc:
        _fail(f"OpenAI request failed: {exc}")
    except OpenAIError as exc:
        if "Missing credentials" in str(exc):
            _fail("OpenAI authentication failed. Check OPENAI_API_KEY.")
        _fail(f"OpenAI client error: {exc}")
    except (ValueError, RuntimeError) as exc:
        _fail(str(exc))

    typer.echo(reply)
