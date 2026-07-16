# Repository Guidelines and Lockfile Tracking Design

## Background

Phase 2 added environment-selected Responses and Chat Completions support, but the repository-level agent instructions still describe only the original project skeleton. `AGENTS.md` and `uv.lock` are currently untracked. The guidelines also contain corrupted punctuation that should be normalized to valid UTF-8 text.

## Goals

- Update `AGENTS.md` to match the current Phase 2 architecture and workflow.
- Preserve its concise repository-guideline format rather than turning it into a product manual.
- Document the two API modes and environment-only credential configuration.
- Make `uv.lock` a required tracked file and explain how contributors update it.
- Verify the documented development commands before committing.
- Commit the guideline and lockfile changes, then push the current `main` branch to `origin`.

## Non-goals

- Do not change application behavior, dependencies, package versions, or API defaults.
- Do not track `.idea/`, `.env`, caches, virtual environments, secrets, or model responses.
- Do not create a pull request or additional feature branch.
- Do not manually edit generated lockfile contents.

## `AGENTS.md` Content

Keep the existing five-section structure:

1. Project Structure & Module Organization
2. Build, Test, and Development Commands
3. Coding Style & Naming Conventions
4. Testing Guidelines
5. Commit & Pull Request Guidelines

Update the structure section with the current module boundaries:

- `src/cdy_agent/config.py` owns model and API-mode resolution.
- `src/cdy_agent/openai_client.py` owns the OpenAI-compatible Responses and Chat Completions SDK boundary.
- `src/cdy_agent/cli.py` owns Typer commands and user-facing error presentation.

Document the configuration contract:

- `CDY_AGENT_API_MODE` accepts exactly `responses` and `chat_completions` and defaults to `responses`.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `CDY_AGENT_MODEL` are environment configuration.
- Credentials and `.env` contents must never be committed.

Document lockfile policy:

- `uv.lock` is committed so contributors resolve consistent dependency versions.
- Contributors update it through `uv sync` or other appropriate `uv` commands, not by manual editing.

The command list must include:

```text
uv sync --extra dev
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
```

Normalize corrupted punctuation to valid UTF-8 while avoiding unrelated prose expansion.

## Lockfile Handling

Use the existing generated `uv.lock` as the starting point. Run `uv sync --extra dev` to verify it agrees with `pyproject.toml`. If `uv` updates the file as part of normal resolution, review the resulting diff and commit it. Do not regenerate dependencies for unrelated reasons or hand-edit package records.

## Verification

Before commit and push, run:

```powershell
uv sync --extra dev
uv run pytest -p no:cacheprovider
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
git diff --check
```

All commands must exit successfully. Tests must remain offline and must not require a real API key.

## Git Scope and Publication

The implementation commit tracks `AGENTS.md` and `uv.lock`. The approved design and implementation-plan records are also tracked under `docs/superpowers/` as part of the repository's established development workflow. `.idea/` remains untracked.

Push the verified local `main` branch to its configured `origin` without force-pushing. Success means the remote accepts the latest local commit and local `main` no longer has unpushed commits relative to `origin/main`.

## Acceptance Criteria

- `AGENTS.md` accurately reflects the dual-API Phase 2 architecture and contains no corrupted punctuation.
- `AGENTS.md` explicitly requires tracking and `uv`-managed updating of `uv.lock`.
- `uv.lock` is tracked by Git and is consistent with `pyproject.toml`.
- `.idea/` remains untracked.
- All verification commands pass.
- The resulting commits are pushed to `origin/main` without a force push.
