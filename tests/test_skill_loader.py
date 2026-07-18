import os
import shutil
from pathlib import Path

import pytest

from cdy_agent.skills.loader import (
    InvalidSkillError,
    discover_skills,
    revalidate_tools_file,
)


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


def test_skills_root_probe_oserror_is_diagnosed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / ".cdy-agent" / "skills"
    original_lstat = Path.lstat

    def fail_root_probe(path: Path):
        if path == root:
            raise PermissionError("root is unreadable")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_root_probe)

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert len(discovery.diagnostics) == 1
    assert discovery.diagnostics[0].entry == "skills"
    assert discovery.diagnostics[0].code == "invalid_skills_root"
    assert discovery.diagnostics[0].message == "Skills root is invalid."


def test_discovers_sorted_metadata_instructions_and_optional_tools(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path, "zeta", "# Zeta")
    alpha = write_skill(tmp_path, "alpha", "# Alpha")
    (alpha / "tools.py").write_text(
        "def create_tools(workspace): return []\n", encoding="utf-8"
    )

    discovery = discover_skills(tmp_path)

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
    target.write_text(
        "def create_tools(workspace): return []\n", encoding="utf-8"
    )
    os.symlink(target, linked_tools / "tools.py")

    discovery = discover_skills(tmp_path)

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

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert discovery.diagnostics[0].code == "invalid_skills_root"


def test_discovery_does_not_execute_tools_file(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "inert")
    marker = tmp_path / "executed"
    (directory / "tools.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )

    discovery = discover_skills(tmp_path)

    assert [skill.metadata.name for skill in discovery.skills] == ["inert"]
    assert discovery.skills[0].has_tools is True
    assert not marker.exists()


def test_rejects_symlinked_skill_file(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "linked_skill")
    skill_file = directory / "SKILL.md"
    target = tmp_path / "outside-skill.md"
    target.write_bytes(skill_file.read_bytes())
    skill_file.unlink()
    os.symlink(target, skill_file)

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert [item.entry for item in discovery.diagnostics] == ["linked_skill"]


def test_rejects_oversized_tools_file(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "oversized_tools")
    (directory / "tools.py").write_bytes(b"x" * (1024 * 1024 + 1))

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert [item.entry for item in discovery.diagnostics] == ["oversized_tools"]


def test_revalidate_rejects_tools_replaced_with_symlink(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed")
    tools_path = directory / "tools.py"
    tools_path.write_text("", encoding="utf-8")
    skill = discover_skills(tmp_path).skills[0]
    target = tmp_path / "replacement.py"
    target.write_text("", encoding="utf-8")
    tools_path.unlink()
    os.symlink(target, tools_path)

    with pytest.raises(InvalidSkillError, match="symbolic links"):
        revalidate_tools_file(skill, tmp_path)


def test_revalidate_rejects_tools_replaced_with_directory(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed")
    tools_path = directory / "tools.py"
    tools_path.write_text("", encoding="utf-8")
    skill = discover_skills(tmp_path).skills[0]
    tools_path.unlink()
    tools_path.mkdir()

    with pytest.raises(InvalidSkillError, match="regular file"):
        revalidate_tools_file(skill, tmp_path)


def test_revalidate_rejects_tools_that_grew_beyond_limit(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed")
    tools_path = directory / "tools.py"
    tools_path.write_text("", encoding="utf-8")
    skill = discover_skills(tmp_path).skills[0]
    tools_path.write_bytes(b"x" * (1024 * 1024 + 1))

    with pytest.raises(InvalidSkillError, match="exceeds 1 MiB"):
        revalidate_tools_file(skill, tmp_path)


def test_revalidate_rejects_tools_resolving_outside_workspace(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed")
    tools_path = directory / "tools.py"
    tools_path.write_text("", encoding="utf-8")
    skill = discover_skills(tmp_path).skills[0]
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "tools.py").write_text("", encoding="utf-8")
    shutil.rmtree(directory)
    os.symlink(outside, directory, target_is_directory=True)

    try:
        with pytest.raises(InvalidSkillError, match="outside the workspace"):
            revalidate_tools_file(skill, tmp_path)
    finally:
        shutil.rmtree(outside)
