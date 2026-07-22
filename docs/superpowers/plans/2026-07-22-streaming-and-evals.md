# Streaming Output and Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable streaming output and offline evaluation case execution for phase 8.

**Architecture:** Keep provider details in `openai_client.py`, orchestration in `agent.py`, user output and command wiring in `cli.py`, and eval-case loading/execution in a new focused module. Streaming is opt-in and follows existing configuration precedence: CLI override, environment, workspace config, default.

**Tech Stack:** Python 3.10+, Typer, PyYAML, pytest, OpenAI Python SDK boundary.

## Global Constraints

- Use `uv run pytest` for verification.
- Tests must not require real API keys, network access, or user filesystem state.
- Do not commit credentials, model responses, caches, or IDE settings.
- Keep module boundaries aligned with `config.py`, `openai_client.py`, `agent.py`, and `cli.py`.

---

### Task 1: Streaming Configuration

**Files:**
- Modify: `src/cdy_agent/config.py`
- Modify: `src/cdy_agent/cli.py`
- Test: `tests/test_config.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `resolve_streaming(stream_override: bool | None = None, workspace_config: WorkspaceConfig | None = None) -> bool`
- Produces: `WorkspaceConfig.stream: bool | None`

- [ ] Write failing config tests for workspace, environment, CLI override, default, and invalid boolean values.
- [ ] Run focused config tests and confirm failures.
- [ ] Implement boolean parsing and workspace `stream` support.
- [ ] Run focused config tests and confirm pass.
- [ ] Add CLI tests for `--stream`, `--no-stream`, config default, and `config show`.
- [ ] Implement CLI option propagation.
- [ ] Run focused CLI tests and confirm pass.

### Task 2: Streaming Model and Agent Boundary

**Files:**
- Modify: `src/cdy_agent/openai_client.py`
- Modify: `src/cdy_agent/agent.py`
- Test: `tests/test_openai_client.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `ModelGateway.stream(...) -> Iterator[str]`
- Produces: `Agent.run_stream(...) -> str`

- [ ] Write failing OpenAI boundary tests for Responses and Chat Completions text deltas.
- [ ] Run focused OpenAI tests and confirm failures.
- [ ] Implement streaming event normalization.
- [ ] Run focused OpenAI tests and confirm pass.
- [ ] Write failing Agent tests for streaming direct final replies and tool-call fallback.
- [ ] Run focused Agent tests and confirm failures.
- [ ] Implement `Agent.run_stream`, yielding text chunks and returning the final text to callers through a callback interface.
- [ ] Run focused Agent tests and confirm pass.

### Task 3: CLI Streaming Output

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Agent.run_stream(messages, on_text, recorder) -> str`

- [ ] Write failing CLI tests proving streamed chunks are printed without adding extra line breaks and chat persistence stores the final reply.
- [ ] Run focused CLI tests and confirm failures.
- [ ] Implement traced streaming helper and wire `ask`/`chat`.
- [ ] Run focused CLI tests and confirm pass.

### Task 4: Offline Eval Cases

**Files:**
- Create: `src/cdy_agent/evals.py`
- Modify: `src/cdy_agent/cli.py`
- Test: `tests/test_evals.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `run_eval_file(path: Path, agent_factory: Callable[[str], AgentLike]) -> EvalReport`
- Produces: CLI command `cdy-agent evals run <file> --workspace <path>`

- [ ] Write failing eval runner tests for pass/fail summaries, required fields, and non-network behavior.
- [ ] Run focused eval tests and confirm failures.
- [ ] Implement YAML/JSON case loading and exact/contains assertions.
- [ ] Run focused eval tests and confirm pass.
- [ ] Add CLI tests for eval command output and exit code.
- [ ] Implement Typer subcommand wiring.
- [ ] Run focused CLI eval tests and confirm pass.

### Task 5: Verification

- [ ] Run `uv run pytest`.
- [ ] Run `uv run cdy-agent --help`.
- [ ] Run `uv run cdy-agent ask --help`.
- [ ] Report exact verification results and any remaining risk.
