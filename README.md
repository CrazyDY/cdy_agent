# CDY Agent

CDY Agent is a local personal AI assistant built step by step to learn practical Agent development.

## Current stage

The project can make one-shot calls through either the Responses API or the Chat Completions API and print the model's reply. Conversations, tools, Skills, and memory will be added in later stages.

## Configuration

Choose an API mode and set the matching provider configuration in the current PowerShell session:

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

`CDY_AGENT_API_MODE` accepts `responses` or `chat_completions`; it defaults to `responses`. `OPENAI_BASE_URL` can point to a compatible API gateway. The `--model` option still overrides `CDY_AGENT_MODEL`; when neither is set, the default model is `gpt-5.6-terra`.

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
