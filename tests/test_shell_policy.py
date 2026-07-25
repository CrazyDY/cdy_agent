from pathlib import Path

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
