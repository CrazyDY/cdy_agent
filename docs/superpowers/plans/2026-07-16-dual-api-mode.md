# Dual API Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the OpenAI Responses path while adding an explicitly selected Chat Completions path that works with DeepSeek.

**Architecture:** `config.py` validates `CDY_AGENT_API_MODE`, `cli.py` passes the resolved value into the existing SDK boundary, and `openai_client.py` dispatches between the two OpenAI SDK surfaces. The design keeps provider detection and provider-specific classes out of scope.

**Tech Stack:** Python 3.10+, Typer, OpenAI Python SDK, pytest, uv, Hatchling

## Global Constraints

- `CDY_AGENT_API_MODE` accepts exactly `responses` and `chat_completions` after whitespace trimming and lowercase normalization.
- Missing or blank `CDY_AGENT_API_MODE` resolves to `responses`.
- Invalid modes fail before SDK-client construction or an API call.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `CDY_AGENT_MODEL` retain their existing behavior.
- Do not add provider auto-detection, API-mode CLI options, provider adapter classes, streaming, conversation state, tools, retries, `.env` loading, or live-network pytest tests.
- Every behavior change follows red-green-refactor and is committed separately.

---

## File Structure

- Modify `src/cdy_agent/config.py`: own API-mode constants, normalization, and validation.
- Modify `tests/test_config.py`: specify API-mode configuration behavior.
- Modify `src/cdy_agent/openai_client.py`: dispatch requests and extract reply text for both SDK APIs.
- Modify `tests/test_openai_client.py`: provide dual-surface fakes and cover dispatch/output failures.
- Modify `src/cdy_agent/cli.py`: resolve and pass API mode while preserving error presentation.
- Modify `tests/test_cli.py`: verify CLI wiring and invalid-mode reporting.
- Modify `README.md`: document OpenAI Responses and DeepSeek Chat Completions setup.

### Task 1: Resolve and Validate API Mode

**Files:**
- Modify: `src/cdy_agent/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `CDY_AGENT_API_MODE` from `os.environ`.
- Produces: `DEFAULT_API_MODE: str`, `SUPPORTED_API_MODES: tuple[str, ...]`, and `resolve_api_mode() -> str`.

- [ ] **Step 1: Write failing configuration tests**

Append to `tests/test_config.py` and extend its import:

```python
from cdy_agent.config import (
    DEFAULT_API_MODE,
    SUPPORTED_API_MODES,
    resolve_api_mode,
)


@pytest.mark.parametrize("api_mode", [None, "   "])
def test_api_mode_defaults_to_responses(
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str | None,
) -> None:
    if api_mode is None:
        monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)
    else:
        monkeypatch.setenv("CDY_AGENT_API_MODE", api_mode)

    assert resolve_api_mode() == DEFAULT_API_MODE == "responses"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (" responses ", "responses"),
        (" CHAT_COMPLETIONS ", "chat_completions"),
    ],
)
def test_api_mode_normalizes_supported_values(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: str,
) -> None:
    monkeypatch.setenv("CDY_AGENT_API_MODE", configured)

    assert resolve_api_mode() == expected


def test_api_mode_rejects_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_API_MODE", "legacy")

    with pytest.raises(ValueError) as exc_info:
        resolve_api_mode()

    message = str(exc_info.value)
    assert "legacy" in message
    assert all(mode in message for mode in SUPPORTED_API_MODES)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
uv run pytest tests/test_config.py -v
```

Expected: collection fails because `DEFAULT_API_MODE`, `SUPPORTED_API_MODES`, and `resolve_api_mode` do not exist.

- [ ] **Step 3: Add the minimal resolver**

Add to `src/cdy_agent/config.py`:

```python
DEFAULT_API_MODE = "responses"
SUPPORTED_API_MODES = ("responses", "chat_completions")


def resolve_api_mode() -> str:
    """Resolve and validate the configured OpenAI-compatible API mode."""
    configured_mode = os.getenv("CDY_AGENT_API_MODE")
    if not configured_mode or not configured_mode.strip():
        return DEFAULT_API_MODE

    normalized_mode = configured_mode.strip().lower()
    if normalized_mode not in SUPPORTED_API_MODES:
        supported = ", ".join(SUPPORTED_API_MODES)
        raise ValueError(
            f"Unsupported CDY_AGENT_API_MODE {normalized_mode!r}. "
            f"Choose one of: {supported}."
        )
    return normalized_mode
```

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_config.py -v
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add src/cdy_agent/config.py tests/test_config.py
git commit -m "Add API mode configuration"
```

### Task 2: Dispatch Across Both SDK API Surfaces

**Files:**
- Modify: `src/cdy_agent/openai_client.py`
- Test: `tests/test_openai_client.py`

**Interfaces:**
- Consumes: `api_mode: str` resolved by `resolve_api_mode()` and an optional injected OpenAI-compatible client.
- Produces: `generate_reply(prompt: str, *, model: str, api_mode: str, client: OpenAI | None = None) -> str`.

- [ ] **Step 1: Replace the single-surface fakes with dual-surface fakes**

In `tests/test_openai_client.py`, keep `FakeResponses` and add:

```python
class FakeCompletions:
    def __init__(self, output_text: object) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.output_text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(
        self,
        responses_output: str | None = "unused",
        chat_output: object = "unused",
    ) -> None:
        self.responses = FakeResponses(responses_output)
        self.chat = SimpleNamespace(
            completions=FakeCompletions(chat_output),
        )
```

Update every existing `generate_reply(...)` call to include `api_mode="responses"`. Update existing fake construction such as `FakeClient("reply")` to `FakeClient(responses_output="reply")` where clarity is needed.

- [ ] **Step 2: Write failing Chat Completions and dispatch tests**

Add:

```python
def test_generate_reply_uses_chat_completions_mode() -> None:
    client = FakeClient(chat_output="Hello from DeepSeek.")

    result = generate_reply(
        "  Hello  ",
        model="deepseek-v4-flash",
        api_mode="chat_completions",
        client=client,
    )

    assert result == "Hello from DeepSeek."
    assert client.chat.completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    ]
    assert client.responses.calls == []


def test_generate_reply_uses_only_responses_mode() -> None:
    client = FakeClient(responses_output="Hello from OpenAI.")

    result = generate_reply(
        "Hello",
        model="gpt-5.6-terra",
        api_mode="responses",
        client=client,
    )

    assert result == "Hello from OpenAI."
    assert client.chat.completions.calls == []


def test_generate_reply_rejects_invalid_api_mode_before_api_call() -> None:
    client = FakeClient()

    with pytest.raises(ValueError, match="Unsupported API mode"):
        generate_reply(
            "Hello",
            model="test-model",
            api_mode="legacy",
            client=client,
        )

    assert client.responses.calls == []
    assert client.chat.completions.calls == []
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
uv run pytest tests/test_openai_client.py -v
```

Expected: tests fail because `generate_reply` does not accept `api_mode` and has no Chat Completions branch.

- [ ] **Step 4: Implement minimal dual-mode dispatch**

Change `generate_reply` in `src/cdy_agent/openai_client.py` to:

```python
def generate_reply(
    prompt: str,
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one non-empty text reply for a user prompt."""
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("Prompt must not be empty.")
    if api_mode not in {"responses", "chat_completions"}:
        raise ValueError(f"Unsupported API mode: {api_mode!r}.")

    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise MissingAPIKeyError("OPENAI_API_KEY is required.")
        active_client = OpenAI()
    else:
        active_client = client

    if api_mode == "responses":
        response = active_client.responses.create(
            model=model,
            input=normalized_prompt,
        )
        output_text = response.output_text
    else:
        response = active_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": normalized_prompt}],
        )
        try:
            output_text = response.choices[0].message.content
        except (AttributeError, IndexError):
            output_text = None

    if not isinstance(output_text, str) or not output_text.strip():
        raise RuntimeError("OpenAI returned an empty response.")

    return output_text
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_openai_client.py -v
```

Expected: all SDK-boundary tests pass.

- [ ] **Step 6: Add malformed Chat Completions regression coverage**

Add to `tests/test_openai_client.py`:

```python
@pytest.mark.parametrize("chat_output", [None, "   ", ["not", "text"]])
def test_generate_reply_rejects_empty_or_non_text_chat_output(
    chat_output: object,
) -> None:
    client = FakeClient(chat_output=chat_output)

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="deepseek-v4-flash",
            api_mode="chat_completions",
            client=client,
        )
```

Add these malformed-response tests. Each test overrides only the fake
`create()` result so it exercises the real extraction logic:

```python
def test_generate_reply_rejects_missing_chat_choice() -> None:
    client = FakeClient()
    client.chat.completions.create = lambda **kwargs: SimpleNamespace(choices=[])

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="deepseek-v4-flash",
            api_mode="chat_completions",
            client=client,
        )


def test_generate_reply_rejects_missing_chat_content() -> None:
    client = FakeClient()
    client.chat.completions.create = lambda **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace())]
    )

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="deepseek-v4-flash",
            api_mode="chat_completions",
            client=client,
        )
```

- [ ] **Step 7: Run focused and full tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_openai_client.py -v
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Support dual OpenAI API modes"
```

### Task 3: Wire API Mode Through the CLI

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `resolve_api_mode() -> str` and the updated `generate_reply(..., api_mode: str) -> str`.
- Produces: unchanged `cdy-agent ask PROMPT [--model MODEL]` command with environment-selected API behavior.

- [ ] **Step 1: Update CLI fakes and write failing wiring tests**

Change CLI fake signatures from:

```python
def fake_generate_reply(prompt: str, *, model: str) -> str:
```

to:

```python
def fake_generate_reply(
    prompt: str,
    *,
    model: str,
    api_mode: str,
) -> str:
```

Record `api_mode` in the successful-call tests. Add:

```python
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
    monkeypatch.setenv("CDY_AGENT_API_MODE", "legacy")

    result = runner.invoke(app, ["ask", "Hello"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "CDY_AGENT_API_MODE" in result.stderr
    assert "responses" in result.stderr
    assert "chat_completions" in result.stderr
```

- [ ] **Step 2: Run focused CLI tests and verify RED**

Run:

```powershell
uv run pytest tests/test_cli.py -v
```

Expected: tests fail because the CLI does not resolve or pass `api_mode`.

- [ ] **Step 3: Wire the resolver into the command**

Update imports in `src/cdy_agent/cli.py`:

```python
from .config import resolve_api_mode, resolve_model
```

Update the request call:

```python
reply = generate_reply(
    prompt,
    model=resolve_model(model),
    api_mode=resolve_api_mode(),
)
```

The existing `except (ValueError, RuntimeError) as exc` branch reports invalid configuration without another error type.

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_cli.py -v
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Select API mode in ask command"
```

### Task 4: Document and Verify Both Modes

**Files:**
- Modify: `README.md`
- Test: `tests/test_cli.py` only if user-visible help wording changes during implementation.

**Interfaces:**
- Consumes: the completed environment configuration and unchanged `ask` command.
- Produces: copyable PowerShell setup for OpenAI Responses and DeepSeek Chat Completions.

- [ ] **Step 1: Update README configuration and examples**

Replace the Responses-only stage description with wording that the project supports one-shot calls through Responses or Chat Completions. Document these exact examples:

```powershell
# OpenAI Responses API
$env:OPENAI_API_KEY = "your-openai-key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:CDY_AGENT_MODEL = "gpt-5.6-terra"
$env:CDY_AGENT_API_MODE = "responses"
uv run cdy-agent ask "Introduce yourself in one sentence."

# DeepSeek Chat Completions API
$env:OPENAI_API_KEY = "your-deepseek-key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:CDY_AGENT_MODEL = "deepseek-v4-flash"
$env:CDY_AGENT_API_MODE = "chat_completions"
uv run cdy-agent ask "Introduce yourself in one sentence."
```

State that `responses` is the default, list both legal mode values, and state that `--model` still overrides `CDY_AGENT_MODEL`.

- [ ] **Step 2: Run complete offline verification**

Run:

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
```

Expected: the full test suite passes, both help commands exit 0, and Hatchling builds both source and wheel distributions successfully.

- [ ] **Step 3: Commit Task 4**

```powershell
git add README.md
git commit -m "Document dual API mode setup"
```

- [ ] **Step 4: Perform the user-owned DeepSeek smoke test**

In the same PowerShell session where the real key is already available, run without printing the key:

```powershell
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:CDY_AGENT_MODEL = "deepseek-v4-flash"
$env:CDY_AGENT_API_MODE = "chat_completions"
uv run cdy-agent ask "Introduce yourself in one sentence."
```

Expected: a non-empty reply and process exit code 0. Do not commit the key or response text.
