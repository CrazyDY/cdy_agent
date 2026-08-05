# Repository Guidelines

## Project Overview

CDY Agent is a Python 3.10+ local personal-assistant CLI built with Typer and the
OpenAI Python SDK. It supports both the Responses API and Chat Completions API,
streaming and non-streaming Agent Tool Loops, workspace-scoped tools, standard
Agent Skills, persistent conversations and explicit memories, observability, and
file-based evals.

Keep the implementation deliberately direct. Do not introduce an Agent framework,
a generic provider abstraction, automatic memory extraction, a workflow engine,
multi-Agent orchestration, MCP, or a Web UI unless a separately approved design
requires it.

## Source Layout and Ownership

The project uses a `src` layout. Application code belongs in `src/cdy_agent/`, and
tests belong in `tests/` with matching `test_<feature>.py` names. The console entry
point declared in `pyproject.toml` is `cdy_agent.cli:app`.

Keep these boundaries clear:

- `src/cdy_agent/cli.py` owns Typer commands, terminal interaction, configuration
  wiring, confirmation prompts, and user-facing error presentation.
- `src/cdy_agent/config.py` owns workspace configuration loading and effective
  model, API mode, system prompt, and streaming resolution.
- `src/cdy_agent/agent.py` owns the bounded, API-neutral model/tool loop. It must
  not own terminal I/O or persistence policy.
- `src/cdy_agent/openai_client.py` is the only OpenAI-compatible SDK boundary. It
  normalizes Responses and Chat Completions results, continuation state, streamed
  events, tool calls, and usage data.
- `src/cdy_agent/conversation.py` owns in-memory message and conversation models.
- `src/cdy_agent/tools/` owns tool contracts, registry dispatch, confirmations,
  workspace filesystem access, bounded process execution, Shell policy and
  approvals, notes, Todos, and memory tools.
- `src/cdy_agent/skills/` owns standard workspace Skill discovery, validation,
  activation, resource access, and confirmed script execution.
- `src/cdy_agent/memory/` owns the workspace SQLite boundary, saved conversations,
  and explicit long-term memories.
- `src/cdy_agent/observability/` owns structured logging, immutable trace models,
  token/price accounting, recording, and JSONL trace storage.
- `src/cdy_agent/evals.py` owns YAML/JSON eval validation, execution through an
  injected Agent-like object, and deterministic `exact`/`contains` assertions.
- `evals/` contains version-controlled eval definitions such as
  `evals/smoke.yaml`. Keep unit tests offline even though running the CLI eval
  command normally invokes the configured provider.
- `docs/superpowers/specs/` and `docs/superpowers/plans/` are design records. Update
  the active roadmap and README when delivered behavior changes.

Do not move provider logic into the CLI, persistence into the Agent loop, or user
interaction into tools. Add focused modules instead of expanding one file into a
general abstraction layer.

## CLI Surface

The current top-level commands are:

- `ask` — one stateless Agent turn.
- `chat` — a new or explicitly resumed persistent conversation.
- `sessions` — list and delete saved conversations.
- `memories` — add, list, search, update, and delete explicit memories.
- `traces` — list and inspect saved Agent traces.
- `config show` — print effective non-secret configuration.
- `evals run` — run YAML/JSON eval cases and return exit code 1 on any failure.

Preserve these semantics: `ask` stays stateless; `chat` saves only complete,
successful user/assistant turns; memories are searched or changed only after an
explicit user request; streaming must use the same bounded Agent Tool Loop rather
than replaying a request in non-streaming mode.

## Workspace State and Configuration

Runtime state is scoped to `<workspace>/.cdy-agent/`. It may include:

- `config.yaml` for non-secret workspace defaults.
- `cdy-agent.sqlite3` for conversations and explicit memories.
- `notes.json` and `todos.json` for personal tools.
- `traces.jsonl` for sanitized call traces.
- `shell-approvals.json` for exact persistent Shell approvals.
- `skills/<skill-name>/` for workspace Agent Skills.

Treat the entire directory as user/runtime data. It is Git-ignored and must not be
committed, overwritten casually, or used by tests outside a temporary workspace.

Configuration precedence is CLI override, environment variable, workspace
configuration, then built-in default where that setting supports all four layers.
The workspace file accepts only `model`, `api_mode`, `base_url`, `system_prompt`, `stream`,
`max_model_calls`, `log_level`, `rebuild_frontend`, and an `observability` mapping containing
`input_cost_per_million`/`output_cost_per_million`.

Supported environment variables are:

- `OPENAI_API_KEY` — provider credential; required for real calls and never stored.
- `OPENAI_BASE_URL` — OpenAI-compatible provider or gateway URL; overrides the
  workspace `base_url` setting.
- `CDY_AGENT_MODEL` — default model unless `--model` overrides it.
- `CDY_AGENT_API_MODE` — exactly `responses` or `chat_completions`; defaults to
  `responses`.
- `CDY_AGENT_STREAM` — boolean streaming default.
- `CDY_AGENT_MAX_MODEL_CALLS` — positive integer model-call limit; defaults to
  `8`, and `--max-model-calls` takes precedence where an Agent is created.
- `CDY_AGENT_LOG_LEVEL` — `DEBUG`, `INFO`, `WARNING`, or `ERROR`.
- `CDY_AGENT_REBUILD_FRONTEND` — boolean; when true the `web` command rebuilds the
  Vue production assets on every start. Defaults to false, which reuses an existing
  build and only runs the build when `web` static assets are missing.
- `CDY_AGENT_INPUT_COST_PER_MILLION` and
  `CDY_AGENT_OUTPUT_COST_PER_MILLION` — optional non-negative prices that must be
  configured as a pair.

Never place real credentials in source files, tests, command examples, logs,
traces, eval fixtures, committed `.env` files, or workspace configuration.

## Tool and Security Invariants

Security behavior is part of the public contract. Preserve it when refactoring:

- Resolve and revalidate workspace paths before access. Reject traversal,
  symlinks, Windows reparse points, non-regular targets, and workspace escapes.
- Keep file content UTF-8 where the tool contract requires it. Bound input/output
  sizes and return structured `ToolResult` failures rather than leaking internal
  exceptions to the model.
- Use `shell=False` and argv arrays for processes. Keep timeouts, sanitized
  environments, output limits, process-tree cleanup, and workspace cwd behavior.
- Shell policy binds classification, confirmation, persistent approval, and launch
  to one prepared executable and exact argv. Do not widen the conservative
  read-only auto-approval rules or allow prefixes/wildcards in saved approvals.
- Writes, destructive personal-data mutations, memory mutations, and Skill script
  execution require the existing confirmation flow. Denial must fail closed.
- Keep atomic replace/write behavior and schema/version validation for JSON,
  SQLite, traces, and approval files. Do not replace corrupt state with empty data.
- Observability is best effort and must not change the Agent result. Logs and
  traces exclude prompts, replies, tool arguments/results, confirmation text,
  credentials, and environment dumps.
- `Agent.max_model_calls` remains the termination guard for both streaming and
  non-streaming loops. Tool failures are serialized back to the model when they
  are recoverable.

## Standard Agent Skills

Workspace Skills live under `<workspace>/.cdy-agent/skills/<skill-name>/` and use a
strict frontmatter `SKILL.md`. Only `scripts/`, `references/`, and `assets/` are
recognized resource trees.

Discovery must not execute code. Activation returns instructions and a resource
manifest without eagerly reading resources. Resource reads require an active
Skill. Every script execution requires a fresh confirmation, uses a bounded
`shell=False` process, and revalidates the resource before execution. The legacy
root `tools.py`/`create_tools()` dynamic-registration format is unsupported and
must not be restored accidentally.

## Build and Development Commands

The repository tracks `uv.lock`; use `uv` so contributors resolve the same
versions. Never hand-edit the lockfile. When an operation can update it, pass
`--default-index https://pypi.org/simple` so local mirror settings do not rewrite
registry URLs.

Common commands:

```powershell
uv sync --extra dev --default-index https://pypi.org/simple
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv run cdy-agent evals --help
uv build
```

If `uv` is unavailable, use `python -m pip install -e ".[dev]"` in an activated
environment, but do not generate or edit `uv.lock` with pip.

Ruff is present in the development dependency group, but the repository currently
has no enforced Ruff configuration or clean whole-repository Ruff baseline. It is
safe to use Ruff diagnostically on changed files; do not apply broad automatic
fixes or unrelated formatting churn.

## Coding Style

Use four-space indentation, UTF-8, type hints on public functions, and concise
docstrings where behavior is not obvious. Follow standard Python naming:
`snake_case` for modules/functions/variables, `PascalCase` for classes, and
`UPPER_SNAKE_CASE` for constants.

Prefer immutable dataclasses for domain records, `collections.abc` for public
collection protocols, `pathlib.Path` for paths, and explicit structured results at
tool/storage boundaries. Preserve existing exception and CLI error contracts.
Avoid unrelated formatting changes and speculative abstractions.

## Testing Guidelines

Tests use pytest and must be offline and deterministic. Name files
`test_<feature>.py` and functions `test_<behavior>()`. Add focused unit tests for
new behavior and regression tests for fixes.

- Mock OpenAI SDK, process, clock, filesystem race, and confirmation boundaries.
- Use `tmp_path`; never read or mutate contributor workspace state.
- Explicitly isolate all relevant provider/configuration environment variables,
  especially `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CDY_AGENT_API_MODE`, model,
  model-call limit, streaming, logging, and pricing variables.
- Use the shared `make_symlink` fixture for tests that require symlink creation; it
  skips only when the current platform/account lacks that capability.
- Test both `responses` and `chat_completions`, and both streaming and
  non-streaming paths when changing the SDK or Agent boundary.
- Verify tool confirmation denial, malformed arguments, workspace escape,
  continuation identity, model-call limits, and persistence failure isolation as
  applicable.
- Validate repository eval files with an injected fake Agent. Do not run real
  provider evals as part of the automated suite.

Run focused tests while iterating, then `uv run pytest` before committing. Run CLI
help commands for command-surface changes and `uv build` for packaging or release
changes.

## Git and Documentation Hygiene

Use short imperative commit summaries such as `Add API mode configuration` or
`Harden Shell approval validation`. Keep each commit scoped and explain
non-obvious security or compatibility tradeoffs in the body.

Pull requests should describe the change, motivation, and verification performed;
link issues and include CLI output or screenshots for user-visible changes. Update
README examples and the relevant design/roadmap document when CLI behavior,
configuration, persistence formats, or security semantics change.

Do not commit `.venv/`, `.env`, `.cdy-agent/`, `.pytest_cache/`, `__pycache__/`,
`.idea/`, generated distributions, model responses, local debug files, or secrets.
Preserve unrelated user changes and untracked local tooling files.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo
root), reach for it before grep/find or reading files when you need to understand
or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in
  one call—the relevant symbols' verbatim source plus call paths, including
  dynamic-dispatch hops grep cannot follow. Name a file or symbol in the query to
  read its current line-numbered source. If deferred, load it by name through tool
  search.
- **Shell** (always available): `codegraph explore "<symbol names or question>"`
  prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely; indexing is the
user's decision.
<!-- CODEGRAPH_END -->
