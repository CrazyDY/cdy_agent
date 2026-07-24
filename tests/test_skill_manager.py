from pathlib import Path

import pytest

from cdy_agent.skills.manager import SkillManager


def test_skill_manager_constructor_accepts_only_workspace(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError):
        SkillManager(tmp_path, object(), object())


def write_skill(tmp_path: Path, name: str = "content-summary") -> Path:
    directory = tmp_path / ".cdy-agent" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: Use {name} for matching tasks.\n"
            "license: Apache-2.0\n"
            "metadata:\n"
            '  version: "1.0"\n'
            "allowed-tools: Read Bash(git:*)\n"
            "---\n\n"
            f"# {name}\n"
        ),
        encoding="utf-8",
    )
    return directory


def test_list_and_search_report_resource_counts(tmp_path: Path) -> None:
    directory = write_skill(tmp_path)
    for relative in (
        "scripts/run.py",
        "references/guide.md",
        "assets/a.txt",
        "assets/b.txt",
    ):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding="utf-8")

    manager = SkillManager(tmp_path)
    listing = manager.list_skills()["skills"][0]
    match = manager.search_skills("matching tasks")["matches"][0]

    expected = {"scripts": 1, "references": 1, "assets": 2}
    assert listing["resource_counts"] == expected
    assert match["resource_counts"] == expected
    assert listing["active"] is False
    assert "has_tools" not in listing


def test_activation_returns_metadata_manifest_and_is_idempotent(
    tmp_path: Path,
) -> None:
    directory = write_skill(tmp_path)
    reference = directory / "references" / "guide.md"
    reference.parent.mkdir()
    reference.write_text("# Guide", encoding="utf-8")
    manager = SkillManager(tmp_path)

    first = manager.activate("content-summary")
    second = manager.activate("content-summary")

    assert first.ok
    assert first.data["status"] == "activated"
    assert second.data["status"] == "already_active"
    assert first.data["metadata"] == {
        "name": "content-summary",
        "description": "Use content-summary for matching tasks.",
        "license": "Apache-2.0",
        "compatibility": None,
        "metadata": {"version": "1.0"},
        "allowed-tools": "Read Bash(git:*)",
    }
    assert first.data["directory"] == str(directory.resolve())
    assert first.data["relative_paths"] == (
        "Resolve resource paths from the Skill directory."
    )
    assert first.data["resources"] == [
        {
            "category": "references",
            "path": "references/guide.md",
            "size": len("# Guide"),
        }
    ]


def test_active_resources_requires_activation_and_filters_categories(
    tmp_path: Path,
) -> None:
    directory = write_skill(tmp_path)
    for relative in ("scripts/run.py", "references/guide.md"):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding="utf-8")
    manager = SkillManager(tmp_path)

    inactive = manager.active_resources("content-summary", ("scripts",))
    assert inactive.code == "skill_not_active"

    manager.activate("content-summary")
    scripts = manager.active_resources("content-summary", ("scripts",))
    assert [item.relative_path for item in scripts] == ["scripts/run.py"]


def test_resolve_active_resource_enforces_activation_and_category(
    tmp_path: Path,
) -> None:
    directory = write_skill(tmp_path)
    reference = directory / "references" / "guide.md"
    reference.parent.mkdir()
    reference.write_text("# Guide", encoding="utf-8")
    manager = SkillManager(tmp_path)

    inactive = manager.resolve_active_resource(
        "content-summary", "references/guide.md", ("references", "assets")
    )
    assert inactive.code == "skill_not_active"

    assert manager.activate("content-summary").ok
    resolved = manager.resolve_active_resource(
        "content-summary", "references/guide.md", ("references", "assets")
    )
    wrong_category = manager.resolve_active_resource(
        "content-summary", "references/guide.md", ("scripts",)
    )
    unknown = manager.resolve_active_resource(
        "content-summary", "../SKILL.md", ("references", "assets")
    )

    assert resolved.relative_path == "references/guide.md"
    assert wrong_category.code == "wrong_resource_category"
    assert unknown.code == "unknown_resource"


def test_resolve_active_resource_revalidates_manifest_entry(
    tmp_path: Path,
) -> None:
    directory = write_skill(tmp_path)
    script = directory / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('ok')", encoding="utf-8")
    manager = SkillManager(tmp_path)
    assert manager.activate("content-summary").ok
    script.unlink()
    script.mkdir()

    result = manager.resolve_active_resource(
        "content-summary", "scripts/run.py", ("scripts",)
    )

    assert result.code == "invalid_resource"


def test_activation_revalidates_skill_before_marking_it_active(
    tmp_path: Path,
) -> None:
    directory = write_skill(tmp_path)
    manager = SkillManager(tmp_path)
    skill_file = directory / "SKILL.md"
    skill_file.unlink()
    skill_file.mkdir()

    result = manager.activate("content-summary")

    assert result.code == "invalid_skill"
    assert manager.list_skills()["skills"][0]["active"] is False


def test_public_methods_preserve_invalid_and_unknown_skill_codes(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / ".cdy-agent" / "skills" / "invalid-skill"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("not metadata\n", encoding="utf-8")
    manager = SkillManager(tmp_path)

    calls = (
        manager.activate,
        lambda name: manager.active_resources(name, ("scripts",)),
        lambda name: manager.resolve_active_resource(
            name, "scripts/run.py", ("scripts",)
        ),
    )

    for call in calls:
        assert call("invalid-skill").code == "invalid_skill"
        assert call("missing-skill").code == "unknown_skill"
