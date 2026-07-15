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
from cdy_agent.cli import app


runner = CliRunner()


def test_cli_help_describes_local_personal_assistant() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CDY local personal AI assistant" in result.stdout


def test_ask_outputs_reply_and_uses_environment_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")

    def fake_generate_reply(prompt: str, *, model: str) -> str:
        calls.append((prompt, model))
        return "Model reply"

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 0
    assert result.stdout == "Model reply\n"
    assert result.stderr == ""
    assert calls == [("Hello", "env-model")]


def test_ask_model_option_overrides_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")

    def fake_generate_reply(prompt: str, *, model: str) -> str:
        calls.append(model)
        return "Model reply"

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(
        app,
        ["ask", "Hello", "--model", "  cli-model  "],
    )

    assert result.exit_code == 0
    assert calls == ["cli-model"]


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
            "Check OPENAI_API_KEY",
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
    def fake_generate_reply(prompt: str, *, model: str) -> str:
        raise error

    monkeypatch.setattr(cli, "generate_reply", fake_generate_reply)

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert expected_message in result.stderr
