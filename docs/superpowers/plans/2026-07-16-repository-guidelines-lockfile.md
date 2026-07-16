# Repository Guidelines and Lockfile Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the repository instructions for the Phase 2 dual-API architecture, track the uv lockfile, verify the repository, and push `main` to `origin`.

**Architecture:** `AGENTS.md` remains the concise contributor contract and documents current module boundaries, configuration, testing, and lockfile policy. `uv.lock` remains generated and is validated through `uv`; no application code or dependency declarations change.

**Tech Stack:** Python 3.10+, uv, pytest, Typer, OpenAI Python SDK, Git

## Global Constraints

- Do not change application behavior, dependencies, package versions, or API defaults.
- `CDY_AGENT_API_MODE` accepts exactly `responses` and `chat_completions` and defaults to `responses`.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `CDY_AGENT_MODEL` remain environment configuration.
- Tests must remain offline and require no real API key.
- Track `AGENTS.md` and `uv.lock`; keep `.idea/`, `.env`, caches, virtual environments, secrets, and model responses untracked.
- Update `uv.lock` through `uv`, never by manual editing.
- Push `main` to `origin` without force-pushing and do not create a pull request.

---

## File Structure

- Add `AGENTS.md`: repository-level instructions for agents and contributors.
- Add `uv.lock`: generated dependency-resolution lockfile maintained by uv.
- No application or test source file changes.

### Task 1: Update Repository Instructions and Track the Lockfile

**Files:**
- Add: `AGENTS.md`
- Add: `uv.lock`

**Interfaces:**
- Consumes: the current `src/cdy_agent/` module layout, `pyproject.toml`, and generated uv resolution.
- Produces: a UTF-8 repository instruction contract and a tracked lockfile consistent with `pyproject.toml`.

- [ ] **Step 1: Replace `AGENTS.md` with the approved content**

Use this complete content:

```markdown
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

The repository tracks `uv.lock`; prefer `uv` so contributors resolve the same dependency versions. Update the lockfile through `uv sync` or another appropriate `uv` command, never by editing it manually.

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
```

- [ ] **Step 2: Validate and update the generated lockfile with uv**

Run:

```powershell
uv sync --extra dev
```

Expected: exit code 0. `uv.lock` is created or confirmed current from `pyproject.toml`; do not edit it manually.

- [ ] **Step 3: Review the exact tracking scope**

Run:

```powershell
git status --short
Get-Content -Raw AGENTS.md
git diff --check
```

Expected: `AGENTS.md` and `uv.lock` are ready to add, `.idea/` remains untracked, and there are no whitespace errors. Existing committed design/plan files are not altered.

- [ ] **Step 4: Commit the instructions and lockfile**

Run:

```powershell
git add AGENTS.md uv.lock
git diff --cached --check
git commit -m "Track repository guidelines and lockfile"
```

Expected: one scoped commit containing only `AGENTS.md` and `uv.lock`. `.idea/` remains untracked.

### Task 2: Verify and Push `main`

**Files:**
- Verify only: `AGENTS.md`, `uv.lock`, application code, tests, and package metadata.

**Interfaces:**
- Consumes: the Task 1 commit on local `main` and configured `origin` remote.
- Produces: a verified `origin/main` containing the latest local commits.

- [ ] **Step 1: Run complete offline verification**

Run:

```powershell
uv run pytest -p no:cacheprovider
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
git diff --check
```

Expected: 38 tests pass, both help commands exit 0, both distribution artifacts build, and Git reports no whitespace errors.

- [ ] **Step 2: Confirm the branch and remote publication scope**

Run:

```powershell
git branch --show-current
git remote get-url origin
git status --short
```

Expected: branch is `main`, remote is the configured `origin`, and `.idea/` is the only remaining untracked repository item.

- [ ] **Step 3: Push without force**

Run:

```powershell
git push origin main
```

Expected: the remote accepts the update without force-pushing.

- [ ] **Step 4: Verify local and remote tracking state**

Run:

```powershell
git fetch origin main
git rev-list --left-right --count origin/main...main
git status --short
```

Expected: rev-list prints `0 0`, proving local and remote `main` match; `.idea/` remains untracked.
