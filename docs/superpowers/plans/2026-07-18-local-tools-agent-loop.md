# Local Tools Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dual-API Agent Tool Loop that can safely read and write workspace files and execute explicitly approved, allowlisted commands.

**Architecture:** A single `Agent` owns the model/tool loop and consumes a thin normalized model gateway implemented by `openai_client.py`. A registry dispatches uniform tool calls to focused filesystem and shell tools, while the CLI supplies workspace configuration and a confirmation callback for side effects.

**Tech Stack:** Python 3.10+, OpenAI Python SDK, Typer, pytest, standard-library `dataclasses`, `json`, `pathlib`, and `subprocess`.

## Global Constraints

- Support exactly `responses` and `chat_completions`; do not add a general provider abstraction.
- `ask` and `chat` both use the same Agent entry point.
- The default workspace is the real current working directory resolved when the command starts; `--workspace` may override it.
- `read_file` runs without confirmation; every `write_file` and `run_shell` call requires a default-No confirmation.
- File access must stay inside the real workspace, including through symlinks.
- `read_file` accepts UTF-8 regular files and returns at most 1 MiB with an explicit truncation marker.
- `write_file` does not create parent directories and requires both `overwrite=true` and user confirmation to replace a file.
- Shell execution uses `shell=False`, `cwd=workspace`, argument arrays, a 1–30 second timeout, and 64 KiB caps for stdout and stderr.
- Allow only `pwd`, `ls`, `find`, `rg`, `grep`, `sed`, `head`, `tail`, `wc`, `sort`, `uniq`, plus `git status` and `git diff`.
- The Agent makes at most 8 model calls per user turn.
- Tests must not use a real API key, network, or contributor filesystem.

---

## File Structure

- Create `src/cdy_agent/tools/__init__.py`: export built-in registry construction.
- Create `src/cdy_agent/tools/base.py`: normalized calls, results, confirmation requests, and tool protocol.
- Create `src/cdy_agent/tools/registry.py`: schema collection, JSON argument decoding, lookup, confirmation, and dispatch.
- Create `src/cdy_agent/tools/filesystem.py`: workspace path resolver plus read/write tools.
- Create `src/cdy_agent/tools/shell.py`: argv validation and subprocess execution.
- Create `src/cdy_agent/agent.py`: normalized model outcomes and the bounded tool loop.
- Modify `src/cdy_agent/openai_client.py`: implement the model gateway for both SDK APIs.
- Modify `src/cdy_agent/cli.py`: construct the Agent, resolve workspace, and prompt for confirmation.
- Modify `src/cdy_agent/conversation.py`: no new message roles; only add final replies after Agent success.
- Create `tests/test_tool_registry.py`, `tests/test_filesystem_tools.py`, `tests/test_shell_tool.py`, and `tests/test_agent.py`.
- Modify `tests/test_openai_client.py`, `tests/test_cli.py`, `README.md`, and the roadmap design.

### Task 1: Normalized Tool Contracts and Registry

**Files:**
- Create: `src/cdy_agent/tools/base.py`
- Create: `src/cdy_agent/tools/registry.py`
- Test: `tests/test_tool_registry.py`

**Interfaces:**
- Produces: `ToolCall(call_id: str, name: str, arguments_json: str)`.
- Produces: `ToolResult.success(data)` and `ToolResult.failure(code, message)`, with `to_json() -> str`.
- Produces: `Tool(name, description, parameters, requires_confirmation, execute)` protocol.
- Produces: `ConfirmationRequest(tool_name, arguments, description)` and `ConfirmationCallback`.
- Produces: `ToolRegistry.definitions` and `ToolRegistry.execute(call, confirm) -> ToolResult`.

- [ ] **Step 1: Write failing registry tests**

```python
# tests/test_tool_registry.py
import json
from dataclasses import dataclass
from typing import Any

from cdy_agent.tools.base import ConfirmationRequest, ToolCall, ToolResult
from cdy_agent.tools.registry import ToolRegistry


@dataclass
class EchoTool:
    name: str = "echo"
    description: str = "Echo text."
    parameters: dict[str, Any] = None  # type: ignore[assignment]
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
            return ToolResult.failure("invalid_arguments", "text must be a string.")
        return ToolResult.success({"text": arguments["text"]})


def test_registry_exposes_function_definition_and_executes() -> None:
    registry = ToolRegistry([EchoTool()])
    result = registry.execute(
        ToolCall("call-1", "echo", '{"text":"hello"}'),
        confirm=lambda request: True,
    )
    assert registry.definitions == ({
        "type": "function",
        "name": "echo",
        "description": "Echo text.",
        "parameters": EchoTool().parameters,
    },)
    assert json.loads(result.to_json()) == {
        "ok": True,
        "data": {"text": "hello"},
    }


def test_registry_returns_structured_errors() -> None:
    registry = ToolRegistry([EchoTool()])
    assert registry.execute(ToolCall("1", "missing", "{}"), lambda _: True).code == "unknown_tool"
    assert registry.execute(ToolCall("2", "echo", "{"), lambda _: True).code == "invalid_arguments"
    assert registry.execute(ToolCall("3", "echo", "[]"), lambda _: True).code == "invalid_arguments"


def test_registry_denies_confirmed_tool_without_executing() -> None:
    tool = EchoTool(requires_confirmation=True)
    requests: list[ConfirmationRequest] = []
    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        confirm=lambda request: requests.append(request) or False,
    )
    assert result.code == "approval_denied"
    assert requests[0].tool_name == "echo"
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `uv run pytest tests/test_tool_registry.py -v`

Expected: collection fails because `cdy_agent.tools` does not exist.

- [ ] **Step 3: Implement the contracts and registry**

```python
# src/cdy_agent/tools/base.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    code: str | None = None
    message: str | None = None

    @classmethod
    def success(cls, data: Any) -> "ToolResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: str, message: str) -> "ToolResult":
        return cls(ok=False, code=code, message=message)

    def to_json(self) -> str:
        value = {"ok": True, "data": self.data} if self.ok else {
            "ok": False,
            "error": {"code": self.code, "message": self.message},
        }
        return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class ConfirmationRequest:
    tool_name: str
    arguments: dict[str, Any]
    description: str


ConfirmationCallback = Callable[[ConfirmationRequest], bool]


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool

    def confirmation_description(self, arguments: dict[str, Any]) -> str: ...
    def execute(self, arguments: dict[str, Any]) -> ToolResult: ...
```

```python
# src/cdy_agent/tools/registry.py
from __future__ import annotations

import json
from collections.abc import Iterable

from .base import ConfirmationCallback, ConfirmationRequest, Tool, ToolCall, ToolResult


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @property
    def definitions(self) -> tuple[dict[str, object], ...]:
        return tuple({
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        } for tool in self._tools.values())

    def execute(self, call: ToolCall, confirm: ConfirmationCallback) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.failure("unknown_tool", f"Unknown tool: {call.name}.")
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            return ToolResult.failure("invalid_arguments", "Arguments must be valid JSON.")
        if not isinstance(arguments, dict):
            return ToolResult.failure("invalid_arguments", "Arguments must be a JSON object.")
        if tool.requires_confirmation:
            request = ConfirmationRequest(
                tool.name,
                arguments,
                tool.confirmation_description(arguments),
            )
            if not confirm(request):
                return ToolResult.failure("approval_denied", "User declined this tool call.")
        return tool.execute(arguments)
```

For test doubles that do not require confirmation, add `confirmation_description()` returning `"Echo text."`; do not weaken the production protocol.

- [ ] **Step 4: Run focused and existing tests**

Run: `uv run pytest tests/test_tool_registry.py tests/test_conversation.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cdy_agent/tools/base.py src/cdy_agent/tools/registry.py tests/test_tool_registry.py
git commit -m "Add tool registry contracts"
```

### Task 2: Workspace Resolver and File Reading

**Files:**
- Create: `src/cdy_agent/tools/filesystem.py`
- Test: `tests/test_filesystem_tools.py`

**Interfaces:**
- Produces: `resolve_workspace(path: Path) -> Path`.
- Produces: `ReadFileTool(workspace: Path)` implementing `Tool`.
- Uses: `ToolResult` from Task 1.

- [ ] **Step 1: Write failing path and read tests**

```python
# tests/test_filesystem_tools.py
from pathlib import Path

import pytest

from cdy_agent.tools.filesystem import ReadFileTool, resolve_workspace


def test_resolve_workspace_requires_directory(tmp_path: Path) -> None:
    assert resolve_workspace(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError, match="workspace"):
        resolve_workspace(tmp_path / "missing")


def test_read_file_reads_utf8_and_truncates(tmp_path: Path) -> None:
    (tmp_path / "short.txt").write_text("你好", encoding="utf-8")
    (tmp_path / "large.txt").write_bytes(b"a" * (1024 * 1024 + 1))
    tool = ReadFileTool(tmp_path)
    assert tool.execute({"path": "short.txt"}).data == {
        "path": str((tmp_path / "short.txt").resolve()),
        "content": "你好",
        "truncated": False,
    }
    large = tool.execute({"path": "large.txt"})
    assert large.ok is True
    assert large.data["truncated"] is True
    assert len(large.data["content"]) == 1024 * 1024


@pytest.mark.parametrize("arguments", [{}, {"path": 1}, {"path": "a", "extra": 1}])
def test_read_file_rejects_invalid_arguments(tmp_path: Path, arguments: dict[str, object]) -> None:
    assert ReadFileTool(tmp_path).execute(arguments).code == "invalid_arguments"


def test_read_file_rejects_escape_directory_and_binary(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    (tmp_path / "binary").write_bytes(b"\xff")
    tool = ReadFileTool(tmp_path)
    assert tool.execute({"path": "../outside.txt"}).code == "path_outside_workspace"
    assert tool.execute({"path": "folder"}).code == "not_a_file"
    assert tool.execute({"path": "binary"}).code == "unsupported_encoding"
```

Add a symlink test guarded by `pytest.skip` when the platform cannot create symlinks; point the link outside and assert `path_outside_workspace`.

- [ ] **Step 2: Run the tests to verify failure**

Run: `uv run pytest tests/test_filesystem_tools.py -v`

Expected: collection fails because `filesystem.py` does not exist.

- [ ] **Step 3: Implement workspace resolution and `ReadFileTool`**

Use these exact constants and validation rules:

```python
MAX_READ_BYTES = 1024 * 1024


def resolve_workspace(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Invalid workspace directory: {path}.")
    return resolved


def _resolve_existing(workspace: Path, raw_path: object) -> Path | ToolResult:
    if not isinstance(raw_path, str) or not raw_path:
        return ToolResult.failure("invalid_arguments", "path must be a non-empty string.")
    target = (workspace / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        return ToolResult.failure("path_outside_workspace", "Path is outside the workspace.")
    return target
```

`ReadFileTool.execute()` must require exactly `{"path"}`, reject non-files, read at most `MAX_READ_BYTES + 1` bytes, truncate before UTF-8 decoding, and map `UnicodeDecodeError` to `unsupported_encoding` and `OSError` to `file_error`. Its schema must set `additionalProperties: false` and its `requires_confirmation` must be `False`.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_filesystem_tools.py tests/test_tool_registry.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cdy_agent/tools/filesystem.py tests/test_filesystem_tools.py
git commit -m "Add workspace file reading"
```

### Task 3: Confirmed File Writing

**Files:**
- Modify: `src/cdy_agent/tools/filesystem.py`
- Modify: `tests/test_filesystem_tools.py`

**Interfaces:**
- Produces: `WriteFileTool(workspace: Path)` implementing `Tool`.
- `confirmation_description()` returns the operation, absolute target, and UTF-8 byte count.

- [ ] **Step 1: Add failing write tests**

```python
from cdy_agent.tools.filesystem import WriteFileTool


def test_write_file_creates_and_explicitly_overwrites(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path)
    created = tool.execute({"path": "note.txt", "content": "hello"})
    assert created.ok is True
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"
    denied = tool.execute({"path": "note.txt", "content": "new"})
    assert denied.code == "overwrite_not_allowed"
    replaced = tool.execute({"path": "note.txt", "content": "new", "overwrite": True})
    assert replaced.ok is True
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "new"


def test_write_file_requires_existing_parent_and_stays_in_workspace(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path)
    assert tool.execute({"path": "missing/note.txt", "content": "x"}).code == "parent_not_found"
    assert tool.execute({"path": "../outside.txt", "content": "x"}).code == "path_outside_workspace"


def test_write_description_identifies_create_or_overwrite(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path)
    create = tool.confirmation_description({"path": "note.txt", "content": "你好"})
    assert "create" in create.lower()
    assert str((tmp_path / "note.txt").resolve()) in create
    assert "6 bytes" in create
```

Also parametrize exact accepted keys and types: `path: str`, `content: str`, optional `overwrite: bool`; assert extra keys and wrong types return `invalid_arguments` before filesystem mutation.

- [ ] **Step 2: Run the new tests to verify failure**

Run: `uv run pytest tests/test_filesystem_tools.py -v`

Expected: import fails because `WriteFileTool` is absent.

- [ ] **Step 3: Implement `WriteFileTool`**

Set `requires_confirmation = True`. Resolve an existing target with `.resolve()`; for a new target, resolve its existing parent and then append `Path(raw_path).name`. Reject an absent parent, a directory target, workspace escape, and an existing file unless `overwrite is True`. Write with `target.write_text(content, encoding="utf-8")`; return `{path, bytes, overwritten}` on success and `file_error` on `OSError`.

Keep confirmation pure: it must validate enough to describe the exact operation but must not create or modify a file.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_filesystem_tools.py tests/test_tool_registry.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cdy_agent/tools/filesystem.py tests/test_filesystem_tools.py
git commit -m "Add confirmed file writing"
```

### Task 4: Allowlisted Shell Tool

**Files:**
- Create: `src/cdy_agent/tools/shell.py`
- Test: `tests/test_shell_tool.py`

**Interfaces:**
- Produces: `ShellTool(workspace: Path, runner=subprocess.run)`.
- Uses: `ToolResult`; requires confirmation.

- [ ] **Step 1: Write failing validation and execution tests**

```python
# tests/test_shell_tool.py
import subprocess
from pathlib import Path

import pytest

from cdy_agent.tools.shell import ShellTool


@pytest.mark.parametrize("argv", [
    ["rm", "file"], ["/bin/ls"], ["./ls"], ["git", "log"],
    ["git", "-C", "..", "status"], ["git", "--git-dir=../.git", "diff"],
])
def test_shell_rejects_disallowed_commands(tmp_path: Path, argv: list[str]) -> None:
    assert ShellTool(tmp_path).execute({"argv": argv}).code == "command_not_allowed"


def test_shell_invokes_runner_without_shell(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, "ok", "")
    result = ShellTool(tmp_path, runner=runner).execute({
        "argv": ["git", "status", "--short"], "timeout_seconds": 4,
    })
    assert result.ok is True
    assert calls == [{
        "argv": ["git", "status", "--short"],
        "cwd": tmp_path.resolve(), "shell": False, "capture_output": True,
        "text": True, "timeout": 4, "check": False,
    }]


def test_shell_metacharacters_are_plain_arguments(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    ShellTool(tmp_path, runner=runner).execute({"argv": ["rg", "|", "."]})
    assert calls == [["rg", "|", "."]]
```

Add tests for empty/non-string argv, extra arguments, timeout values 0 and 31, `TimeoutExpired`, `OSError`, nonzero exit, and independent 64 KiB truncation of stdout and stderr.

- [ ] **Step 2: Run the tests to verify failure**

Run: `uv run pytest tests/test_shell_tool.py -v`

Expected: collection fails because `shell.py` does not exist.

- [ ] **Step 3: Implement `ShellTool`**

Define:

```python
ALLOWED_COMMANDS = frozenset({
    "pwd", "ls", "find", "rg", "grep", "sed", "head", "tail",
    "wc", "sort", "uniq",
})
ALLOWED_GIT_SUBCOMMANDS = frozenset({"status", "diff"})
MAX_OUTPUT_CHARS = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30
```

Require exactly `argv` and optional `timeout_seconds`. Reject a blank argv, non-string elements, a command containing `/` or `\\`, and Git calls whose second element is not an allowed subcommand. Call the injected runner with the exact keyword arguments asserted above. Return `command_timeout`, `command_failed`, or `execution_error` as applicable; success data contains `returncode`, `stdout`, `stderr`, `stdout_truncated`, and `stderr_truncated`.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_shell_tool.py tests/test_tool_registry.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cdy_agent/tools/shell.py tests/test_shell_tool.py
git commit -m "Add allowlisted shell tool"
```

### Task 5: Dual-API Model Gateway

**Files:**
- Modify: `src/cdy_agent/openai_client.py`
- Modify: `tests/test_openai_client.py`

**Interfaces:**
- Produces: `FinalResponse(text: str)` and `ToolCallResponse(calls, continuation)`.
- Produces: `ModelGateway.create(messages, tools, continuation=None, tool_outputs=())`.
- Consumes: `Message`, `ToolCall`, and `(call_id, result_json)` outputs.

- [ ] **Step 1: Add failing Responses adapter tests**

Create fake SDK responses containing `output_text`, `output`, `id`, and function-call items. Assert the first request includes `tools` in Responses function format and parses every item into `ToolCall`. For continuation assert the second request supplies `previous_response_id` and `input` items shaped as:

```python
{"type": "function_call_output", "call_id": "call-1", "output": result_json}
```

Assert a text-only response becomes `FinalResponse`, and missing text plus missing calls raises `RuntimeError("OpenAI returned an unsupported response.")`.

- [ ] **Step 2: Add failing Chat Completions adapter tests**

Create fake assistant messages with `content` and `tool_calls`. Assert tool definitions are converted to:

```python
{"type": "function", "function": {
    "name": "read_file", "description": "Read a file.", "parameters": parameters,
}}
```

Assert continuation retains the assistant tool-call message and appends one `{"role": "tool", "tool_call_id": call_id, "content": result_json}` per output before the next request.

- [ ] **Step 3: Run adapter tests to verify failure**

Run: `uv run pytest tests/test_openai_client.py -v`

Expected: failures show the normalized outcome and gateway interfaces are absent.

- [ ] **Step 4: Implement normalized gateway without breaking wrapper functions**

Add frozen dataclasses for both outcomes and API-specific continuation values. `ModelGateway` owns `model`, `api_mode`, and the SDK client. Preserve `generate_reply()` and `generate_reply_for_messages()` as compatibility wrappers that call the gateway with no tools and require `FinalResponse`.

Responses parsing must read all `response.output` items whose `type == "function_call"`; Chat parsing must read all `message.tool_calls`. Validate non-empty call IDs, names, and string JSON arguments. Keep existing missing-key client construction and API-mode validation behavior.

- [ ] **Step 5: Run client regression tests**

Run: `uv run pytest tests/test_openai_client.py -v`

Expected: old and new tests all pass.

- [ ] **Step 6: Commit**

```bash
git add src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Adapt dual APIs for tool calls"
```

### Task 6: Bounded Agent Tool Loop and Built-in Registry

**Files:**
- Create: `src/cdy_agent/agent.py`
- Create: `src/cdy_agent/tools/__init__.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `Agent(gateway, registry, confirm, max_model_calls=8)`.
- Produces: `Agent.run(messages: Sequence[Message]) -> str`.
- Produces: `AgentLoopLimitError`.
- Produces: `create_builtin_registry(workspace: Path) -> ToolRegistry`.

- [ ] **Step 1: Write failing Agent tests**

```python
# tests/test_agent.py
from cdy_agent.agent import Agent, AgentLoopLimitError
from cdy_agent.conversation import Message
from cdy_agent.openai_client import FinalResponse, ToolCallResponse
from cdy_agent.tools.base import ToolCall, ToolResult


class FakeGateway:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return next(self.outcomes)


class FakeRegistry:
    definitions = ({"type": "function", "name": "echo", "description": "", "parameters": {}},)
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []
    def execute(self, call: ToolCall, confirm: object) -> ToolResult:
        self.calls.append(call)
        return ToolResult.success({"value": call.name})


def test_agent_returns_direct_response() -> None:
    gateway = FakeGateway([FinalResponse("done")])
    assert Agent(gateway, FakeRegistry(), lambda _: False).run(
        [Message("user", "hello")]
    ) == "done"


def test_agent_executes_batch_and_continues() -> None:
    calls = (ToolCall("1", "a", "{}"), ToolCall("2", "b", "{}"))
    gateway = FakeGateway([ToolCallResponse(calls, "next"), FinalResponse("done")])
    registry = FakeRegistry()
    assert Agent(gateway, registry, lambda _: True).run([Message("user", "go")]) == "done"
    assert registry.calls == list(calls)
    assert gateway.calls[1]["tool_outputs"] == (
        ("1", ToolResult.success({"value": "a"}).to_json()),
        ("2", ToolResult.success({"value": "b"}).to_json()),
    )
```

Add a test with eight consecutive `ToolCallResponse` objects and assert the ninth request is never made and `AgentLoopLimitError` is raised. Add tests for empty histories and `max_model_calls < 1`.

- [ ] **Step 2: Run Agent tests to verify failure**

Run: `uv run pytest tests/test_agent.py -v`

Expected: collection fails because `agent.py` does not exist.

- [ ] **Step 3: Implement the loop**

```python
class AgentLoopLimitError(RuntimeError):
    pass


class Agent:
    def __init__(self, gateway, registry, confirm, max_model_calls: int = 8) -> None:
        if max_model_calls < 1:
            raise ValueError("max_model_calls must be at least 1.")
        self._gateway = gateway
        self._registry = registry
        self._confirm = confirm
        self._max_model_calls = max_model_calls

    def run(self, messages: Sequence[Message]) -> str:
        if not messages:
            raise ValueError("Conversation history must not be empty.")
        continuation = None
        outputs: tuple[tuple[str, str], ...] = ()
        for _ in range(self._max_model_calls):
            outcome = self._gateway.create(
                messages=messages,
                tools=self._registry.definitions,
                continuation=continuation,
                tool_outputs=outputs,
            )
            if isinstance(outcome, FinalResponse):
                return outcome.text
            outputs = tuple(
                (call.call_id, self._registry.execute(call, self._confirm).to_json())
                for call in outcome.calls
            )
            continuation = outcome.continuation
        raise AgentLoopLimitError("Agent exceeded the maximum of 8 model calls.")
```

`create_builtin_registry()` returns `ToolRegistry([ReadFileTool(workspace), WriteFileTool(workspace), ShellTool(workspace)])` in that deterministic order.

- [ ] **Step 4: Run Agent and tool tests**

Run: `uv run pytest tests/test_agent.py tests/test_tool_registry.py tests/test_filesystem_tools.py tests/test_shell_tool.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cdy_agent/agent.py src/cdy_agent/tools/__init__.py tests/test_agent.py
git commit -m "Add bounded agent tool loop"
```

### Task 7: CLI Agent Integration and Confirmation

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `resolve_workspace`, `create_builtin_registry`, `ModelGateway`, and `Agent`.
- Produces: `_confirm_tool(request: ConfirmationRequest) -> bool` and a shared `_create_agent(model, api_mode, workspace)` helper.

- [ ] **Step 1: Replace CLI mocks with failing Agent-boundary tests**

Patch `_create_agent` with a fake exposing `run(messages)`. Assert `ask` passes one normalized user message, while `chat` passes accumulated user/final-assistant history and appends only returned final replies.

Add tests:

```python
def test_ask_defaults_workspace_to_invocation_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(cli, "_create_agent", lambda model, api_mode, workspace: seen.append(workspace) or FakeAgent("ok"))
    result = runner.invoke(app, ["ask", "hello"])
    assert result.exit_code == 0
    assert seen == [tmp_path.resolve()]


def test_ask_rejects_invalid_workspace():
    result = runner.invoke(app, ["ask", "hello", "--workspace", "missing"])
    assert result.exit_code == 1
    assert "workspace" in result.stderr.lower()


def test_confirmation_defaults_to_no():
    request = ConfirmationRequest("run_shell", {"argv": ["pwd"]}, "Run ['pwd']")
    result = runner.invoke(confirm_test_app, [], input="\n")
    assert result.exit_code == 0
    assert result.stdout.endswith("DENIED\n")
```

Also assert `n`, invalid input, EOF, and keyboard interrupt deny; only case-insensitive `y` and `yes` approve. Assert the operation description appears before the prompt.

- [ ] **Step 2: Run CLI tests to verify failure**

Run: `uv run pytest tests/test_cli.py -v`

Expected: failures show `--workspace`, `_create_agent`, and confirmation behavior are absent.

- [ ] **Step 3: Integrate the Agent**

Add `workspace: Annotated[Path | None, typer.Option(...)] = None` to both commands. Resolve `(workspace or Path.cwd())` at command execution. `_create_agent` constructs `ModelGateway`, the built-in registry, and `Agent(..., confirm=_confirm_tool)`.

Implement confirmation with `typer.confirm(request.description, default=False)` and catch `EOFError`, `KeyboardInterrupt`, and `click.Abort`, returning `False`. Both commands catch `AgentLoopLimitError` through the existing user-facing error path. In `chat`, append the final assistant message only after `agent.run()` returns successfully.

- [ ] **Step 4: Run CLI and full regression tests**

Run: `uv run pytest tests/test_cli.py tests/test_conversation.py tests/test_config.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Connect CLI to local tool agent"
```

### Task 8: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`

**Interfaces:**
- Documents the shipped command options, confirmations, workspace boundary, tool list, and changed stage order.

- [ ] **Step 1: Run the complete pre-documentation verification**

Run: `uv run pytest`

Expected: all tests pass with no network or real credentials.

- [ ] **Step 2: Update user documentation**

Change README “当前阶段” to state that both API modes support local tools. Add examples:

```powershell
uv run cdy-agent ask "读取 README.md 并总结"
uv run cdy-agent ask "检查仓库状态" --workspace .
uv run cdy-agent chat --workspace .
```

Document all three tools, the default current-directory workspace, the 1 MiB/64 KiB/30 second limits, default-No confirmation for writes and Shell, explicit overwrite requirement, and the exact Shell allowlist. Update the roadmap so stage 4 records that local read/write/Shell safety shipped with the Tool Loop and stage 5 no longer lists those same items as future work.

- [ ] **Step 3: Run final verification**

Run: `uv run pytest`

Expected: all tests pass.

Run: `uv run cdy-agent --help`

Expected: exit 0 and command list includes `ask` and `chat`.

Run: `uv run cdy-agent ask --help`

Expected: exit 0 and output includes `--workspace` and `--model`.

Run: `uv run cdy-agent chat --help`

Expected: exit 0 and output includes `--workspace` and `--model`.

Run: `uv build`

Expected: source and wheel distributions are built successfully.

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md
git commit -m "Document local tool agent usage"
```
