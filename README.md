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
