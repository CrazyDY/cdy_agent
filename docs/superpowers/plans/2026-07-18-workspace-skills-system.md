# Workspace Skills System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace-defined, model-activated Skills with Markdown instructions, optional user-approved Python tools, and atomic dynamic tool registration.

**Architecture:** A strict loader discovers Skill metadata without importing Python. A process-local manager activates instructions, asks before importing an optional `tools.py`, and atomically adds validated tools to the existing registry; two ordinary management tools expose discovery and activation to the model.

**Tech Stack:** Python 3.10+, standard-library `dataclasses`, `importlib`, `pathlib`, `re`, `sys`, and `uuid`; existing `Tool`, `ToolResult`, `ToolRegistry`, Typer CLI, OpenAI-compatible gateway, and pytest.

## Global Constraints

- Discover only direct children of `<workspace>/.cdy-agent/skills/`; a missing directory is an empty Skill set and must not create files.
- `SKILL.md` is at most 256 KiB and has exactly the required single-line `name` and `description` metadata fields plus nonblank Markdown instructions.
- Skill names match `[a-z][a-z0-9_]{0,63}`; descriptions are nonblank after trimming and at most 500 characters.
- Reject symlinked Skills roots, Skill directories, `SKILL.md`, and `tools.py`, and reject every resolved path outside the workspace.
- `tools.py` is optional, must be a regular file no larger than 1 MiB, and is revalidated immediately before import.
- Discovery never imports Python; instruction-only Skills activate without confirmation.
- Python runs in the main process with current-user permissions only after a default-No confirmation for that Skill in the current process.
- A successful activation is idempotent; rejected or failed activation does not change manager or registry state.
- Tool registration is all-or-nothing and rejects conflicts with built-ins, management tools, or previously activated Skills.
- Do not add YAML, plugin, sandbox, subprocess, dependency-installation, hot-reload, persistent-trust, session, or memory dependencies/features.
- Tests use temporary workspaces and fakes only; they never access provider credentials, the network, or contributor data.
- TDD preflight ruling: Tasks 1, 3, and 4 must move imports of not-yet-created modules inside a test helper that catches `ModuleNotFoundError` and calls `pytest.fail()`, so RED is an ordinary test failure rather than a collection error. Task 5's Agent Registry refresh test is a characterization regression expected to pass before the CLI integration change; the CLI construction test supplies that task's RED.

---

## File Structure

- Create `src/cdy_agent/skills/models.py`: immutable discovery records and diagnostics.
- Create `src/cdy_agent/skills/loader.py`: strict, non-executing workspace Skill discovery.
- Create `src/cdy_agent/skills/manager.py`: process-local activation, authorization, Python loading, and state.
- Create `src/cdy_agent/skills/tools.py`: `list_skills` and `activate_skill` model-tool adapters.
- Create `src/cdy_agent/skills/__init__.py`: narrow construction exports.
- Modify `src/cdy_agent/tools/registry.py`: validated atomic batch registration.
- Modify `src/cdy_agent/cli.py`: construct Skills alongside built-in tools.
- Modify `README.md`: user format, activation flow, trust boundary, and limits.
- Modify `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`: mark phase 6 delivered.
- Create `tests/test_skill_loader.py`, `tests/test_skill_manager.py`, and `tests/test_skill_tools.py`.
- Modify `tests/test_tool_registry.py`, `tests/test_agent.py`, and `tests/test_cli.py` for integration regressions.

### Task 1: Strict Non-Executing Skill Discovery

**Files:**
- Create: `src/cdy_agent/skills/models.py`
- Create: `src/cdy_agent/skills/loader.py`
- Create: `tests/test_skill_loader.py`

**Interfaces:**
- Produces: `SkillMetadata(name: str, description: str)`.
- Produces: `DiscoveredSkill(metadata: SkillMetadata, directory: Path, instructions: str, tools_path: Path | None)` and `has_tools: bool`.
- Produces: `SkillDiagnostic(entry: str, code: str, message: str)`.
- Produces: `SkillDiscovery(skills: tuple[DiscoveredSkill, ...], diagnostics: tuple[SkillDiagnostic, ...])`.
- Produces: `discover_skills(workspace: Path) -> SkillDiscovery`.

- [ ] **Step 1: Write failing happy-path discovery tests**

```python
# tests/test_skill_loader.py
from pathlib import Path

from cdy_agent.skills.loader import discover_skills


def write_skill(root: Path, name: str, body: str = "# Instructions") -> Path:
    directory = root / ".cdy-agent" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use {name}.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


def test_missing_skills_directory_is_empty_and_not_created(tmp_path: Path) -> None:
    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert discovery.diagnostics == ()
    assert not (tmp_path / ".cdy-agent").exists()


def test_discovers_sorted_metadata_instructions_and_optional_tools(tmp_path: Path) -> None:
    write_skill(tmp_path, "zeta", "# Zeta")
    alpha = write_skill(tmp_path, "alpha", "# Alpha")
    (alpha / "tools.py").write_text("def create_tools(workspace): return []\n")

    discovery = discover_skills(tmp_path)

    assert [item.metadata.name for item in discovery.skills] == ["alpha", "zeta"]
    assert discovery.skills[0].metadata.description == "Use alpha."
    assert discovery.skills[0].instructions == "# Alpha"
    assert discovery.skills[0].tools_path == alpha / "tools.py"
    assert discovery.skills[1].tools_path is None
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `uv run pytest tests/test_skill_loader.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'cdy_agent.skills'`.

- [ ] **Step 3: Implement records and the minimal strict loader**

```python
# src/cdy_agent/skills/models.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str


@dataclass(frozen=True)
class DiscoveredSkill:
    metadata: SkillMetadata
    directory: Path
    instructions: str
    tools_path: Path | None

    @property
    def has_tools(self) -> bool:
        return self.tools_path is not None


@dataclass(frozen=True)
class SkillDiagnostic:
    entry: str
    code: str
    message: str


@dataclass(frozen=True)
class SkillDiscovery:
    skills: tuple[DiscoveredSkill, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
```

```python
# src/cdy_agent/skills/loader.py
from __future__ import annotations

import re
from pathlib import Path

from cdy_agent.tools.filesystem import resolve_workspace

from .models import DiscoveredSkill, SkillDiagnostic, SkillDiscovery, SkillMetadata


MAX_SKILL_BYTES = 256 * 1024
MAX_TOOLS_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class InvalidSkillError(ValueError):
    pass


def discover_skills(workspace: Path) -> SkillDiscovery:
    workspace = resolve_workspace(workspace)
    root = workspace / ".cdy-agent" / "skills"
    if not root.exists() and not root.is_symlink():
        return SkillDiscovery((), ())
    try:
        _require_safe(root, workspace, directory=True)
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except (InvalidSkillError, OSError):
        diagnostic = SkillDiagnostic("skills", "invalid_skills_root", "Skills root is invalid.")
        return SkillDiscovery((), (diagnostic,))

    skills: list[DiscoveredSkill] = []
    diagnostics: list[SkillDiagnostic] = []
    for entry in entries:
        try:
            skills.append(_load_entry(entry, workspace))
        except (InvalidSkillError, OSError, UnicodeDecodeError) as error:
            diagnostics.append(SkillDiagnostic(entry.name, "invalid_skill", str(error)))
    return SkillDiscovery(tuple(skills), tuple(diagnostics))


def revalidate_tools_file(skill: DiscoveredSkill, workspace: Path) -> None:
    if skill.tools_path is None:
        return
    _require_safe(skill.tools_path, resolve_workspace(workspace), directory=False)
    if skill.tools_path.stat().st_size > MAX_TOOLS_BYTES:
        raise InvalidSkillError("tools.py exceeds 1 MiB.")


def _load_entry(directory: Path, workspace: Path) -> DiscoveredSkill:
    _require_safe(directory, workspace, directory=True)
    skill_file = directory / "SKILL.md"
    _require_safe(skill_file, workspace, directory=False)
    raw = skill_file.read_bytes()
    if len(raw) > MAX_SKILL_BYTES:
        raise InvalidSkillError("SKILL.md exceeds 256 KiB.")
    metadata, instructions = _parse_skill(raw.decode("utf-8"))
    if metadata.name != directory.name:
        raise InvalidSkillError("Skill name must match its directory.")
    tools_path = directory / "tools.py"
    if tools_path.exists() or tools_path.is_symlink():
        _require_safe(tools_path, workspace, directory=False)
        if tools_path.stat().st_size > MAX_TOOLS_BYTES:
            raise InvalidSkillError("tools.py exceeds 1 MiB.")
    else:
        tools_path = None
    return DiscoveredSkill(metadata, directory.resolve(), instructions, tools_path)


def _require_safe(path: Path, workspace: Path, *, directory: bool) -> None:
    if path.is_symlink():
        raise InvalidSkillError("Skill paths must not be symbolic links.")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise InvalidSkillError("Skill path is outside the workspace.") from error
    if directory and not resolved.is_dir():
        raise InvalidSkillError("Expected a directory.")
    if not directory and not resolved.is_file():
        raise InvalidSkillError("Expected a regular file.")


def _parse_skill(text: str) -> tuple[SkillMetadata, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise InvalidSkillError("SKILL.md must start with metadata.")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise InvalidSkillError("SKILL.md metadata is not closed.") from error
    values: dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" not in line:
            raise InvalidSkillError("Metadata must use key: value lines.")
        key, value = (part.strip() for part in line.split(":", 1))
        if key not in {"name", "description"} or key in values or not value:
            raise InvalidSkillError("Metadata fields are invalid.")
        values[key] = value
    if set(values) != {"name", "description"}:
        raise InvalidSkillError("name and description are required.")
    if NAME_PATTERN.fullmatch(values["name"]) is None:
        raise InvalidSkillError("Skill name is invalid.")
    description = values["description"].strip()
    if not description or len(description) > 500:
        raise InvalidSkillError("Skill description must be 1 to 500 characters.")
    instructions = "\n".join(lines[closing + 1:]).strip()
    if not instructions:
        raise InvalidSkillError("Skill instructions must not be empty.")
    return SkillMetadata(values["name"], description), instructions
```

- [ ] **Step 4: Add table-driven invalid-entry isolation tests**

```python
# append to tests/test_skill_loader.py
import os

import pytest


@pytest.mark.parametrize(
    "content",
    [
        "# no metadata\n",
        "---\nname: Bad-Name\ndescription: bad\n---\nbody\n",
        "---\nname: sample\ndescription:\n---\nbody\n",
        "---\nname: sample\ndescription: ok\nextra: no\n---\nbody\n",
        "---\nname: sample\ndescription: ok\n---\n   \n",
    ],
)
def test_invalid_skill_is_diagnosed_without_hiding_valid_skill(
    tmp_path: Path, content: str
) -> None:
    write_skill(tmp_path, "valid")
    invalid = tmp_path / ".cdy-agent" / "skills" / "sample"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text(content, encoding="utf-8")

    discovery = discover_skills(tmp_path)

    assert [skill.metadata.name for skill in discovery.skills] == ["valid"]
    assert discovery.diagnostics[0].entry == "sample"
    assert discovery.diagnostics[0].code == "invalid_skill"


def test_symlinked_skill_directory_is_rejected(tmp_path: Path) -> None:
    target = write_skill(tmp_path, "target")
    os.symlink(target, target.parent / "linked", target_is_directory=True)

    discovery = discover_skills(tmp_path)

    assert "linked" in [item.entry for item in discovery.diagnostics]


def test_rejects_oversized_skill_and_symlinked_tools(tmp_path: Path) -> None:
    oversized = write_skill(tmp_path, "oversized")
    (oversized / "SKILL.md").write_bytes(b"x" * (256 * 1024 + 1))
    linked_tools = write_skill(tmp_path, "linked_tools")
    target = tmp_path / "outside.py"
    target.write_text("def create_tools(workspace): return []\n", encoding="utf-8")
    os.symlink(target, linked_tools / "tools.py")

    discovery = discover_skills(tmp_path)

    assert [item.entry for item in discovery.diagnostics] == ["linked_tools", "oversized"]


def test_rejects_symlinked_skills_root(tmp_path: Path) -> None:
    target = tmp_path / "real-skills"
    target.mkdir()
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    os.symlink(target, data / "skills", target_is_directory=True)

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert discovery.diagnostics[0].code == "invalid_skills_root"
```

- [ ] **Step 5: Run loader tests and commit**

Run: `uv run pytest tests/test_skill_loader.py -v`

Expected: all tests pass.

```bash
git add src/cdy_agent/skills/models.py src/cdy_agent/skills/loader.py tests/test_skill_loader.py
git commit -m "Add workspace skill discovery"
```

### Task 2: Atomic Dynamic Tool Registration

**Files:**
- Modify: `src/cdy_agent/tools/registry.py`
- Modify: `tests/test_tool_registry.py`

**Interfaces:**
- Consumes: existing structural `Tool` contract.
- Produces: `ToolRegistry.register_many(tools: Iterable[Tool]) -> ToolResult`.
- Success data is `{"names": list[str]}`; failures use `invalid_tools` or `tool_name_conflict` and leave the registry unchanged.

- [ ] **Step 1: Write failing registration tests**

```python
# append to tests/test_tool_registry.py
def test_register_many_adds_valid_tools_in_order() -> None:
    registry = ToolRegistry([EchoTool(name="first")])

    result = registry.register_many([EchoTool(name="second"), EchoTool(name="third")])

    assert result == ToolResult.success({"names": ["second", "third"]})
    assert [item["name"] for item in registry.definitions] == ["first", "second", "third"]


def test_register_many_is_atomic_on_name_conflict() -> None:
    registry = ToolRegistry([EchoTool(name="existing")])

    result = registry.register_many([EchoTool(name="new"), EchoTool(name="existing")])

    assert result.code == "tool_name_conflict"
    assert [item["name"] for item in registry.definitions] == ["existing"]


def test_register_many_rejects_invalid_tool_without_mutation() -> None:
    registry = ToolRegistry([EchoTool(name="existing")])
    invalid = EchoTool(name="new")
    invalid.parameters = []  # type: ignore[assignment]

    result = registry.register_many([invalid])

    assert result.code == "invalid_tools"
    assert [item["name"] for item in registry.definitions] == ["existing"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_tool_registry.py -v`

Expected: FAIL with `AttributeError: 'ToolRegistry' object has no attribute 'register_many'`.

- [ ] **Step 3: Implement complete prevalidation and atomic update**

```python
# add near the imports in src/cdy_agent/tools/registry.py
import re

TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")

# add inside ToolRegistry in src/cdy_agent/tools/registry.py
    def register_many(self, tools: Iterable[Tool]) -> ToolResult:
        try:
            candidates = tuple(tools)
        except (TypeError, RuntimeError):
            return ToolResult.failure("invalid_tools", "Tool factory must return an iterable.")
        names: list[str] = []
        for tool in candidates:
            if not _valid_tool(tool):
                return ToolResult.failure("invalid_tools", "Skill returned an invalid tool.")
            names.append(tool.name)
        if len(names) != len(set(names)) or any(name in self._tools for name in names):
            return ToolResult.failure("tool_name_conflict", "Tool name conflicts with an existing tool.")
        self._tools.update(zip(names, candidates))
        return ToolResult.success({"names": names})


def _valid_tool(tool: object) -> bool:
    return (
        isinstance(getattr(tool, "name", None), str)
        and TOOL_NAME_PATTERN.fullmatch(tool.name) is not None
        and isinstance(getattr(tool, "description", None), str)
        and bool(tool.description)
        and isinstance(getattr(tool, "parameters", None), dict)
        and isinstance(getattr(tool, "requires_confirmation", None), bool)
        and callable(getattr(tool, "preflight", None))
        and callable(getattr(tool, "confirmation_description", None))
        and callable(getattr(tool, "execute", None))
    )
```

- [ ] **Step 4: Run registry tests and commit**

Run: `uv run pytest tests/test_tool_registry.py -v`

Expected: all tests pass.

```bash
git add src/cdy_agent/tools/registry.py tests/test_tool_registry.py
git commit -m "Add atomic tool registration"
```

### Task 3: Process-Local Skill Activation Manager

**Files:**
- Create: `src/cdy_agent/skills/manager.py`
- Create: `tests/test_skill_manager.py`

**Interfaces:**
- Consumes: `SkillDiscovery`, `discover_skills()`, `revalidate_tools_file()`, `ToolRegistry.register_many()`, and `ConfirmationCallback`.
- Produces: `SkillManager(workspace: Path, registry: ToolRegistry, confirm: ConfirmationCallback)`.
- Produces: `SkillManager.list_skills() -> dict[str, object]` and `activate(name: str) -> ToolResult`.

- [ ] **Step 1: Write failing instruction-only activation test**

```python
# tests/test_skill_manager.py
from pathlib import Path

from cdy_agent.skills.manager import SkillManager
from cdy_agent.tools.base import ToolResult
from cdy_agent.tools.registry import ToolRegistry


def write_skill(tmp_path: Path, name: str, tools: str | None = None) -> Path:
    directory = tmp_path / ".cdy-agent" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use {name}.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    if tools is not None:
        (directory / "tools.py").write_text(tools, encoding="utf-8")
    return directory


def test_instruction_only_skill_activates_without_confirmation(tmp_path: Path) -> None:
    write_skill(tmp_path, "content_summary")
    confirmations = []
    manager = SkillManager(tmp_path, ToolRegistry([]), confirmations.append)

    first = manager.activate("content_summary")
    second = manager.activate("content_summary")

    assert first.data == {
        "name": "content_summary", "status": "activated",
        "instructions": "# content_summary", "tools": [],
    }
    assert second.data["status"] == "already_active"
    assert confirmations == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_skill_manager.py -v`

Expected: collection fails because `cdy_agent.skills.manager` does not exist.

- [ ] **Step 3: Implement manager state and instruction-only activation**

```python
# src/cdy_agent/skills/manager.py
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

from cdy_agent.tools.base import ConfirmationCallback, ConfirmationRequest, ToolResult
from cdy_agent.tools.registry import ToolRegistry

from .loader import InvalidSkillError, discover_skills, revalidate_tools_file
from .models import DiscoveredSkill


class SkillManager:
    def __init__(
        self, workspace: Path, registry: ToolRegistry, confirm: ConfirmationCallback
    ) -> None:
        self.workspace = workspace.resolve()
        self.registry = registry
        self.confirm = confirm
        discovery = discover_skills(self.workspace)
        self._skills = {skill.metadata.name: skill for skill in discovery.skills}
        self._diagnostics = discovery.diagnostics
        self._active: dict[str, tuple[str, ...]] = {}

    def list_skills(self) -> dict[str, object]:
        return {
            "skills": [
                {
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "has_tools": skill.has_tools,
                    "active": skill.metadata.name in self._active,
                }
                for skill in self._skills.values()
            ],
            "diagnostics": [
                {"entry": item.entry, "code": item.code, "message": item.message}
                for item in self._diagnostics
            ],
        }

    def activate(self, name: str) -> ToolResult:
        skill = self._skills.get(name)
        if skill is None:
            if any(item.entry == name for item in self._diagnostics):
                return ToolResult.failure("invalid_skill", f"Skill '{name}' is invalid.")
            return ToolResult.failure("unknown_skill", f"Unknown Skill: {name}.")
        if name in self._active:
            return self._success(skill, "already_active", self._active[name])
        if skill.tools_path is None:
            self._active[name] = ()
            return self._success(skill, "activated", ())
        return self._activate_tools(skill)

    def _success(
        self, skill: DiscoveredSkill, status: str, names: tuple[str, ...]
    ) -> ToolResult:
        return ToolResult.success({
            "name": skill.metadata.name,
            "status": status,
            "instructions": skill.instructions,
            "tools": list(names),
        })

    def _activate_tools(self, skill: DiscoveredSkill) -> ToolResult:
        raise NotImplementedError
```

- [ ] **Step 4: Add failing approved, denied, failed, and conflicting Python tests**

```python
# append to tests/test_skill_manager.py
ECHO_TOOL = '''
from cdy_agent.tools.base import ToolResult
class Echo:
    name = "skill_echo"
    description = "Echo text."
    parameters = {"type": "object", "properties": {}}
    requires_confirmation = False
    def preflight(self, arguments): return None
    def confirmation_description(self, arguments): return "Echo."
    def execute(self, arguments): return ToolResult.success(arguments)
def create_tools(workspace): return [Echo()]
'''


def test_python_skill_requires_one_confirmation_and_registers_tool(tmp_path: Path) -> None:
    write_skill(tmp_path, "python_skill", ECHO_TOOL)
    requests = []
    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, lambda request: requests.append(request) or True)

    assert manager.activate("python_skill").data["tools"] == ["skill_echo"]
    assert manager.activate("python_skill").data["status"] == "already_active"
    assert len(requests) == 1
    assert requests[0].tool_name == "activate_skill"
    assert str(tmp_path / ".cdy-agent/skills/python_skill/tools.py") in requests[0].description
    assert [item["name"] for item in registry.definitions] == ["skill_echo"]


def test_denied_or_broken_python_skill_does_not_mutate_registry(tmp_path: Path) -> None:
    write_skill(tmp_path, "denied", ECHO_TOOL)
    denied_registry = ToolRegistry([])
    denied = SkillManager(tmp_path, denied_registry, lambda request: False).activate("denied")
    assert denied.code == "approval_denied"
    assert denied_registry.definitions == ()

    write_skill(tmp_path, "broken", "raise RuntimeError('secret detail')\n")
    broken_registry = ToolRegistry([])
    broken = SkillManager(tmp_path, broken_registry, lambda request: True).activate("broken")
    assert broken.code == "load_failed"
    assert "secret detail" not in (broken.message or "")
    assert broken_registry.definitions == ()


class SimpleTool:
    description = "Simple."
    parameters = {"type": "object", "properties": {}}
    requires_confirmation = False

    def __init__(self, name: str) -> None:
        self.name = name

    def preflight(self, arguments):
        return None

    def confirmation_description(self, arguments):
        return "Run simple tool."

    def execute(self, arguments):
        return ToolResult.success(arguments)


def test_conflicting_python_skill_is_not_marked_active(tmp_path: Path) -> None:
    conflicting_source = ECHO_TOOL.replace('name = "skill_echo"', 'name = "existing"')
    write_skill(tmp_path, "conflicting", conflicting_source)
    registry = ToolRegistry([])
    assert registry.register_many([SimpleTool("existing")]).ok
    manager = SkillManager(tmp_path, registry, lambda request: True)

    first = manager.activate("conflicting")
    second = manager.activate("conflicting")

    assert first.code == "tool_name_conflict"
    assert second.code == "tool_name_conflict"
    assert [item["name"] for item in registry.definitions] == ["existing"]
```

- [ ] **Step 5: Implement authorized import with cleanup and atomic registration**

```python
# replace _activate_tools in src/cdy_agent/skills/manager.py
    def _activate_tools(self, skill: DiscoveredSkill) -> ToolResult:
        assert skill.tools_path is not None
        try:
            revalidate_tools_file(skill, self.workspace)
        except (InvalidSkillError, OSError):
            return ToolResult.failure("invalid_skill", f"Skill '{skill.metadata.name}' changed or is invalid.")
        request = ConfirmationRequest(
            "activate_skill",
            {"name": skill.metadata.name},
            f"Run Skill '{skill.metadata.name}' Python code from {skill.tools_path} with current user permissions.",
        )
        if not self.confirm(request):
            return ToolResult.failure("approval_denied", "User declined this Skill activation.")
        try:
            revalidate_tools_file(skill, self.workspace)
        except (InvalidSkillError, OSError):
            return ToolResult.failure("invalid_skill", f"Skill '{skill.metadata.name}' changed or is invalid.")

        module_name = f"_cdy_agent_skill_{skill.metadata.name}_{uuid4().hex}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, skill.tools_path)
            if spec is None or spec.loader is None:
                return ToolResult.failure("load_failed", f"Could not load Skill '{skill.metadata.name}'.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            factory = getattr(module, "create_tools", None)
            if not callable(factory):
                return ToolResult.failure("invalid_tools", "Skill must define create_tools(workspace).")
            tools = factory(self.workspace)
            registered = self.registry.register_many(tools)
        except Exception:
            return ToolResult.failure("load_failed", f"Could not load Skill '{skill.metadata.name}'.")
        finally:
            sys.modules.pop(module_name, None)
        if not registered.ok:
            return registered
        names = tuple(registered.data["names"])
        self._active[skill.metadata.name] = names
        return self._success(skill, "activated", names)
```

- [ ] **Step 6: Run manager tests and commit**

Run: `uv run pytest tests/test_skill_manager.py tests/test_skill_loader.py tests/test_tool_registry.py -v`

Expected: all tests pass.

```bash
git add src/cdy_agent/skills/manager.py tests/test_skill_manager.py
git commit -m "Add workspace skill activation"
```

### Task 4: Model-Facing Skill Management Tools

**Files:**
- Create: `src/cdy_agent/skills/tools.py`
- Create: `src/cdy_agent/skills/__init__.py`
- Create: `tests/test_skill_tools.py`

**Interfaces:**
- Consumes: `SkillManager.list_skills()` and `SkillManager.activate(name)`.
- Produces: `ListSkillsTool(manager)` and `ActivateSkillTool(manager)` satisfying `Tool`.
- Produces: `create_skill_tools(manager: SkillManager) -> tuple[ListSkillsTool, ActivateSkillTool]`.

- [ ] **Step 1: Write failing schema and dispatch tests**

```python
# tests/test_skill_tools.py
from types import SimpleNamespace

from cdy_agent.skills.tools import ActivateSkillTool, ListSkillsTool
from cdy_agent.tools.base import ToolResult


class FakeManager:
    def list_skills(self):
        return {"skills": [], "diagnostics": []}

    def activate(self, name):
        return ToolResult.success({"name": name})


def test_list_skills_accepts_only_empty_arguments() -> None:
    tool = ListSkillsTool(FakeManager())
    assert tool.execute({}).data == {"skills": [], "diagnostics": []}
    assert tool.preflight({"extra": True}).code == "invalid_arguments"
    assert tool.requires_confirmation is False


def test_activate_skill_requires_one_valid_name() -> None:
    tool = ActivateSkillTool(FakeManager())
    assert tool.execute({"name": "research"}).data == {"name": "research"}
    assert tool.preflight({}).code == "invalid_arguments"
    assert tool.preflight({"name": 1}).code == "invalid_arguments"
    assert tool.requires_confirmation is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_skill_tools.py -v`

Expected: collection fails because `cdy_agent.skills.tools` does not exist.

- [ ] **Step 3: Implement both adapters and package exports**

```python
# src/cdy_agent/skills/tools.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cdy_agent.tools.base import ToolResult

from .loader import NAME_PATTERN
from .manager import SkillManager


@dataclass
class ListSkillsTool:
    manager: SkillManager
    name: str = "list_skills"
    description: str = "List workspace Skills available for optional activation."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False,
    })
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return None if not arguments else ToolResult.failure("invalid_arguments", "No arguments are accepted.")

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "List workspace Skills."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or ToolResult.success(self.manager.list_skills())


@dataclass
class ActivateSkillTool:
    manager: SkillManager
    name: str = "activate_skill"
    description: str = "Activate one workspace Skill and receive its instructions and tools."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        if set(arguments) != {"name"} or not isinstance(arguments["name"], str) or NAME_PATTERN.fullmatch(arguments["name"]) is None:
            return ToolResult.failure("invalid_arguments", "name must be a valid Skill name.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Activate Skill {arguments.get('name', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = self.preflight(arguments)
        return invalid or self.manager.activate(arguments["name"])


def create_skill_tools(manager: SkillManager) -> tuple[ListSkillsTool, ActivateSkillTool]:
    return ListSkillsTool(manager), ActivateSkillTool(manager)
```

```python
# src/cdy_agent/skills/__init__.py
from .manager import SkillManager
from .tools import create_skill_tools

__all__ = ["SkillManager", "create_skill_tools"]
```

- [ ] **Step 4: Run management-tool tests and commit**

Run: `uv run pytest tests/test_skill_tools.py -v`

Expected: all tests pass.

```bash
git add src/cdy_agent/skills/__init__.py src/cdy_agent/skills/tools.py tests/test_skill_tools.py
git commit -m "Expose skill management tools"
```

### Task 5: CLI and Agent Loop Integration

**Files:**
- Modify: `src/cdy_agent/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_agent.py`

**Interfaces:**
- Consumes: `SkillManager`, `create_skill_tools()`, `_confirm_tool`, and `ToolRegistry.register_many()`.
- Preserves: `_create_agent(model: str, api_mode: str, workspace: Path) -> Agent` and all CLI command signatures.
- Produces: every CLI-created Agent starts with deterministic `list_skills` and `activate_skill` definitions after existing built-ins.

- [ ] **Step 1: Write failing CLI construction and authorization-description tests**

```python
# append to tests/test_cli.py
def test_create_agent_registers_skill_management_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "ModelGateway", lambda **kwargs: object())

    agent = cli._create_agent("model", "responses", tmp_path)

    names = [definition["name"] for definition in agent._registry.definitions]
    assert names[-2:] == ["list_skills", "activate_skill"]


def test_skill_code_confirmation_warns_about_current_user_permissions() -> None:
    request = ConfirmationRequest(
        "activate_skill",
        {"name": "research"},
        "Run Skill 'research' Python code from /workspace/tools.py with current user permissions.",
    )
    monkey_app = typer.Typer()

    @monkey_app.callback(invoke_without_command=True)
    def invoke() -> None:
        typer.echo("APPROVED" if cli._confirm_tool(request) else "DENIED")

    result = runner.invoke(monkey_app, [], input="\n")
    assert "current user permissions" in result.stdout
    assert result.stdout.endswith("DENIED\n")
```

- [ ] **Step 2: Run focused CLI tests to verify failure**

Run: `uv run pytest tests/test_cli.py::test_create_agent_registers_skill_management_tools tests/test_cli.py::test_skill_code_confirmation_warns_about_current_user_permissions -v`

Expected: the registration assertion fails because the two tools are absent.

- [ ] **Step 3: Wire Skills into the existing factory**

```python
# add imports in src/cdy_agent/cli.py
from .skills import SkillManager, create_skill_tools

# replace _create_agent body
def _create_agent(model: str, api_mode: str, workspace: Path) -> Agent:
    """Construct the CLI's shared model-and-local-tools boundary."""
    gateway = ModelGateway(model=model, api_mode=api_mode)
    registry = create_builtin_registry(workspace)
    manager = SkillManager(workspace, registry, _confirm_tool)
    registered = registry.register_many(create_skill_tools(manager))
    if not registered.ok:
        raise RuntimeError(registered.message or "Could not register Skill tools.")
    return Agent(gateway, registry, _confirm_tool)
```

- [ ] **Step 4: Write a failing Agent regression proving definitions refresh after activation**

```python
# append to tests/test_agent.py
def test_agent_refreshes_definitions_after_registry_mutation() -> None:
    class AddTool:
        name = "add_tool"
        description = "Add a tool."
        parameters = {"type": "object", "properties": {}}
        requires_confirmation = False

        def __init__(self, registry: object) -> None:
            self.registry = registry

        def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
            return None

        def confirmation_description(self, arguments: dict[str, Any]) -> str:
            return "Add."

        def execute(self, arguments: dict[str, Any]) -> ToolResult:
            result = self.registry.register_many([EchoTool()])
            return result

    class EchoTool:
        name = "dynamic_echo"
        description = "Echo."
        parameters = {"type": "object", "properties": {}}
        requires_confirmation = False
        def preflight(self, arguments): return None
        def confirmation_description(self, arguments): return "Echo."
        def execute(self, arguments): return ToolResult.success(arguments)

    registry = ToolRegistry([])
    registry.register_many([AddTool(registry)])
    gateway = FakeGateway([
        ToolCallResponse((ToolCall("1", "add_tool", "{}"),), ResponsesContinuation("next")),
        FinalResponse("done"),
    ])

    assert Agent(gateway, registry, lambda request: True).run([Message("user", "go")]) == "done"
    assert [item["name"] for item in gateway.calls[0]["tools"]] == ["add_tool"]
    assert [item["name"] for item in gateway.calls[1]["tools"]] == ["add_tool", "dynamic_echo"]
```

Add the exact import to `tests/test_agent.py`:

```python
from cdy_agent.tools.registry import ToolRegistry
```

- [ ] **Step 5: Run integration and full regression tests**

Run: `uv run pytest tests/test_cli.py tests/test_agent.py -v`

Expected: all tests pass.

Run: `uv run pytest`

Expected: all tests pass with no network calls.

- [ ] **Step 6: Commit CLI and loop integration**

```bash
git add src/cdy_agent/cli.py tests/test_cli.py tests/test_agent.py
git commit -m "Integrate workspace skills"
```

### Task 6: Documentation and Release Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`

**Interfaces:**
- Documents the exact `SKILL.md` format and `create_tools(workspace)` contract delivered by Tasks 1–5.
- Marks phase 6 delivered without claiming persistence, memory, sandboxing, dependencies, or hot reload.

- [ ] **Step 1: Update README current-stage text and add a Skills section**

Replace the current-stage paragraph with:

```markdown
项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和进程内多轮会话，两种 API 模式均可通过同一个 Agent Tool Loop 使用受限的本地工具。模型还可以从工作区发现并按需激活 Skills；带 Python 工具的 Skill 在当前进程首次加载前需要用户明确授权。持久化会话和长期记忆将在后续阶段加入。
```

Add after the personal-data section:

```markdown
### 工作区 Skills

Skills 位于 `<workspace>/.cdy-agent/skills/<skill_name>/`。每个 Skill 必须包含 `SKILL.md`：

```text
---
name: research
description: Search and summarize local project information.
---

# Research

这里写激活后交给模型的完整说明。
```

Skill 可选提供单个 `tools.py`，其中必须定义 `create_tools(workspace: Path)` 并返回符合项目 `Tool` 协议的可迭代对象。模型初始只看到 Skill 名称和摘要；调用 `activate_skill` 后才获得完整说明和新工具。

发现过程不会执行 Python。加载 `tools.py` 前，CLI 会显示绝对路径并默认拒绝；批准后代码以当前用户权限在主进程运行，因此只应激活可信工作区中的代码。授权只在当前进程有效。首版不提供沙箱、依赖安装、Skill 间依赖、辅助 Python 包、热重载或持久信任。
```

- [ ] **Step 2: Mark phase 6 delivered in the roadmap**

Replace the phase 6 paragraph with:

```markdown
### 6. Skills 系统

本阶段已经交付工作区 Skills 系统。模型可以从 `<workspace>/.cdy-agent/skills/` 发现名称与摘要并按需激活完整说明；Skill 可选注册 Python 函数工具。发现不执行代码，Python 在当前进程首次加载前需用户确认，动态工具通过现有 Registry 原子注册。
```

- [ ] **Step 3: Run final automated and CLI/package verification**

Run: `uv run pytest`

Expected: all tests pass.

Run: `uv run cdy-agent --help`

Expected: exit code 0 and help includes `ask` and `chat`.

Run: `uv run cdy-agent ask --help`

Expected: exit code 0 and help includes `--workspace`.

Run: `uv run cdy-agent chat --help`

Expected: exit code 0 and help includes `--workspace`.

Run: `uv build`

Expected: exit code 0 and both source and wheel distributions are built successfully.

- [ ] **Step 4: Review the diff for secrets and generated artifacts**

Run: `git status --short && git diff --check && git diff --stat`

Expected: only intended source, test, README, roadmap, and plan-related files appear; no `.env`, API keys, model responses, caches, `.idea/`, `.venv/`, or build artifacts are staged.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md
git commit -m "Document workspace skills"
```
