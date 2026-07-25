from pathlib import Path

import pytest

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
        {"argv": ["python", "bad\0argument"]},
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
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "status",
        "--short",
    )
    assert diff.argv == (
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--",
        "file.py",
    )


def test_policy_hardens_git_after_global_options_and_for_bare_git(
    tmp_path: Path,
) -> None:
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    with_directory = policy.prepare({
        "argv": ["git", "-C", ".", "diff", "--", "file.py"],
    })
    with_override = policy.prepare({
        "argv": ["git", "-c", "core.fsmonitor=true", "diff"],
    })
    with_long_options = policy.prepare({
        "argv": [
            "git",
            "--git-dir",
            ".git",
            "--work-tree",
            ".",
            "status",
            "--short",
        ],
    })
    bare = policy.prepare({"argv": ["git"]})

    assert with_directory.argv == (
        "git",
        "-C",
        ".",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--",
        "file.py",
    )
    assert with_override.argv == (
        "git",
        "-c",
        "core.fsmonitor=true",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
    )
    assert with_long_options.argv == (
        "git",
        "--git-dir",
        ".git",
        "--work-tree",
        ".",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "status",
        "--short",
    )
    assert bare.argv == (
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
    )


def test_policy_preserves_diff_safety_named_path_operands(
    tmp_path: Path,
) -> None:
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    prepared = policy.prepare({
        "argv": [
            "git",
            "diff",
            "--no-ext-diff",
            "--stat",
            "--no-textconv",
            "--",
            "--no-ext-diff",
            "--no-textconv",
        ],
    })

    assert prepared.argv == (
        "git",
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "--stat",
        "--no-ext-diff",
        "--no-textconv",
        "--",
        "--no-ext-diff",
        "--no-textconv",
    )


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
