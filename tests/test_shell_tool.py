import subprocess
from inspect import signature
from pathlib import Path

import pytest

from cdy_agent.tools.shell import MAX_OUTPUT_BYTES, ShellTool


def test_shell_constructor_cannot_disable_confirmation(tmp_path: Path) -> None:
    assert tuple(signature(ShellTool).parameters) == ("workspace", "runner")
    with pytest.raises(TypeError):
        ShellTool(tmp_path, requires_confirmation=False)  # type: ignore[call-arg]


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
    "argv",
    [
        ["find", ".", "-exec", "sh", "-c", "id", ";"],
        ["find", ".", "-execdir", "id", ";"],
        ["find", ".", "-ok", "id", ";"],
        ["find", ".", "-okdir", "id", ";"],
        ["rg", "--pre", "sh", "x"],
        ["rg", "--pre=sh", "x"],
        ["sed", "-e", "1e id", "file"],
        ["sed", "--expression=1e id", "file"],
        ["git", "diff", "--ext-diff"],
        ["git", "-c", "diff.external=id", "diff"],
        ["sed", "-e", "s/.*/touch owned/e", "file"],
        ["sed", "s|x|y|e", "file"],
        ["sed", "p\ne touch owned", "file"],
        ["sed", "-f", "commands.sed", "file"],
        ["sed", "--file=commands.sed", "file"],
        ["sed", "/x/e touch owned", "file"],
        ["sed", "1!e touch owned", "file"],
        ["sed", "{e touch owned}", "file"],
        ["sed", "s/x/y/w owned", "file"],
        ["git", "diff", "--ext", "helper"],
        ["git", "diff", "--textc", "file"],
    ],
)
def test_shell_rejects_execution_delegation_without_runner(
    tmp_path: Path, argv: list[str]
) -> None:
    calls: list[list[str]] = []
    tool = ShellTool(tmp_path, runner=lambda value, **_: calls.append(value))  # type: ignore[arg-type]
    assert tool.execute({"argv": argv}).code == "command_not_allowed"
    assert calls == []


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
    environment = calls[0].pop("env")
    assert isinstance(environment, dict)
    assert environment["GIT_PAGER"] == "cat"
    assert environment["PAGER"] == "cat"
    assert "GIT_EXTERNAL_DIFF" not in environment
    assert "RIPGREP_CONFIG_PATH" not in environment
    assert "PATH" in environment
    assert calls == [
        {
            "argv": [
                "git", "--no-pager", "-c", "core.fsmonitor=false",
                "status", "--short",
            ],
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
    assert calls == [["rg", "--no-config", "|", "."]]


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
    stdout = "a" * MAX_OUTPUT_BYTES
    stderr = "b" * (MAX_OUTPUT_BYTES + 1)

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)

    result = ShellTool(tmp_path, runner=runner).execute({"argv": ["ls"]})
    assert result.data == {
        "returncode": 0,
        "stdout": stdout,
        "stderr": "b" * MAX_OUTPUT_BYTES,
        "stdout_truncated": False,
        "stderr_truncated": True,
    }


def test_shell_caps_utf8_bytes_and_drops_only_incomplete_codepoint(tmp_path: Path) -> None:
    output = "a" * (MAX_OUTPUT_BYTES - 1) + "你" + "z"

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, output, "你" * 30000)
    result = ShellTool(tmp_path, runner=runner).execute({"argv": ["pwd"]})
    assert result.data["stdout"] == "a" * (MAX_OUTPUT_BYTES - 1)
    assert len(result.data["stderr"].encode()) <= MAX_OUTPUT_BYTES
    assert result.data["stdout_truncated"] is True


def test_shell_confirmation_names_exact_argv_and_workspace(tmp_path: Path) -> None:
    description = ShellTool(tmp_path).confirmation_description(
        {"argv": ["rg", "x y", "."]}
    )
    assert repr(["rg", "--no-config", "x y", "."]) in description
    assert str(tmp_path.resolve()) in description


@pytest.mark.parametrize(
    ("user_argv", "effective_argv"),
    [
        (["rg", "x"], ["rg", "--no-config", "x"]),
        (
            ["git", "status", "--short"],
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "status", "--short"],
        ),
        (
            ["git", "diff", "--", "file"],
            [
                "git", "--no-pager", "-c", "core.fsmonitor=false", "diff",
                "--no-ext-diff", "--no-textconv", "--", "file",
            ],
        ),
        (
            ["git", "diff", "--stat"],
            [
                "git", "--no-pager", "-c", "core.fsmonitor=false", "diff",
                "--stat", "--no-ext-diff", "--no-textconv",
            ],
        ),
        (
            ["git", "diff", "--no-ext-diff", "--stat", "--no-textconv"],
            [
                "git", "--no-pager", "-c", "core.fsmonitor=false", "diff",
                "--stat", "--no-ext-diff", "--no-textconv",
            ],
        ),
    ],
)
def test_shell_confirmation_and_execution_use_effective_argv(
    tmp_path: Path, user_argv: list[str], effective_argv: list[str]
) -> None:
    calls: list[list[str]] = []
    tool = ShellTool(
        tmp_path,
        runner=lambda argv, **kwargs: calls.append(argv)
        or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert repr(effective_argv) in tool.confirmation_description({"argv": user_argv})
    assert tool.execute({"argv": user_argv}).ok
    assert calls == [effective_argv]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["sed", "-n", "1p", "file"], ["sed", "-n", "1p", "file"]),
        (
            ["sed", "-e", "s/foo/here/g", "file"],
            ["sed", "-e", "s/foo/here/g", "file"],
        ),
    ],
)
def test_shell_retains_safe_sed_scripts(
    tmp_path: Path, argv: list[str], expected: list[str]
) -> None:
    calls: list[list[str]] = []
    tool = ShellTool(
        tmp_path,
        runner=lambda argv, **kwargs: calls.append(argv)
        or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert tool.execute({"argv": argv}).ok
    assert calls == [expected]


def test_registry_rejects_disallowed_shell_before_confirmation(tmp_path: Path) -> None:
    from cdy_agent.tools.base import ToolCall
    from cdy_agent.tools.registry import ToolRegistry

    callbacks: list[object] = []
    result = ToolRegistry([ShellTool(tmp_path)]).execute(
        ToolCall("1", "shell", '{"argv":["find",".","-exec","id",";"]}'),
        lambda request: callbacks.append(request) or True,
    )
    assert result.code == "command_not_allowed"
    assert callbacks == []
