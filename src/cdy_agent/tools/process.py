from __future__ import annotations

import os
import subprocess
import threading
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
    error: OSError | None = None


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
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
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
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            for thread in threads:
                thread.join()
            raise
        for thread in threads:
            thread.join()
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        for thread in threads:
            if thread.ident is not None:
                thread.join()
        raise

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
    except OSError as error:
        state.error = error
    finally:
        stream.close()
