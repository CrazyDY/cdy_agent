import builtins
from collections.abc import Sequence

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from typer.testing import CliRunner

from cdy_agent import cli
from cdy_agent import openai_client
from cdy_agent.cli import app
from cdy_agent.conversation import Message


runner = CliRunner()


def test_cli_help_describes_local_personal_assistant() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CDY local personal AI assistant" in result.stdout


def test_ask_outputs_reply_and_uses_environment_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply(
        prompt: str,
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append((prompt, model, api_mode))
        return "Model reply"

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 0
    assert result.stdout == "Model reply\n"
    assert result.stderr == ""
    assert calls == [("Hello", "env-model", "responses")]


def test_ask_model_option_overrides_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply(
        prompt: str,
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append((model, api_mode))
        return "Model reply"

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(
        app,
        ["ask", "Hello", "--model", "  cli-model  "],
    )

    assert result.exit_code == 0
    assert calls == [("cli-model", "responses")]


def test_ask_uses_chat_completions_mode_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("CDY_AGENT_API_MODE", "chat_completions")

    def fake_generate_reply(
        prompt: str,
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append(api_mode)
        return "Model reply"

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 0
    assert calls == ["chat_completions"]


def test_ask_reports_invalid_api_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("CDY_AGENT_API_MODE", "legacy")

    def fake_generate_reply(
        prompt: str,
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append(api_mode)
        return "Model reply"

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert calls == []
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
        (
            ValueError("Prompt must not be empty."),
            "Prompt must not be empty",
        ),
        (
            RuntimeError("OpenAI returned an empty response."),
            "OpenAI returned an empty response",
        ),
    ],
)
def test_ask_reports_expected_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_message: str,
) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply(
        prompt: str,
        *,
        model: str,
        api_mode: str,
    ) -> str:
        raise error

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert expected_message in result.stderr


def test_ask_reports_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply(
        prompt: str,
        *,
        model: str,
        api_mode: str,
    ) -> str:
        raise openai_client.MissingAPIKeyError("OPENAI_API_KEY is required.")

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Check OPENAI_API_KEY" in result.stderr


def test_chat_sends_complete_history_across_two_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Message, ...], str, str]] = []
    replies = iter(["First reply", "Second reply"])
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply_for_messages(
        messages: Sequence[Message],
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append((tuple(messages), model, api_mode))
        return next(replies)

    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        fake_generate_reply_for_messages,
    )

    result = runner.invoke(
        app,
        ["chat"],
        input="First question\nFollow-up\n/exit\n",
    )

    assert result.exit_code == 0
    assert "Assistant: First reply" in result.stdout
    assert "Assistant: Second reply" in result.stdout
    assert calls == [
        (
            (Message(role="user", content="First question"),),
            "env-model",
            "responses",
        ),
        (
            (
                Message(role="user", content="First question"),
                Message(role="assistant", content="First reply"),
                Message(role="user", content="Follow-up"),
            ),
            "env-model",
            "responses",
        ),
    ]


@pytest.mark.parametrize("command", ["/exit", "  /QUIT  "])
def test_chat_exit_commands_do_not_call_model(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    calls: list[bool] = []
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)
    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        lambda *args, **kwargs: calls.append(True),
    )

    result = runner.invoke(app, ["chat"], input=f"{command}\n")

    assert result.exit_code == 0
    assert calls == []


def test_chat_ignores_blank_input_and_honors_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.setenv("CDY_AGENT_API_MODE", "chat_completions")

    def fake_generate_reply_for_messages(
        messages: Sequence[Message],
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append((model, api_mode))
        return "Reply"

    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        fake_generate_reply_for_messages,
    )

    result = runner.invoke(
        app,
        ["chat", "--model", "cli-model"],
        input="   \nHello\n/quit\n",
    )

    assert result.exit_code == 0
    assert calls == [("cli-model", "chat_completions")]


def test_chat_eof_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    result = runner.invoke(app, ["chat"], input="")

    assert result.exit_code == 0


def test_chat_keyboard_interrupt_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def raise_keyboard_interrupt(*args: object, **kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", raise_keyboard_interrupt)

    result = runner.invoke(app, ["chat"])

    assert result.exit_code == 0


def test_chat_reports_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply_for_messages(
        messages: Sequence[Message],
        *,
        model: str,
        api_mode: str,
    ) -> str:
        raise APIConnectionError(request=REQUEST)

    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        fake_generate_reply_for_messages,
    )

    result = runner.invoke(app, ["chat"], input="Hello\n")

    assert result.exit_code == 1
    assert "Check OPENAI_BASE_URL and your network connection" in result.stderr
    assert "Traceback" not in result.stderr
