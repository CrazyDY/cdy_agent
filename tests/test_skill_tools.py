from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import cdy_agent.skills as skills_package
import cdy_agent.skills.loader as skill_loader
import pytest
from cdy_agent.skills import SkillManager
from cdy_agent.skills.tools import (
    ActivateSkillTool,
    ListSkillsTool,
    ReadSkillResourceTool,
    RunSkillScriptTool,
    SearchSkillsTool,
    create_skill_tools,
)
from cdy_agent.skills.models import SkillResource
from cdy_agent.tools.base import ToolCall, ToolResult
from cdy_agent.tools.process import MAX_OUTPUT_BYTES
from cdy_agent.tools.registry import ToolRegistry


class FakeManager:
    workspace = Path("/workspace")

    def list_skills(self) -> dict[str, list[object]]:
        return {"skills": [], "diagnostics": []}

    def search_skills(self, query: str, limit: int) -> dict[str, object]:
        return {"query": query, "limit": limit, "matches": []}

    def activate(self, name: str) -> ToolResult:
        return ToolResult.success({"name": name})

    def resolve_active_resource(
        self,
        name: str,
        path: str,
        categories: tuple[str, ...],
    ) -> SkillResource | ToolResult:
        return ToolResult.failure("unknown_resource", "Unknown resource.")

    def active_resources(
        self,
        name: str,
        categories: tuple[str, ...],
    ) -> tuple[SkillResource, ...] | ToolResult:
        return ToolResult.failure("unknown_resource", "Unknown resource.")


def write_runtime_skill(tmp_path: Path) -> tuple[SkillManager, Path]:
    directory = tmp_path / ".cdy-agent" / "skills" / "runtime-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        (
            "---\n"
            "name: runtime-skill\n"
            "description: Run and read test resources.\n"
            "---\n\n"
            "# Runtime\n"
        ),
        encoding="utf-8",
    )
    return SkillManager(tmp_path), directory


def test_list_skills_accepts_only_empty_arguments() -> None:
    tool = ListSkillsTool(FakeManager())

    assert tool.execute({}).data == {"skills": [], "diagnostics": []}
    assert tool.preflight({"extra": True}).code == "invalid_arguments"
    assert tool.requires_confirmation is False


def test_activate_skill_requires_exactly_one_valid_name() -> None:
    tool = ActivateSkillTool(FakeManager())

    assert tool.execute({"name": "research-skill"}).data == {
        "name": "research-skill"
    }
    assert tool.preflight({}).code == "invalid_arguments"
    assert tool.preflight({"name": 1}).code == "invalid_arguments"
    assert tool.preflight({"name": "research-skill", "extra": True}).code == (
        "invalid_arguments"
    )
    assert tool.requires_confirmation is False


def test_search_skills_requires_query_and_accepts_optional_limit() -> None:
    tool = SearchSkillsTool(FakeManager())

    assert tool.execute({"query": "durable notes"}).data == {
        "query": "durable notes",
        "limit": 5,
        "matches": [],
    }
    assert tool.execute({"query": "durable notes", "limit": 3}).data == {
        "query": "durable notes",
        "limit": 3,
        "matches": [],
    }
    assert tool.preflight({}).code == "invalid_arguments"
    assert tool.preflight({"query": ""}).code == "invalid_arguments"
    assert tool.preflight({"query": "x", "limit": 0}).code == "invalid_arguments"
    assert tool.preflight({"query": "x", "limit": 11}).code == "invalid_arguments"
    assert tool.preflight({"query": "x", "extra": True}).code == (
        "invalid_arguments"
    )
    assert tool.requires_confirmation is False


def test_management_tools_expose_exact_schemas() -> None:
    manager = FakeManager()

    assert ListSkillsTool(manager).parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert ActivateSkillTool(manager).parameters == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    assert SearchSkillsTool(manager).parameters == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def test_management_tool_factory_has_stable_order_and_types() -> None:
    manager = FakeManager()

    tools = create_skill_tools(manager)

    assert tuple(type(tool) for tool in tools) == (
        ListSkillsTool,
        SearchSkillsTool,
        ActivateSkillTool,
        ReadSkillResourceTool,
        RunSkillScriptTool,
    )
    assert [tool.name for tool in tools] == [
        "list_skills",
        "search_skills",
        "activate_skill",
        "read_skill_resource",
        "run_skill_script",
    ]
    assert all(tool.manager is manager for tool in tools)


def test_skill_tool_factory_has_stable_five_tool_order() -> None:
    tools = create_skill_tools(FakeManager())

    assert [tool.name for tool in tools] == [
        "list_skills",
        "search_skills",
        "activate_skill",
        "read_skill_resource",
        "run_skill_script",
    ]
    assert [tool.requires_confirmation for tool in tools] == [
        False,
        False,
        False,
        False,
        True,
    ]


def test_resource_and_script_tools_expose_closed_schemas() -> None:
    manager = FakeManager()
    assert ReadSkillResourceTool(manager).parameters == {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["name", "path"],
        "additionalProperties": False,
    }
    assert RunSkillScriptTool(manager).parameters == {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "argv": {"type": "array", "items": {"type": "string"}},
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 300,
            },
        },
        "required": ["name", "argv"],
        "additionalProperties": False,
    }


def test_read_skill_resource_returns_text_and_binary_metadata(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    reference = directory / "references" / "guide.md"
    asset = directory / "assets" / "image.bin"
    reference.parent.mkdir()
    asset.parent.mkdir()
    reference.write_text("# Guide", encoding="utf-8")
    asset.write_bytes(b"\xff\xfe")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    tool = ReadSkillResourceTool(manager)

    text = tool.execute(
        {"name": "runtime-skill", "path": "references/guide.md"}
    )
    binary = tool.execute(
        {"name": "runtime-skill", "path": "assets/image.bin"}
    )

    assert text.data == {
        "path": str(reference.resolve()),
        "relative_path": "references/guide.md",
        "size": 7,
        "binary": False,
        "content": "# Guide",
    }
    assert binary.data == {
        "path": str(asset.resolve()),
        "relative_path": "assets/image.bin",
        "size": 2,
        "binary": True,
    }


def test_read_skill_resource_rejects_content_larger_than_one_mibibyte(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    reference = directory / "references" / "large.txt"
    reference.parent.mkdir()
    reference.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    result = ReadSkillResourceTool(manager).execute(
        {"name": "runtime-skill", "path": "references/large.txt"}
    )

    assert not result.ok
    assert result.code == "resource_too_large"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"name": "runtime-skill"},
        {"name": "runtime-skill", "path": ""},
        {"name": "runtime_skill", "path": "references/guide.md"},
        {
            "name": "runtime-skill",
            "path": "references/guide.md",
            "extra": True,
        },
    ],
)
def test_read_skill_resource_rejects_invalid_arguments(
    arguments: dict[str, object],
) -> None:
    result = ReadSkillResourceTool(FakeManager()).preflight(arguments)

    assert result is not None
    assert result.code == "invalid_arguments"


def test_read_skill_resource_requires_an_active_skill(tmp_path: Path) -> None:
    _, directory = write_runtime_skill(tmp_path)
    reference = directory / "references" / "guide.md"
    reference.parent.mkdir()
    reference.write_text("# Guide", encoding="utf-8")
    manager = SkillManager(tmp_path)

    result = ReadSkillResourceTool(manager).preflight(
        {"name": "runtime-skill", "path": "references/guide.md"}
    )

    assert result is not None
    assert result.code == "skill_not_active"


def test_read_skill_resource_rejects_script_category(tmp_path: Path) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('ok')", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    result = ReadSkillResourceTool(manager).preflight(
        {"name": "runtime-skill", "path": "scripts/run.py"}
    )

    assert result is not None
    assert result.code == "wrong_resource_category"


def test_read_skill_resource_preserves_manager_failure_identity() -> None:
    failure = ToolResult.failure("invalid_resource", "Resource changed.")

    class FailingManager(FakeManager):
        def resolve_active_resource(
            self,
            name: str,
            path: str,
            categories: tuple[str, ...],
        ) -> SkillResource | ToolResult:
            return failure

    result = ReadSkillResourceTool(FailingManager()).preflight(
        {"name": "runtime-skill", "path": "references/guide.md"}
    )

    assert result is failure


def test_run_skill_script_resolves_one_script_and_never_uses_shell(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('ok')", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    tool = RunSkillScriptTool(manager, runner=runner)
    result = tool.execute(
        {
            "name": "runtime-skill",
            "argv": ["uv", "run", "scripts/run.py", "--value", "x|y"],
            "timeout_seconds": 45,
        }
    )

    assert result.ok
    assert calls == [
        (
            [
                "uv",
                "run",
                str(script.resolve()),
                "--value",
                "x|y",
            ],
            {
                "cwd": directory.resolve(),
                "shell": False,
                "capture_output": True,
                "text": True,
                "env": calls[0][1]["env"],
                "timeout": 45,
                "check": False,
            },
        )
    ]


def test_run_skill_script_accepts_direct_executable_form(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.exe"
    script.parent.mkdir()
    script.write_bytes(b"test executable")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    seen: list[list[str]] = []

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {"name": "runtime-skill", "argv": ["scripts/run.exe", "--flag"]}
    )

    assert result.ok
    assert seen == [[str(script.resolve()), "--flag"]]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["python", "-c", "print('no script')"],
        ["python", "scripts/a.py", "scripts/b.py"],
        ["python", 1],
    ],
)
def test_run_skill_script_rejects_invalid_script_commands(
    tmp_path: Path, argv: list[object]
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    scripts = directory / "scripts"
    scripts.mkdir()
    (scripts / "a.py").write_text("", encoding="utf-8")
    (scripts / "b.py").write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    result = RunSkillScriptTool(manager).preflight(
        {"name": "runtime-skill", "argv": argv}
    )

    assert result is not None
    assert result.code in {"invalid_arguments", "invalid_script_command"}


def test_run_skill_script_rejects_nul_in_argv(tmp_path: Path) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    result = RunSkillScriptTool(manager).preflight(
        {
            "name": "runtime-skill",
            "argv": ["python", "scripts/run.py", "bad\0argument"],
        }
    )

    assert result is not None
    assert result.code == "invalid_arguments"


@pytest.mark.parametrize("timeout", [1, 300])
def test_run_skill_script_accepts_timeout_range(
    tmp_path: Path, timeout: int
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    seen: list[int] = []

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs["timeout"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {
            "name": "runtime-skill",
            "argv": ["python", "scripts/run.py"],
            "timeout_seconds": timeout,
        }
    )

    assert result.ok
    assert seen == [timeout]


def test_run_skill_script_defaults_timeout_to_thirty_seconds(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    seen: list[object] = []

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {"name": "runtime-skill", "argv": ["python", "scripts/run.py"]}
    )

    assert result.ok
    assert seen == [30]


@pytest.mark.parametrize("timeout", [0, 301, True, 1.5, "30"])
def test_run_skill_script_rejects_invalid_timeout(
    tmp_path: Path, timeout: object
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    result = RunSkillScriptTool(manager).preflight(
        {
            "name": "runtime-skill",
            "argv": ["python", "scripts/run.py"],
            "timeout_seconds": timeout,
        }
    )

    assert result is not None
    assert result.code == "invalid_arguments"


def test_run_skill_script_maps_missing_executable_to_execution_error(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
        raise FileNotFoundError("missing runtime")

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {"name": "runtime-skill", "argv": ["missing", "scripts/run.py"]}
    )

    assert not result.ok
    assert result.code == "execution_error"
    assert "Traceback" not in (result.message or "")


def test_run_skill_script_maps_value_error_to_execution_error(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
        raise ValueError("invalid process arguments")

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {"name": "runtime-skill", "argv": ["python", "scripts/run.py"]}
    )

    assert not result.ok
    assert result.code == "execution_error"
    assert "Traceback" not in (result.message or "")


def test_run_skill_script_maps_timeout(tmp_path: Path) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {
            "name": "runtime-skill",
            "argv": ["python", "scripts/run.py"],
            "timeout_seconds": 7,
        }
    )

    assert not result.ok
    assert result.code == "script_timeout"
    assert result.message == "Script timed out after 7 seconds."


def test_run_skill_script_caps_stdout_and_stderr_independently(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    stdout = "a" * MAX_OUTPUT_BYTES
    stderr = "你" * MAX_OUTPUT_BYTES

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {"name": "runtime-skill", "argv": ["python", "scripts/run.py"]}
    )

    assert result.ok
    assert result.data["stdout"] == stdout
    assert len(result.data["stderr"].encode("utf-8")) <= MAX_OUTPUT_BYTES
    assert result.data["stdout_truncated"] is False
    assert result.data["stderr_truncated"] is True


def test_default_script_runner_safely_decodes_binary_output(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "binary.py"
    script.parent.mkdir()
    script.write_text(
        "import os\nos.write(1, b'valid\\xffoutput')\n",
        encoding="utf-8",
    )
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    result = RunSkillScriptTool(manager).execute(
        {
            "name": "runtime-skill",
            "argv": [sys.executable, "scripts/binary.py"],
        }
    )

    assert result.ok
    assert result.data["stdout"] == "valid\ufffdoutput"
    assert result.data["stdout_truncated"] is False


def test_default_script_runner_uses_bounded_retention_for_noisy_process(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "noisy.py"
    script.parent.mkdir()
    script.write_text(
        (
            "import os\n"
            f"os.write(1, b'x' * {MAX_OUTPUT_BYTES * 4})\n"
            f"os.write(2, b'y' * {MAX_OUTPUT_BYTES * 4})\n"
        ),
        encoding="utf-8",
    )
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    tool = RunSkillScriptTool(manager)

    result = tool.execute(
        {
            "name": "runtime-skill",
            "argv": [sys.executable, "scripts/noisy.py"],
        }
    )

    assert tool.runner is not subprocess.run
    assert result.ok
    assert len(result.data["stdout"].encode("utf-8")) <= MAX_OUTPUT_BYTES
    assert len(result.data["stderr"].encode("utf-8")) <= MAX_OUTPUT_BYTES
    assert result.data["stdout_truncated"] is True
    assert result.data["stderr_truncated"] is True


def test_run_skill_script_returns_structured_nonzero_failure(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 9, "out", "err")

    result = RunSkillScriptTool(manager, runner=runner).execute(
        {"name": "runtime-skill", "argv": ["python", "scripts/run.py"]}
    )

    assert not result.ok
    assert result.code == "script_failed"
    assert result.message == "Script exited with return code 9."
    assert result.data == {
        "returncode": 9,
        "stdout": "out",
        "stderr": "err",
        "stdout_truncated": False,
        "stderr_truncated": False,
    }


def test_script_confirmation_describes_resolved_execution(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    arguments = {
        "name": "runtime-skill",
        "argv": ["python", "scripts/run.py", "--flag"],
    }

    description = RunSkillScriptTool(manager).confirmation_description(
        arguments
    )

    assert "runtime-skill" in description
    assert str(script.resolve()) in description
    assert repr(["python", str(script.resolve()), "--flag"]) in description
    assert str(directory.resolve()) in description
    assert "current user permissions" in description


def test_run_skill_script_registry_denial_does_not_call_runner(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    called = False

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    tool = RunSkillScriptTool(manager, runner=runner)
    registry = ToolRegistry([tool])
    result = registry.execute(
        ToolCall(
            "run-1",
            "run_skill_script",
            '{"name":"runtime-skill","argv":["python","scripts/run.py"]}',
        ),
        lambda request: False,
    )

    assert not result.ok
    assert result.code == "approval_denied"
    assert called is False
    assert tool._approval_digest is None


def test_run_skill_script_requires_approval_every_time_despite_allowed_tools(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    (directory / "SKILL.md").write_text(
        (
            "---\n"
            "name: runtime-skill\n"
            "description: Run and read test resources.\n"
            "allowed-tools: run_skill_script\n"
            "---\n\n"
            "# Runtime\n"
        ),
        encoding="utf-8",
    )
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    approvals: list[str] = []
    runs = 0

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal runs
        runs += 1
        return subprocess.CompletedProcess(argv, 0, "", "")

    def confirm(request: object) -> bool:
        approvals.append(request.description)  # type: ignore[attr-defined]
        return True

    tool = RunSkillScriptTool(manager, runner=runner)
    registry = ToolRegistry([tool])
    call = ToolCall(
        "run-1",
        "run_skill_script",
        '{"name":"runtime-skill","argv":["python","scripts/run.py"]}',
    )

    first = registry.execute(call, confirm)
    second = registry.execute(call, confirm)

    assert first.ok and second.ok
    assert runs == 2
    assert len(approvals) == 2
    assert tool._approval_digest is None


def test_run_skill_script_next_preflight_clears_approval_digest(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('approved')", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    arguments = {
        "name": "runtime-skill",
        "argv": ["python", "scripts/run.py"],
    }
    tool = RunSkillScriptTool(manager)

    tool.confirmation_description(arguments)
    assert tool._approval_digest is not None

    assert tool.preflight(arguments) is None
    assert tool._approval_digest is None


def test_run_skill_script_revalidates_after_confirmation(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    called = False

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def replace_script(request: object) -> bool:
        script.unlink()
        script.mkdir()
        return True

    registry = ToolRegistry([RunSkillScriptTool(manager, runner=runner)])
    result = registry.execute(
        ToolCall(
            "run-1",
            "run_skill_script",
            '{"name":"runtime-skill","argv":["python","scripts/run.py"]}',
        ),
        replace_script,
    )

    assert not result.ok
    assert result.code == "invalid_resource"
    assert called is False


def test_run_skill_script_rejects_regular_file_replacement_after_confirmation(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('approved')", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    called = False

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def replace_script(request: object) -> bool:
        script.unlink()
        script.write_text("print('replacement')", encoding="utf-8")
        return True

    registry = ToolRegistry([RunSkillScriptTool(manager, runner=runner)])
    result = registry.execute(
        ToolCall(
            "run-1",
            "run_skill_script",
            '{"name":"runtime-skill","argv":["python","scripts/run.py"]}',
        ),
        replace_script,
    )

    assert not result.ok
    assert result.code == "invalid_resource"
    assert called is False


def test_run_skill_script_rejects_same_size_timestamp_restored_rewrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('approved')", encoding="utf-8")
    original_stat = script.stat()
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    resource = manager._skills["runtime-skill"].resources[0]
    called = False

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def rewrite_script(request: object) -> bool:
        script.write_text("print('modified')", encoding="utf-8")
        os.utime(
            script,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        monkeypatch.setattr(
            skill_loader,
            "_resource_identity",
            lambda path: resource._identity,
        )
        return True

    tool = RunSkillScriptTool(manager, runner=runner)
    result = ToolRegistry([tool]).execute(
        ToolCall(
            "run-1",
            "run_skill_script",
            '{"name":"runtime-skill","argv":["python","scripts/run.py"]}',
        ),
        rewrite_script,
    )

    assert not result.ok
    assert result.code == "invalid_resource"
    assert called is False
    assert tool._approval_digest is None


@pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="symbolic links are unavailable"
)
def test_run_skill_script_rejects_nested_directory_symlink_swap(
    tmp_path: Path,
) -> None:
    _, directory = write_runtime_skill(tmp_path)
    nested = directory / "scripts" / "nested"
    nested.mkdir(parents=True)
    script = nested / "run.py"
    script.write_text("print('approved')", encoding="utf-8")
    manager = SkillManager(tmp_path)
    manager.activate("runtime-skill")
    called = False

    def runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def swap_nested_directory(request: object) -> bool:
        renamed = nested.with_name("renamed")
        nested.rename(renamed)
        os.symlink(renamed, nested, target_is_directory=True)
        return True

    result = ToolRegistry(
        [RunSkillScriptTool(manager, runner=runner)]
    ).execute(
        ToolCall(
            "run-1",
            "run_skill_script",
            (
                '{"name":"runtime-skill",'
                '"argv":["python","scripts/nested/run.py"]}'
            ),
        ),
        swap_nested_directory,
    )

    assert not result.ok
    assert result.code == "invalid_resource"
    assert called is False


def test_run_skill_script_preserves_active_resource_failure_identity() -> None:
    failure = ToolResult.failure("skill_not_active", "Skill is not active.")

    class FailingManager(FakeManager):
        def active_resources(
            self,
            name: str,
            categories: tuple[str, ...],
        ) -> tuple[SkillResource, ...] | ToolResult:
            return failure

    result = RunSkillScriptTool(FailingManager()).preflight(
        {"name": "runtime-skill", "argv": ["python", "scripts/run.py"]}
    )

    assert result is failure


def test_skills_package_exports_only_public_management_api() -> None:
    assert skills_package.__all__ == ["SkillManager", "create_skill_tools"]


def test_activate_tool_preserves_manager_failure_identity() -> None:
    failure = ToolResult.failure("invalid_skill", "Skill changed.")

    class FailingManager(FakeManager):
        def activate(self, name: str) -> ToolResult:
            return failure

    result = ActivateSkillTool(FailingManager()).execute(
        {"name": "research-skill"}
    )

    assert result is failure
