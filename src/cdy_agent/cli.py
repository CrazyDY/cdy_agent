"""Command-line interface for CDY Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn
from uuid import uuid4

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
from .conversation import Conversation, Message
from .memory import ConversationStore, ConversationStoreError
from .openai_client import MissingAPIKeyError, ModelGateway
from .skills import SkillManager, create_skill_tools
from .tools import create_builtin_registry
from .tools.base import ConfirmationRequest
from .tools.filesystem import resolve_workspace


app = typer.Typer(help="Run the CDY local personal AI assistant.")
sessions_app = typer.Typer(help="List and delete saved conversations.")
app.add_typer(sessions_app, name="sessions")

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
    ConversationStoreError,
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
    manager = SkillManager(workspace, registry, _confirm_tool)
    registered = registry.register_many(create_skill_tools(manager))
    if not registered.ok:
        raise RuntimeError(registered.message or "Could not register Skill tools.")
    return Agent(gateway, registry, _confirm_tool)


@sessions_app.command("list")
def list_sessions(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved conversations."),
    ] = None,
) -> None:
    """List saved conversations, newest first."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        summaries = ConversationStore(active_workspace).list_summaries()
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not summaries:
        typer.echo("No saved conversations.")
        return
    for summary in summaries:
        typer.echo(
            f"{summary.id}  {summary.updated_at}  "
            f"{summary.message_count} messages  {summary.preview}"
        )


@sessions_app.command("delete")
def delete_session(
    session_id: Annotated[
        str,
        typer.Argument(help="Complete ID of the conversation to delete."),
    ],
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved conversations."),
    ] = None,
) -> None:
    """Delete one saved conversation after confirmation."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = ConversationStore(active_workspace)
        approved = typer.confirm(
            f"Delete conversation {session_id}?", default=False
        )
        if not approved:
            typer.echo("Aborted.")
            return
        store.delete(session_id)
    except (EOFError, KeyboardInterrupt, typer.Abort):
        typer.echo("Aborted.")
        return
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    typer.echo(f"Deleted conversation {session_id}.")


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
    resume: Annotated[
        str | None,
        typer.Option(help="Resume a saved conversation by its complete ID."),
    ] = None,
) -> None:
    """Start a new conversation or explicitly resume a saved one."""
    try:
        active_model = resolve_model(model)
        api_mode = resolve_api_mode()
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = ConversationStore(active_workspace)
        agent = _create_agent(active_model, api_mode, active_workspace)
        conversation = Conversation()
        if resume is None:
            session_id = str(uuid4())
        else:
            stored = store.load(resume)
            session_id = stored.id
            for message in stored.messages:
                conversation.append(message.role, message.content)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)

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

        user_message = conversation.append("user", normalized_prompt)
        try:
            reply = agent.run(conversation.history)
            assistant_message = Message(role="assistant", content=reply.strip())
            store.append_turn(session_id, user_message, assistant_message)
        except REQUEST_ERRORS as exc:
            _fail_for_exception(exc)
        conversation.append(assistant_message.role, assistant_message.content)
        typer.echo(f"Assistant: {assistant_message.content}")
