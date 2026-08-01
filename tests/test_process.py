from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cdy_agent.run_control import AgentRunCancelled, RunControl
from cdy_agent.tools import process as process_module


class _BlockingProcess:
    """Test double for a running child process with blocking waits."""

    def __init__(self) -> None:
        self.pid = 1234
        self.returncode: int | None = None
        self.stdout = object()
        self.stderr = object()
        self.wait_entered = threading.Event()
        self._terminated = threading.Event()
        self.terminated = False
        self.reaped = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_entered.set()
        if not self._terminated.wait(timeout):
            raise subprocess.TimeoutExpired(["fake"], timeout)
        self.returncode = -9
        return self.returncode


def test_cancellation_terminates_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Removing process cancellation cleanup would leave the child running."""
    process = _BlockingProcess()
    popen_options: list[dict[str, object]] = []

    def popen(*args: object, **kwargs: object) -> _BlockingProcess:
        popen_options.append(kwargs)
        return process

    def terminate(
        target: _BlockingProcess,
        windows_job: int | None,
    ) -> None:
        target.terminated = True
        target._terminated.set()

    def reap(target: _BlockingProcess, deadline: float) -> None:
        target.reaped = True
        target.wait(timeout=0)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(process_module, "_drain_stream", lambda stream, state: None)
    monkeypatch.setattr(process_module, "_terminate_process_tree", terminate)
    monkeypatch.setattr(process_module, "_reap_process", reap)
    control = RunControl()
    errors: list[BaseException] = []
    finished = threading.Event()

    def run() -> None:
        try:
            process_module.run_bounded_process(
                ["fake"],
                cwd=tmp_path,
                shell=False,
                capture_output=True,
                text=True,
                env={},
                timeout=10,
                check=False,
                run_control=control,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert process.wait_entered.wait(timeout=1)
    control.cancel()
    assert finished.wait(timeout=1)
    thread.join()

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], AgentRunCancelled)
    assert process.terminated is True
    assert process.reaped is True
    assert popen_options[0]["stdin"] is subprocess.DEVNULL


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-specific")
def test_cancellation_kills_process_group_children(tmp_path: Path) -> None:
    """Removing group termination would leave a real grandchild alive."""
    child_pid_path = tmp_path / "child.pid"
    source = (
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(30)\n"
    )
    control = RunControl()
    errors: list[BaseException] = []
    finished = threading.Event()

    def run() -> None:
        try:
            process_module.run_bounded_process(
                [sys.executable, "-c", source],
                cwd=tmp_path,
                shell=False,
                capture_output=True,
                text=True,
                env=dict(os.environ),
                timeout=10,
                check=False,
                run_control=control,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 3
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid_path.exists()
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))

        control.cancel()
        assert finished.wait(timeout=3)
        thread.join()

        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], AgentRunCancelled)
        deadline = time.monotonic() + 3
        while _process_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _process_exists(child_pid)
    finally:
        control.cancel()
        finished.wait(timeout=1)
        thread.join(timeout=1)
        if child_pid is not None and _process_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
