import os
import shutil
import sys
from pathlib import Path

import pytest

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
    write_skill(tmp_path, "writer")
    confirmations = []
    manager = SkillManager(tmp_path, ToolRegistry([]), confirmations.append)

    first = manager.activate("writer")
    second = manager.activate("writer")

    assert first.data == {
        "name": "writer",
        "status": "activated",
        "instructions": "# writer",
        "tools": [],
    }
    assert second.data["status"] == "already_active"
    assert confirmations == []


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


def test_python_skill_requires_one_confirmation_and_registers_tool(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "python_skill", ECHO_TOOL)
    requests = []
    registry = ToolRegistry([])
    manager = SkillManager(
        tmp_path, registry, lambda request: requests.append(request) or True
    )

    assert manager.activate("python_skill").data["tools"] == ["skill_echo"]
    assert manager.activate("python_skill").data["status"] == "already_active"
    assert len(requests) == 1
    assert requests[0].tool_name == "activate_skill"
    assert str(tmp_path / ".cdy-agent/skills/python_skill/tools.py") in requests[0].description
    assert [item["name"] for item in registry.definitions] == ["skill_echo"]


def test_denied_or_broken_python_skill_does_not_mutate_registry(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "denied", ECHO_TOOL)
    denied_registry = ToolRegistry([])
    denied = SkillManager(
        tmp_path, denied_registry, lambda request: False
    ).activate("denied")
    assert denied.code == "approval_denied"
    assert denied_registry.definitions == ()

    write_skill(tmp_path, "broken", "raise RuntimeError('secret detail')\n")
    broken_registry = ToolRegistry([])
    broken = SkillManager(
        tmp_path, broken_registry, lambda request: True
    ).activate("broken")
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


def test_list_skills_and_activation_errors_preserve_public_codes(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "valid")
    invalid = tmp_path / ".cdy-agent" / "skills" / "invalid"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("not metadata\n", encoding="utf-8")
    manager = SkillManager(tmp_path, ToolRegistry([]), lambda request: True)

    listing = manager.list_skills()

    assert listing["skills"] == [
        {
            "name": "valid",
            "description": "Use valid.",
            "has_tools": False,
            "active": False,
        }
    ]
    assert listing["diagnostics"][0]["entry"] == "invalid"
    assert manager.activate("invalid").code == "invalid_skill"
    assert manager.activate("missing").code == "unknown_skill"


def test_tools_are_revalidated_before_requesting_approval(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed", ECHO_TOOL)
    requests = []
    manager = SkillManager(
        tmp_path, ToolRegistry([]), lambda request: requests.append(request) or True
    )
    (directory / "tools.py").unlink()

    result = manager.activate("changed")

    assert result.code == "invalid_skill"
    assert requests == []


def test_removed_workspace_before_activation_is_invalid_without_mutation(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "removed", ECHO_TOOL)
    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, lambda request: True)
    shutil.rmtree(tmp_path)

    result = manager.activate("removed")

    assert result.code == "invalid_skill"
    assert registry.definitions == ()
    assert manager.list_skills()["skills"][0]["active"] is False


def test_tools_are_revalidated_again_after_approval(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed", ECHO_TOOL)
    outside = tmp_path / "outside.py"
    outside.write_text(ECHO_TOOL, encoding="utf-8")

    def approve_and_replace(request):
        (directory / "tools.py").unlink()
        os.symlink(outside, directory / "tools.py")
        return True

    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, approve_and_replace)

    result = manager.activate("changed")

    assert result.code == "invalid_skill"
    assert registry.definitions == ()


def test_removed_workspace_during_approval_is_invalid_without_mutation(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "removed", ECHO_TOOL)

    def approve_and_remove(request):
        shutil.rmtree(tmp_path)
        return True

    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, approve_and_remove)

    result = manager.activate("removed")

    assert result.code == "invalid_skill"
    assert registry.definitions == ()
    assert manager.list_skills()["skills"][0]["active"] is False


def test_failed_activations_use_unique_module_names_and_clean_sys_modules(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "module_names"
    source = f'''
from pathlib import Path
from cdy_agent.tools.base import ToolResult
with Path({str(marker)!r}).open("a", encoding="utf-8") as stream:
    stream.write(__name__ + "\\n")
class Echo:
    name = "existing"
    description = "Echo text."
    parameters = {{"type": "object", "properties": {{}}}}
    requires_confirmation = False
    def preflight(self, arguments): return None
    def confirmation_description(self, arguments): return "Echo."
    def execute(self, arguments): return ToolResult.success(arguments)
def create_tools(workspace): return [Echo()]
'''
    write_skill(tmp_path, "conflicting", source)
    registry = ToolRegistry([SimpleTool("existing")])
    manager = SkillManager(tmp_path, registry, lambda request: True)

    assert manager.activate("conflicting").code == "tool_name_conflict"
    assert manager.activate("conflicting").code == "tool_name_conflict"

    names = marker.read_text(encoding="utf-8").splitlines()
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(name.startswith("_cdy_agent_skill_conflicting_") for name in names)
    assert all(name not in sys.modules for name in names)


def test_factory_failure_is_redacted_and_module_is_cleaned_up(tmp_path: Path) -> None:
    marker = tmp_path / "module_name"
    source = f'''
from pathlib import Path
Path({str(marker)!r}).write_text(__name__, encoding="utf-8")
def create_tools(workspace):
    raise RuntimeError("factory secret")
'''
    write_skill(tmp_path, "broken_factory", source)
    manager = SkillManager(tmp_path, ToolRegistry([]), lambda request: True)

    result = manager.activate("broken_factory")

    module_name = marker.read_text(encoding="utf-8")
    assert result.code == "load_failed"
    assert "factory secret" not in (result.message or "")
    assert module_name not in sys.modules


@pytest.mark.parametrize(
    "source",
    [
        "value = 1\n",
        "create_tools = None\n",
    ],
)
def test_missing_or_noncallable_factory_is_invalid_tools(
    tmp_path: Path, source: str
) -> None:
    write_skill(tmp_path, "bad_factory", source)
    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, lambda request: True)

    result = manager.activate("bad_factory")

    assert result.code == "invalid_tools"
    assert registry.definitions == ()
    assert manager.list_skills()["skills"][0]["active"] is False


@pytest.mark.parametrize(
    "source",
    [
        "def create_tools(workspace): return 42\n",
        '''
def create_tools(workspace):
    def broken():
        yield None
        raise ValueError("materialization secret")
    return broken()
''',
    ],
)
def test_unmaterializable_tool_result_is_invalid_tools(
    tmp_path: Path, source: str
) -> None:
    write_skill(tmp_path, "bad_result", source)
    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, lambda request: True)

    result = manager.activate("bad_result")

    assert result.code == "invalid_tools"
    assert "materialization secret" not in (result.message or "")
    assert registry.definitions == ()
    assert manager.list_skills()["skills"][0]["active"] is False


def test_tool_property_failure_during_validation_is_invalid_tools(
    tmp_path: Path,
) -> None:
    source = '''
class InvalidTool:
    @property
    def name(self):
        raise RuntimeError("validation secret")
def create_tools(workspace): return [InvalidTool()]
'''
    write_skill(tmp_path, "bad_tool", source)
    registry = ToolRegistry([])
    manager = SkillManager(tmp_path, registry, lambda request: True)

    result = manager.activate("bad_tool")

    assert result.code == "invalid_tools"
    assert "validation secret" not in (result.message or "")
    assert registry.definitions == ()
    assert manager.list_skills()["skills"][0]["active"] is False
