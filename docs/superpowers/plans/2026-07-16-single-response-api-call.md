# Single Responses API Call Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested `cdy-agent ask` command that resolves model configuration, performs one OpenAI Responses API request, and prints a user-friendly result or error.

**Architecture:** `config.py` resolves the model from CLI override, environment, and default values. `openai_client.py` is a thin injectable SDK boundary, while `cli.py` owns terminal input, output, and error presentation; tests replace the network boundary and never require credentials.

**Tech Stack:** Python 3.10+, OpenAI Python SDK 1.99+, Typer 0.12+, pytest 8+, Hatchling, uv

## Global Constraints

- Keep application code under `src/cdy_agent/` and tests under `tests/`.
- Use four-space indentation, UTF-8, public-function type hints, and concise docstrings.
- Read credentials only through `OPENAI_API_KEY`; never add an API-key CLI option or print the key.
- Let the OpenAI SDK read `OPENAI_BASE_URL`; do not implement `.env` loading.
- Resolve models in this order: `--model`, `CDY_AGENT_MODEL`, then `gpt-5.6-terra`.
- Treat blank model values as unset and strip valid model values.
- Automated tests must not require credentials, network access, or the contributor's real filesystem.
- Do not add conversations, tools, Skills, streaming, custom retries, or a generic model-provider abstraction.
- Preserve unrelated untracked `.idea/`, `AGENTS.md`, and `uv.lock`; do not stage them.

---

## File Structure

- Create `src/cdy_agent/config.py`: own the application model default and precedence rules.
- Create `src/cdy_agent/openai_client.py`: normalize a prompt, invoke one Responses API request, and return non-empty text.
- Modify `src/cdy_agent/cli.py`: add the `ask` command and map expected failures to concise stderr output.
- Create `tests/test_config.py`: specify model precedence and whitespace behavior.
- Create `tests/test_openai_client.py`: specify the SDK boundary with an injected fake client.
- Modify `tests/test_cli.py`: specify the `ask` command, model forwarding, stdout, stderr, and exit codes.
- Modify `README.md`: document environment variables, one-shot usage, and manual smoke testing.
- Leave `pyproject.toml` unchanged because OpenAI, Typer, and pytest are already declared.

### Task 1: Model Configuration Resolution

**Files:**
- Create: `tests/test_config.py`
- Create: `src/cdy_agent/config.py`

**Interfaces:**
- Consumes: optional `model_override: str | None` and the `CDY_AGENT_MODEL` environment variable.
- Produces: `DEFAULT_MODEL: str` and `resolve_model(model_override: str | None = None) -> str`.

- [ ] **Step 1: Write the failing configuration tests**

Create `tests/test_config.py` with:

```python
import pytest

from cdy_agent.config import DEFAULT_MODEL, resolve_model


def test_model_override_takes_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")

    assert resolve_model("  cli-model  ") == "cli-model"


def test_environment_model_takes_priority_over_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "  env-model  ")

    assert resolve_model() == "env-model"


def test_blank_override_falls_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")

    assert resolve_model("   ") == "env-model"


def test_blank_environment_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "   ")

    assert resolve_model() == DEFAULT_MODEL


def test_missing_environment_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_MODEL", raising=False)

    assert resolve_model() == "gpt-5.6-terra"
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run:

```powershell
uv run pytest tests/test_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'cdy_agent.config'`.

- [ ] **Step 3: Implement the minimal configuration module**

Create `src/cdy_agent/config.py` with:

```python
"""Application configuration for CDY Agent."""

from __future__ import annotations

import os


DEFAULT_MODEL = "gpt-5.6-terra"


def resolve_model(model_override: str | None = None) -> str:
    """Resolve the model from a CLI override, environment, or default."""
    if model_override and model_override.strip():
        return model_override.strip()

    environment_model = os.getenv("CDY_AGENT_MODEL")
    if environment_model and environment_model.strip():
        return environment_model.strip()

    return DEFAULT_MODEL
```

- [ ] **Step 4: Run the configuration tests and full regression suite**

Run each command separately:

```powershell
uv run pytest tests/test_config.py -v
uv run pytest
```

Expected: five configuration tests pass; the full suite reports six passing tests.

- [ ] **Step 5: Commit model configuration**

```powershell
git add -- src/cdy_agent/config.py tests/test_config.py
git commit -m "Add model configuration resolution"
```

### Task 2: Injectable Responses API Boundary

**Files:**
- Create: `tests/test_openai_client.py`
- Create: `src/cdy_agent/openai_client.py`

**Interfaces:**
- Consumes: `prompt: str`, resolved `model: str`, and optional `client: OpenAI | None`.
- Produces: `generate_reply(prompt: str, *, model: str, client: OpenAI | None = None) -> str`.

- [ ] **Step 1: Write the failing SDK-boundary tests**

Create `tests/test_openai_client.py` with:

```python
from types import SimpleNamespace
from typing import Any

import pytest

from cdy_agent import openai_client
from cdy_agent.openai_client import generate_reply


class FakeResponses:
    def __init__(self, output_text: str | None) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, output_text: str | None) -> None:
        self.responses = FakeResponses(output_text)


def test_generate_reply_sends_normalized_prompt_and_model() -> None:
    client = FakeClient("Hello from the model.")

    result = generate_reply(
        "  Hello  ",
        model="gpt-5.6-terra",
        client=client,
    )

    assert result == "Hello from the model."
    assert client.responses.calls == [
        {"model": "gpt-5.6-terra", "input": "Hello"}
    ]


def test_generate_reply_rejects_blank_prompt_before_api_call() -> None:
    client = FakeClient("unused")

    with pytest.raises(ValueError, match="Prompt must not be empty"):
        generate_reply("   ", model="gpt-5.6-terra", client=client)

    assert client.responses.calls == []


def test_generate_reply_rejects_blank_output() -> None:
    client = FakeClient("   ")

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply("Hello", model="gpt-5.6-terra", client=client)


def test_generate_reply_creates_default_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient("Created through the SDK factory.")
    factory_calls: list[bool] = []

    def fake_openai_factory() -> FakeClient:
        factory_calls.append(True)
        return client

    monkeypatch.setattr(openai_client, "OpenAI", fake_openai_factory)

    result = generate_reply("Hello", model="gpt-5.6-terra")

    assert result == "Created through the SDK factory."
    assert factory_calls == [True]
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run:

```powershell
uv run pytest tests/test_openai_client.py -v
```

Expected: collection fails because `cdy_agent.openai_client` does not exist.

- [ ] **Step 3: Implement the minimal Responses API boundary**

Create `src/cdy_agent/openai_client.py` with:

```python
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
```

- [ ] **Step 4: Run the SDK-boundary tests and full regression suite**

Run each command separately:

```powershell
uv run pytest tests/test_openai_client.py -v
uv run pytest
```

Expected: four SDK-boundary tests pass; the full suite reports ten passing tests.

- [ ] **Step 5: Commit the Responses API boundary**

```powershell
git add -- src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Add single response API boundary"
```

### Task 3: Ask Command and User-Facing Errors

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/cdy_agent/cli.py`

**Interfaces:**
- Consumes: `resolve_model(model_override)` and `generate_reply(prompt, *, model)` from Tasks 1 and 2.
- Produces: `cdy-agent ask PROMPT [--model MODEL]` with stdout on success, stderr on expected failure, and exit codes `0` or `1`.

- [ ] **Step 1: Replace the CLI tests with the complete failing behavior suite**

Replace `tests/test_cli.py` with:

```python
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
```

- [ ] **Step 2: Run the CLI tests and verify the missing command failure**

Run:

```powershell
uv run pytest tests/test_cli.py -v
```

Expected: the existing help test passes, while `ask` behavior tests fail because the command and imported boundaries are not wired into `cli.py`.

- [ ] **Step 3: Implement the ask command and error mapping**

Replace `src/cdy_agent/cli.py` with:

```python
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
```

- [ ] **Step 4: Run CLI tests, help checks, and the full suite**

Run each command separately:

```powershell
uv run pytest tests/test_cli.py -v
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
```

Expected:

- Ten CLI test cases pass: one help test, two standalone success tests, and seven parameterized error cases.
- The full suite reports nineteen passing test cases.
- Root help lists the `ask` command.
- Ask help documents `PROMPT` and `--model`.

- [ ] **Step 5: Commit the ask command**

```powershell
git add -- src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Add one-shot ask command"
```

### Task 4: Usage Documentation and Release Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the `ask` command and three environment variables implemented or delegated by prior tasks.
- Produces: copyable setup, usage, test, and build commands without embedding real credentials.

- [ ] **Step 1: Update README for the single-call stage**

Replace `README.md` with:

````markdown
# CDY Agent

CDY Agent is a local personal AI assistant built step by step to learn practical Agent development.

## Current stage

The project can send one text prompt through the OpenAI Responses API and print the model's reply. Conversations, tools, Skills, and memory will be added in later stages.

## Configuration

Set the API key in the current PowerShell session:

```powershell
$env:OPENAI_API_KEY="your-api-key"
```

Optional configuration:

```powershell
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
$env:CDY_AGENT_MODEL="gpt-5.6-terra"
```

`OPENAI_BASE_URL` can point to a compatible API gateway. The model is resolved from `--model`, then `CDY_AGENT_MODEL`, then `gpt-5.6-terra`.

## Usage

```powershell
uv run cdy-agent ask "用一句话介绍你自己"
uv run cdy-agent ask "解释 Agent Loop" --model gpt-5.6-luna
```

## Development

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --extra dev
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
```
````

- [ ] **Step 2: Run all automated release checks**

Run each command separately:

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
git diff --check
```

Expected:

- Pytest reports nineteen passing test cases without network access.
- Both help commands exit with code `0` and show the expected command or arguments.
- Hatchling builds `dist/cdy_agent-0.1.0.tar.gz` and `dist/cdy_agent-0.1.0-py3-none-any.whl`.
- `git diff --check` exits with code `0` and prints no whitespace errors.

- [ ] **Step 3: Commit the usage documentation**

```powershell
git add -- README.md
git commit -m "Document single response usage"
```

- [ ] **Step 4: Verify credentials exist without printing them**

Run:

```powershell
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "OPENAI_API_KEY is not set in this PowerShell session."
}
```

Expected: no output and exit code `0`. If the command throws, stop and ask the user to set the variable in the active execution environment; never ask them to paste the key into chat.

- [ ] **Step 5: Perform the real API smoke test**

Run:

```powershell
uv run cdy-agent ask "用一句话介绍你自己"
```

Expected: a non-empty model reply and exit code `0`. Do not save the response to a fixture, snapshot, or committed file.

## Final Verification

After all commits and the manual smoke test, run each command again:

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
git status --short
```

Expected:

- All nineteen test cases pass.
- Both CLI help commands succeed.
- Source and wheel distributions build successfully.
- The only untracked paths are the pre-existing `.idea/`, `AGENTS.md`, and `uv.lock`.
