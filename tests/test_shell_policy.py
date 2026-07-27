import os
import sys
from pathlib import Path

import pytest

from cdy_agent.tools.shell_approvals import ShellApprovalStore
from cdy_agent.tools.shell_policy import (
    ShellExecutionDecision,
    ShellExecutionPolicy,
)

BUILTIN_COMMANDS = frozenset(
    {
        "pwd",
        "ls",
        "rg",
        "grep",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "git",
    }
)


def _bind_executables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *commands: str,
    inside_workspace: bool = False,
) -> dict[str, Path]:
    directory = (
        tmp_path / "bin"
        if inside_workspace
        else tmp_path.parent / f"{tmp_path.name}-bin"
    )
    directory.mkdir(exist_ok=True)
    resolved: dict[str, Path] = {}
    for command in commands:
        suffix = ".exe" if os.name == "nt" else ""
        executable = directory / f"{command}{suffix}"
        if os.name == "nt":
            executable.write_bytes(b"MZ\x00\x00")
        elif sys.platform == "darwin":
            executable.write_bytes(b"\xcf\xfa\xed\xfe")
        else:
            executable.write_bytes(b"\x7fELF")
        executable.chmod(0o755)
        resolved[command] = executable.resolve()
    monkeypatch.setenv("PATH", str(directory))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".EXE")
    return resolved


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


def test_policy_builds_final_rg_and_git_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executables = _bind_executables(monkeypatch, tmp_path, "rg", "git")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    rg = policy.prepare({"argv": ["rg", "needle", "."]})
    status = policy.prepare({"argv": ["git", "status", "--short"]})
    diff = policy.prepare({"argv": ["git", "diff", "--", "file.py"]})

    assert rg.argv == (str(executables["rg"]), "--no-config", "needle", ".")
    assert status.argv == (
        str(executables["git"]),
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "status",
        "--short",
    )
    assert diff.argv == (
        str(executables["git"]),
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _bind_executables(monkeypatch, tmp_path, "git")["git"]
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    with_directory = policy.prepare(
        {
            "argv": ["git", "-C", ".", "diff", "--", "file.py"],
        }
    )
    with_override = policy.prepare(
        {
            "argv": ["git", "-c", "core.fsmonitor=true", "diff"],
        }
    )
    with_long_options = policy.prepare(
        {
            "argv": [
                "git",
                "--git-dir",
                ".git",
                "--work-tree",
                ".",
                "status",
                "--short",
            ],
        }
    )
    bare = policy.prepare({"argv": ["git"]})

    assert with_directory.argv == (
        str(git),
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
        str(git),
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
        str(git),
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
        str(git),
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
    )


def test_policy_preserves_diff_safety_named_path_operands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _bind_executables(monkeypatch, tmp_path, "git")["git"]
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    prepared = policy.prepare(
        {
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
        }
    )

    assert prepared.argv == (
        str(git),
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
        ["ls", "-la", "src"],
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
        ["git", "diff", "-U3", "--", "README.md"],
        ["git", "diff", "--unified=3", "--", "README.md"],
    ],
)
def test_safe_workspace_read_commands_auto_approve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    executable = _bind_executables(monkeypatch, tmp_path, argv[0])[argv[0]]
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("needle\n", encoding="utf-8")
    if argv[0] == "git":
        (tmp_path / ".git").mkdir()
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(executable.parent,),
        git_repository_probe=lambda executable, workspace, environment: (
            workspace / ".git",
            workspace / ".git",
            workspace,
        ),
    )

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
        ["sort", "README.md", "-oout.txt"],
        ["uniq", "README.md", "out.txt"],
        ["wc", "--files0-from=list.txt"],
        ["git", "log"],
        ["git", "diff", "--output=out.patch"],
        ["git", "diff", "--ext-diff"],
        ["git", "diff", "--textconv"],
        [
            "git",
            "diff",
            "--unified",
            "--no-index",
            "-U",
            "..",
            "README.md",
        ],
        ["git", "diff", "--unified=invalid", "--", "README.md"],
        ["ls", "--unknown-option"],
        ["grep", "Project", "README.md", "-e", "../outside"],
        ["sed", "-n", "1p", "README.md"],
        ["find", ".", "-exec", "id", ";"],
    ],
)
def test_unproven_or_mutating_commands_require_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    if argv[0] in BUILTIN_COMMANDS:
        _bind_executables(monkeypatch, tmp_path, argv[0])
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    executable = _bind_executables(monkeypatch, tmp_path, argv[0])[argv[0]]
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(executable.parent,),
        git_repository_probe=lambda executable, workspace, environment: (
            workspace / ".git",
            workspace / ".git",
            workspace,
        ),
    )

    assert (
        policy.classify({"argv": argv}).decision
        is ShellExecutionDecision.REQUIRE_CONFIRMATION
    )


def test_symlink_to_external_input_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _bind_executables(monkeypatch, tmp_path, "head")["head"]
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(head.parent,),
    )

    assert (
        policy.classify({"argv": ["head", "linked.txt"]}).decision
        is ShellExecutionDecision.REQUIRE_CONFIRMATION
    )


def test_unavailable_builtin_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
    assert result.command is not None
    assert result.command.argv[0] == "rg"


def test_unavailable_builtin_ignores_legacy_bare_name_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    approvals = ShellApprovalStore(tmp_path)
    stored = approvals.add(("rg", "--no-config", "needle", "."))
    assert stored.ok
    policy = ShellExecutionPolicy(tmp_path, approvals)

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


def test_workspace_local_builtin_shadow_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = _bind_executables(
        monkeypatch,
        tmp_path,
        "rg",
        inside_workspace=True,
    )["rg"]
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
    assert result.command is not None
    assert result.command.argv == (str(shadow), "--no-config", "needle", ".")


def test_external_path_wrapper_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rg = _bind_executables(monkeypatch, tmp_path, "rg")["rg"]
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
    assert result.command is not None
    assert result.command.argv == (str(rg), "--no-config", "needle", ".")


@pytest.mark.skipif(
    os.name != "nt",
    reason="SystemRoot is a Windows trust-boundary input.",
)
def test_system_root_environment_cannot_extend_trusted_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_windows = tmp_path / "fake-windows"
    fake_system = fake_windows / "System32"
    fake_system.mkdir(parents=True)
    rg = fake_system / "rg.EXE"
    rg.write_bytes(b"MZ\x00\x00")
    monkeypatch.setenv("SystemRoot", str(fake_windows))
    monkeypatch.setenv("PATH", str(fake_system))
    monkeypatch.setenv("PATHEXT", ".EXE")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
    assert result.command is not None
    assert result.command.argv[0] == str(rg.resolve())


def test_trusted_system_executable_auto_approves_safe_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rg = _bind_executables(monkeypatch, tmp_path, "rg")["rg"]
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(rg.parent,),
    )

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.AUTO_APPROVE
    assert result.command is not None
    assert result.command.argv == (str(rg), "--no-config", "needle", ".")


def test_script_wrapper_in_trusted_root_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = tmp_path.parent / f"{tmp_path.name}-trusted-script"
    trusted.mkdir()
    suffix = ".EXE" if os.name == "nt" else ""
    rg = trusted / f"rg{suffix}"
    rg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    rg.chmod(0o755)
    monkeypatch.setenv("PATH", str(trusted))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".EXE")
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(trusted,),
    )

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
    assert result.command is not None
    assert result.command.argv[0] == str(rg.resolve())


def test_relative_path_wrapper_requires_confirmation_and_binds_workspace_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rg = _bind_executables(
        monkeypatch,
        tmp_path,
        "rg",
        inside_workspace=True,
    )["rg"]
    monkeypatch.setenv("PATH", "bin")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
    assert result.command is not None
    assert result.command.argv[0] == str(rg)


def test_trusted_root_symlink_escape_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = tmp_path.parent / f"{tmp_path.name}-trusted"
    trusted.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-rg"
    if os.name == "nt":
        outside = outside.with_suffix(".exe")
        outside.write_bytes(b"MZ\x00\x00")
        link = trusted / "rg.exe"
    else:
        outside.write_bytes(b"\x7fELF")
        link = trusted / "rg"
    outside.chmod(0o755)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")
    monkeypatch.setenv("PATH", str(trusted))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".EXE")
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(trusted,),
    )

    result = policy.classify({"argv": ["rg", "needle", "."]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


def test_ls_recursive_dereference_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ls = _bind_executables(monkeypatch, tmp_path, "ls")["ls"]
    outside = tmp_path.parent / f"{tmp_path.name}-outside-directory"
    outside.mkdir()
    link = tmp_path / "linked-directory"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable.")
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(ls.parent,),
    )

    result = policy.classify({"argv": ["ls", "-RL", "linked-directory"]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize(
    "argv",
    [
        ["grep", "needle"],
        ["grep", "needle", "-"],
        ["head"],
        ["head", "-"],
        ["tail"],
        ["tail", "-"],
        ["wc"],
        ["wc", "-"],
        ["sort"],
        ["sort", "-"],
        ["uniq"],
        ["uniq", "-"],
        ["rg", "needle", "-"],
    ],
)
def test_commands_that_can_read_stdin_require_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    executable = _bind_executables(monkeypatch, tmp_path, argv[0])[argv[0]]
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(executable.parent,),
    )

    result = policy.classify({"argv": argv})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize(
    "argv",
    [
        ["ls", ".cdy-agent"],
        ["ls", "-a", "."],
        ["ls", "-R", "."],
        ["rg", "secret", ".cdy-agent"],
        ["rg", "--hidden", "secret", "."],
        ["grep", "secret", ".cdy-agent/shell-approvals.json"],
        ["head", ".cdy-agent/shell-approvals.json"],
        ["tail", ".cdy-agent/shell-approvals.json"],
        ["wc", ".cdy-agent/shell-approvals.json"],
        ["sort", ".cdy-agent/shell-approvals.json"],
        ["uniq", ".cdy-agent/shell-approvals.json"],
        ["git", "status", "--short"],
        ["git", "diff", "--stat"],
        ["git", "diff", "--", ".cdy-agent/shell-approvals.json"],
    ],
)
def test_machine_state_reads_require_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    executable = _bind_executables(monkeypatch, tmp_path, argv[0])[argv[0]]
    data_directory = tmp_path / ".cdy-agent"
    data_directory.mkdir()
    (data_directory / "shell-approvals.json").write_text(
        '{"version":1,"allowed_commands":[]}',
        encoding="utf-8",
    )
    if argv[0] == "git":
        (tmp_path / ".git").mkdir()
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(executable.parent,),
        git_repository_probe=lambda executable, workspace, environment: (
            workspace / ".git",
            workspace / ".git",
            workspace,
        ),
    )

    result = policy.classify({"argv": argv})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


def test_git_probe_uses_scrubbed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _bind_executables(monkeypatch, tmp_path, "git")["git"]
    (tmp_path / ".git").mkdir()
    names = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    }
    for name in names:
        monkeypatch.setenv(name, str(tmp_path.parent / "outside"))
    captured: list[dict[str, str]] = []

    def probe(
        executable: Path,
        workspace: Path,
        environment: dict[str, str],
    ) -> tuple[Path, Path, Path]:
        captured.append(dict(environment))
        return workspace / ".git", workspace / ".git", workspace

    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(git.parent,),
        git_repository_probe=probe,
    )

    result = policy.classify({"argv": ["git", "status", "--short"]})

    assert result.decision is ShellExecutionDecision.AUTO_APPROVE
    assert captured
    assert names.isdisjoint(captured[0])


@pytest.mark.parametrize("external_component", [0, 1, 2])
def test_git_probe_rejects_effective_repository_paths_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    external_component: int,
) -> None:
    git = _bind_executables(monkeypatch, tmp_path, "git")["git"]
    (tmp_path / ".git").mkdir()
    paths = [tmp_path / ".git", tmp_path / ".git", tmp_path]
    outside = tmp_path.parent / f"{tmp_path.name}-outside-metadata"
    outside.mkdir()
    paths[external_component] = outside
    policy = ShellExecutionPolicy(
        tmp_path,
        ShellApprovalStore(tmp_path),
        trusted_executable_roots=(git.parent,),
        git_repository_probe=lambda executable, workspace, environment: (
            paths[0],
            paths[1],
            paths[2],
        ),
    )

    result = policy.classify({"argv": ["git", "diff", "--stat"]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


def test_external_gitdir_file_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_executables(monkeypatch, tmp_path, "git")
    outside_git = tmp_path.parent / f"{tmp_path.name}-gitdir"
    outside_git.mkdir()
    (tmp_path / ".git").write_text(
        f"gitdir: {outside_git}\n",
        encoding="utf-8",
    )
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["git", "status", "--short"]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


def test_external_git_directory_symlink_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_executables(monkeypatch, tmp_path, "git")
    outside_git = tmp_path.parent / f"{tmp_path.name}-gitdir"
    outside_git.mkdir()
    try:
        (tmp_path / ".git").symlink_to(outside_git, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable.")
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": ["git", "diff", "--stat"]})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "status", "--short"],
        ["git", "diff", "--stat"],
    ],
)
def test_external_linked_worktree_commondir_requires_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    _bind_executables(monkeypatch, tmp_path, "git")
    linked_gitdir = tmp_path / "linked-gitdir"
    linked_gitdir.mkdir()
    outside_commondir = tmp_path.parent / f"{tmp_path.name}-commondir"
    outside_commondir.mkdir()
    (linked_gitdir / "commondir").write_text(
        f"{outside_commondir}\n",
        encoding="utf-8",
    )
    (tmp_path / ".git").write_text(
        f"gitdir: {linked_gitdir}\n",
        encoding="utf-8",
    )
    policy = ShellExecutionPolicy(tmp_path, ShellApprovalStore(tmp_path))

    result = policy.classify({"argv": argv})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "status", "--short"],
        ["git", "diff", "--stat"],
    ],
)
def test_parent_repository_discovery_requires_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    _bind_executables(monkeypatch, tmp_path, "git")
    repository = tmp_path / "repository"
    workspace = repository / "nested-workspace"
    workspace.mkdir(parents=True)
    (repository / ".git").mkdir()
    policy = ShellExecutionPolicy(workspace, ShellApprovalStore(workspace))

    result = policy.classify({"argv": argv})

    assert result.decision is ShellExecutionDecision.REQUIRE_CONFIRMATION
