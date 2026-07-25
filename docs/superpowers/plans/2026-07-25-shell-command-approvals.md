# Tiered Shell Command Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Shell command allowlist with parameter-aware automatic read-only execution, per-call confirmation for other commands, and exact-argv persistent workspace approvals.

**Architecture:** Extend the generic tool registry with dynamic confirmation and a three-way confirmation decision while retaining Boolean callback compatibility. Keep machine-managed approvals in a focused atomic JSON store, and keep Shell argument preparation and conservative read-only classification in a separate policy module used by `ShellTool`.

**Tech Stack:** Python 3.10+, dataclasses, enums, pathlib, subprocess with `shell=False`, standard-library JSON/tempfile/os, Typer, pytest, uv, Hatchling.

## Global Constraints

- Use four-space indentation, UTF-8, public type hints, concise docstrings, and PEP 8.
- Do not add a provider abstraction or change `config.py`; approvals are not stored in `.cdy-agent/config.yaml`.
- Store approvals only at `<workspace>/.cdy-agent/shell-approvals.json`.
- Match the final executed argv element-by-element, in order and case-sensitively; do not add prefix, wildcard, regex, or executable-only rules.
- Keep `shell=False`, workspace cwd, sanitized environment, maximum 30-second timeout, and independent stdout/stderr byte limits.
- Automatic read-only execution is limited to `pwd`, `ls`, `rg`, `grep`, `head`, `tail`, `wc`, `sort`, `uniq`, `git status`, and `git diff`, with conservative option and workspace-path validation.
- Unknown, writing, delegating, workspace-external, or unparseable command forms require confirmation instead of being rejected.
- Only structurally invalid tool arguments are rejected before confirmation.
- Only Shell supports persistent approval; all other tools keep their current default-No confirmation behavior.
- Tests must remain offline and inject process and filesystem boundaries.
- Use `uv run pytest`, `uv run cdy-agent --help`, `uv run cdy-agent ask --help`, and `uv build`.

---

## File Structure

- Create `src/cdy_agent/tools/shell_approvals.py`: strict versioned JSON loading, exact argv lookup, safe workspace path resolution, and atomic append.
- Create `src/cdy_agent/tools/shell_policy.py`: validated command preparation, final argv construction, conservative read-only parsers, workspace path checks, and execution classification.
- Modify `src/cdy_agent/tools/base.py`: confirmation request capability flag and `ConfirmationDecision`.
- Modify `src/cdy_agent/tools/registry.py`: per-argument confirmation, Boolean compatibility, and persist-before-execute handling.
- Modify `src/cdy_agent/tools/shell.py`: delegate validation/policy, expose dynamic confirmation and persistent approval hooks, and retain subprocess/result handling.
- Modify `src/cdy_agent/tools/__init__.py`: construct the Shell approval store and policy for the active workspace.
- Modify `src/cdy_agent/cli.py`: render Shell `once/always/deny` interaction and return typed decisions.
- Create `tests/test_shell_approvals.py`: isolated approval-store tests.
- Create `tests/test_shell_policy.py`: isolated command preparation and classification tests.
- Modify `tests/test_tool_registry.py`: dynamic confirmation and persistence protocol tests.
- Modify `tests/test_shell_tool.py`: arbitrary-command, effective-argv, policy integration, and runner regression tests.
- Modify `tests/test_cli.py`: `y/a/n`, interruption, and non-Shell prompt tests.
- Modify `README.md`: user-facing policy, approval file, revocation, and risk documentation.

---

### Task 1: Add Dynamic and Persistent Confirmation to the Tool Registry

**Files:**
- Modify: `src/cdy_agent/tools/base.py:43-60`
- Modify: `src/cdy_agent/tools/registry.py:42-72`
- Test: `tests/test_tool_registry.py`

**Interfaces:**
- Consumes: existing `Tool.preflight`, `Tool.confirmation_description`, `Tool.execute`, and `Tool.requires_confirmation`.
- Produces: `ConfirmationDecision`, `ConfirmationRequest.allow_always`, optional `tool.requires_confirmation_for(arguments) -> bool`, and optional `tool.remember_approval(arguments) -> ToolResult`.
- Preserves: callbacks returning `True` mean allow once and callbacks returning `False` mean deny.

- [ ] **Step 1: Write failing registry tests**

Add the import and helper to `tests/test_tool_registry.py`:

```python
from cdy_agent.tools.base import (
    ConfirmationDecision,
    ConfirmationRequest,
    ToolCall,
    ToolResult,
)


@dataclass
class DynamicEchoTool(EchoTool):
    confirmation_required: bool = True
    remembered: list[dict[str, Any]] = None  # type: ignore[assignment]
    remember_result: ToolResult = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.remembered = []
        self.remember_result = ToolResult.success({"remembered": True})

    def requires_confirmation_for(self, arguments: dict[str, Any]) -> bool:
        return self.confirmation_required

    def remember_approval(self, arguments: dict[str, Any]) -> ToolResult:
        self.remembered.append(dict(arguments))
        return self.remember_result
```

Add these tests:

```python
def test_registry_skips_callback_when_dynamic_tool_is_auto_approved() -> None:
    callbacks: list[ConfirmationRequest] = []
    tool = DynamicEchoTool(confirmation_required=False)

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: callbacks.append(request) or ConfirmationDecision.DENY,
    )

    assert result.ok
    assert callbacks == []


def test_registry_allows_once_without_persisting() -> None:
    tool = DynamicEchoTool()
    requests: list[ConfirmationRequest] = []

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: requests.append(request)
        or ConfirmationDecision.ALLOW_ONCE,
    )

    assert result.ok
    assert requests[0].allow_always is True
    assert tool.remembered == []


def test_registry_persists_before_execution() -> None:
    events: list[str] = []
    tool = DynamicEchoTool()
    tool.remember_approval = lambda arguments: (
        events.append("remember")
        or ToolResult.success({"remembered": True})
    )  # type: ignore[method-assign]
    tool.execute = lambda arguments: (
        events.append("execute") or ToolResult.success({"text": arguments["text"]})
    )  # type: ignore[method-assign]

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result.ok
    assert events == ["remember", "execute"]


def test_registry_does_not_execute_when_persistence_fails() -> None:
    executions: list[dict[str, Any]] = []
    tool = DynamicEchoTool()
    tool.remember_result = ToolResult.failure(
        "approval_store_error", "Could not save Shell approval."
    )
    tool.execute = lambda arguments: (
        executions.append(arguments) or ToolResult.success({})
    )  # type: ignore[method-assign]

    result = ToolRegistry([tool]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result.code == "approval_store_error"
    assert executions == []


def test_registry_rejects_always_for_tool_without_persistence_hook() -> None:
    result = ToolRegistry([EchoTool(requires_confirmation=True)]).execute(
        ToolCall("1", "echo", '{"text":"hello"}'),
        lambda request: ConfirmationDecision.ALLOW_ALWAYS,
    )

    assert result.code == "persistent_approval_not_supported"
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
uv run pytest tests/test_tool_registry.py -k "dynamic_tool or allows_once or persists_before or persistence_fails or rejects_always" -v
```

Expected: collection or import failure because `ConfirmationDecision` and
`ConfirmationRequest.allow_always` do not exist.

- [ ] **Step 3: Implement the confirmation types**

Update `src/cdy_agent/tools/base.py`:

```python
from enum import Enum


class ConfirmationDecision(str, Enum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"


@dataclass(frozen=True)
class ConfirmationRequest:
    tool_name: str
    arguments: dict[str, Any]
    description: str
    allow_always: bool = False


ConfirmationCallback = Callable[
    [ConfirmationRequest], bool | ConfirmationDecision
]
```

Keep the existing `Tool` protocol unchanged apart from using the new callback
alias. Dynamic hooks are intentionally optional so every existing tool does
not need boilerplate methods.

- [ ] **Step 4: Implement registry decision normalization and hooks**

Add to `src/cdy_agent/tools/registry.py`:

```python
from .base import (
    ConfirmationCallback,
    ConfirmationDecision,
    ConfirmationRequest,
    Tool,
    ToolCall,
    ToolResult,
)


def _confirmation_required(tool: Tool, arguments: dict[str, object]) -> bool:
    dynamic = getattr(tool, "requires_confirmation_for", None)
    if callable(dynamic):
        return bool(dynamic(arguments))
    return tool.requires_confirmation


def _normalize_decision(
    decision: bool | ConfirmationDecision,
) -> ConfirmationDecision:
    if isinstance(decision, ConfirmationDecision):
        return decision
    return (
        ConfirmationDecision.ALLOW_ONCE
        if decision
        else ConfirmationDecision.DENY
    )
```

Replace the static confirmation block in `ToolRegistry.execute` with:

```python
        if _confirmation_required(tool, arguments):
            remember = getattr(tool, "remember_approval", None)
            try:
                request = ConfirmationRequest(
                    tool.name,
                    arguments,
                    tool.confirmation_description(arguments),
                    allow_always=callable(remember),
                )
                decision = _normalize_decision(confirm(request))
            except BaseException:
                try:
                    _cancel_tool(tool)
                except BaseException:
                    pass
                raise
            if decision is ConfirmationDecision.DENY:
                _cancel_tool(tool)
                return ToolResult.failure(
                    "approval_denied", "User declined this tool call."
                )
            if decision is ConfirmationDecision.ALLOW_ALWAYS:
                if not callable(remember):
                    _cancel_tool(tool)
                    return ToolResult.failure(
                        "persistent_approval_not_supported",
                        "This tool does not support persistent approval.",
                    )
                remembered = remember(arguments)
                if not remembered.ok:
                    _cancel_tool(tool)
                    return remembered
        return tool.execute(arguments)
```

- [ ] **Step 5: Run registry tests**

Run:

```powershell
uv run pytest tests/test_tool_registry.py tests/test_skill_tools.py -v
```

Expected: PASS, including existing Boolean confirmation callbacks and
Skill cancellation lifecycle tests.

- [ ] **Step 6: Commit the registry protocol**

```powershell
git add src/cdy_agent/tools/base.py src/cdy_agent/tools/registry.py tests/test_tool_registry.py
git commit -m "Add dynamic tool confirmation decisions"
```

---

### Task 2: Add the Atomic Workspace Shell Approval Store

**Files:**
- Create: `src/cdy_agent/tools/shell_approvals.py`
- Create: `tests/test_shell_approvals.py`

**Interfaces:**
- Consumes: resolved workspace paths and the project convention that `.cdy-agent/` is ignored.
- Produces: `ShellApprovalStore(workspace, replace=os.replace)`,
  `contains(argv: Sequence[str]) -> ToolResult`, and
  `add(argv: Sequence[str]) -> ToolResult`.
- Result data: `contains` returns a Boolean in `ToolResult.data`; `add` returns
  `{"path": str, "count": int}`.

- [ ] **Step 1: Write failing load and exact-match tests**

Create `tests/test_shell_approvals.py`:

```python
import json
import os
from pathlib import Path

import pytest

from cdy_agent.tools.shell_approvals import ShellApprovalStore


def test_missing_approval_store_is_empty_without_writing(tmp_path: Path) -> None:
    result = ShellApprovalStore(tmp_path).contains(["uv", "run", "pytest"])

    assert result.ok and result.data is False
    assert not (tmp_path / ".cdy-agent").exists()


def test_approval_store_matches_only_exact_argv(tmp_path: Path) -> None:
    store = ShellApprovalStore(tmp_path)
    assert store.add(["python", "script.py"]).ok

    assert store.contains(["python", "script.py"]).data is True
    assert store.contains(["Python", "script.py"]).data is False
    assert store.contains(["python", "script.py", "--delete"]).data is False
    assert store.contains(["script.py", "python"]).data is False


def test_approval_store_writes_versioned_json_and_deduplicates(
    tmp_path: Path,
) -> None:
    store = ShellApprovalStore(tmp_path)
    assert store.add(["uv", "run", "pytest"]).ok
    assert store.add(["uv", "run", "pytest"]).ok

    document = json.loads(
        (tmp_path / ".cdy-agent" / "shell-approvals.json").read_text(
            encoding="utf-8"
        )
    )
    assert document == {
        "version": 1,
        "allowed_commands": [["uv", "run", "pytest"]],
    }
```

- [ ] **Step 2: Run the store tests and verify failure**

Run:

```powershell
uv run pytest tests/test_shell_approvals.py -v
```

Expected: import failure because `shell_approvals.py` does not exist.

- [ ] **Step 3: Implement strict loading and atomic writing**

Create `src/cdy_agent/tools/shell_approvals.py` with these public definitions:

```python
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .base import ToolResult
from .filesystem import resolve_workspace

APPROVAL_VERSION = 1
DATA_DIRECTORY = ".cdy-agent"
APPROVAL_FILENAME = "shell-approvals.json"
Replace = Callable[
    [
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ],
    None,
]


class ShellApprovalStore:
    def __init__(
        self,
        workspace: Path,
        replace: Replace = os.replace,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self._replace = replace

    def contains(self, argv: Sequence[str]) -> ToolResult:
        loaded = self._load()
        if not loaded.ok:
            return loaded
        command = list(argv)
        return ToolResult.success(command in loaded.data)

    def add(self, argv: Sequence[str]) -> ToolResult:
        command = list(argv)
        if not _valid_command(command):
            return ToolResult.failure(
                "invalid_arguments", "Approval argv must be non-empty strings."
            )
        loaded = self._load()
        if not loaded.ok:
            return loaded
        commands = [list(item) for item in loaded.data]
        if command not in commands:
            commands.append(command)
        return self._save(commands)
```

Implement the validation and private methods with:

```python
def _valid_command(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _valid_document(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"version", "allowed_commands"}
        and value["version"] == APPROVAL_VERSION
        and isinstance(value["allowed_commands"], list)
        and all(_valid_command(item) for item in value["allowed_commands"])
    )
```

Add these methods inside `ShellApprovalStore`:

```python
    def _load(self) -> ToolResult:
        target = self._target(create_directory=False)
        if isinstance(target, ToolResult):
            return target
        if target is None:
            return ToolResult.success([])
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except OSError:
            return ToolResult.failure(
                "approval_store_error", "Could not read Shell approvals."
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ToolResult.failure(
                "invalid_approval_store", "Stored Shell approvals are invalid."
            )
        if not _valid_document(document):
            return ToolResult.failure(
                "invalid_approval_store", "Stored Shell approvals are invalid."
            )
        return ToolResult.success([
            list(command) for command in document["allowed_commands"]
        ])

    def _save(self, commands: list[list[str]]) -> ToolResult:
        document = {
            "version": APPROVAL_VERSION,
            "allowed_commands": commands,
        }
        if not _valid_document(document):
            return ToolResult.failure(
                "invalid_approval_store",
                "Refusing to write invalid Shell approvals.",
            )
        target = self._target(create_directory=True)
        if isinstance(target, ToolResult) or target is None:
            return target or ToolResult.failure(
                "approval_store_error", "Could not create Shell approval store."
            )
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{APPROVAL_FILENAME}.",
            )
            temporary = Path(raw_path)
            with os.fdopen(
                descriptor, "w", encoding="utf-8", newline="\n"
            ) as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            self._replace(temporary, target)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return ToolResult.failure(
                "approval_store_error", "Could not write Shell approvals."
            )
        return ToolResult.success({
            "path": str(target),
            "count": len(commands),
        })

    def _target(
        self, create_directory: bool
    ) -> Path | ToolResult | None:
        data_directory = self.workspace / DATA_DIRECTORY
        try:
            if not data_directory.exists() and not data_directory.is_symlink():
                if not create_directory:
                    return None
                data_directory.mkdir()
            resolved_directory = data_directory.resolve()
            resolved_directory.relative_to(self.workspace)
            if not resolved_directory.is_dir():
                return ToolResult.failure(
                    "approval_store_error",
                    "Shell approval path is not a directory.",
                )
            target = resolved_directory / APPROVAL_FILENAME
            if target.is_symlink() or target.exists():
                resolved_target = target.resolve()
                resolved_target.relative_to(self.workspace)
                if not resolved_target.is_file():
                    return ToolResult.failure(
                        "approval_store_error",
                        "Shell approval path is not a file.",
                    )
                return resolved_target
            return target
        except ValueError:
            return ToolResult.failure(
                "path_outside_workspace",
                "Shell approvals are outside the workspace.",
            )
        except OSError:
            return ToolResult.failure(
                "approval_store_error", "Could not access Shell approvals."
            )
```

- [ ] **Step 4: Add failure, containment, and atomicity tests**

Append:

```python
def test_invalid_approval_documents_fail_closed(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    target = data / "shell-approvals.json"

    for content in (
        "{",
        '{"version":2,"allowed_commands":[]}',
        '{"version":1,"allowed_commands":["uv"]}',
        '{"version":1,"allowed_commands":[[]]}',
        '{"version":1,"allowed_commands":[["uv",1]]}',
        '{"version":1,"allowed_commands":[],"extra":true}',
    ):
        target.write_text(content, encoding="utf-8")
        assert (
            ShellApprovalStore(tmp_path).contains(["uv"]).code
            == "invalid_approval_store"
        )


def test_symlinked_store_outside_workspace_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    data = tmp_path / ".cdy-agent"
    try:
        data.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    result = ShellApprovalStore(tmp_path).add(["uv"])

    assert result.code == "path_outside_workspace"


def test_failed_replace_preserves_existing_store(tmp_path: Path) -> None:
    store = ShellApprovalStore(tmp_path)
    assert store.add(["first"]).ok
    target = tmp_path / ".cdy-agent" / "shell-approvals.json"
    original = target.read_bytes()

    failing = ShellApprovalStore(
        tmp_path,
        replace=lambda source, destination: (_ for _ in ()).throw(
            OSError("replace failed")
        ),
    )
    result = failing.add(["second"])

    assert result.code == "approval_store_error"
    assert target.read_bytes() == original
    assert list(target.parent.glob(".shell-approvals.json.*")) == []
```

- [ ] **Step 5: Run store tests**

Run:

```powershell
uv run pytest tests/test_shell_approvals.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit the approval store**

```powershell
git add src/cdy_agent/tools/shell_approvals.py tests/test_shell_approvals.py
git commit -m "Add workspace shell approval store"
```

---

### Task 3: Prepare Arbitrary Shell Commands and Integrate Persistent Approval

**Files:**
- Create: `src/cdy_agent/tools/shell_policy.py`
- Modify: `src/cdy_agent/tools/shell.py`
- Modify: `src/cdy_agent/tools/__init__.py`
- Modify: `tests/test_shell_tool.py`
- Test: `tests/test_shell_policy.py`

**Interfaces:**
- Consumes: `ShellApprovalStore.contains/add`, existing process helpers, and dynamic registry hooks from Task 1.
- Produces: `PreparedShellCommand`, `ShellExecutionDecision`,
  `ShellPolicyResult`, `ShellExecutionPolicy.classify(arguments)`, and
  `ShellExecutionPolicy.remember(arguments)`.
- `ShellTool.requires_confirmation_for(arguments)` returns `False` for
  `AUTO_APPROVE`, `True` for `REQUIRE_CONFIRMATION`, and lets `preflight`
  return the failure for `REJECT`.

- [ ] **Step 1: Write failing command-preparation tests**

Create `tests/test_shell_policy.py`:

```python
from pathlib import Path

from cdy_agent.tools.shell_approvals import ShellApprovalStore
from cdy_agent.tools.shell_policy import (
    ShellExecutionDecision,
    ShellExecutionPolicy,
)


def test_policy_rejects_invalid_tool_arguments(tmp_path: Path) -> None:
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    for arguments in (
        {},
        {"argv": []},
        {"argv": "ls"},
        {"argv": ["ls", 1]},
        {"argv": ["ls"], "extra": True},
        {"argv": ["ls"], "timeout_seconds": 0},
        {"argv": ["ls"], "timeout_seconds": 31},
        {"argv": ["ls"], "timeout_seconds": True},
    ):
        result = policy.classify(arguments)
        assert result.decision is ShellExecutionDecision.REJECT
        assert result.failure is not None
        assert result.failure.code == "invalid_arguments"


def test_policy_builds_final_rg_and_git_argv(tmp_path: Path) -> None:
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    rg = policy.prepare({"argv": ["rg", "needle", "."]})
    status = policy.prepare({"argv": ["git", "status", "--short"]})
    diff = policy.prepare({"argv": ["git", "diff", "--", "file.py"]})

    assert rg.argv == ("rg", "--no-config", "needle", ".")
    assert status.argv == (
        "git", "--no-pager", "--no-optional-locks",
        "-c", "core.fsmonitor=false", "status", "--short",
    )
    assert diff.argv == (
        "git", "--no-pager", "--no-optional-locks",
        "-c", "core.fsmonitor=false", "diff",
        "--no-ext-diff", "--no-textconv", "--", "file.py",
    )
```

- [ ] **Step 2: Run preparation tests and verify failure**

Run:

```powershell
uv run pytest tests/test_shell_policy.py -k "rejects_invalid or builds_final" -v
```

Expected: import failure because `shell_policy.py` does not exist.

- [ ] **Step 3: Implement preparation and policy result types**

Create `src/cdy_agent/tools/shell_policy.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .base import ToolResult
from .filesystem import resolve_workspace
from .shell_approvals import ShellApprovalStore

DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30


class ShellExecutionDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REJECT = "reject"


@dataclass(frozen=True)
class PreparedShellCommand:
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class ShellPolicyResult:
    decision: ShellExecutionDecision
    command: PreparedShellCommand | None = None
    failure: ToolResult | None = None


class ShellExecutionPolicy:
    def __init__(
        self,
        workspace: Path,
        approvals: ShellApprovalStore,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self.approvals = approvals

    def prepare(
        self, arguments: dict[str, Any]
    ) -> PreparedShellCommand | ToolResult:
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return validated
        user_argv, timeout = validated
        return PreparedShellCommand(
            tuple(_effective_argv(user_argv)),
            timeout,
        )

    def classify(self, arguments: dict[str, Any]) -> ShellPolicyResult:
        prepared = self.prepare(arguments)
        if isinstance(prepared, ToolResult):
            return ShellPolicyResult(
                ShellExecutionDecision.REJECT,
                failure=prepared,
            )
        allowed = self.approvals.contains(prepared.argv)
        if allowed.ok and allowed.data is True:
            return ShellPolicyResult(
                ShellExecutionDecision.AUTO_APPROVE,
                command=prepared,
            )
        return ShellPolicyResult(
            ShellExecutionDecision.REQUIRE_CONFIRMATION,
            command=prepared,
        )

    def remember(self, arguments: dict[str, Any]) -> ToolResult:
        prepared = self.prepare(arguments)
        if isinstance(prepared, ToolResult):
            return prepared
        return self.approvals.add(prepared.argv)


def _validate_arguments(
    arguments: dict[str, Any],
) -> tuple[list[str], int] | ToolResult:
    if set(arguments) not in ({"argv"}, {"argv", "timeout_seconds"}):
        return ToolResult.failure(
            "invalid_arguments",
            "argv is required; timeout_seconds is optional.",
        )
    argv = arguments["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(element, str) for element in argv)
    ):
        return ToolResult.failure(
            "invalid_arguments", "argv must be a non-empty list of strings."
        )
    timeout = arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
    ):
        return ToolResult.failure(
            "invalid_arguments",
            f"timeout_seconds must be an integer from 1 to "
            f"{MAX_TIMEOUT_SECONDS}.",
        )
    return argv, timeout


def _effective_argv(argv: list[str]) -> list[str]:
    if argv[0] == "rg":
        return ["rg", "--no-config", *argv[1:]]
    if argv[0] != "git" or len(argv) < 2:
        return list(argv)
    prefix = [
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        argv[1],
    ]
    user_arguments = [
        argument
        for argument in argv[2:]
        if argument not in {"--no-ext-diff", "--no-textconv"}
    ]
    if argv[1] != "diff":
        return [*prefix, *user_arguments]
    safety = ["--no-ext-diff", "--no-textconv"]
    try:
        separator = user_arguments.index("--")
    except ValueError:
        return [*prefix, *user_arguments, *safety]
    return [
        *prefix,
        *user_arguments[:separator],
        *safety,
        *user_arguments[separator:],
    ]
```

- [ ] **Step 4: Write failing Shell integration tests**

Replace allowlist rejection tests in `tests/test_shell_tool.py` with:

```python
def test_shell_arbitrary_command_requires_confirmation_in_registry(
    tmp_path: Path,
) -> None:
    from cdy_agent.tools.base import ToolCall
    from cdy_agent.tools.registry import ToolRegistry

    calls: list[list[str]] = []
    requests: list[object] = []
    tool = ShellTool(
        tmp_path,
        runner=lambda argv, **kwargs: calls.append(argv)
        or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    denied = ToolRegistry([tool]).execute(
        ToolCall("1", "shell", '{"argv":["python","-c","print(1)"]}'),
        lambda request: requests.append(request) or False,
    )

    assert denied.code == "approval_denied"
    assert calls == []
    assert len(requests) == 1


def test_shell_allow_always_persists_final_argv_before_running(
    tmp_path: Path,
) -> None:
    from cdy_agent.tools.base import ConfirmationDecision, ToolCall
    from cdy_agent.tools.registry import ToolRegistry

    calls: list[list[str]] = []
    registry = ToolRegistry([
        ShellTool(
            tmp_path,
            runner=lambda argv, **kwargs: calls.append(argv)
            or subprocess.CompletedProcess(argv, 0, "", ""),
        )
    ])
    call = ToolCall("1", "shell", '{"argv":["python","script.py"]}')

    first = registry.execute(
        call, lambda request: ConfirmationDecision.ALLOW_ALWAYS
    )
    callbacks: list[object] = []
    second = registry.execute(
        call, lambda request: callbacks.append(request) or False
    )

    assert first.ok and second.ok
    assert calls == [
        ["python", "script.py"],
        ["python", "script.py"],
    ]
    assert callbacks == []
```

Update effective Git argv assertions to include `--no-optional-locks`. Delete
tests asserting `rm`, paths, `git log`, `find -exec`, `rg --pre`, and `sed`
are rejected; Task 4 replaces them with confirmation-classification tests.

- [ ] **Step 5: Refactor `ShellTool` to use the policy**

Change the constructor fields and description:

```python
@dataclass
class ShellTool:
    workspace: Path
    runner: Runner = subprocess.run
    policy: ShellExecutionPolicy | None = None
    name: str = field(default="shell", init=False)
    description: str = field(
        default=(
            "Run a command in the workspace. Proven read-only commands and "
            "persistently approved exact commands can run automatically."
        ),
        init=False,
    )
    requires_confirmation: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()
        if self.policy is None:
            approvals = ShellApprovalStore(self.workspace)
            self.policy = ShellExecutionPolicy(self.workspace, approvals)
```

Implement the hooks:

```python
    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        result = self.policy.classify(arguments)
        return result.failure

    def requires_confirmation_for(
        self, arguments: dict[str, Any]
    ) -> bool:
        result = self.policy.classify(arguments)
        return result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION

    def remember_approval(self, arguments: dict[str, Any]) -> ToolResult:
        return self.policy.remember(arguments)
```

Make `confirmation_description` and `execute` call `policy.prepare` again.
The description must end with `with current user permissions.` and show
`list(prepared.argv)`. Execution passes `list(prepared.argv)` to the existing
runner and retains every existing environment, timeout, output, and error
mapping.

In `create_builtin_registry`, construct one store and policy:

```python
    approval_store = ShellApprovalStore(workspace)
    shell_policy = ShellExecutionPolicy(workspace, approval_store)
```

and register:

```python
ShellTool(workspace, policy=shell_policy)
```

- [ ] **Step 6: Run Shell integration tests**

Run:

```powershell
uv run pytest tests/test_shell_policy.py tests/test_shell_approvals.py tests/test_shell_tool.py tests/test_tool_registry.py -v
```

Expected: PASS for argument validation, arbitrary-command confirmation,
persist-before-run, effective argv, and subprocess safety.

- [ ] **Step 7: Commit arbitrary command and approval integration**

```powershell
git add src/cdy_agent/tools/shell_policy.py src/cdy_agent/tools/shell.py src/cdy_agent/tools/__init__.py tests/test_shell_policy.py tests/test_shell_tool.py
git commit -m "Integrate persistent Shell command approvals"
```

---

### Task 4: Add Conservative Built-In Read-Only Classification

**Files:**
- Modify: `src/cdy_agent/tools/shell_policy.py`
- Modify: `tests/test_shell_policy.py`

**Interfaces:**
- Consumes: validated user argv, final argv, resolved workspace, and approval store.
- Produces: `_is_safe_read_only(user_argv, workspace) -> bool`.
- Policy order: invalid arguments reject; safe built-in command auto-approves;
  exact stored final argv auto-approves; everything else confirms.

- [ ] **Step 1: Write the safe-command and downgrade matrix**

Append to `tests/test_shell_policy.py`:

```python
import pytest


@pytest.mark.parametrize(
    "argv",
    [
        ["pwd"],
        ["pwd", "-P"],
        ["ls"],
        ["ls", "-la", "."],
        ["rg", "needle", "."],
        ["rg", "-n", "--glob", "*.py", "needle", "src"],
        ["grep", "-n", "needle", "README.md"],
        ["head", "-n", "5", "README.md"],
        ["tail", "-n", "5", "README.md"],
        ["wc", "-l", "README.md"],
        ["sort", "-r", "README.md"],
        ["uniq", "-c", "README.md"],
        ["git", "status", "--short"],
        ["git", "diff", "--stat"],
        ["git", "diff", "--", "README.md"],
    ],
)
def test_safe_workspace_read_commands_auto_approve(
    tmp_path: Path, argv: list[str]
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("needle\n", encoding="utf-8")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": argv})

    assert result.decision is ShellExecutionDecision.AUTO_APPROVE


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-c", "print(1)"],
        ["./script"],
        ["/bin/ls"],
        ["rg", "--pre", "python", "needle", "."],
        ["rg", "--pre=python", "needle", "."],
        ["sort", "-o", "out.txt", "README.md"],
        ["sort", "--output=out.txt", "README.md"],
        ["sort", "--compress-program=python", "README.md"],
        ["uniq", "README.md", "out.txt"],
        ["wc", "--files0-from=list.txt"],
        ["git", "log"],
        ["git", "diff", "--output=out.patch"],
        ["git", "diff", "--ext-diff"],
        ["git", "diff", "--textconv"],
        ["ls", "--unknown-option"],
        ["sed", "-n", "1p", "README.md"],
        ["find", ".", "-exec", "id", ";"],
    ],
)
def test_unproven_or_mutating_commands_require_confirmation(
    tmp_path: Path, argv: list[str]
) -> None:
    (tmp_path / "README.md").write_text("needle\n", encoding="utf-8")
    (tmp_path / "list.txt").write_text("README.md\n", encoding="utf-8")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": argv})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
```

- [ ] **Step 2: Write workspace-boundary tests**

Append:

```python
@pytest.mark.parametrize(
    "argv",
    [
        ["ls", ".."],
        ["rg", "needle", ".."],
        ["grep", "needle", "../outside.txt"],
        ["head", "../outside.txt"],
        ["tail", "../outside.txt"],
        ["wc", "../outside.txt"],
        ["sort", "../outside.txt"],
        ["uniq", "../outside.txt"],
        ["git", "diff", "--", "../outside.txt"],
    ],
)
def test_workspace_external_reads_require_confirmation(
    tmp_path: Path, argv: list[str]
) -> None:
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    assert policy.classify(
        {"argv": argv}
    ).decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


def test_symlink_to_external_input_requires_confirmation(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    assert policy.classify(
        {"argv": ["head", "linked.txt"]}
    ).decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
```

- [ ] **Step 3: Run classifier tests and verify failure**

Run:

```powershell
uv run pytest tests/test_shell_policy.py -k "safe_workspace or unproven or external_reads or symlink" -v
```

Expected: safe commands currently require confirmation because classification
does not yet recognize read-only calls.

- [ ] **Step 4: Implement conservative option parsing helpers**

Add these module-level constants to `shell_policy.py`:

```python
SAFE_COMMANDS = frozenset({
    "pwd", "ls", "rg", "grep", "head", "tail", "wc", "sort", "uniq",
})
PWD_OPTIONS = frozenset({"-L", "-P", "--logical", "--physical"})
LS_SHORT_OPTIONS = frozenset("aAlhRdF1rtS")
LS_LONG_OPTIONS = frozenset({
    "--all", "--almost-all", "--human-readable", "--recursive",
    "--directory", "--classify", "--group-directories-first",
})
RG_FLAGS = frozenset({
    "-n", "--line-number", "-i", "--ignore-case", "-S", "--smart-case",
    "-F", "--fixed-strings", "-l", "--files-with-matches", "--files",
    "--hidden", "--no-ignore",
})
RG_VALUE_OPTIONS = frozenset({
    "-g", "--glob", "-t", "--type", "-T", "--type-not",
    "-A", "--after-context", "-B", "--before-context",
    "-C", "--context", "-e", "--regexp",
})
GREP_FLAGS = frozenset({
    "-n", "--line-number", "-i", "--ignore-case", "-F", "--fixed-strings",
    "-E", "--extended-regexp", "-l", "--files-with-matches",
    "-v", "--invert-match", "-s", "--no-messages",
})
GREP_VALUE_OPTIONS = frozenset({
    "-e", "--regexp", "-A", "--after-context", "-B", "--before-context",
    "-C", "--context",
})
HEAD_TAIL_FLAGS = frozenset({"-q", "--quiet", "--silent", "-v", "--verbose"})
HEAD_TAIL_VALUE_OPTIONS = frozenset({
    "-n", "--lines", "-c", "--bytes",
})
WC_FLAGS = frozenset({
    "-c", "--bytes", "-m", "--chars", "-l", "--lines",
    "-w", "--words", "-L", "--max-line-length",
})
SORT_FLAGS = frozenset({
    "-b", "--ignore-leading-blanks", "-f", "--ignore-case",
    "-n", "--numeric-sort", "-r", "--reverse", "-s", "--stable",
    "-u", "--unique",
})
SORT_VALUE_OPTIONS = frozenset({
    "-k", "--key", "-t", "--field-separator",
})
UNIQ_FLAGS = frozenset({
    "-c", "--count", "-d", "--repeated", "-D", "--all-repeated",
    "-i", "--ignore-case", "-u", "--unique",
})
UNIQ_VALUE_OPTIONS = frozenset({
    "-f", "--skip-fields", "-s", "--skip-chars", "-w", "--check-chars",
})
GIT_STATUS_FLAGS = frozenset({
    "-s", "--short", "-b", "--branch", "--porcelain",
    "--long", "-z", "--null", "-u", "--untracked-files",
    "--ignored", "--no-renames",
})
GIT_DIFF_FLAGS = frozenset({
    "--stat", "--numstat", "--shortstat", "--name-only", "--name-status",
    "--check", "--summary", "--patch", "-p",
    "-w", "--ignore-all-space", "--no-renames", "--cached", "--staged",
})
GIT_DIFF_VALUE_OPTIONS = frozenset({"-U", "--unified"})
```

Add this conservative parser and the command-specific classifiers:

```python
@dataclass(frozen=True)
class ParsedOptions:
    positionals: tuple[str, ...]
    seen: frozenset[str]


def _parse_options(
    arguments: list[str],
    flags: frozenset[str],
    value_options: frozenset[str] = frozenset(),
    combined_short_flags: frozenset[str] = frozenset(),
) -> ParsedOptions | None:
    positionals: list[str] = []
    seen: set[str] = set()
    options_finished = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if options_finished or argument == "-" or not argument.startswith("-"):
            positionals.append(argument)
            index += 1
            continue
        if argument == "--":
            options_finished = True
            index += 1
            continue
        if argument in flags:
            seen.add(argument)
            index += 1
            continue
        if argument in value_options:
            if index + 1 >= len(arguments):
                return None
            seen.add(argument)
            index += 2
            continue
        matched_value = next(
            (
                option
                for option in value_options
                if option.startswith("--")
                and argument.startswith(f"{option}=")
            ),
            None,
        )
        if matched_value is not None:
            seen.add(matched_value)
            index += 1
            continue
        if (
            argument.startswith("-")
            and not argument.startswith("--")
            and len(argument) > 2
            and all(
                character in combined_short_flags
                for character in argument[1:]
            )
        ):
            seen.update(f"-{character}" for character in argument[1:])
            index += 1
            continue
        return None
    return ParsedOptions(tuple(positionals), frozenset(seen))


def _has_option(
    arguments: list[str], names: frozenset[str]
) -> bool:
    return any(
        argument in names
        or any(
            name.startswith("--") and argument.startswith(f"{name}=")
            for name in names
        )
        for argument in arguments
    )


def _paths_within_workspace(paths: list[str], workspace: Path) -> bool:
    for raw_path in paths:
        if raw_path == "-":
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        try:
            candidate.resolve().relative_to(workspace)
        except (OSError, ValueError):
            return False
    return True


def _safe_pwd(arguments: list[str]) -> bool:
    parsed = _parse_options(arguments, PWD_OPTIONS)
    return parsed is not None and not parsed.positionals


def _safe_ls(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        LS_LONG_OPTIONS | frozenset(f"-{item}" for item in LS_SHORT_OPTIONS),
        combined_short_flags=LS_SHORT_OPTIONS,
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_rg(arguments: list[str], workspace: Path) -> bool:
    if _has_option(arguments, frozenset({"--pre"})):
        return False
    parsed = _parse_options(
        arguments,
        RG_FLAGS,
        RG_VALUE_OPTIONS,
        combined_short_flags=frozenset("niSFl"),
    )
    if parsed is None:
        return False
    pattern_is_option = bool(
        parsed.seen & frozenset({"-e", "--regexp"})
    )
    paths = list(parsed.positionals)
    if "--files" not in parsed.seen and not pattern_is_option:
        if not paths:
            return False
        paths = paths[1:]
    return _paths_within_workspace(paths, workspace)


def _safe_grep(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        GREP_FLAGS,
        GREP_VALUE_OPTIONS,
        combined_short_flags=frozenset("niFElvs"),
    )
    if parsed is None:
        return False
    pattern_is_option = bool(
        parsed.seen & frozenset({"-e", "--regexp"})
    )
    paths = list(parsed.positionals)
    if not pattern_is_option:
        if not paths:
            return False
        paths = paths[1:]
    return _paths_within_workspace(paths, workspace)


def _safe_head_or_tail(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        HEAD_TAIL_FLAGS,
        HEAD_TAIL_VALUE_OPTIONS,
        combined_short_flags=frozenset("qv"),
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_wc(arguments: list[str], workspace: Path) -> bool:
    if _has_option(arguments, frozenset({"--files0-from"})):
        return False
    parsed = _parse_options(
        arguments,
        WC_FLAGS,
        combined_short_flags=frozenset("cmlwL"),
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_sort(arguments: list[str], workspace: Path) -> bool:
    dangerous = frozenset({
        "-o", "--output", "--compress-program",
        "-T", "--temporary-directory",
    })
    if _has_option(arguments, dangerous):
        return False
    parsed = _parse_options(
        arguments,
        SORT_FLAGS,
        SORT_VALUE_OPTIONS,
        combined_short_flags=frozenset("bfnrsu"),
    )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _safe_uniq(arguments: list[str], workspace: Path) -> bool:
    parsed = _parse_options(
        arguments,
        UNIQ_FLAGS,
        UNIQ_VALUE_OPTIONS,
        combined_short_flags=frozenset("cdDiu"),
    )
    if parsed is None or len(parsed.positionals) > 1:
        return False
    return _paths_within_workspace(list(parsed.positionals), workspace)


def _safe_git(argv: list[str], workspace: Path) -> bool:
    if len(argv) < 2 or argv[1] not in {"status", "diff"}:
        return False
    arguments = argv[2:]
    if argv[1] == "status":
        parsed = _parse_options(arguments, GIT_STATUS_FLAGS)
    else:
        dangerous = frozenset({
            "--output", "--ext-diff", "--textconv",
        })
        if _has_option(arguments, dangerous):
            return False
        parsed = _parse_options(
            arguments,
            GIT_DIFF_FLAGS,
            GIT_DIFF_VALUE_OPTIONS,
        )
    return parsed is not None and _paths_within_workspace(
        list(parsed.positionals), workspace
    )


def _is_safe_read_only(argv: list[str], workspace: Path) -> bool:
    command = argv[0]
    if command == "pwd":
        return _safe_pwd(argv[1:])
    if command == "ls":
        return _safe_ls(argv[1:], workspace)
    if command == "rg":
        return _safe_rg(argv[1:], workspace)
    if command == "grep":
        return _safe_grep(argv[1:], workspace)
    if command in {"head", "tail"}:
        return _safe_head_or_tail(argv[1:], workspace)
    if command == "wc":
        return _safe_wc(argv[1:], workspace)
    if command == "sort":
        return _safe_sort(argv[1:], workspace)
    if command == "uniq":
        return _safe_uniq(argv[1:], workspace)
    if command == "git":
        return _safe_git(argv, workspace)
    return False
```

This intentionally treats every option absent from the constants as
unproven and routes it to confirmation.

- [ ] **Step 5: Put read-only classification before stored approvals**

Update `ShellExecutionPolicy.classify` so it keeps the original validated
user argv for safety parsing and follows this order:

```python
        validated = _validate_arguments(arguments)
        if isinstance(validated, ToolResult):
            return ShellPolicyResult(
                ShellExecutionDecision.REJECT,
                failure=validated,
            )
        user_argv, timeout = validated
        prepared = PreparedShellCommand(
            tuple(_effective_argv(user_argv)),
            timeout,
        )
        if _is_safe_read_only(user_argv, self.workspace):
            return ShellPolicyResult(
                ShellExecutionDecision.AUTO_APPROVE,
                command=prepared,
            )
        allowed = self.approvals.contains(prepared.argv)
        if allowed.ok and allowed.data is True:
            return ShellPolicyResult(
                ShellExecutionDecision.AUTO_APPROVE,
                command=prepared,
            )
        return ShellPolicyResult(
            ShellExecutionDecision.REQUIRE_CONFIRMATION,
            command=prepared,
        )
```

An approval-store read failure intentionally follows the final confirmation
branch so `y` remains available and `a` reports the write/load failure.

- [ ] **Step 6: Run policy and Shell regression tests**

Run:

```powershell
uv run pytest tests/test_shell_policy.py tests/test_shell_tool.py tests/test_shell_approvals.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit read-only classification**

```powershell
git add src/cdy_agent/tools/shell_policy.py tests/test_shell_policy.py
git commit -m "Auto-approve safe read-only Shell commands"
```

---

### Task 5: Add Once/Always/Deny CLI Interaction

**Files:**
- Modify: `src/cdy_agent/cli.py:110-117`
- Modify: `tests/test_cli.py:40-53`

**Interfaces:**
- Consumes: `ConfirmationRequest.allow_always` and `ConfirmationDecision`.
- Produces: `_confirm_tool(request) -> ConfirmationDecision`.
- Preserves: non-Shell/non-persistent requests show `[y/N]` and never return
  `ALLOW_ALWAYS`.

- [ ] **Step 1: Write failing persistent-prompt tests**

Update the import in `tests/test_cli.py` and add tests:

```python
from cdy_agent.tools.base import (
    ConfirmationDecision,
    ConfirmationRequest,
    ToolCall,
    ToolResult,
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y\n", ConfirmationDecision.ALLOW_ONCE),
        ("yes\n", ConfirmationDecision.ALLOW_ONCE),
        ("a\n", ConfirmationDecision.ALLOW_ALWAYS),
        ("always\n", ConfirmationDecision.ALLOW_ALWAYS),
        ("\n", ConfirmationDecision.DENY),
        ("other\n", ConfirmationDecision.DENY),
    ],
)
def test_shell_confirmation_supports_once_always_and_deny(
    answer: str,
    expected: ConfirmationDecision,
) -> None:
    request = ConfirmationRequest(
        "shell",
        {"argv": ["python", "script.py"]},
        (
            "Run command ['python', 'script.py'] in workspace /workspace "
            "with current user permissions."
        ),
        allow_always=True,
    )
    monkey_app = typer.Typer()

    @monkey_app.callback(invoke_without_command=True)
    def invoke() -> None:
        typer.echo(cli._confirm_tool(request).value)

    result = runner.invoke(monkey_app, [], input=answer)

    assert result.exit_code == 0
    assert "[y] once / [a] always / [N] deny:" in result.stdout
    assert result.stdout.endswith(f"{expected.value}\n")


def test_non_persistent_confirmation_keeps_yes_no_prompt() -> None:
    request = ConfirmationRequest(
        "write_file",
        {"path": "note.txt"},
        "Write note.txt.",
    )
    monkey_app = typer.Typer()

    @monkey_app.callback(invoke_without_command=True)
    def invoke() -> None:
        typer.echo(cli._confirm_tool(request).value)

    result = runner.invoke(monkey_app, [], input="a\n")

    assert "[y/N]:" in result.stdout
    assert "always" not in result.stdout
    assert result.stdout.endswith("deny\n")
```

Update `confirm_test`, the personal-tool callback, and the Skill-script
callback so they do not rely on enum truthiness:

```python
@confirm_test_app.callback(invoke_without_command=True)
def confirm_test() -> None:
    decision = cli._confirm_tool(confirmation_request)
    typer.echo(
        "APPROVED"
        if decision is ConfirmationDecision.ALLOW_ONCE
        else "DENIED"
    )
```

Use the same identity comparison inside the two local `invoke()` callbacks:

```python
        decision = cli._confirm_tool(request)
        typer.echo(
            "APPROVED"
            if decision is ConfirmationDecision.ALLOW_ONCE
            else "DENIED"
        )
```

- [ ] **Step 2: Run CLI confirmation tests and verify failure**

Run:

```powershell
uv run pytest tests/test_cli.py -k "confirmation" -v
```

Expected: FAIL because `_confirm_tool` returns `bool` and never renders the
persistent prompt.

- [ ] **Step 3: Implement typed CLI confirmation**

Replace `_confirm_tool` with:

```python
def _confirm_tool(request: ConfirmationRequest) -> ConfirmationDecision:
    """Confirm a tool call, treating interruptions as denial."""
    prompt = (
        "[y] once / [a] always / [N] deny: "
        if request.allow_always
        else "[y/N]: "
    )
    try:
        typer.echo(f"{request.description} {prompt}", nl=False)
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt, typer.Abort):
        return ConfirmationDecision.DENY
    if answer in {"y", "yes"}:
        return ConfirmationDecision.ALLOW_ONCE
    if request.allow_always and answer in {"a", "always"}:
        return ConfirmationDecision.ALLOW_ALWAYS
    return ConfirmationDecision.DENY
```

Import `ConfirmationDecision` beside `ConfirmationRequest`.

- [ ] **Step 4: Run CLI and cross-tool confirmation tests**

Run:

```powershell
uv run pytest tests/test_cli.py tests/test_note_tools.py tests/test_todo_tools.py tests/test_memory_tools.py tests/test_skill_tools.py -v
```

Expected: PASS; only persistent Shell requests expose `always`.

- [ ] **Step 5: Commit CLI interaction**

```powershell
git add src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Add persistent Shell approval prompt"
```

---

### Task 6: Document, Verify, and Build

**Files:**
- Modify: `README.md`
- Verify: all source and test files from Tasks 1-5

**Interfaces:**
- Consumes: completed behavior from Tasks 1-5.
- Produces: documented user contract and full repository verification evidence.

- [ ] **Step 1: Update README Shell documentation**

Add a Shell approval section near the existing local-tool documentation with
this content:

```markdown
### Shell 命令审批

Shell 工具使用参数数组和 `shell=False`，并固定在当前 workspace 中运行。
参数安全且仅访问 workspace 内文件的 `pwd`、`ls`、`rg`、`grep`、`head`、
`tail`、`wc`、`sort`、`uniq`、`git status` 和 `git diff` 可以自动执行。
带有未知参数、写入参数、外部程序委托或 workspace 外路径的调用会请求确认。

确认时输入 `y` 仅允许本次执行；输入 `a` 会把最终实际执行的完整 argv
保存到 `<workspace>/.cdy-agent/shell-approvals.json`，以后在同一 workspace
中精确匹配时不再询问。匹配区分大小写、参数顺序和参数内容，不支持前缀
或通配符。编辑或删除该 JSON 文件即可撤销授权。

解释器、脚本和路径程序以当前用户权限运行。选择永久允许意味着信任相同
argv 的后续执行，即使对应脚本或程序内容后来发生变化。审批文件已由 Git
忽略，并且工具参数不会写入结构化日志或 trace。
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
uv run pytest tests/test_shell_approvals.py tests/test_shell_policy.py tests/test_shell_tool.py tests/test_tool_registry.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the complete offline suite**

Run:

```powershell
uv run pytest
```

Expected: PASS with no network or real API credential use.

- [ ] **Step 4: Verify CLI entry points**

Run:

```powershell
uv run cdy-agent --help
uv run cdy-agent ask --help
```

Expected: both commands exit 0 and display their Typer help.

- [ ] **Step 5: Build distributions**

Run:

```powershell
uv build
```

Expected: exit 0 and produce source and wheel distributions through Hatchling.

- [ ] **Step 6: Inspect the final change set**

Run:

```powershell
git status --short
git diff --check
git diff --stat HEAD
```

Expected: no whitespace errors; only intentional Shell approval source, tests,
and README changes are pending. Existing unrelated user files remain untouched.

- [ ] **Step 7: Commit documentation and final adjustments**

```powershell
git add README.md
git commit -m "Document tiered Shell command approvals"
```
