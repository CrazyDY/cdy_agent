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
