import json
from pathlib import Path

import pytest

from cdy_agent.tools.shell_approvals import ShellApprovalStore


def test_missing_approval_store_is_empty_without_writing(
    tmp_path: Path,
) -> None:
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


def test_invalid_approval_documents_fail_closed(tmp_path: Path) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    target = data / "shell-approvals.json"

    for content in (
        "{",
        '{"version":2,"allowed_commands":[]}',
        '{"version":true,"allowed_commands":[]}',
        '{"version":1.0,"allowed_commands":[]}',
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


@pytest.mark.parametrize(
    "content",
    [
        '{"version":2,"version":1,"allowed_commands":[]}',
        (
            '{"version":1,"allowed_commands":[],'
            '"allowed_commands":[["uv"]]}'
        ),
    ],
)
def test_duplicate_json_keys_fail_closed(
    tmp_path: Path,
    content: str,
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    (data / "shell-approvals.json").write_text(
        content,
        encoding="utf-8",
    )

    result = ShellApprovalStore(tmp_path).contains(["uv"])

    assert result.code == "invalid_approval_store"


def test_add_normalizes_duplicate_commands_in_existing_store(
    tmp_path: Path,
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    target = data / "shell-approvals.json"
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "allowed_commands": [
                    ["uv", "run", "pytest"],
                    ["uv", "run", "pytest"],
                ],
            }
        ),
        encoding="utf-8",
    )

    result = ShellApprovalStore(tmp_path).add(["python", "script.py"])

    assert result.ok
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "version": 1,
        "allowed_commands": [
            ["uv", "run", "pytest"],
            ["python", "script.py"],
        ],
    }


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


def test_symlinked_store_directory_inside_workspace_is_rejected(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual-state"
    actual.mkdir()
    data = tmp_path / ".cdy-agent"
    try:
        data.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    result = ShellApprovalStore(tmp_path).add(["uv"])

    assert result.code == "approval_store_error"
    assert not (actual / "shell-approvals.json").exists()


def test_symlinked_approval_file_inside_workspace_is_rejected(
    tmp_path: Path,
) -> None:
    data = tmp_path / ".cdy-agent"
    data.mkdir()
    actual = tmp_path / "other-state.json"
    actual.write_text(
        '{"version":1,"allowed_commands":[["uv"]]}',
        encoding="utf-8",
    )
    target = data / "shell-approvals.json"
    try:
        target.symlink_to(actual)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    result = ShellApprovalStore(tmp_path).contains(["uv"])

    assert result.code == "approval_store_error"


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


def test_post_replace_target_swap_fails_closed(tmp_path: Path) -> None:
    store = ShellApprovalStore(tmp_path)
    assert store.add(["first"]).ok
    data = tmp_path / ".cdy-agent"
    target = data / "shell-approvals.json"
    outside = tmp_path / "outside.json"
    outside.write_text(
        '{"version":1,"allowed_commands":[["outside"]]}',
        encoding="utf-8",
    )
    probe = tmp_path / "probe-link"
    try:
        probe.symlink_to(outside)
        probe.unlink()
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    def swap_after_replace(source: object, destination: object) -> None:
        import os

        os.replace(source, destination)
        Path(destination).unlink()
        Path(destination).symlink_to(outside)

    racing = ShellApprovalStore(tmp_path, replace=swap_after_replace)

    result = racing.add(["second"])

    assert result.code == "approval_store_error"
    assert target.is_symlink()


def test_post_replace_directory_identity_change_fails_closed(
    tmp_path: Path,
) -> None:
    store = ShellApprovalStore(tmp_path)
    assert store.add(["first"]).ok
    data = tmp_path / ".cdy-agent"
    moved = tmp_path / "moved-state"

    def replace_and_swap_directory(
        source: object,
        destination: object,
    ) -> None:
        import os

        os.replace(source, destination)
        data.rename(moved)
        data.mkdir()
        (data / "shell-approvals.json").write_text(
            '{"version":1,"allowed_commands":[["replacement"]]}',
            encoding="utf-8",
        )

    racing = ShellApprovalStore(
        tmp_path,
        replace=replace_and_swap_directory,
    )

    result = racing.add(["second"])

    assert result.code == "approval_store_error"
