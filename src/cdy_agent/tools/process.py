from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


MAX_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass
class _DrainState:
    retained: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    error: BaseException | None = None


def sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("GIT_EXTERNAL_DIFF", None)
    environment.pop("RIPGREP_CONFIG_PATH", None)
    environment.update({"GIT_PAGER": "cat", "PAGER": "cat"})
    return environment


def limited_output(
    output: str, limit: int = MAX_OUTPUT_BYTES
) -> tuple[str, bool]:
    encoded = output.encode("utf-8")
    if len(encoded) <= limit:
        return output, False
    limited = encoded[:limit]
    return limited.decode("utf-8", errors="ignore"), True


def run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    shell: bool,
    capture_output: bool,
    text: bool,
    env: Mapping[str, str],
    timeout: int,
    check: bool,
) -> BoundedProcessResult:
    if shell or not capture_output or not text or check:
        raise ValueError("Unsupported bounded process options.")
    deadline = time.monotonic() + timeout
    popen_options: dict[str, object] = {}
    if os.name == "posix":
        popen_options["start_new_session"] = True
    elif os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        **popen_options,
    )
    windows_job = _assign_windows_job(process)
    if process.stdout is None or process.stderr is None:
        _terminate_process_tree(process, windows_job)
        _reap_process(process, deadline)
        _close_windows_job(windows_job)
        raise OSError("Could not capture process output.")

    stdout_state = _DrainState()
    stderr_state = _DrainState()
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stdout, stdout_state),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, stderr_state),
        daemon=True,
    )
    threads = (stdout_thread, stderr_thread)
    try:
        for thread in threads:
            thread.start()
        _wait_for_process(process, argv, timeout, deadline)
        if not _join_threads(threads, deadline):
            raise subprocess.TimeoutExpired(
                list(argv),
                timeout,
                output=bytes(stdout_state.retained),
                stderr=bytes(stderr_state.retained),
            )
    except BaseException:
        for cleanup in (
            lambda: _terminate_process_tree(process, windows_job),
            lambda: _request_pipe_closes(process),
            lambda: _join_threads(threads, deadline),
            lambda: _reap_process(process, deadline),
        ):
            try:
                cleanup()
            except BaseException:
                pass
        raise
    finally:
        _close_windows_job(windows_job)

    for state in (stdout_state, stderr_state):
        if state.error is not None:
            raise OSError("Could not read process output.") from state.error
    if process.returncode is None:
        raise OSError("Process did not report a return code.")
    return BoundedProcessResult(
        returncode=process.returncode,
        stdout=bytes(stdout_state.retained).decode("utf-8", errors="replace"),
        stderr=bytes(stderr_state.retained).decode("utf-8", errors="replace"),
        stdout_truncated=stdout_state.truncated,
        stderr_truncated=stderr_state.truncated,
    )


def _drain_stream(stream: BinaryIO, state: _DrainState) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = MAX_OUTPUT_BYTES - len(state.retained)
            if remaining > 0:
                state.retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                state.truncated = True
    except (OSError, ValueError) as error:
        state.error = error
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _wait_for_process(
    process: subprocess.Popen[bytes],
    argv: Sequence[str],
    timeout: int,
    deadline: float,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        if process.poll() is None:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        return
    process.wait(timeout=remaining)


def _join_threads(
    threads: tuple[threading.Thread, ...], deadline: float
) -> bool:
    for thread in threads:
        if not thread.is_alive():
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        thread.join(remaining)
    return all(not thread.is_alive() for thread in threads)


def _terminate_process_tree(
    process: subprocess.Popen[bytes], windows_job: int | None
) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        return
    if os.name == "nt" and windows_job is not None:
        if _terminate_windows_job(windows_job):
            return
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _request_pipe_closes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is None or stream.closed:
            continue
        threading.Thread(
            target=_close_stream,
            args=(stream,),
            daemon=True,
        ).start()


def _close_stream(stream: BinaryIO) -> None:
    try:
        stream.close()
    except (OSError, ValueError):
        pass


def _reap_process(
    process: subprocess.Popen[bytes], deadline: float
) -> None:
    if process.poll() is not None:
        return
    remaining = deadline - time.monotonic()
    if remaining > 0:
        try:
            process.wait(timeout=remaining)
            return
        except subprocess.TimeoutExpired:
            pass
    threading.Thread(target=process.wait, daemon=True).start()


def _assign_windows_job(process: subprocess.Popen[bytes]) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.AssignProcessToJobObject.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.CreateJobObjectW(None, None)
        process_handle = getattr(process, "_handle", None)
        if not handle or process_handle is None:
            if handle:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
            return None
        if not kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(handle),
            ctypes.c_void_p(int(process_handle)),
        ):
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return None
        return int(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _terminate_windows_job(handle: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
        )
        return bool(
            kernel32.TerminateJobObject(ctypes.c_void_p(handle), 1)
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _close_windows_job(handle: int | None) -> None:
    if handle is None:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except (AttributeError, OSError, TypeError, ValueError):
        pass
