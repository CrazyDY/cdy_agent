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


class _FinishedProcess:
    """Test double for a process that has already exited successfully."""

    def __init__(self) -> None:
        self.pid = 1234
        self.returncode = 0
        self.stdout = object()
        self.stderr = object()

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
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


def test_cancellation_after_wait_raises_before_returning_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Removing the post-drain check can return a result after cancellation."""
    process = _FinishedProcess()
    control = RunControl()

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(process_module, "_drain_stream", lambda stream, state: None)
    monkeypatch.setattr(process_module, "_join_threads", lambda threads, deadline: True)
    monkeypatch.setattr(
        process_module,
        "_wait_for_process",
        lambda *args: control.cancel(),
    )

    with pytest.raises(AgentRunCancelled):
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


def test_cancellation_uses_fresh_bounded_deadline_for_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reusing an expired command deadline skips cancellation cleanup waits."""
    process = _FinishedProcess()
    control = RunControl()
    cleanup_deadlines: list[float] = []
    clock = iter((0.0, 10.0))

    def cancel_and_raise(*args: object) -> None:
        control.cancel()
        raise AgentRunCancelled()

    monkeypatch.setattr(process_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(process_module, "_drain_stream", lambda stream, state: None)
    monkeypatch.setattr(
        process_module,
        "_wait_for_process",
        cancel_and_raise,
    )
    monkeypatch.setattr(
        process_module,
        "_join_threads",
        lambda threads, deadline: cleanup_deadlines.append(deadline) or True,
    )
    monkeypatch.setattr(
        process_module,
        "_reap_process",
        lambda target, deadline: cleanup_deadlines.append(deadline),
    )

    with pytest.raises(AgentRunCancelled):
        process_module.run_bounded_process(
            ["fake"],
            cwd=tmp_path,
            shell=False,
            capture_output=True,
            text=True,
            env={},
            timeout=1,
            check=False,
            run_control=control,
        )

    assert cleanup_deadlines == [11.0, 11.0]


@pytest.mark.skipif(os.name != "nt", reason="Windows process jobs are Windows-specific")
def test_windows_process_registers_callback_after_job_assignment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Registering before Job assignment can lose the assigned handle on cancel."""
    process = _FinishedProcess()
    control = RunControl()
    events: list[str] = []
    popen_options: list[dict[str, object]] = []

    def popen(*args: object, **kwargs: object) -> _FinishedProcess:
        popen_options.append(kwargs)
        return process

    def register(self: RunControl, callback: object) -> object:
        events.append("register")
        return lambda: events.append("unregister")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(process_module, "_assign_windows_job", lambda target: events.append("assign") or 7)
    monkeypatch.setattr(process_module, "_close_windows_job", lambda handle: events.append("close"))
    monkeypatch.setattr(process_module, "_drain_stream", lambda stream, state: None)
    monkeypatch.setattr(process_module, "_join_threads", lambda threads, deadline: True)
    monkeypatch.setattr(RunControl, "add_cancel_callback", register)

    result = process_module.run_bounded_process(
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

    assert result.returncode == 0
    assert popen_options[0]["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    assert events == ["assign", "register", "unregister", "close"]


@pytest.mark.skipif(os.name != "nt", reason="Windows process jobs are Windows-specific")
def test_windows_cancellation_terminates_the_assigned_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A callback that drops the Job handle leaves Windows descendants alive."""
    process = _BlockingProcess()
    control = RunControl()
    terminated_handles: list[int | None] = []
    errors: list[BaseException] = []
    events: list[str] = []
    finished = threading.Event()

    def terminate(target: _BlockingProcess, handle: int | None) -> None:
        terminated_handles.append(handle)
        target._terminated.set()

    def reap(target: _BlockingProcess, deadline: float) -> None:
        target.wait(timeout=0)

    original_register = RunControl.add_cancel_callback

    def register(self: RunControl, callback: object) -> object:
        unregister = original_register(self, callback)

        def tracked_unregister() -> None:
            events.append("unregister")
            unregister()

        return tracked_unregister

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

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(process_module, "_assign_windows_job", lambda target: 7)
    monkeypatch.setattr(process_module, "_drain_stream", lambda stream, state: None)
    monkeypatch.setattr(process_module, "_terminate_process_tree", terminate)
    monkeypatch.setattr(process_module, "_reap_process", reap)
    monkeypatch.setattr(process_module, "_close_windows_job", lambda handle: events.append("close"))
    monkeypatch.setattr(RunControl, "add_cancel_callback", register)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert process.wait_entered.wait(timeout=1)
    control.cancel()
    assert finished.wait(timeout=1)
    thread.join()

    assert len(errors) == 1
    assert isinstance(errors[0], AgentRunCancelled)
    assert terminated_handles == [7, 7]
    assert events == ["unregister", "close"]


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
