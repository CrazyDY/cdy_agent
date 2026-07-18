import builtins
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
import typer
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from typer.testing import CliRunner

from cdy_agent import cli, openai_client
from cdy_agent.agent import AgentLoopLimitError
from cdy_agent.cli import app
from cdy_agent.conversation import Message
from cdy_agent.tools.base import ConfirmationRequest


runner = CliRunner()
confirm_test_app = typer.Typer()
confirmation_request = ConfirmationRequest(
    "run_shell", {"argv": ["pwd"]}, "Run ['pwd']"
)


@confirm_test_app.callback(invoke_without_command=True)
def confirm_test() -> None:
    typer.echo("APPROVED" if cli._confirm_tool(confirmation_request) else "DENIED")


class FakeAgent:
    def __init__(
        self,
        replies: str | Sequence[str] = "Model reply",
        error: Exception | None = None,
    ) -> None:
        self.replies = iter([replies] if isinstance(replies, str) else replies)
        self.error = error
        self.calls: list[tuple[Message, ...]] = []

    def run(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        if self.error is not None:
            raise self.error
        return next(self.replies)


@pytest.fixture(autouse=True)
def default_model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)


def test_cli_help_describes_local_personal_assistant() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CDY local personal AI assistant" in result.stdout


def test_ask_passes_normalized_user_message_to_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent()
    calls: list[tuple[str, str, Path]] = []

    def fake_create_agent(model: str, api_mode: str, workspace: Path) -> FakeAgent:
        calls.append((model, api_mode, workspace))
        return agent

    monkeypatch.setattr(cli, "_create_agent", fake_create_agent)

    result = runner.invoke(app, ["ask", "  Hello  ", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == "Model reply\n"
    assert calls == [("env-model", "responses", tmp_path.resolve())]
    assert agent.calls == [(Message(role="user", content="Hello"),)]


def test_ask_model_and_api_mode_are_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, str, Path]] = []
    monkeypatch.setenv("CDY_AGENT_API_MODE", "chat_completions")
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, api_mode, workspace: seen.append(
            (model, api_mode, workspace)
        ) or FakeAgent(),
    )

    result = runner.invoke(
        app, ["ask", "Hello", "--model", "  cli-model  ", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert seen == [("cli-model", "chat_completions", tmp_path.resolve())]


def test_ask_defaults_workspace_to_invocation_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, api_mode, workspace: seen.append(workspace) or FakeAgent("ok"),
    )

    result = runner.invoke(app, ["ask", "hello"])

    assert result.exit_code == 0
    assert seen == [tmp_path.resolve()]


def test_ask_rejects_invalid_workspace_before_creating_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli, "_create_agent", lambda *args: calls.append(True))

    result = runner.invoke(
        app, ["ask", "hello", "--workspace", str(tmp_path / "missing")]
    )

    assert result.exit_code == 1
    assert "workspace" in result.stderr.lower()
    assert calls == []


def test_ask_rejects_blank_prompt_before_creating_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli, "_create_agent", lambda *args: calls.append(True))

    result = runner.invoke(
        app, ["ask", "   ", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "Prompt must not be empty" in result.stderr
    assert calls == []


def test_chat_passes_accumulated_history_and_appends_final_replies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent(["First reply", "Second reply"])
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app,
        ["chat", "--workspace", str(tmp_path)],
        input=" First question \nFollow-up\n/exit\n",
    )

    assert result.exit_code == 0
    assert "Assistant: First reply" in result.stdout
    assert "Assistant: Second reply" in result.stdout
    assert agent.calls == [
        (Message(role="user", content="First question"),),
        (
            Message(role="user", content="First question"),
            Message(role="assistant", content="First reply"),
            Message(role="user", content="Follow-up"),
        ),
    ]


def test_chat_does_not_append_assistant_message_after_failed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent(error=AgentLoopLimitError("model loop exhausted"))
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="Hello\n"
    )

    assert result.exit_code == 1
    assert "model loop exhausted" in result.stderr
    assert "Assistant:" not in result.stdout
    assert agent.calls == [(Message(role="user", content="Hello"),)]


@pytest.mark.parametrize("command", ["/exit", "  /QUIT  "])
def test_chat_exit_commands_do_not_call_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    agent = FakeAgent()
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input=f"{command}\n"
    )

    assert result.exit_code == 0
    assert agent.calls == []


def test_chat_ignores_blank_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent("Reply")
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="   \nHello\n/quit\n"
    )

    assert result.exit_code == 0
    assert agent.calls == [(Message(role="user", content="Hello"),)]


def test_chat_defaults_workspace_to_invocation_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, api_mode, workspace: seen.append(workspace) or FakeAgent(),
    )

    result = runner.invoke(app, ["chat"], input="/exit\n")

    assert result.exit_code == 0
    assert seen == [tmp_path.resolve()]


def test_chat_rejects_invalid_workspace_before_prompting(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path / "missing")], input=""
    )

    assert result.exit_code == 1
    assert "workspace" in result.stderr.lower()
    assert "You:" not in result.stdout


def test_chat_eof_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent())
    result = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="")
    assert result.exit_code == 0


def test_chat_keyboard_interrupt_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent())

    def raise_keyboard_interrupt(*args: object, **kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", raise_keyboard_interrupt)
    result = runner.invoke(app, ["chat", "--workspace", str(tmp_path)])
    assert result.exit_code == 0


def test_create_agent_wires_gateway_registry_and_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway = object()
    registry = object()
    seen: list[tuple[object, object, object]] = []
    monkeypatch.setattr(cli, "ModelGateway", lambda **kwargs: gateway)
    monkeypatch.setattr(cli, "create_builtin_registry", lambda workspace: registry)
    monkeypatch.setattr(
        cli,
        "Agent",
        lambda built_gateway, built_registry, confirm: seen.append(
            (built_gateway, built_registry, confirm)
        ) or "agent",
    )

    result = cli._create_agent("model", "responses", tmp_path)

    assert result == "agent"
    assert seen == [(gateway, registry, cli._confirm_tool)]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y\n", "APPROVED\n"), ("YES\n", "APPROVED\n"), ("\n", "DENIED\n"),
     ("n\n", "DENIED\n"), ("invalid\nn\n", "DENIED\n")],
)
def test_confirmation_answers(answer: str, expected: str) -> None:
    result = runner.invoke(confirm_test_app, [], input=answer)

    assert result.exit_code == 0
    assert "Run ['pwd']" in result.stdout
    assert result.stdout.endswith(expected)


def test_confirmation_eof_denies() -> None:
    result = runner.invoke(confirm_test_app, [], input="")
    assert result.exit_code == 0
    assert result.stdout.endswith("DENIED\n")


@pytest.mark.parametrize("error", [KeyboardInterrupt(), typer.Abort()])
def test_confirmation_interrupt_denies(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    def raise_error(*args: object, **kwargs: object) -> bool:
        raise error

    monkeypatch.setattr(typer, "confirm", raise_error)
    result = runner.invoke(confirm_test_app)
    assert result.exit_code == 0
    assert result.stdout.endswith("DENIED\n")


def test_ask_reports_invalid_api_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDY_AGENT_API_MODE", "legacy")
    result = runner.invoke(app, ["ask", "Hello"])
    assert result.exit_code == 1
    assert "CDY_AGENT_API_MODE" in result.stderr
    assert "responses" in result.stderr
    assert "chat_completions" in result.stderr


REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            AuthenticationError(
                "invalid key",
                response=httpx.Response(401, request=REQUEST),
                body=None,
            ),
            "Check OPENAI_API_KEY",
        ),
        (
            OpenAIError("Missing credentials"),
            "OpenAI client error: Missing credentials",
        ),
        (
            APIConnectionError(request=REQUEST),
            "Check OPENAI_BASE_URL and your network connection",
        ),
        (
            RateLimitError(
                "rate limited",
                response=httpx.Response(429, request=REQUEST),
                body=None,
            ),
            "rate limit",
        ),
        (
            APIError("server error", REQUEST, body=None),
            "OpenAI request failed: server error",
        ),
        (ValueError("Prompt must not be empty."), "Prompt must not be empty"),
        (
            RuntimeError("OpenAI returned an empty response."),
            "OpenAI returned an empty response",
        ),
        (AgentLoopLimitError("Agent loop exhausted."), "Agent loop exhausted"),
    ],
)
def test_ask_reports_expected_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
    expected_message: str,
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent(error=error))
    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert expected_message in result.stderr
    assert "Traceback" not in result.stderr


def test_ask_reports_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda *args: FakeAgent(
            error=openai_client.MissingAPIKeyError("OPENAI_API_KEY is required.")
        ),
    )
    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "Check OPENAI_API_KEY" in result.stderr
