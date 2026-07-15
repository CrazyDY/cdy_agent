# CDY Agent

CDY Agent is a local personal AI assistant built step by step to learn practical Agent development.

## Current stage

The project currently provides its Python package and Typer command-line skeleton. Model calls, conversations, tools, Skills, and memory will be added in later stages.

## Development

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --extra dev
uv run pytest
uv run cdy-agent --help
uv build
```
