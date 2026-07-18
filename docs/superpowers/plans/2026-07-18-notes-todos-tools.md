# Notes and Todo Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace-persistent note and Todo tools to the existing dual-API Agent Tool Loop with strict validation, confirmation for every mutation, and atomic JSON storage.

**Architecture:** A focused `PersonalStore` owns `.cdy-agent/notes.json` and `.cdy-agent/todos.json`, including workspace containment, strict versioned-document validation, and atomic replacement. Separate note and Todo tool modules own operation schemas and semantics; the existing Registry, Agent, API adapters, and CLI confirmation callback remain generic.

**Tech Stack:** Python 3.10+, standard-library `dataclasses`, `datetime`, `json`, `os`, `pathlib`, `tempfile`, `uuid`, existing `ToolResult`/`ToolRegistry`, and pytest.

## Global Constraints

- Persist data only under `<workspace>/.cdy-agent/notes.json` and `<workspace>/.cdy-agent/todos.json`.
- Use version `1` documents with exactly `{"version": 1, "items": [...]}` at the top level.
- Do not add SQLite, migrations, CLI subcommands, editing, search, tags, priority, deadlines, or multi-process locking.
- Do not expose storage paths in model tool arguments.
- Require default-No confirmation for create, complete, and delete operations; list and get operations run automatically.
- Note titles are nonblank after trimming and at most 200 characters; note content is at most 64 KiB as UTF-8.
- Todo text is nonblank after trimming and at most 1,000 characters.
- IDs are canonical UUID strings and timestamps are UTC ISO 8601 strings ending in `Z`.
- Reject malformed, non-UTF-8, unknown-version, structurally invalid, or symlink-escaping stores without overwriting them.
- Use a same-directory temporary file and `os.replace` for atomic writes.
- Tests must not use real provider credentials, network access, or contributor data.

---

## File Structure

- Create `src/cdy_agent/tools/personal_store.py`: versioned note/Todo document validation, workspace-safe path resolution, reads, and atomic writes.
- Create `src/cdy_agent/tools/notes.py`: four note tools and note-specific validation.
- Create `src/cdy_agent/tools/todos.py`: four Todo tools and Todo-specific validation.
- Modify `src/cdy_agent/tools/__init__.py`: construct one shared store and register all eight new tools deterministically.
- Create `tests/test_personal_store.py`: persistence, validation, containment, and atomic failure tests.
- Create `tests/test_note_tools.py`: note schemas, lifecycle, validation, and confirmation tests.
- Create `tests/test_todo_tools.py`: Todo schemas, lifecycle, validation, and confirmation tests.
- Modify `tests/test_agent.py`: built-in registry ordering and real gateway-definition regression.
- Modify `tests/test_cli.py`: generic confirmation output regression using a personal-tool request.
- Modify `.gitignore`: ignore `.cdy-agent/` at any workspace level.
- Modify `README.md`: document Phase 5 operations, storage, confirmations, and single-process limitation.
- Modify `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`: mark Phase 5 delivered precisely.

### Task 1: Personal Store Persistence Boundary

**Files:**
- Create: `src/cdy_agent/tools/personal_store.py`
- Create: `tests/test_personal_store.py`

**Interfaces:**
- Consumes: `resolve_workspace(path: Path) -> Path` and `ToolResult`.
- Produces: `PersonalStore(workspace: Path)`.
- Produces: `PersonalStore.load_notes() -> ToolResult` and `save_notes(items: list[dict[str, Any]]) -> ToolResult`.
- Produces: `PersonalStore.load_todos() -> ToolResult` and `save_todos(items: list[dict[str, Any]]) -> ToolResult`.
- Successful loads place a fresh `list[dict[str, Any]]` in `ToolResult.data`; successful saves return `{"path": str, "count": int}`.

- [ ] **Step 1: Write failing empty-store and persistence tests**

```python
# tests/test_personal_store.py
import json
from pathlib import Path

from cdy_agent.tools.personal_store import PersonalStore


NOTE = {
    "id": "00000000-0000-4000-8000-000000000001",
    "title": "Plan",
    "content": "Ship phase five",
    "created_at": "2026-07-18T02:00:00Z",
}
TODO = {
    "id": "00000000-0000-4000-8000-000000000002",
    "text": "Write tests",
    "completed": False,
    "created_at": "2026-07-18T02:01:00Z",
    "completed_at": None,
}


def test_empty_store_reads_without_creating_data_directory(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path)

    assert store.load_notes().data == []
    assert store.load_todos().data == []
    assert not (tmp_path / ".cdy-agent").exists()


def test_store_persists_versioned_note_and_todo_documents(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path)

    assert store.save_notes([NOTE]).ok
    assert store.save_todos([TODO]).ok
    assert PersonalStore(tmp_path).load_notes().data == [NOTE]
    assert PersonalStore(tmp_path).load_todos().data == [TODO]
    assert json.loads((tmp_path / ".cdy-agent/notes.json").read_text()) == {
        "version": 1,
        "items": [NOTE],
    }
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `uv run pytest tests/test_personal_store.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'cdy_agent.tools.personal_store'`.

- [ ] **Step 3: Implement workspace-safe empty reads and versioned atomic writes**

```python
# src/cdy_agent/tools/personal_store.py
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from .base import ToolResult
from .filesystem import resolve_workspace


STORE_VERSION = 1
DATA_DIRECTORY = ".cdy-agent"


class PersonalStore:
    def __init__(
        self,
        workspace: Path,
        replace: Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes],
                           str | bytes | os.PathLike[str] | os.PathLike[bytes]], None]
        = os.replace,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self._replace = replace

    def load_notes(self) -> ToolResult:
        return self._load("notes.json", _validate_notes)

    def save_notes(self, items: list[dict[str, Any]]) -> ToolResult:
        return self._save("notes.json", items, _validate_notes)

    def load_todos(self) -> ToolResult:
        return self._load("todos.json", _validate_todos)

    def save_todos(self, items: list[dict[str, Any]]) -> ToolResult:
        return self._save("todos.json", items, _validate_todos)

    def _load(self, filename: str, validator: Callable[[object], bool]) -> ToolResult:
        target = self._target(filename, create_directory=False)
        if isinstance(target, ToolResult):
            return target
        if target is None:
            return ToolResult.success([])
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return ToolResult.failure("invalid_store", "Stored personal data is invalid.")
        if not validator(document):
            return ToolResult.failure("invalid_store", "Stored personal data is invalid.")
        return ToolResult.success([dict(item) for item in document["items"]])

    def _save(
        self,
        filename: str,
        items: list[dict[str, Any]],
        validator: Callable[[object], bool],
    ) -> ToolResult:
        document = {"version": STORE_VERSION, "items": items}
        if not validator(document):
            return ToolResult.failure("invalid_store", "Refusing to write invalid personal data.")
        target = self._target(filename, create_directory=True)
        if isinstance(target, ToolResult) or target is None:
            return target or ToolResult.failure("store_error", "Could not create data store.")
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(dir=target.parent, prefix=f".{filename}.")
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            self._replace(temporary, target)
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            return ToolResult.failure("store_error", "Could not write personal data.")
        return ToolResult.success({"path": str(target), "count": len(items)})

    def _target(self, filename: str, create_directory: bool) -> Path | ToolResult | None:
        data_directory = self.workspace / DATA_DIRECTORY
        try:
            if not data_directory.exists() and not data_directory.is_symlink():
                if not create_directory:
                    return None
                data_directory.mkdir()
            resolved_directory = data_directory.resolve()
            resolved_directory.relative_to(self.workspace)
            if not resolved_directory.is_dir():
                return ToolResult.failure("store_error", "Personal data path is not a directory.")
            target = resolved_directory / filename
            if target.is_symlink() or target.exists():
                resolved_target = target.resolve()
                resolved_target.relative_to(self.workspace)
                if not resolved_target.is_file():
                    return ToolResult.failure("store_error", "Personal data path is not a file.")
                return resolved_target
            return target
        except ValueError:
            return ToolResult.failure("path_outside_workspace", "Personal data is outside the workspace.")
        except OSError:
            return ToolResult.failure("store_error", "Could not access personal data.")


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc


def _valid_document(
    document: object,
    item_validator: Callable[[object], bool],
) -> bool:
    if not isinstance(document, dict) or set(document) != {"version", "items"}:
        return False
    if document["version"] != STORE_VERSION or not isinstance(document["items"], list):
        return False
    items = document["items"]
    if not all(item_validator(item) for item in items):
        return False
    identifiers = [item["id"] for item in items]
    return len(identifiers) == len(set(identifiers))


def _valid_note(item: object) -> bool:
    if not isinstance(item, dict) or set(item) != {"id", "title", "content", "created_at"}:
        return False
    title = item["title"]
    content = item["content"]
    return (
        _is_uuid(item["id"])
        and isinstance(title, str)
        and bool(title.strip())
        and len(title) <= 200
        and isinstance(content, str)
        and len(content.encode("utf-8")) <= 64 * 1024
        and _is_utc_timestamp(item["created_at"])
    )


def _valid_todo(item: object) -> bool:
    if not isinstance(item, dict) or set(item) != {
        "id", "text", "completed", "created_at", "completed_at"
    }:
        return False
    text = item["text"]
    completed = item["completed"]
    completed_at = item["completed_at"]
    completion_is_valid = (
        _is_utc_timestamp(completed_at) if completed else completed_at is None
    )
    return (
        _is_uuid(item["id"])
        and isinstance(text, str)
        and bool(text.strip())
        and len(text) <= 1000
        and type(completed) is bool
        and _is_utc_timestamp(item["created_at"])
        and completion_is_valid
    )


def _validate_notes(document: object) -> bool:
    return _valid_document(document, _valid_note)


def _validate_todos(document: object) -> bool:
    return _valid_document(document, _valid_todo)
```

- [ ] **Step 4: Run focused tests and existing filesystem regressions**

Run: `uv run pytest tests/test_personal_store.py tests/test_filesystem_tools.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the persistence boundary**

```bash
git add src/cdy_agent/tools/personal_store.py tests/test_personal_store.py
git commit -m "Add personal data store"
```

### Task 2: Store Corruption, Containment, and Atomic Failure

**Files:**
- Modify: `tests/test_personal_store.py`
- Modify: `src/cdy_agent/tools/personal_store.py`

**Interfaces:**
- Consumes and preserves all `PersonalStore` interfaces from Task 1.
- Guarantees corrupt data is never treated as empty or overwritten.
- Guarantees `.cdy-agent` and its two data files cannot resolve outside workspace.

- [ ] **Step 1: Add failing strict-validation and symlink tests**

```python
# append to tests/test_personal_store.py
import pytest


@pytest.mark.parametrize(
    "document",
    [
        {"version": 2, "items": []},
        {"version": 1, "items": [], "extra": True},
        {"version": 1, "items": [{**NOTE, "extra": True}]},
        {"version": 1, "items": [NOTE, NOTE]},
        {"version": 1, "items": [{**TODO, "completed": True}]},
    ],
)
def test_store_rejects_invalid_documents(tmp_path: Path, document: object) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    filename = "todos.json" if isinstance(document, dict) and document.get("items") and "text" in document["items"][0] else "notes.json"
    (data / filename).write_text(json.dumps(document), encoding="utf-8")

    result = PersonalStore(tmp_path).load_todos() if filename == "todos.json" else PersonalStore(tmp_path).load_notes()

    assert result.code == "invalid_store"


def test_store_rejects_data_directory_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-store"
    outside.mkdir()
    try:
        (tmp_path / ".cdy-agent").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("platform cannot create symlinks")

    assert PersonalStore(tmp_path).load_notes().code == "path_outside_workspace"


def test_store_rejects_data_file_symlink_escape_and_non_utf8(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-notes.json"
    outside.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")
    try:
        (data / "notes.json").symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("platform cannot create symlinks")
    assert PersonalStore(tmp_path).load_notes().code == "path_outside_workspace"

    (data / "notes.json").unlink()
    (data / "notes.json").write_bytes(b"\xff")
    assert PersonalStore(tmp_path).load_notes().code == "invalid_store"
```

- [ ] **Step 2: Add the failing atomic-replace preservation test**

```python
def test_failed_atomic_replace_preserves_original_and_removes_temp_file(
    tmp_path: Path,
) -> None:
    store = PersonalStore(tmp_path)
    assert store.save_notes([NOTE]).ok
    original = (tmp_path / ".cdy-agent/notes.json").read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    result = PersonalStore(tmp_path, replace=fail_replace).save_notes([])

    assert result.code == "store_error"
    assert (tmp_path / ".cdy-agent/notes.json").read_bytes() == original
    assert list((tmp_path / ".cdy-agent").glob(".notes.json.*")) == []
```

- [ ] **Step 3: Run tests to verify the new cases fail for the expected reasons**

Run: `uv run pytest tests/test_personal_store.py -v`

Expected: at least one new strict-validation or containment test fails; no unrelated test errors.

- [ ] **Step 4: Complete strict validators and path handling**

Use these exact helper contracts in `personal_store.py`:

```python
def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc
```

Implement validators with exact key-set comparisons and `type(value) is bool` for booleans. Validate title/text after trimming without altering stored valid data, validate content by UTF-8 byte length, reject duplicate IDs, require `completed_at is None` when incomplete, and require a valid UTC timestamp when complete. Preserve the generic `invalid_store`/`store_error` messages without embedding corrupt content or raw exception strings.

- [ ] **Step 5: Run focused and full storage-related tests**

Run: `uv run pytest tests/test_personal_store.py tests/test_filesystem_tools.py tests/test_shell_tool.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit store hardening**

```bash
git add src/cdy_agent/tools/personal_store.py tests/test_personal_store.py
git commit -m "Harden personal data storage"
```

### Task 3: Note Tools

**Files:**
- Create: `src/cdy_agent/tools/notes.py`
- Create: `tests/test_note_tools.py`

**Interfaces:**
- Consumes: `PersonalStore`, `ToolResult`, and Registry's `preflight`/confirmation/`execute` lifecycle.
- Produces: `CreateNoteTool`, `ListNotesTool`, `GetNoteTool`, and `DeleteNoteTool`.
- Constructors accept one shared `PersonalStore`; `CreateNoteTool` additionally accepts `id_factory: Callable[[], str]` and `now_factory: Callable[[], str]` with production UUID/UTC defaults.

- [ ] **Step 1: Write failing schemas and note lifecycle tests**

```python
# tests/test_note_tools.py
from pathlib import Path

from cdy_agent.tools.base import ToolCall
from cdy_agent.tools.notes import CreateNoteTool, DeleteNoteTool, GetNoteTool, ListNotesTool
from cdy_agent.tools.personal_store import PersonalStore
from cdy_agent.tools.registry import ToolRegistry


NOTE_ID = "00000000-0000-4000-8000-000000000010"
NOW = "2026-07-18T03:00:00Z"


def build_tools(tmp_path: Path):
    store = PersonalStore(tmp_path)
    return (
        store,
        CreateNoteTool(store, id_factory=lambda: NOTE_ID, now_factory=lambda: NOW),
        ListNotesTool(store),
        GetNoteTool(store),
        DeleteNoteTool(store),
    )


def test_note_tool_schemas_and_confirmation_flags(tmp_path: Path) -> None:
    _, create, list_notes, get, delete = build_tools(tmp_path)
    assert [tool.name for tool in (create, list_notes, get, delete)] == [
        "create_note", "list_notes", "get_note", "delete_note"
    ]
    assert [tool.requires_confirmation for tool in (create, list_notes, get, delete)] == [True, False, False, True]
    assert create.parameters["additionalProperties"] is False
    assert list_notes.parameters == {"type": "object", "properties": {}, "additionalProperties": False}


def test_note_lifecycle_and_list_omits_content(tmp_path: Path) -> None:
    store, create, list_notes, get, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, list_notes, get, delete])
    confirmations = []

    created = registry.execute(
        ToolCall("1", "create_note", '{"title":" Plan ","content":"Details"}'),
        lambda request: confirmations.append(request) or True,
    )
    assert created.data["id"] == NOTE_ID
    assert created.data["title"] == "Plan"
    assert "7 bytes" in confirmations[0].description
    assert list_notes.execute({}).data == [{"id": NOTE_ID, "title": "Plan", "created_at": NOW}]
    assert get.execute({"note_id": NOTE_ID}).data["content"] == "Details"
    assert delete.execute({"note_id": NOTE_ID}).ok
    assert store.load_notes().data == []
```

- [ ] **Step 2: Write failing validation and pre-confirmation tests**

```python
def test_note_validation_and_missing_delete_happen_before_confirmation(tmp_path: Path) -> None:
    _, create, _, _, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, delete])
    confirmations = []

    cases = [
        (ToolCall("1", "create_note", '{"title":" ","content":"x"}'), "invalid_arguments"),
        (ToolCall("2", "create_note", '{"title":"x","content":1}'), "invalid_arguments"),
        (ToolCall("3", "create_note", '{"title":"x","content":"x","extra":1}'), "invalid_arguments"),
        (ToolCall("4", "delete_note", '{"note_id":"bad"}'), "invalid_arguments"),
        (ToolCall("5", "delete_note", '{"note_id":"00000000-0000-4000-8000-000000000099"}'), "note_not_found"),
    ]
    for call, code in cases:
        assert registry.execute(call, lambda request: confirmations.append(request) or True).code == code
    assert confirmations == []
    assert create.execute({"title": "x", "content": "你" * 21846}).code == "invalid_arguments"


def test_declined_note_mutations_do_not_create_or_change_store(tmp_path: Path) -> None:
    store, create, _, _, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, delete])
    denied_create = registry.execute(
        ToolCall("1", "create_note", '{"title":"Plan","content":"Details"}'),
        lambda _: False,
    )
    assert denied_create.code == "approval_denied"
    assert not (tmp_path / ".cdy-agent").exists()

    assert create.execute({"title": "Plan", "content": "Details"}).ok
    denied_delete = registry.execute(
        ToolCall("2", "delete_note", f'{{"note_id":"{NOTE_ID}"}}'),
        lambda _: False,
    )
    assert denied_delete.code == "approval_denied"
    assert len(store.load_notes().data) == 1
```

- [ ] **Step 3: Run note tests to verify failure**

Run: `uv run pytest tests/test_note_tools.py -v`

Expected: collection fails because `cdy_agent.tools.notes` does not exist.

- [ ] **Step 4: Implement all four note tools**

```python
# src/cdy_agent/tools/notes.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from .base import ToolResult
from .personal_store import PersonalStore


MAX_TITLE_CHARACTERS = 200
MAX_CONTENT_BYTES = 64 * 1024


def _new_id() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _find(items: list[dict[str, Any]], note_id: str) -> dict[str, Any] | None:
    return next((item for item in items if item["id"] == note_id), None)


def _validate_create(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"title", "content"}:
        return ToolResult.failure("invalid_arguments", "title and content are required.")
    title, content = arguments["title"], arguments["content"]
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE_CHARACTERS:
        return ToolResult.failure("invalid_arguments", "title must be 1 to 200 characters.")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        return ToolResult.failure("invalid_arguments", "content must be at most 64 KiB of UTF-8 text.")
    return None


def _validate_id(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"note_id"} or not _valid_id(arguments.get("note_id")):
        return ToolResult.failure("invalid_arguments", "note_id must be a canonical UUID.")
    return None


def _validate_empty(arguments: dict[str, Any]) -> ToolResult | None:
    if arguments:
        return ToolResult.failure("invalid_arguments", "No arguments are accepted.")
    return None


@dataclass
class CreateNoteTool:
    store: PersonalStore
    id_factory: Callable[[], str] = _new_id
    now_factory: Callable[[], str] = _now
    name: str = "create_note"
    description: str = "Create a persistent note in the workspace."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
        "required": ["title", "content"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_create(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        if _validate_create(arguments) is not None:
            return "Invalid create_note arguments."
        size = len(arguments["content"].encode("utf-8"))
        return f"Create note '{arguments['title'].strip()}' with {size} bytes of UTF-8 text."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_create(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        note = {
            "id": self.id_factory(),
            "title": arguments["title"].strip(),
            "content": arguments["content"],
            "created_at": self.now_factory(),
        }
        items = [*loaded.data, note]
        items.sort(key=lambda item: (item["created_at"], item["id"]))
        saved = self.store.save_notes(items)
        return ToolResult.success(dict(note)) if saved.ok else saved


@dataclass
class ListNotesTool:
    store: PersonalStore
    name: str = "list_notes"
    description: str = "List persistent note summaries from the workspace."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False,
    })
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_empty(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "List notes."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_empty(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        items = sorted(loaded.data, key=lambda item: (item["created_at"], item["id"]))
        return ToolResult.success([
            {key: item[key] for key in ("id", "title", "created_at")} for item in items
        ])


@dataclass
class GetNoteTool:
    store: PersonalStore
    name: str = "get_note"
    description: str = "Get one persistent note by ID."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"note_id": {"type": "string"}},
        "required": ["note_id"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_id(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Get note {arguments.get('note_id', '')}."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        note = _find(loaded.data, arguments["note_id"])
        return ToolResult.success(dict(note)) if note else ToolResult.failure("note_not_found", "Note was not found.")


@dataclass
class DeleteNoteTool:
    store: PersonalStore
    name: str = "delete_note"
    description: str = "Delete one persistent note by ID."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"note_id": {"type": "string"}},
        "required": ["note_id"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        if _find(loaded.data, arguments["note_id"]) is None:
            return ToolResult.failure("note_not_found", "Note was not found.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        loaded = self.store.load_notes()
        note = _find(loaded.data, arguments.get("note_id", "")) if loaded.ok else None
        return f"Delete note {note['id']} titled '{note['title']}'." if note else "Delete note."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_notes()
        if not loaded.ok:
            return loaded
        note = _find(loaded.data, arguments["note_id"])
        if note is None:
            return ToolResult.failure("note_not_found", "Note was not found.")
        saved = self.store.save_notes([item for item in loaded.data if item["id"] != note["id"]])
        return ToolResult.success({"id": note["id"], "deleted": True}) if saved.ok else saved
```

- [ ] **Step 5: Run focused note, store, and Registry tests**

Run: `uv run pytest tests/test_note_tools.py tests/test_personal_store.py tests/test_tool_registry.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit note tools**

```bash
git add src/cdy_agent/tools/notes.py tests/test_note_tools.py
git commit -m "Add persistent note tools"
```

### Task 4: Todo Tools

**Files:**
- Create: `src/cdy_agent/tools/todos.py`
- Create: `tests/test_todo_tools.py`

**Interfaces:**
- Consumes: `PersonalStore`, `ToolResult`, and the Registry lifecycle.
- Produces: `CreateTodoTool`, `ListTodosTool`, `CompleteTodoTool`, and `DeleteTodoTool`.
- Constructors accept one shared `PersonalStore`; create and complete tools accept injectable generators needed for deterministic UUID/UTC tests.

- [ ] **Step 1: Write failing Todo schema and lifecycle tests**

```python
# tests/test_todo_tools.py
from pathlib import Path

from cdy_agent.tools.base import ToolCall
from cdy_agent.tools.personal_store import PersonalStore
from cdy_agent.tools.registry import ToolRegistry
from cdy_agent.tools.todos import CompleteTodoTool, CreateTodoTool, DeleteTodoTool, ListTodosTool


TODO_ID = "00000000-0000-4000-8000-000000000020"
CREATED = "2026-07-18T04:00:00Z"
COMPLETED = "2026-07-18T05:00:00Z"


def build_tools(tmp_path: Path):
    store = PersonalStore(tmp_path)
    return (
        store,
        CreateTodoTool(store, id_factory=lambda: TODO_ID, now_factory=lambda: CREATED),
        ListTodosTool(store),
        CompleteTodoTool(store, now_factory=lambda: COMPLETED),
        DeleteTodoTool(store),
    )


def test_todo_lifecycle(tmp_path: Path) -> None:
    store, create, list_todos, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, list_todos, complete, delete])

    created = registry.execute(ToolCall("1", "create_todo", '{"text":" Write tests "}'), lambda _: True)
    assert created.data == {
        "id": TODO_ID, "text": "Write tests", "completed": False,
        "created_at": CREATED, "completed_at": None,
    }
    assert list_todos.execute({}).data == [created.data]
    finished = registry.execute(ToolCall("2", "complete_todo", f'{{"todo_id":"{TODO_ID}"}}'), lambda _: True)
    assert finished.data["completed"] is True
    assert finished.data["completed_at"] == COMPLETED
    assert registry.execute(ToolCall("3", "delete_todo", f'{{"todo_id":"{TODO_ID}"}}'), lambda _: True).ok
    assert store.load_todos().data == []
```

- [ ] **Step 2: Add failing validation, repeated completion, and confirmation tests**

```python
def test_todo_preflight_rejects_invalid_missing_and_completed_items(tmp_path: Path) -> None:
    _, create, _, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, complete, delete])
    confirmations = []

    assert registry.execute(ToolCall("1", "create_todo", '{"text":" "}'), lambda request: confirmations.append(request) or True).code == "invalid_arguments"
    assert registry.execute(ToolCall("2", "complete_todo", '{"todo_id":"bad"}'), lambda request: confirmations.append(request) or True).code == "invalid_arguments"
    missing = "00000000-0000-4000-8000-000000000099"
    assert registry.execute(ToolCall("3", "delete_todo", f'{{"todo_id":"{missing}"}}'), lambda request: confirmations.append(request) or True).code == "todo_not_found"
    assert confirmations == []

    assert registry.execute(ToolCall("4", "create_todo", '{"text":"x"}'), lambda _: True).ok
    assert registry.execute(ToolCall("5", "complete_todo", f'{{"todo_id":"{TODO_ID}"}}'), lambda _: True).ok
    assert complete.preflight({"todo_id": TODO_ID}).code == "todo_already_completed"


def test_declined_todo_mutations_do_not_create_or_change_store(tmp_path: Path) -> None:
    store, create, _, complete, delete = build_tools(tmp_path)
    registry = ToolRegistry([create, complete, delete])
    assert registry.execute(ToolCall("1", "create_todo", '{"text":"Write tests"}'), lambda _: False).code == "approval_denied"
    assert not (tmp_path / ".cdy-agent").exists()

    assert create.execute({"text": "Write tests"}).ok
    assert registry.execute(ToolCall("2", "complete_todo", f'{{"todo_id":"{TODO_ID}"}}'), lambda _: False).code == "approval_denied"
    assert registry.execute(ToolCall("3", "delete_todo", f'{{"todo_id":"{TODO_ID}"}}'), lambda _: False).code == "approval_denied"
    assert store.load_todos().data[0]["completed"] is False
```

- [ ] **Step 3: Run Todo tests to verify failure**

Run: `uv run pytest tests/test_todo_tools.py -v`

Expected: collection fails because `cdy_agent.tools.todos` does not exist.

- [ ] **Step 4: Implement all four Todo tools**

```python
# src/cdy_agent/tools/todos.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from .base import ToolResult
from .personal_store import PersonalStore


MAX_TODO_CHARACTERS = 1000


def _new_id() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _find(items: list[dict[str, Any]], todo_id: str) -> dict[str, Any] | None:
    return next((item for item in items if item["id"] == todo_id), None)


def _validate_create(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"text"}:
        return ToolResult.failure("invalid_arguments", "text is required.")
    text = arguments["text"]
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TODO_CHARACTERS:
        return ToolResult.failure("invalid_arguments", "text must be 1 to 1000 characters.")
    return None


def _validate_id(arguments: dict[str, Any]) -> ToolResult | None:
    if set(arguments) != {"todo_id"} or not _valid_id(arguments.get("todo_id")):
        return ToolResult.failure("invalid_arguments", "todo_id must be a canonical UUID.")
    return None


def _validate_empty(arguments: dict[str, Any]) -> ToolResult | None:
    if arguments:
        return ToolResult.failure("invalid_arguments", "No arguments are accepted.")
    return None


@dataclass
class CreateTodoTool:
    store: PersonalStore
    id_factory: Callable[[], str] = _new_id
    now_factory: Callable[[], str] = _now
    name: str = "create_todo"
    description: str = "Create a persistent Todo in the workspace."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_create(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return f"Create Todo: {arguments['text'].strip()}." if _validate_create(arguments) is None else "Invalid create_todo arguments."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_create(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = {
            "id": self.id_factory(),
            "text": arguments["text"].strip(),
            "completed": False,
            "created_at": self.now_factory(),
            "completed_at": None,
        }
        items = [*loaded.data, todo]
        items.sort(key=lambda item: (item["created_at"], item["id"]))
        saved = self.store.save_todos(items)
        return ToolResult.success(dict(todo)) if saved.ok else saved


@dataclass
class ListTodosTool:
    store: PersonalStore
    name: str = "list_todos"
    description: str = "List persistent Todos from the workspace."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False,
    })
    requires_confirmation: bool = False

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        return _validate_empty(arguments)

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return "List Todos."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_empty(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        items = sorted(loaded.data, key=lambda item: (item["created_at"], item["id"]))
        return ToolResult.success([dict(item) for item in items])


@dataclass
class CompleteTodoTool:
    store: PersonalStore
    now_factory: Callable[[], str] = _now
    name: str = "complete_todo"
    description: str = "Mark one persistent Todo complete by ID."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"todo_id": {"type": "string"}},
        "required": ["todo_id"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = _find(loaded.data, arguments["todo_id"])
        if todo is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        if todo["completed"]:
            return ToolResult.failure("todo_already_completed", "Todo is already completed.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        loaded = self.store.load_todos()
        todo = _find(loaded.data, arguments.get("todo_id", "")) if loaded.ok else None
        return f"Complete Todo {todo['id']}: {todo['text']}." if todo else "Complete Todo."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = _find(loaded.data, arguments["todo_id"])
        if todo is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        if todo["completed"]:
            return ToolResult.failure("todo_already_completed", "Todo is already completed.")
        todo["completed"] = True
        todo["completed_at"] = self.now_factory()
        saved = self.store.save_todos(loaded.data)
        return ToolResult.success(dict(todo)) if saved.ok else saved


@dataclass
class DeleteTodoTool:
    store: PersonalStore
    name: str = "delete_todo"
    description: str = "Delete one persistent Todo by ID."
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {"todo_id": {"type": "string"}},
        "required": ["todo_id"],
        "additionalProperties": False,
    })
    requires_confirmation: bool = True

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        if _find(loaded.data, arguments["todo_id"]) is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        return None

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        loaded = self.store.load_todos()
        todo = _find(loaded.data, arguments.get("todo_id", "")) if loaded.ok else None
        return f"Delete Todo {todo['id']}: {todo['text']}." if todo else "Delete Todo."

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        invalid = _validate_id(arguments)
        if invalid is not None:
            return invalid
        loaded = self.store.load_todos()
        if not loaded.ok:
            return loaded
        todo = _find(loaded.data, arguments["todo_id"])
        if todo is None:
            return ToolResult.failure("todo_not_found", "Todo was not found.")
        saved = self.store.save_todos([item for item in loaded.data if item["id"] != todo["id"]])
        return ToolResult.success({"id": todo["id"], "deleted": True}) if saved.ok else saved
```

- [ ] **Step 5: Run focused Todo, note, store, and Registry tests**

Run: `uv run pytest tests/test_todo_tools.py tests/test_note_tools.py tests/test_personal_store.py tests/test_tool_registry.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit Todo tools**

```bash
git add src/cdy_agent/tools/todos.py tests/test_todo_tools.py
git commit -m "Add persistent todo tools"
```

### Task 5: Built-in Agent Integration and User-Facing Documentation

**Files:**
- Modify: `src/cdy_agent/tools/__init__.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_cli.py`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md`

**Interfaces:**
- Consumes: all eight tool classes and `PersonalStore`.
- Produces: `create_builtin_registry(workspace)` with one shared store and deterministic tool order.
- Preserves: existing CLI and Agent public interfaces.

- [ ] **Step 1: Update the failing built-in registry expectation**

```python
# replace the expected tuple in tests/test_agent.py
def test_builtin_registry_has_deterministic_order(tmp_path: Path) -> None:
    assert tuple(
        definition["name"]
        for definition in create_builtin_registry(tmp_path).definitions
    ) == (
        "read_file", "write_file", "shell",
        "create_note", "list_notes", "get_note", "delete_note",
        "create_todo", "list_todos", "complete_todo", "delete_todo",
    )
```

- [ ] **Step 2: Run the integration test to verify failure**

Run: `uv run pytest tests/test_agent.py::test_builtin_registry_has_deterministic_order -v`

Expected: FAIL because the registry still exposes only three tools.

- [ ] **Step 3: Register the personal tools using one shared Store**

```python
# src/cdy_agent/tools/__init__.py
from pathlib import Path

from .filesystem import ReadFileTool, WriteFileTool
from .notes import CreateNoteTool, DeleteNoteTool, GetNoteTool, ListNotesTool
from .personal_store import PersonalStore
from .registry import ToolRegistry
from .shell import ShellTool
from .todos import CompleteTodoTool, CreateTodoTool, DeleteTodoTool, ListTodosTool


def create_builtin_registry(workspace: Path) -> ToolRegistry:
    """Create the deterministic registry of built-in local tools."""
    store = PersonalStore(workspace)
    return ToolRegistry([
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        ShellTool(workspace),
        CreateNoteTool(store),
        ListNotesTool(store),
        GetNoteTool(store),
        DeleteNoteTool(store),
        CreateTodoTool(store),
        ListTodosTool(store),
        CompleteTodoTool(store),
        DeleteTodoTool(store),
    ])


__all__ = ["create_builtin_registry"]
```

- [ ] **Step 4: Add a CLI confirmation-description regression**

```python
# append to tests/test_cli.py
def test_personal_tool_confirmation_description_is_shown_once() -> None:
    request = ConfirmationRequest(
        "create_todo",
        {"text": "Write tests"},
        "Create Todo: Write tests.",
    )
    monkey_app = typer.Typer()

    @monkey_app.callback(invoke_without_command=True)
    def invoke() -> None:
        typer.echo("APPROVED" if cli._confirm_tool(request) else "DENIED")

    result = runner.invoke(monkey_app, [], input="y\n")
    assert result.exit_code == 0
    assert result.stdout.count("Create Todo: Write tests.") == 1
    assert result.stdout.endswith("APPROVED\n")
```

- [ ] **Step 5: Ignore personal data and document the feature**

Add this exact pattern to `.gitignore`:

```gitignore
.cdy-agent/
```

Replace README's current-stage paragraph with:

```markdown
项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和进程内多轮会话，两种 API 模式均可通过同一个 Agent Tool Loop 使用受限的本地文件、Shell、笔记和 Todo 工具。笔记与 Todo 按 workspace 持久化；Skills、持久化会话和长期记忆将在后续阶段加入。
```

Append these bullets to README's local-tool list:

```markdown
- `create_note`、`list_notes`、`get_note`、`delete_note`：创建、列出、查看和删除 workspace 笔记。
- `create_todo`、`list_todos`、`complete_todo`、`delete_todo`：创建、列出、完成和删除 workspace Todo。
```

Append this subsection after the existing Shell safety paragraph:

```markdown
### 笔记与 Todo 数据

笔记保存在 `<workspace>/.cdy-agent/notes.json`，Todo 保存在 `<workspace>/.cdy-agent/todos.json`。创建、完成和删除操作每次都需要默认 No 的用户确认；列表和查看不会请求确认，也不会为了空列表创建数据目录。

数据文件使用严格校验的版本化 JSON 和原子替换写入。格式损坏、版本未知或路径越过 workspace 时，工具会拒绝操作，不会用空数据覆盖原文件。同一 workspace 首版只允许一个 `cdy-agent` 进程执行修改。
```

Replace the roadmap's Phase 5 section with:

```markdown
### 5. 笔记与 Todo 工具

本阶段已经交付按 workspace 持久化的笔记与 Todo 工具。笔记支持创建、列表、查看和删除；Todo 支持创建、列表、完成和删除。创建、完成和删除逐次请求用户确认，数据保存在受 workspace 边界保护且默认由 Git 忽略的 `.cdy-agent` 目录中。
```

- [ ] **Step 6: Run integration and CLI regressions**

Run: `uv run pytest tests/test_agent.py tests/test_cli.py tests/test_note_tools.py tests/test_todo_tools.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit integration and documentation**

```bash
git add src/cdy_agent/tools/__init__.py tests/test_agent.py tests/test_cli.py .gitignore README.md docs/superpowers/specs/2026-07-15-personal-agent-learning-roadmap-design.md
git commit -m "Integrate personal assistant tools"
```

### Task 6: Phase 5 Verification and Review Fixes

**Files:**
- Modify only files implicated by verification or review findings.

**Interfaces:**
- Verifies all approved Phase 5 behavior without adding scope.

- [ ] **Step 1: Run the complete offline test suite**

Run: `uv run pytest`

Expected: all tests pass with zero failures, errors, or network access.

- [ ] **Step 2: Verify all CLI help surfaces**

Run: `uv run cdy-agent --help`

Expected: exit code 0 and commands `ask` and `chat` are listed.

Run: `uv run cdy-agent ask --help`

Expected: exit code 0 and `--model` plus `--workspace` are listed.

Run: `uv run cdy-agent chat --help`

Expected: exit code 0 and `--model` plus `--workspace` are listed.

- [ ] **Step 3: Build both distributions**

Run: `UV_CACHE_DIR=/tmp/cdy-agent-phase5-cache uv build`

Expected: exit code 0 with one source archive and one wheel under `dist/`.

- [ ] **Step 4: Check diffs and generated-file hygiene**

Run: `git diff --check`

Expected: no output and exit code 0.

Run: `git status --short`

Expected: only intentional Phase 5 changes or build artifacts ignored by repository policy; no `.cdy-agent/`, caches, secrets, or captured model responses are tracked.

- [ ] **Step 5: Request code review and address only verified findings**

Use `superpowers:requesting-code-review` against the Phase 5 commit range. For every actionable finding, use `superpowers:receiving-code-review`, reproduce or validate it with a focused test, implement the smallest in-scope correction, and rerun the focused plus complete verification commands.

- [ ] **Step 6: Commit review fixes if any**

```bash
git status --short
git add -u -- src tests README.md .gitignore docs
git commit -m "Fix phase five review findings"
```

Confirm `git status --short` contains only verified review fixes before staging. If review produces no changes, skip this step and do not create an empty commit.
