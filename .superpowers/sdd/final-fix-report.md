# Final Review Fix Report

## Scope and root causes

- Shell validation only checked the executable (and basic git subcommand), leaving execution-delegating options available to allowlisted programs.
- Registry confirmation ran before tool-specific validation, so invalid or unsafe calls could prompt users.
- Chat continuation stored only the latest assistant tool-call message, replacing prior current-turn rounds.
- Shell output limits counted Python characters instead of encoded UTF-8 bytes.
- Confirmation and symlink edge cases lacked focused regression coverage.

## RED evidence

Command:

`UV_CACHE_DIR=/tmp/cdy-agent-final-fix-cache uv run pytest tests/test_shell_tool.py tests/test_tool_registry.py tests/test_filesystem_tools.py tests/test_openai_client.py -q`

Result: collection failed as expected because `MAX_OUTPUT_BYTES` did not exist. This was the first missing behavior reached by the added regression suite.

## GREEN evidence

- Focused affected suite: `108 passed in 0.36s`.
- Full suite after all additions: `165 passed in 0.46s`.
- `uv run cdy-agent --help`: exit 0; `ask` and `chat` commands listed.
- `uv run cdy-agent ask --help`: exit 0; prompt, model, and workspace interface intact.
- First sandboxed `uv build`: failed only because network access to PyPI was denied.
- Approved network retry of `UV_CACHE_DIR=/tmp/cdy-agent-final-fix-cache uv build`: built both sdist and wheel successfully.
- `git diff --check`: exit 0.

## Files changed

- `src/cdy_agent/tools/base.py`: added the tool preflight contract.
- `src/cdy_agent/tools/registry.py`: runs preflight before confirmation.
- `src/cdy_agent/tools/filesystem.py`: pure read/write preflight with direct execution validation preserved.
- `src/cdy_agent/tools/shell.py`: blocks execution delegation, preflights calls, includes argv/workspace in confirmation, and caps UTF-8 bytes.
- `src/cdy_agent/openai_client.py`: accumulates Chat Completions tool-round history.
- `tests/test_shell_tool.py`, `tests/test_tool_registry.py`, `tests/test_filesystem_tools.py`, `tests/test_openai_client.py`: focused regressions.

## Self-review

- `shell=False` remains explicit.
- All listed executable names remain available; only execution-affecting forms are rejected.
- Responses API continuation behavior is untouched.
- Write and shell `execute` methods retain validation for safe direct calls.
- Preflight performs resolution and metadata checks only; it does not write or run commands.
- Path resolution retains the approved v1 `Path.resolve` semantics; no TOCTOU/openat expansion was introduced.
- No provider abstraction, API mode, CLI, documentation, or limit changes beyond the requested fixes.
- Existing untracked scratch review artifacts were not modified.
