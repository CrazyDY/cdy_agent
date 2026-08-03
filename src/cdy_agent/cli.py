"""Command-line interface for CDY Agent."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import uuid4

import typer
import uvicorn
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)

from .agent import Agent, AgentLoopLimitError
from .config import (
    CONFIG_RELATIVE_PATH,
    WorkspaceConfig,
    load_workspace_config,
    resolve_api_mode,
    resolve_max_model_calls,
    resolve_model,
    resolve_rebuild_frontend,
    resolve_streaming,
    resolve_system_prompt,
)
from .conversation import Conversation, Message
from .evals import EvalFileError, run_eval_file
from .mcp import McpManager, load_mcp_config
from .memory import (
    ConversationStore,
    ConversationStoreError,
    MemoryDraft,
    MemoryStore,
    MemoryStoreError,
    StoredMemory,
)
from .observability import (
    Pricing,
    TraceRecord,
    TraceRecorder,
    TraceStore,
    TraceStoreError,
    resolve_pricing,
)
from .observability.logging import configure_structured_logging, resolve_log_level
from .openai_client import MissingAPIKeyError
from .runtime import create_agent_runtime
from .tools.base import ConfirmationDecision, ConfirmationRequest, ToolResult
from .tools.filesystem import resolve_workspace
from .tools.registry import ToolRegistry
from .web.app import WebDependencies, WebSettings, create_web_app
from .web.auth import BrowserCapability
from .web.turns import ConfirmationBroker, TurnCoordinator, TurnDependencies

app = typer.Typer(help="Run the CDY local personal AI assistant.")
sessions_app = typer.Typer(help="List and delete saved conversations.")
memories_app = typer.Typer(help="Manage explicit long-term memories.")
traces_app = typer.Typer(help="List and inspect saved call traces.")
config_app = typer.Typer(help="Inspect effective non-secret configuration.")
evals_app = typer.Typer(help="Run offline evaluation cases.")
mcp_app = typer.Typer(help="Inspect and check workspace MCP servers.")
app.add_typer(sessions_app, name="sessions")
app.add_typer(memories_app, name="memories")
app.add_typer(traces_app, name="traces")
app.add_typer(config_app, name="config")
app.add_typer(evals_app, name="evals")
app.add_typer(mcp_app, name="mcp")

_SOURCE_FRONTEND_DIRECTORY = Path(__file__).resolve().parents[2] / "frontend"
_PACKAGED_FRONTEND_DIRECTORY = Path(__file__).with_name("frontend")
_FRONTEND_DIRECTORY = (
    _SOURCE_FRONTEND_DIRECTORY
    if (_SOURCE_FRONTEND_DIRECTORY / "package.json").is_file()
    else _PACKAGED_FRONTEND_DIRECTORY
)
_WEB_STATIC_DIRECTORY = Path(__file__).with_name("web") / "static"

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
    MemoryStoreError,
    TraceStoreError,
    EvalFileError,
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


def _close_agent(agent: object) -> None:
    close = getattr(agent, "close", None)
    if callable(close):
        close()


def _confirm_tool(request: ConfirmationRequest) -> ConfirmationDecision:
    """Confirm a tool call, treating interruptions as denial."""
    prompt = "[y] once / [a] always / [N] deny: " if request.allow_always else "[y/N]: "
    try:
        typer.echo(f"{request.description} {prompt}", nl=False)
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt, typer.Abort):
        return ConfirmationDecision.DENY
    if answer in {"y", "yes"}:
        return ConfirmationDecision.ALLOW_ONCE
    if request.allow_always and answer in {"a", "always"}:
        return ConfirmationDecision.ALLOW_ALWAYS
    return ConfirmationDecision.DENY


def _create_agent(
    model: str,
    api_mode: str,
    workspace: Path,
    max_model_calls: int | None = None,
) -> Agent:
    """Construct the CLI's shared model-and-local-tools boundary."""
    workspace_config = load_workspace_config(workspace)
    return create_agent_runtime(
        model=model,
        api_mode=api_mode,
        workspace=workspace,
        confirm=_confirm_tool,
        max_model_calls=resolve_max_model_calls(max_model_calls, workspace_config),
        system_prompt=resolve_system_prompt(workspace_config),
    )


def _load_configured_workspace(workspace: Path | None) -> tuple[Path, WorkspaceConfig]:
    active_workspace = resolve_workspace(workspace or Path.cwd())
    return active_workspace, load_workspace_config(active_workspace)


def _configure_logging_for_workspace(workspace_config: WorkspaceConfig) -> None:
    configure_structured_logging(resolve_log_level(workspace_config))


def _effective_log_level_name(workspace_config: WorkspaceConfig) -> str:
    if (configured := os.getenv("CDY_AGENT_LOG_LEVEL")) is not None:
        return configured
    if workspace_config.log_level is not None:
        return workspace_config.log_level
    return "WARNING"


def _run_traced(
    agent: Agent,
    messages: Sequence[Message],
    recorder: TraceRecorder,
    store: TraceStore,
) -> str:
    """Run one agent turn while isolating trace persistence failures."""
    error = None
    try:
        return agent.run(messages, recorder)
    except Exception as exc:
        error = exc
        raise
    finally:
        if not recorder.healthy:
            typer.echo("Warning: Could not save trace.", err=True)
        else:
            try:
                store.append(recorder.finish(error))
            except (TraceStoreError, RuntimeError, ValueError, OSError):
                typer.echo("Warning: Could not save trace.", err=True)


def _run_with_best_effort_trace(
    agent: Agent,
    messages: Sequence[Message],
    *,
    command: str,
    model: str,
    api_mode: str,
    workspace: Path,
    pricing: Pricing | None,
    session_id: str | None = None,
) -> str:
    """Run one agent turn even when trace setup cannot be completed."""
    try:
        recorder = TraceRecorder(
            command,
            model,
            api_mode,
            session_id=session_id,
            pricing=pricing,
        )
        store = TraceStore(workspace)
    except (TraceStoreError, RuntimeError, ValueError, OSError):
        typer.echo("Warning: Could not save trace.", err=True)
        return agent.run(messages)
    return _run_traced(agent, messages, recorder, store)


def _run_stream_with_best_effort_trace(
    agent: Agent,
    messages: Sequence[Message],
    *,
    command: str,
    model: str,
    api_mode: str,
    workspace: Path,
    pricing: Pricing | None,
    session_id: str | None = None,
) -> str:
    """Run one streamed agent turn while preserving best-effort traces."""

    def write_chunk(chunk: str) -> None:
        typer.echo(chunk, nl=False)

    try:
        recorder = TraceRecorder(
            command,
            model,
            api_mode,
            session_id=session_id,
            pricing=pricing,
        )
        store = TraceStore(workspace)
    except (TraceStoreError, RuntimeError, ValueError, OSError):
        typer.echo("Warning: Could not save trace.", err=True)
        return agent.run_stream(messages, write_chunk)

    error = None
    try:
        return agent.run_stream(messages, write_chunk, recorder)
    except Exception as exc:
        error = exc
        raise
    finally:
        if not recorder.healthy:
            typer.echo("Warning: Could not save trace.", err=True)
        else:
            try:
                store.append(recorder.finish(error))
            except (TraceStoreError, RuntimeError, ValueError, OSError):
                typer.echo("Warning: Could not save trace.", err=True)


def _render_trace(record: TraceRecord) -> None:
    """Render only the metadata retained in a trace record."""
    typer.echo(f"ID: {record.trace_id}")
    typer.echo(f"Started: {record.started_at}")
    typer.echo(f"Status: {record.status}")
    typer.echo(f"Command: {record.command}")
    typer.echo(f"Model: {record.model}")
    typer.echo(f"API mode: {record.api_mode}")
    typer.echo(f"Session: {record.session_id or '-'}")
    typer.echo(f"Duration: {record.duration_ms} ms")
    typer.echo(f"Error type: {record.error_type or '-'}")
    if record.usage is None:
        typer.echo("Usage: unknown")
    else:
        typer.echo(
            f"Usage: {record.usage.input_tokens} input, "
            f"{record.usage.output_tokens} output, "
            f"{record.usage.total_tokens} total"
        )
    if record.estimated_cost is None:
        typer.echo("Estimated cost: unknown")
    else:
        typer.echo(
            f"Estimated cost: {record.estimated_cost.input_cost} input, "
            f"{record.estimated_cost.output_cost} output, "
            f"{record.estimated_cost.total_cost} total"
        )
    typer.echo("Model calls:")
    for span in record.model_calls:
        tokens = (
            "unknown tokens"
            if span.usage is None
            else f"{span.usage.total_tokens} tokens"
        )
        typer.echo(
            f"  {span.sequence}. {span.status}, {span.duration_ms} ms, "
            f"{tokens}, error={span.error_type or '-'}"
        )
    typer.echo("Tool calls:")
    for span in record.tool_calls:
        typer.echo(
            f"  {span.sequence}. {span.tool_name}, {span.status}, "
            f"{span.duration_ms} ms, error={span.error_type or '-'}"
        )


def _render_memory(record: StoredMemory) -> None:
    """Render one complete memory record with stable multiline formatting."""
    typer.echo(f"ID: {record.id}")
    typer.echo(f"Updated: {record.updated_at}")
    typer.echo(f"Tags: {', '.join(record.tags) if record.tags else '-'}")
    typer.echo("Content:")
    typer.echo(record.content)


def _render_memory_draft(draft: MemoryDraft) -> None:
    typer.echo(f"Tags: {', '.join(draft.tags) if draft.tags else '-'}")
    typer.echo("Content:")
    typer.echo(draft.content)


def _render_memories(records: tuple[StoredMemory, ...]) -> None:
    for index, record in enumerate(records):
        if index:
            typer.echo()
        _render_memory(record)


def _confirm_memory_change(prompt: str) -> bool:
    try:
        return typer.confirm(prompt, default=False)
    except (EOFError, KeyboardInterrupt, typer.Abort):
        return False


@memories_app.command("add")
def add_memory(
    content: Annotated[
        str,
        typer.Argument(help="Content of the memory to save."),
    ],
    tags: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Tag to attach; may be repeated."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved memories."),
    ] = None,
) -> None:
    """Save one explicit long-term memory after confirmation."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = MemoryStore(active_workspace)
        supplied_tags = tags or []
        prepared = store.prepare_create(content, supplied_tags)
        typer.echo(f"ID: {prepared.memory_id}")
        _render_memory_draft(prepared.draft)
        if not _confirm_memory_change("Create this memory?"):
            typer.echo("Aborted.")
            return
        record = store.commit_create(prepared)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    typer.echo(f"Created memory {record.id}.")


@memories_app.command("list")
def list_memories(
    tags: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Require tag; may be repeated."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved memories."),
    ] = None,
) -> None:
    """List complete saved memories."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        records = MemoryStore(active_workspace).list_memories(tags or [])
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not records:
        typer.echo("No saved memories.")
        return
    _render_memories(records)


@memories_app.command("search")
def search_memories(
    query: Annotated[
        str,
        typer.Argument(help="Keywords to search for."),
    ],
    tags: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Require tag; may be repeated."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved memories."),
    ] = None,
) -> None:
    """Search complete saved memories by keywords and tags."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        records = MemoryStore(active_workspace).search(query, tags or [])
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not records:
        typer.echo("No matching memories.")
        return
    _render_memories(records)


@memories_app.command("update")
def update_memory(
    memory_id: Annotated[
        str,
        typer.Argument(help="Complete UUID of the memory to update."),
    ],
    content: Annotated[
        str,
        typer.Option(help="Replacement memory content."),
    ],
    tags: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Replacement tag; may be repeated."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved memories."),
    ] = None,
) -> None:
    """Replace one complete memory after confirmation."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = MemoryStore(active_workspace)
        supplied_tags = tags or []
        prepared = store.prepare_update(memory_id, content, supplied_tags)
        typer.echo("Current:")
        _render_memory(prepared.before)
        typer.echo("Replacement:")
        _render_memory_draft(prepared.replacement)
        if not _confirm_memory_change("Update this memory?"):
            typer.echo("Aborted.")
            return
        record = store.commit_update(prepared)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    typer.echo(f"Updated memory {record.id}.")


@memories_app.command("delete")
def delete_memory(
    memory_id: Annotated[
        str,
        typer.Argument(help="Complete UUID of the memory to delete."),
    ],
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved memories."),
    ] = None,
) -> None:
    """Delete one complete memory after confirmation."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        store = MemoryStore(active_workspace)
        prepared = store.prepare_delete(memory_id)
        _render_memory(prepared.before)
        if not _confirm_memory_change("Delete this memory?"):
            typer.echo("Aborted.")
            return
        store.commit_delete(prepared)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    typer.echo(f"Deleted memory {prepared.before.id}.")


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
        approved = typer.confirm(f"Delete conversation {session_id}?", default=False)
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


@traces_app.command("list")
def list_traces(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved traces."),
    ] = None,
) -> None:
    """List saved trace metadata, newest first."""
    try:
        records = TraceStore(resolve_workspace(workspace or Path.cwd())).list_traces()
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not records:
        typer.echo("No saved traces.")
        return
    for record in records:
        tokens = (
            "unknown tokens"
            if record.usage is None
            else f"{record.usage.total_tokens} tokens"
        )
        cost = (
            "unknown cost"
            if record.estimated_cost is None
            else f"{record.estimated_cost.total_cost} cost"
        )
        typer.echo(
            f"{record.trace_id}  {record.started_at}  {record.status}  "
            f"{record.command}  {record.model}  {record.duration_ms} ms  "
            f"{tokens}  {cost}"
        )


@traces_app.command("show")
def show_trace(
    trace_id: Annotated[
        str,
        typer.Argument(help="Complete UUID of the trace to show."),
    ],
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing saved traces."),
    ] = None,
) -> None:
    """Show detailed metadata for one saved trace."""
    try:
        record = TraceStore(resolve_workspace(workspace or Path.cwd())).get(trace_id)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    _render_trace(record)


@config_app.command("show")
def show_config(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing optional config.yaml."),
    ] = None,
) -> None:
    """Show effective non-secret configuration for one workspace."""
    try:
        active_workspace, workspace_config = _load_configured_workspace(workspace)
        model = resolve_model(workspace_config=workspace_config)
        api_mode = resolve_api_mode(workspace_config)
        system_prompt = resolve_system_prompt(workspace_config)
        stream = resolve_streaming(workspace_config=workspace_config)
        max_model_calls = resolve_max_model_calls(workspace_config=workspace_config)
        pricing = resolve_pricing(workspace_config)
        resolve_log_level(workspace_config)
        mcp_config = load_mcp_config(active_workspace)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)

    config_path = active_workspace / CONFIG_RELATIVE_PATH
    typer.echo(f"Workspace: {active_workspace}")
    typer.echo(f"Workspace config: {config_path if config_path.exists() else '-'}")
    typer.echo(f"model: {model}")
    typer.echo(f"api_mode: {api_mode}")
    typer.echo(f"stream: {str(stream).lower()}")
    typer.echo(f"max_model_calls: {max_model_calls}")
    typer.echo(f"system_prompt: {system_prompt}")
    typer.echo(f"log_level: {_effective_log_level_name(workspace_config)}")
    typer.echo(
        f"mcp_servers: {', '.join(server.name for server in mcp_config.servers) or '-'}"
    )
    if pricing is None:
        typer.echo("input_cost_per_million: -")
        typer.echo("output_cost_per_million: -")
    else:
        typer.echo(f"input_cost_per_million: {pricing.input_per_million}")
        typer.echo(f"output_cost_per_million: {pricing.output_per_million}")


@evals_app.command("run")
def run_evals(
    eval_file: Annotated[
        Path,
        typer.Argument(help="YAML or JSON eval case file to run."),
    ],
    model: Annotated[
        str | None,
        typer.Option(help="Model override for this eval run."),
    ] = None,
    max_model_calls: Annotated[
        int | None,
        typer.Option(help="Maximum model calls allowed per eval case."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(help="Directory available to local tools."),
    ] = None,
) -> None:
    """Run offline eval cases and summarize exact or contains assertions."""
    try:
        active_workspace, workspace_config = _load_configured_workspace(workspace)
        _configure_logging_for_workspace(workspace_config)
        active_model = resolve_model(model, workspace_config)
        api_mode = resolve_api_mode(workspace_config)
        agent = _create_agent(active_model, api_mode, active_workspace, max_model_calls)
        report = run_eval_file(eval_file, agent)
    except REQUEST_ERRORS as exc:
        if "agent" in locals():
            _close_agent(agent)
        _fail_for_exception(exc)
    _close_agent(agent)
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        typer.echo(f"{status} {result.name}")
        if not result.passed:
            typer.echo(f"  {result.message}")
    typer.echo(f"{report.passed} passed, {report.failed} failed, {report.total} total")
    if report.failed:
        raise typer.Exit(code=1)


@mcp_app.command("servers")
def list_mcp_server_configs(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing optional mcp.yaml."),
    ] = None,
) -> None:
    """Validate and list configured MCP servers without connecting."""
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        mcp_config = load_mcp_config(active_workspace)
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    if not mcp_config.servers:
        typer.echo("No MCP servers configured.")
        return
    for server in mcp_config.servers:
        references = sorted((*server.env_from.values(), *server.headers_from.values()))
        typer.echo(
            f"{server.name}  {server.transport}  credentials="
            f"{','.join(references) if references else '-'}"
        )


@mcp_app.command("check")
def check_mcp_server(
    name: Annotated[str, typer.Argument(help="Configured MCP server name.")],
    workspace: Annotated[
        Path | None,
        typer.Option(help="Workspace containing optional mcp.yaml."),
    ] = None,
) -> None:
    """Connect to one MCP server after confirmation and show a safe summary."""
    manager: McpManager | None = None
    try:
        active_workspace = resolve_workspace(workspace or Path.cwd())
        mcp_config = load_mcp_config(active_workspace)
        manager = McpManager(active_workspace, mcp_config, ToolRegistry([]))
        prepared = manager.prepare_connection(name)
        if isinstance(prepared, ToolResult):
            raise ValueError(prepared.message or "Unknown MCP server.")
        decision = _confirm_tool(
            ConfirmationRequest(
                "connect_mcp_server",
                {"server": name},
                prepared.confirmation_description,
            )
        )
        if decision is ConfirmationDecision.DENY:
            raise ValueError("MCP connection was declined.")
        result = prepared.execute()
        if not result.ok:
            raise RuntimeError(result.message or "Could not connect to MCP server.")
        payload = result.data
        typer.echo(f"name: {payload['name']}")
        typer.echo(f"status: {payload['status']}")
        typer.echo(f"protocol_version: {payload.get('protocol_version') or '-'}")
        typer.echo(f"capabilities: {','.join(payload.get('capabilities', [])) or '-'}")
        typer.echo(f"tools: {len(payload.get('tools', []))}")
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    finally:
        if manager is not None:
            manager.close()


@app.callback()
def main() -> None:
    """Run the CDY local personal AI assistant."""
    try:
        configure_structured_logging(resolve_log_level())
    except ValueError as exc:
        _fail_for_exception(exc)


def _build_web_frontend(*, rebuild: bool = False) -> None:
    """Build the Vue production assets required by the local Web server.

    When ``rebuild`` is False (the default), a previously built static bundle is
    reused and the npm build is skipped. Assets are only built when missing, so
    repeated starts do not rebuild. A True value forces a fresh build.
    """
    if not (_FRONTEND_DIRECTORY / "package.json").is_file():
        if (_WEB_STATIC_DIRECTORY / "index.html").is_file():
            return
        raise RuntimeError("Frontend sources and built Web assets are unavailable.")
    if not rebuild and (_WEB_STATIC_DIRECTORY / "index.html").is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required to build the Web interface.")
    environment = os.environ.copy()
    environment["CDY_AGENT_WEB_STATIC_DIRECTORY"] = str(_WEB_STATIC_DIRECTORY)
    if not (_FRONTEND_DIRECTORY / "node_modules").is_dir():
        _run_frontend_npm(
            npm,
            ["install"],
            environment,
            "Web interface dependency installation failed.",
        )
    _run_frontend_npm(
        npm,
        ["run", "build"],
        environment,
        "Web interface build failed.",
    )


def _run_frontend_npm(
    npm: str,
    arguments: list[str],
    environment: dict[str, str],
    failure_message: str,
) -> None:
    try:
        result = subprocess.run(
            [npm, *arguments],
            cwd=_FRONTEND_DIRECTORY,
            check=False,
            env=environment,
        )
    except OSError as exc:
        raise RuntimeError("Unable to run the Web interface build.") from exc
    if result.returncode != 0:
        raise RuntimeError(failure_message)


@app.command()
def web(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Directory available to the local Web server."),
    ] = None,
    port: Annotated[
        int,
        typer.Option(help="Loopback port for the local Web server."),
    ] = 8000,
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the local UI in a browser."),
    ] = True,
    rebuild_frontend: Annotated[
        bool | None,
        typer.Option(
            "--rebuild-frontend/--no-rebuild-frontend",
            help="Force rebuild the Vue production assets before starting the server. "
            "By default built assets are reused and the build only runs when missing.",
        ),
    ] = None,
    max_model_calls: Annotated[
        int | None,
        typer.Option(help="Maximum model calls allowed per Web turn."),
    ] = None,
) -> None:
    """Start the authenticated local Web interface on IPv4 loopback."""
    listener: socket.socket | None = None
    agent: Agent | None = None
    try:
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            if listener is not None:
                listener.close()
                listener = None
            raise RuntimeError(
                f"Unable to start local Web server on 127.0.0.1:{port}."
            ) from exc

        active_workspace, workspace_config = _load_configured_workspace(workspace)
        rebuild = resolve_rebuild_frontend(rebuild_frontend, workspace_config)
        _build_web_frontend(rebuild=rebuild)
        _configure_logging_for_workspace(workspace_config)
        active_model = resolve_model(workspace_config=workspace_config)
        api_mode = resolve_api_mode(workspace_config)
        system_prompt = resolve_system_prompt(workspace_config)
        model_call_limit = resolve_max_model_calls(max_model_calls, workspace_config)
        pricing = resolve_pricing(workspace_config)

        auth = BrowserCapability.create("127.0.0.1", port)
        broker = ConfirmationBroker()
        agent = create_agent_runtime(
            model=active_model,
            api_mode=api_mode,
            workspace=active_workspace,
            confirm=broker.confirm,
            max_model_calls=model_call_limit,
            system_prompt=system_prompt,
        )
        conversations = ConversationStore(active_workspace)
        coordinator = TurnCoordinator(
            TurnDependencies(
                agent=agent,
                confirmations=broker,
                conversations=conversations,
                traces=TraceStore(active_workspace),
                model=active_model,
                api_mode=api_mode,
                pricing=pricing,
            )
        )
        server = create_web_app(
            WebSettings(
                workspace=active_workspace,
                model=active_model,
                api_mode=api_mode,
            ),
            WebDependencies(
                auth=auth,
                conversation_store=conversations,
                turn_coordinator=coordinator,
            ),
        )

        uvicorn_server = uvicorn.Server(
            uvicorn.Config(
                server,
                host="127.0.0.1",
                port=port,
                log_config=None,
            )
        )
        if open_browser:
            webbrowser.open(auth.launch_url)
        else:
            typer.echo(auth.launch_url)
        uvicorn_server.run(sockets=[listener])
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
    finally:
        if agent is not None:
            _close_agent(agent)
        if listener is not None:
            listener.close()


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
    stream: Annotated[
        bool | None,
        typer.Option(
            "--stream/--no-stream",
            help="Override streamed output for this request.",
        ),
    ] = None,
    max_model_calls: Annotated[
        int | None,
        typer.Option(help="Maximum model calls allowed for this request."),
    ] = None,
) -> None:
    """Send one prompt and print one model reply."""
    try:
        active_workspace, workspace_config = _load_configured_workspace(workspace)
        _configure_logging_for_workspace(workspace_config)
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Prompt must not be empty.")
        active_model = resolve_model(model, workspace_config)
        api_mode = resolve_api_mode(workspace_config)
        stream_output = resolve_streaming(stream, workspace_config)
        pricing = resolve_pricing(workspace_config)
        agent = _create_agent(active_model, api_mode, active_workspace, max_model_calls)
        conversation = Conversation()
        conversation.append("user", normalized_prompt)
        if stream_output:
            reply = _run_stream_with_best_effort_trace(
                agent,
                conversation.history,
                command="ask",
                model=active_model,
                api_mode=api_mode,
                workspace=active_workspace,
                pricing=pricing,
            )
        else:
            reply = _run_with_best_effort_trace(
                agent,
                conversation.history,
                command="ask",
                model=active_model,
                api_mode=api_mode,
                workspace=active_workspace,
                pricing=pricing,
            )
    except REQUEST_ERRORS as exc:
        if "agent" in locals():
            _close_agent(agent)
        _fail_for_exception(exc)

    _close_agent(agent)

    if stream_output:
        typer.echo()
    else:
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
    stream: Annotated[
        bool | None,
        typer.Option(
            "--stream/--no-stream",
            help="Override streamed output for this conversation.",
        ),
    ] = None,
    max_model_calls: Annotated[
        int | None,
        typer.Option(help="Maximum model calls allowed per conversation turn."),
    ] = None,
) -> None:
    """Start a new conversation or explicitly resume a saved one."""
    try:
        active_workspace, workspace_config = _load_configured_workspace(workspace)
        _configure_logging_for_workspace(workspace_config)
        active_model = resolve_model(model, workspace_config)
        api_mode = resolve_api_mode(workspace_config)
        stream_output = resolve_streaming(stream, workspace_config)
        pricing = resolve_pricing(workspace_config)
        store = ConversationStore(active_workspace)
        agent = _create_agent(active_model, api_mode, active_workspace, max_model_calls)
        conversation = Conversation()
        if resume is None:
            session_id = str(uuid4())
        else:
            stored = store.load(resume)
            session_id = stored.id
            for message in stored.messages:
                conversation.append(message.role, message.content)
    except REQUEST_ERRORS as exc:
        if "agent" in locals():
            _close_agent(agent)
        _fail_for_exception(exc)

    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            _close_agent(agent)
            return

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            continue
        if normalized_prompt.lower() in {"/exit", "/quit"}:
            _close_agent(agent)
            return

        user_message = conversation.append("user", normalized_prompt)
        try:
            if stream_output:
                typer.echo("Assistant: ", nl=False)
                reply = _run_stream_with_best_effort_trace(
                    agent,
                    conversation.history,
                    command="chat",
                    model=active_model,
                    api_mode=api_mode,
                    workspace=active_workspace,
                    pricing=pricing,
                    session_id=session_id,
                )
                typer.echo()
            else:
                reply = _run_with_best_effort_trace(
                    agent,
                    conversation.history,
                    command="chat",
                    model=active_model,
                    api_mode=api_mode,
                    workspace=active_workspace,
                    pricing=pricing,
                    session_id=session_id,
                )
            assistant_message = Message(role="assistant", content=reply.strip())
            store.append_turn(session_id, user_message, assistant_message)
            conversation.append(assistant_message.role, assistant_message.content)
            if not stream_output:
                typer.echo(f"Assistant: {assistant_message.content}")
        except REQUEST_ERRORS as exc:
            _close_agent(agent)
            _fail_for_exception(exc)
