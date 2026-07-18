import importlib
import os
from pathlib import Path
from typing import Any, Callable

import pytest


def load_discover_skills() -> Callable[[Path], Any]:
    try:
        module = importlib.import_module("cdy_agent.skills.loader")
    except ModuleNotFoundError as error:
        pytest.fail(str(error), pytrace=False)
    return module.discover_skills


def write_skill(root: Path, name: str, body: str = "# Instructions") -> Path:
    directory = root / ".cdy-agent" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use {name}.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


def test_missing_skills_directory_is_empty_and_not_created(tmp_path: Path) -> None:
    discovery = load_discover_skills()(tmp_path)

    assert discovery.skills == ()
    assert discovery.diagnostics == ()
    assert not (tmp_path / ".cdy-agent").exists()


def test_discovers_sorted_metadata_instructions_and_optional_tools(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "zeta", "# Zeta")
    alpha = write_skill(tmp_path, "alpha", "# Alpha")
    (alpha / "tools.py").write_text(
        "def create_tools(workspace): return []\n", encoding="utf-8"
    )

    discovery = load_discover_skills()(tmp_path)

    assert [item.metadata.name for item in discovery.skills] == ["alpha", "zeta"]
    assert discovery.skills[0].metadata.description == "Use alpha."
    assert discovery.skills[0].instructions == "# Alpha"
    assert discovery.skills[0].tools_path == alpha / "tools.py"
    assert discovery.skills[1].tools_path is None


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

    discovery = load_discover_skills()(tmp_path)

    assert [skill.metadata.name for skill in discovery.skills] == ["valid"]
    assert discovery.diagnostics[0].entry == "sample"
    assert discovery.diagnostics[0].code == "invalid_skill"


def test_symlinked_skill_directory_is_rejected(tmp_path: Path) -> None:
    target = write_skill(tmp_path, "target")
    os.symlink(target, target.parent / "linked", target_is_directory=True)

    discovery = load_discover_skills()(tmp_path)

    assert "linked" in [item.entry for item in discovery.diagnostics]


def test_rejects_oversized_skill_and_symlinked_tools(tmp_path: Path) -> None:
    oversized = write_skill(tmp_path, "oversized")
    (oversized / "SKILL.md").write_bytes(b"x" * (256 * 1024 + 1))
    linked_tools = write_skill(tmp_path, "linked_tools")
    target = tmp_path / "outside.py"
    target.write_text(
        "def create_tools(workspace): return []\n", encoding="utf-8"
    )
    os.symlink(target, linked_tools / "tools.py")

    discovery = load_discover_skills()(tmp_path)

    assert [item.entry for item in discovery.diagnostics] == [
        "linked_tools",
        "oversized",
    ]


def test_rejects_symlinked_skills_root(tmp_path: Path) -> None:
    target = tmp_path / "real-skills"
    target.mkdir()
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    os.symlink(target, data / "skills", target_is_directory=True)

    discovery = load_discover_skills()(tmp_path)

    assert discovery.skills == ()
    assert discovery.diagnostics[0].code == "invalid_skills_root"
