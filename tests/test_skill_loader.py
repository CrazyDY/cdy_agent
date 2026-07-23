import os
import shutil
from pathlib import Path

import pytest

from cdy_agent.skills.loader import (
    MAX_RESOURCES,
    InvalidSkillError,
    discover_skills,
    revalidate_resource,
)


def write_skill(
    root: Path,
    name: str = "sample-skill",
    *,
    frontmatter: str | None = None,
    body: str = "# Instructions",
) -> Path:
    directory = root / ".cdy-agent" / "skills" / name
    directory.mkdir(parents=True)
    metadata = frontmatter or (
        f"name: {name}\n"
        f"description: Use {name} for matching tasks.\n"
    )
    (directory / "SKILL.md").write_text(
        f"---\n{metadata}---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


def test_missing_skills_directory_is_empty_and_not_created(tmp_path: Path) -> None:
    discovery = discover_skills(tmp_path)
    assert discovery.skills == ()
    assert discovery.diagnostics == ()
    assert not (tmp_path / ".cdy-agent").exists()


def test_parses_all_standard_metadata_fields(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "pdf-processing",
        frontmatter=(
            "name: pdf-processing\n"
            "description: Process PDFs when users request document extraction.\n"
            "license: Apache-2.0\n"
            "compatibility: Requires Python 3.10+ and network access\n"
            "metadata:\n"
            "  author: example-org\n"
            '  version: "1.0"\n'
            "allowed-tools: Read Bash(git:*)\n"
        ),
    )

    skill = discover_skills(tmp_path).skills[0]

    assert skill.metadata.name == "pdf-processing"
    assert skill.metadata.license == "Apache-2.0"
    assert skill.metadata.compatibility == (
        "Requires Python 3.10+ and network access"
    )
    assert dict(skill.metadata.metadata) == {
        "author": "example-org",
        "version": "1.0",
    }
    assert skill.metadata.allowed_tools == "Read Bash(git:*)"
    assert skill.instructions == "# Instructions"


@pytest.mark.parametrize(
    ("name", "metadata"),
    [
        ("bad_name", "name: bad_name\ndescription: valid\n"),
        ("-bad", "name: -bad\ndescription: valid\n"),
        ("bad-", "name: bad-\ndescription: valid\n"),
        ("bad--name", "name: bad--name\ndescription: valid\n"),
        ("sample-skill", "name: other-name\ndescription: valid\n"),
        ("sample-skill", "name: sample-skill\ndescription: ''\n"),
        (
            "sample-skill",
            "name: sample-skill\ndescription: valid\nunknown: value\n",
        ),
        (
            "sample-skill",
            "name: sample-skill\ndescription: valid\nmetadata:\n  version: 1\n",
        ),
        (
            "sample-skill",
            "name: sample-skill\ndescription: valid\nallowed-tools: 1\n",
        ),
    ],
)
def test_rejects_nonstandard_frontmatter(
    tmp_path: Path, name: str, metadata: str
) -> None:
    write_skill(tmp_path, name, frontmatter=metadata)
    discovery = discover_skills(tmp_path)
    assert discovery.skills == ()
    assert discovery.diagnostics[0].code == "invalid_skill"


def test_rejects_duplicate_keys_and_empty_body(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        frontmatter=(
            "name: sample-skill\n"
            "name: sample-skill\n"
            "description: valid\n"
        ),
    )
    write_skill(tmp_path, "empty-body", body="   ")
    discovery = discover_skills(tmp_path)
    assert [item.entry for item in discovery.diagnostics] == [
        "empty-body",
        "sample-skill",
    ]


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


def test_symlinked_skill_directory_is_rejected(tmp_path: Path) -> None:
    target = write_skill(tmp_path, "target-skill")
    os.symlink(target, target.parent / "linked-skill", target_is_directory=True)

    discovery = discover_skills(tmp_path)

    assert "linked-skill" in [item.entry for item in discovery.diagnostics]


def test_rejects_symlinked_skills_root(tmp_path: Path) -> None:
    target = tmp_path / "real-skills"
    target.mkdir()
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    os.symlink(target, data / "skills", target_is_directory=True)

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert discovery.diagnostics[0].code == "invalid_skills_root"


def test_rejects_symlinked_skill_file(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "linked-skill")
    skill_file = directory / "SKILL.md"
    target = tmp_path / "outside-skill.md"
    target.write_bytes(skill_file.read_bytes())
    skill_file.unlink()
    os.symlink(target, skill_file)

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert [item.entry for item in discovery.diagnostics] == ["linked-skill"]


def test_rejects_oversized_skill_file(tmp_path: Path) -> None:
    oversized = write_skill(tmp_path, "oversized-skill")
    (oversized / "SKILL.md").write_bytes(b"x" * (256 * 1024 + 1))

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert [item.entry for item in discovery.diagnostics] == ["oversized-skill"]


@pytest.mark.parametrize(
    "content",
    [
        "# no metadata\n",
        "---\nname: Bad-Name\ndescription: bad\n---\nbody\n",
        "---\nname: sample-skill\ndescription:\n---\nbody\n",
        "---\nname: sample-skill\ndescription: ok\nextra: no\n---\nbody\n",
        "---\nname: sample-skill\ndescription: ok\n---\n   \n",
    ],
)
def test_invalid_skill_is_diagnosed_without_hiding_valid_skill(
    tmp_path: Path, content: str
) -> None:
    write_skill(tmp_path, "valid-skill")
    invalid = tmp_path / ".cdy-agent" / "skills" / "sample-skill"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text(content, encoding="utf-8")

    discovery = discover_skills(tmp_path)

    assert [skill.metadata.name for skill in discovery.skills] == ["valid-skill"]
    assert discovery.diagnostics[0].entry == "sample-skill"
    assert discovery.diagnostics[0].code == "invalid_skill"


def test_discovers_only_standard_resources_recursively_in_stable_order(
    tmp_path: Path,
) -> None:
    directory = write_skill(tmp_path)
    files = {
        "scripts/z.py": "print('z')",
        "scripts/nested/a.sh": "exit 0",
        "references/guide.md": "# Guide",
        "assets/template.txt": "template",
        "custom/ignored.txt": "ignored",
        "tools.py": "raise RuntimeError('must stay inert')",
    }
    for relative, content in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    skill = discover_skills(tmp_path).skills[0]

    assert [
        (item.category, item.relative_path, item.size)
        for item in skill.resources
    ] == [
        ("assets", "assets/template.txt", len("template")),
        ("references", "references/guide.md", len("# Guide")),
        ("scripts", "scripts/nested/a.sh", len("exit 0")),
        ("scripts", "scripts/z.py", len("print('z')")),
    ]


def test_rejects_too_many_resources(tmp_path: Path) -> None:
    directory = write_skill(tmp_path)
    scripts = directory / "scripts"
    scripts.mkdir()
    for index in range(MAX_RESOURCES + 1):
        (scripts / f"{index:04}.py").write_text("", encoding="utf-8")

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert "more than 512 resources" in discovery.diagnostics[0].message


@pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="symbolic links are unavailable"
)
def test_rejects_symlinks_inside_standard_resource_trees(tmp_path: Path) -> None:
    directory = write_skill(tmp_path)
    references = directory / "references"
    references.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, references / "linked.md")

    discovery = discover_skills(tmp_path)

    assert discovery.skills == ()
    assert "symbolic links" in discovery.diagnostics[0].message


def test_revalidate_rejects_removed_or_replaced_resource(tmp_path: Path) -> None:
    directory = write_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('ok')", encoding="utf-8")
    skill = discover_skills(tmp_path).skills[0]
    resource = skill.resources[0]
    script.unlink()
    script.mkdir()

    with pytest.raises(InvalidSkillError, match="regular file"):
        revalidate_resource(skill, resource, tmp_path)


def test_revalidate_rejects_workspace_replacement(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "changed-skill")
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('ok')", encoding="utf-8")
    skill = discover_skills(tmp_path).skills[0]
    resource = skill.resources[0]
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_script = outside / "scripts" / "run.py"
    outside_script.parent.mkdir()
    outside_script.write_text("print('outside')", encoding="utf-8")
    shutil.rmtree(directory)
    os.symlink(outside, directory, target_is_directory=True)

    try:
        with pytest.raises(InvalidSkillError, match="symbolic links"):
            revalidate_resource(skill, resource, tmp_path)
    finally:
        shutil.rmtree(outside)
