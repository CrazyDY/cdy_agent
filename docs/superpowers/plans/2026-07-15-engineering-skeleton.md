# Engineering Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a minimal, installable CDY Agent Python package with a tested Typer CLI, a concise README, and successful source/wheel builds.

**Architecture:** This phase creates only the package boundary and terminal entry point. `cdy_agent.cli` owns CLI presentation, while `cdy_agent.__init__` only marks and documents the package; model configuration, OpenAI calls, tools, sessions, and memory remain out of scope until later phases.

**Tech Stack:** Python 3.10+, Typer 0.12+, pytest 8+, Hatchling, uv

## Global Constraints

- Keep application code under `src/cdy_agent/` and tests under `tests/`.
- Use four-space indentation, UTF-8, public-function type hints, and standard Python naming.
- Use the existing console entry point `cdy-agent = "cdy_agent.cli:app"` from `pyproject.toml`.
- Automated tests must not require an OpenAI API key, network access, or the contributor's real filesystem.
- Do not add model configuration, OpenAI calls, tools, Skills, sessions, or memory in this phase.
- Use `uv` for dependency resolution, tests, CLI checks, and builds.
- Preserve unrelated untracked files. Do not stage `.idea/`, `AGENTS.md`, or the pre-existing `uv.lock` without separate user authorization.

---

## File Structure

- Create `README.md`: satisfy the existing package metadata and document the current project boundary and verification commands.
- Create `src/cdy_agent/__init__.py`: mark the directory as the `cdy_agent` package and provide its module docstring.
- Create `src/cdy_agent/cli.py`: define the Typer `app` referenced by the existing console-script entry point.
- Create `tests/test_cli.py`: verify the CLI help contract without starting a subprocess or making a network request.
- Leave `pyproject.toml` unchanged because it already declares the package metadata, dependencies, pytest paths, Hatchling backend, and console entry point needed by this phase.

### Task 1: Buildable and Tested CLI Skeleton

**Files:**
- Create: `README.md`
- Create: `src/cdy_agent/__init__.py`
- Create: `src/cdy_agent/cli.py`
- Create: `tests/test_cli.py`
- Verify: `pyproject.toml`

**Interfaces:**
- Consumes: the existing `cdy-agent = "cdy_agent.cli:app"` console-script declaration, `readme = "README.md"` package metadata, and pytest `pythonpath = ["src"]` configuration.
- Produces: `cdy_agent.cli.app: typer.Typer`, which later phases will extend with agent commands.

- [ ] **Step 1: Add only the packaging prerequisites needed to run the first test**

Create `README.md` with:

````markdown
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
````

Create `src/cdy_agent/__init__.py` with:

```python
"""CDY Agent package."""
```

These files let uv install the project before the CLI module exists. They do not implement the behavior under test.

- [ ] **Step 2: Write the failing CLI help test**

Create `tests/test_cli.py` with:

```python
from typer.testing import CliRunner

from cdy_agent.cli import app


runner = CliRunner()


def test_cli_help_describes_local_personal_assistant() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CDY local personal AI assistant" in result.stdout
```

- [ ] **Step 3: Run the focused test and verify the missing CLI module failure**

Run:

```powershell
uv run pytest tests/test_cli.py::test_cli_help_describes_local_personal_assistant -v
```

Expected: test collection fails with `ModuleNotFoundError: No module named 'cdy_agent.cli'` because the CLI implementation does not exist yet.

- [ ] **Step 4: Add the minimal CLI implementation**

Create `src/cdy_agent/cli.py` with:

```python
"""Command-line interface for CDY Agent."""

from __future__ import annotations

import typer


app = typer.Typer(help="Run the CDY local personal AI assistant.")


@app.callback()
def main() -> None:
    """Run the CDY local personal AI assistant."""
```

- [ ] **Step 5: Run the focused test and verify it passes**

Run:

```powershell
uv run pytest tests/test_cli.py::test_cli_help_describes_local_personal_assistant -v
```

Expected: `1 passed`.

- [ ] **Step 6: Run the complete phase verification**

Run each command separately:

```powershell
uv run pytest
uv run cdy-agent --help
uv build
```

Expected:

- Pytest reports `1 passed`.
- CLI help exits with code `0` and contains `Run the CDY local personal AI assistant.`
- The build exits with code `0` and creates `dist/cdy_agent-0.1.0.tar.gz` and `dist/cdy_agent-0.1.0-py3-none-any.whl`.

- [ ] **Step 7: Check that only intended files will be committed**

Run:

```powershell
git status --short
```

Expected: `README.md`, `src/cdy_agent/__init__.py`, `src/cdy_agent/cli.py`, and `tests/test_cli.py` are untracked; `.idea/`, `AGENTS.md`, and `uv.lock` remain untracked and unstaged; generated `dist/` artifacts do not appear because they are ignored.

- [ ] **Step 8: Commit the verified engineering skeleton**

```powershell
git add -- README.md src/cdy_agent/__init__.py src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Add tested CLI skeleton"
```

## Final Verification

After the commit, run each command separately:

```powershell
uv run pytest
uv run cdy-agent --help
uv build
git status --short
```

Expected:

- All pytest tests pass.
- CLI help exits successfully and describes the local personal AI assistant.
- Hatchling builds both distribution formats successfully.
- Only the pre-existing unrelated `.idea/`, `AGENTS.md`, and `uv.lock` remain untracked.

