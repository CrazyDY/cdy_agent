# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.10+ project using a `src` layout. Application code belongs in `src/cdy_agent/`, and tests belong in `tests/` with matching `test_<feature>.py` names. The console entry point declared in `pyproject.toml` is `cdy_agent.cli:app`.

Keep the current module boundaries clear:

- `src/cdy_agent/config.py` resolves model and API-mode configuration.
- `src/cdy_agent/openai_client.py` owns the OpenAI-compatible Responses and Chat Completions SDK boundary.
- `src/cdy_agent/cli.py` owns Typer commands and user-facing error presentation.
- Future built-in integrations belong under `src/cdy_agent/skills/<skill_name>/`.

Do not commit generated caches, virtual environments, IDE settings, local secrets, or model responses. `.gitignore` excludes `.venv/`, `.env`, `__pycache__/`, and `.pytest_cache/`; keep `.idea/` untracked as well.

## Configuration

Configure providers through environment variables:

- `OPENAI_API_KEY` — provider API credential; never commit it.
- `OPENAI_BASE_URL` — OpenAI-compatible provider or gateway URL.
- `CDY_AGENT_MODEL` — default model used by the CLI unless `--model` overrides it.
- `CDY_AGENT_API_MODE` — accepts exactly `responses` or `chat_completions`; defaults to `responses`.

Do not add real credentials to source files, tests, command examples, logs, or `.env` files committed to Git.

## Build, Test, and Development Commands

The repository tracks `uv.lock`; prefer `uv` so contributors resolve the same dependency versions. Update the lockfile through `uv sync` or another appropriate `uv` command, never by editing it manually. For shared lockfile updates, pass `--default-index https://pypi.org/simple` unless a later approved repository policy changes the index.

- `uv sync --extra dev` — create or update the environment with development dependencies.
- `uv run pytest` — run the complete offline test suite.
- `uv run cdy-agent --help` — verify the Typer entry point and command list.
- `uv run cdy-agent ask --help` — verify the one-shot ask command interface.
- `uv build` — build source and wheel distributions through Hatchling.

If `uv` is unavailable, install with `python -m pip install -e ".[dev]"` and run commands from the activated environment. Do not generate or hand-edit `uv.lock` with pip.

## Coding Style & Naming Conventions

Use four-space indentation, UTF-8 files, type hints on public functions, and concise docstrings where behavior is not obvious. Follow standard Python naming: `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep skill directories and tool names descriptive and lowercase, such as `skills/filesystem/tools.py`.

No formatter or linter is currently configured, so keep changes PEP 8-compliant and avoid unrelated formatting churn. Preserve focused module boundaries and do not introduce a general provider abstraction unless a later approved design requires it.

## Testing Guidelines

Tests use pytest. Name files `test_<feature>.py` and functions `test_<behavior>()`. Add focused unit tests for new modules and regression tests for bug fixes.

Tests must not depend on a real API key, network access, or the contributor's filesystem. Mock SDK and shell boundaries instead. Explicitly isolate tests from provider environment variables such as `CDY_AGENT_API_MODE`. Run `uv run pytest` before committing or opening a pull request.

## Commit & Pull Request Guidelines

Use short, imperative commit summaries such as `Add API mode configuration` and `Document dual API mode setup`. Keep each commit scoped and explain non-obvious tradeoffs in the body.

Pull requests should describe the change, motivation, and verification performed; link relevant issues and include CLI output or screenshots when user-visible behavior changes. Never commit API keys, `.env` contents, IDE settings, caches, or captured model responses.
