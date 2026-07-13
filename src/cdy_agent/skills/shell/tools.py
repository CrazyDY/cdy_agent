"""Shell command skill tools scoped to the current workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cdy_agent.openai_sdk import function_tool

WORKSPACE_ROOT = Path.cwd().resolve()
DEFAULT_TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 20_000


def _resolve_cwd(cwd: str) -> Path:
    target = (WORKSPACE_ROOT / cwd).resolve()
    if target != WORKSPACE_ROOT and WORKSPACE_ROOT not in target.parents:
        raise ValueError(f"cwd is outside workspace: {cwd}")
    return target


@function_tool
def run_bash(command: str, cwd: str = ".", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Run a bash command inside the workspace and return its output.

    Args:
        command: Bash command to execute.
        cwd: Relative workspace directory where the command should run.
        timeout_seconds: Maximum runtime in seconds.
    """

    run_cwd = _resolve_cwd(cwd)
    run_cwd.mkdir(parents=True, exist_ok=True)
    timeout = max(1, min(timeout_seconds, 60))

    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=run_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout[-MAX_OUTPUT_CHARS:]
        stderr = completed.stderr[-MAX_OUTPUT_CHARS:]
        return {
            "command": command,
            "cwd": run_cwd.relative_to(WORKSPACE_ROOT).as_posix() or ".",
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": run_cwd.relative_to(WORKSPACE_ROOT).as_posix() or ".",
            "exit_code": None,
            "stdout": (exc.stdout or "")[-MAX_OUTPUT_CHARS:],
            "stderr": (exc.stderr or "")[-MAX_OUTPUT_CHARS:],
            "timed_out": True,
        }
