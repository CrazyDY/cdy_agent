import pytest

from cdy_agent.run_control import AgentRunCancelled, RunControl


def test_cancel_runs_registered_callbacks_once() -> None:
    """Removing callback-registry clearing would run cleanup twice."""
    control = RunControl()
    calls: list[str] = []
    control.add_cancel_callback(lambda: calls.append("closed"))

    control.cancel()
    control.cancel()

    assert control.cancelled is True
    assert calls == ["closed"]


def test_unregister_prevents_a_callback_from_running() -> None:
    """Removing an active callback must prevent its cleanup side effect."""
    control = RunControl()
    calls: list[str] = []
    unregister = control.add_cancel_callback(lambda: calls.append("closed"))

    unregister()
    control.cancel()

    assert calls == []


def test_callback_added_after_cancellation_runs_immediately() -> None:
    """Cleanup registered after cancellation must not be left pending."""
    control = RunControl()
    calls: list[str] = []
    control.cancel()

    control.add_cancel_callback(lambda: calls.append("closed"))

    assert calls == ["closed"]


def test_callback_failure_does_not_skip_later_cleanup() -> None:
    """A failing cleanup callback must not prevent subsequent cleanup."""
    control = RunControl()
    calls: list[str] = []

    def fail() -> None:
        raise RuntimeError("cleanup failed")

    control.add_cancel_callback(fail)
    control.add_cancel_callback(lambda: calls.append("closed"))

    control.cancel()

    assert calls == ["closed"]


def test_raise_if_cancelled_uses_stable_exception() -> None:
    """Changing cancellation's public exception must fail this contract test."""
    control = RunControl()
    control.cancel()

    with pytest.raises(AgentRunCancelled, match="Agent run was cancelled"):
        control.raise_if_cancelled()
