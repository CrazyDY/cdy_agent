import asyncio
import shutil
from pathlib import Path

import pytest

from cdy_agent.skills.filesystem import tools as filesystem_tools
from cdy_agent.skills.shell import tools as shell_tools


def call_tool(tool, **kwargs):
    return asyncio.run(tool.on_invoke_tool(None, kwargs))


@pytest.fixture(autouse=True)
def clean_workspace_tmp():
    workspace_tmp = Path(".tmp_skill_tests")
    shutil.rmtree(workspace_tmp, ignore_errors=True)
    yield
    shutil.rmtree(workspace_tmp, ignore_errors=True)


def test_filesystem_skill_writes_reads_and_lists_workspace_files():
    written = call_tool(
        filesystem_tools.write_file,
        path=".tmp_skill_tests/example.txt",
        content="第一行\n第二行",
    )
    assert written["path"] == ".tmp_skill_tests/example.txt"

    listing = call_tool(filesystem_tools.list_files, path=".tmp_skill_tests")
    assert listing == [".tmp_skill_tests/example.txt"]

    content = call_tool(filesystem_tools.read_file, path=".tmp_skill_tests/example.txt", start_line=2)
    assert content["content"] == "第二行"


def test_filesystem_skill_rejects_paths_outside_workspace():
    with pytest.raises(ValueError, match="outside workspace"):
        call_tool(filesystem_tools.read_file, path="../secret.txt")


def test_shell_skill_runs_bash_inside_workspace():
    result = call_tool(shell_tools.run_bash, command="printf 'hello'", timeout_seconds=5)

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"
    assert result["stderr"] == ""
    assert result["timed_out"] is False


def test_shell_skill_reports_nonzero_exit_code():
    result = call_tool(shell_tools.run_bash, command="echo problem >&2; exit 7", timeout_seconds=5)

    assert result["exit_code"] == 7
    assert "problem" in result["stderr"]
