# CDY Agent Dual API Mode Design

## Background

Phase 2 currently sends one prompt through the OpenAI Responses API. A manual smoke test against `https://api.deepseek.com` returned HTTP 404 because DeepSeek exposes the OpenAI-compatible Chat Completions API rather than the Responses API.

This change preserves the Responses API learning path while adding the smallest explicit compatibility path needed for DeepSeek. It does not introduce a general provider abstraction.

## Goals

- Support both OpenAI Responses and Chat Completions request shapes.
- Select the request shape explicitly with `CDY_AGENT_API_MODE`.
- Keep `responses` as the default so existing behavior remains unchanged.
- Make invalid configuration fail before any network request.
- Continue reading the API key and base URL through the OpenAI SDK's native environment-variable support.
- Keep all automated tests offline by using injected fake clients.

## Non-goals

- Do not auto-detect a provider from `OPENAI_BASE_URL`.
- Do not add a general `ModelProvider` or adapter-class hierarchy.
- Do not add an API-mode CLI option.
- Do not add streaming, conversation state, tools, retries, or `.env` loading.
- Do not add live API calls to pytest.

## Configuration

Add `CDY_AGENT_API_MODE` with exactly two accepted values:

- `responses`
- `chat_completions`

The value is stripped of surrounding whitespace and normalized to lowercase. A missing or blank value resolves to `responses`. Any other value raises a configuration error that names the invalid value and lists both accepted values. Invalid configuration must be rejected before constructing an SDK client or making an API call.

`OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `CDY_AGENT_MODEL` retain their existing behavior. The `ask` command does not gain a new option.

## Module Design

### `src/cdy_agent/config.py`

Configuration resolution adds:

```text
DEFAULT_API_MODE: str = "responses"
SUPPORTED_API_MODES: tuple[str, ...] = ("responses", "chat_completions")
resolve_api_mode() -> str
```

`resolve_api_mode()` only parses and validates `CDY_AGENT_API_MODE`. It does not inspect the base URL or create an SDK client.

### `src/cdy_agent/openai_client.py`

The public boundary becomes:

```text
generate_reply(
    prompt: str,
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str
```

After validating the prompt, the function dispatches inside the SDK boundary:

- `responses`: call `client.responses.create(model=model, input=normalized_prompt)` and read `response.output_text`.
- `chat_completions`: call `client.chat.completions.create(model=model, messages=[{"role": "user", "content": normalized_prompt}])` and read `response.choices[0].message.content`.

Both paths require a non-empty string reply. A missing first choice, missing content, non-string content, or blank content in Chat Completions is treated as an empty model response and raises the same `RuntimeError` used by the Responses path.

The function accepts only the two resolved modes. It defensively rejects any other mode with `ValueError`, even though normal CLI flow validates the value in `config.py`.

Client construction and missing-key handling remain unchanged. When no client is injected, `OPENAI_API_KEY` must be non-blank before `OpenAI()` is constructed; the SDK reads `OPENAI_BASE_URL` itself.

### `src/cdy_agent/cli.py`

The `ask` command resolves both the model and API mode, then calls:

```text
generate_reply(prompt, model=resolve_model(model), api_mode=resolve_api_mode())
```

Configuration and reply-validation errors continue to be printed to stderr with exit code 1 and no traceback. Existing SDK error mappings remain unchanged.

## Data Flow

```text
user prompt
  -> Typer ask command
  -> resolve_model()
  -> resolve_api_mode(CDY_AGENT_API_MODE)
  -> generate_reply(prompt, model, api_mode)
  -> responses.create(...) OR chat.completions.create(...)
  -> extract and validate text
  -> typer.echo(reply)
```

## Testing

### Configuration tests

Cover the default, blank value, whitespace/case normalization, both valid modes, and an invalid value with a clear error message.

### SDK boundary tests

Use a fake client that records both possible API calls. Verify:

- Responses mode preserves the current request and output behavior.
- Chat Completions mode sends one normalized `user` message and extracts its text.
- Only the selected API surface is called.
- Blank prompts fail before either API surface is called.
- Empty or malformed output from either mode raises `RuntimeError`.
- An invalid direct `api_mode` argument fails before an API call.
- Default SDK-client creation and missing-key behavior still work.

### CLI tests

Verify that `ask` passes the resolved mode into `generate_reply`, defaults to Responses mode, accepts Chat Completions mode from the environment, and reports invalid configuration through stderr with exit code 1.

No automated test uses a real API key or network connection.

## Documentation and Manual Verification

README will explain both modes and include PowerShell examples.

OpenAI Responses example:

```powershell
$env:OPENAI_API_KEY = "your-openai-key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:CDY_AGENT_MODEL = "gpt-5.6-terra"
$env:CDY_AGENT_API_MODE = "responses"
uv run cdy-agent ask "Introduce yourself in one sentence."
```

DeepSeek Chat Completions example:

```powershell
$env:OPENAI_API_KEY = "your-deepseek-key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:CDY_AGENT_MODEL = "deepseek-v4-flash"
$env:CDY_AGENT_API_MODE = "chat_completions"
uv run cdy-agent ask "Introduce yourself in one sentence."
```

Automated verification remains:

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
```

The DeepSeek smoke test succeeds when it prints a non-empty reply and exits with code 0. Secrets and live responses are never committed.

## Acceptance Criteria

- Existing Responses-mode tests and behavior remain valid.
- DeepSeek can be called by setting the documented environment variables and `CDY_AGENT_API_MODE=chat_completions`.
- Invalid mode values fail locally with an actionable message.
- The complete pytest suite, CLI help checks, and package build pass without network access.
