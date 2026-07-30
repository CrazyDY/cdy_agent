from __future__ import annotations

import threading
from collections.abc import Callable


class AgentRunCancelled(RuntimeError):
    """Raised when a caller cooperatively cancels an Agent run."""


class RunControl:
    """Coordinate cooperative cancellation of one Agent run."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: dict[object, Callable[[], None]] = {}

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            self._invoke_callback(callback)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AgentRunCancelled("Agent run was cancelled.")

    def add_cancel_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        key = object()
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._callbacks[key] = callback
                run_now = False
        if run_now:
            self._invoke_callback(callback)

        def unregister() -> None:
            with self._lock:
                self._callbacks.pop(key, None)

        return unregister

    @staticmethod
    def _invoke_callback(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            pass
