import json
import logging

import pytest

from cdy_agent.observability.logging import (
    configure_structured_logging,
    log_event,
    resolve_log_level,
)


def test_log_level_defaults_and_rejects_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_LOG_LEVEL", raising=False)
    assert resolve_log_level() == logging.WARNING
    monkeypatch.setenv("CDY_AGENT_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="CDY_AGENT_LOG_LEVEL"):
        resolve_log_level()


def test_structured_log_contains_only_explicit_safe_fields(capsys) -> None:
    configure_structured_logging(logging.DEBUG)
    log_event(
        logging.INFO,
        "trace_finished",
        trace_id="safe-id",
        status="failed",
        duration_ms=4,
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["event"] == "trace_finished"
    assert payload["trace_id"] == "safe-id"
    assert set(payload) == {
        "timestamp",
        "level",
        "event",
        "trace_id",
        "status",
        "duration_ms",
    }


def test_structured_log_does_not_overwrite_formatter_metadata(capsys) -> None:
    configure_structured_logging(logging.DEBUG)
    log_event(logging.INFO, "trace_finished", timestamp="caller-value")

    payload = json.loads(capsys.readouterr().err)

    assert payload["timestamp"] != "caller-value"
