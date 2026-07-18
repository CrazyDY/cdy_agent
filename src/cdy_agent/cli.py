"""Command-line interface for CDY Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)

from .agent import Agent, AgentLoopLimitError
from .config import resolve_api_mode, resolve_model
from .conversation import Conversation
from .openai_client import MissingAPIKeyError, ModelGateway
from .tools import create_builtin_registry
from .tools.base import ConfirmationRequest
from .tools.filesystem import resolve_workspace


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
    AgentLoopLimitError,
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


def _confirm_tool(request: ConfirmationRequest) -> bool:
    """Ask before a destructive tool call, treating interruptions as denial."""
    try:
        typer.echo(f"{request.description} [y/N]: ", nl=False)
        answer = input()
    except (EOFError, KeyboardInterrupt, typer.Abort):
        return False
    return answer.strip().lower() in {"y", "yes"}


def _create_agent(model: str, api_mode: str, workspace: Path) -> Agent:
    """Construct the CLI's shared model-and-local-tools boundary."""
    gateway = ModelGateway(model=model, api_mode=api_mode)
    registry = create_builtin_registry(workspace)
    return Agent(gateway, registry, _confirm_tool)


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
    workspace: Annotated[
        Path | None,
        typer.Option(help="Directory available to local tools."),
    ] = None,
) -> None:
    """Send one prompt and print one model reply."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Prompt must not be empty.")
        agent = _create_agent(
            resolve_model(model), resolve_api_mode(), active_workspace
        )
        conversation = Conversation()
        conversation.append("user", normalized_prompt)
        reply = agent.run(conversation.history)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)

    typer.echo(reply)


@app.command()
def chat(
    model: Annotated[
        str | None,
        typer.Option(help="Model override for this conversation."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Directory available to local tools."),
    ] = None,
) -> None:
    """Start an in-memory multi-turn conversation."""
    try:
        active_model = resolve_model(model)
        api_mode = resolve_api_mode()
        active_workspace = resolve_workspace(workspace or Path.cwd())
        agent = _create_agent(active_model, api_mode, active_workspace)
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
            reply = agent.run(conversation.history)
        except REQUEST_ERRORS as exc:
            _fail_for_exception(exc)
        conversation.append("assistant", reply)
        typer.echo(f"Assistant: {reply}")
