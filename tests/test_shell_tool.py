import subprocess
from pathlib import Path

import pytest

from cdy_agent.tools.shell import MAX_OUTPUT_CHARS, ShellTool


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "file"],
        ["/bin/ls"],
        ["./ls"],
        [r"bin\\ls"],
        ["git", "log"],
        ["git"],
        ["git", "-C", "..", "status"],
        ["git", "--git-dir=../.git", "diff"],
    ],
)
def test_shell_rejects_disallowed_commands(tmp_path: Path, argv: list[str]) -> None:
    assert ShellTool(tmp_path).execute({"argv": argv}).code == "command_not_allowed"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"argv": []},
        {"argv": "ls"},
        {"argv": ["ls", 1]},
        {"argv": ["ls"], "extra": True},
        {"argv": ["ls"], "timeout_seconds": 0},
        {"argv": ["ls"], "timeout_seconds": 31},
        {"argv": ["ls"], "timeout_seconds": True},
    ],
)
def test_shell_rejects_invalid_arguments(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    assert ShellTool(tmp_path).execute(arguments).code == "invalid_arguments"


def test_shell_invokes_runner_without_shell(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    result = ShellTool(tmp_path, runner=runner).execute(
        {"argv": ["git", "status", "--short"], "timeout_seconds": 4}
    )

    assert result.ok is True
    assert calls == [
        {
            "argv": ["git", "status", "--short"],
            "cwd": tmp_path.resolve(),
            "shell": False,
            "capture_output": True,
            "text": True,
            "timeout": 4,
            "check": False,
        }
    ]


def test_shell_metacharacters_are_plain_arguments(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    ShellTool(tmp_path, runner=runner).execute({"argv": ["rg", "|", "."]})
    assert calls == [["rg", "|", "."]]


def test_shell_uses_default_timeout(tmp_path: Path) -> None:
    calls: list[object] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    ShellTool(tmp_path, runner=runner).execute({"argv": ["pwd"]})
    assert calls == [10]


def test_shell_maps_timeout_to_failure(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    result = ShellTool(tmp_path, runner=runner).execute({"argv": ["ls"]})
    assert result.ok is False
    assert result.code == "command_timeout"


def test_shell_maps_oserror_to_execution_error(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("unavailable")

    result = ShellTool(tmp_path, runner=runner).execute({"argv": ["ls"]})
    assert result.ok is False
    assert result.code == "execution_error"
    assert "unavailable" in (result.message or "")


def test_shell_maps_nonzero_exit_to_command_failed(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, "out", "err")

    result = ShellTool(tmp_path, runner=runner).execute({"argv": ["git", "diff"]})
    assert result.ok is False
    assert result.code == "command_failed"
    assert "2" in (result.message or "")


def test_shell_truncates_stdout_and_stderr_independently(tmp_path: Path) -> None:
    stdout = "a" * (MAX_OUTPUT_CHARS + 1)
    stderr = "b" * MAX_OUTPUT_CHARS

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)

    result = ShellTool(tmp_path, runner=runner).execute({"argv": ["ls"]})
    assert result.data == {
        "returncode": 0,
        "stdout": "a" * MAX_OUTPUT_CHARS,
        "stderr": stderr,
        "stdout_truncated": True,
        "stderr_truncated": False,
    }
