import json
import logging

import pytest

from cdy_agent.config import WorkspaceConfig
from cdy_agent.observability.logging import (
    configure_structured_logging,
    log_event,
    resolve_log_level,
)

TRACE_ID = "f8605a17-cf86-46ce-87ad-7db57533e5dc"
SPAN_ID = "87c9c45f-20ee-4bbe-93df-97cf337fc065"


def test_log_level_defaults_and_rejects_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_LOG_LEVEL", raising=False)
    assert resolve_log_level() == logging.WARNING
    monkeypatch.setenv("CDY_AGENT_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="CDY_AGENT_LOG_LEVEL"):
        resolve_log_level()


def test_workspace_config_supplies_log_level_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_LOG_LEVEL", raising=False)

    assert resolve_log_level(WorkspaceConfig(log_level="INFO")) == logging.INFO


def test_environment_log_level_wins_over_workspace_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_LOG_LEVEL", "ERROR")

    assert resolve_log_level(WorkspaceConfig(log_level="INFO")) == logging.ERROR


@pytest.mark.parametrize("configured", ["debug", " info", "INFO "])
def test_log_level_requires_exact_uppercase(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("CDY_AGENT_LOG_LEVEL", configured)

    with pytest.raises(ValueError, match="CDY_AGENT_LOG_LEVEL"):
        resolve_log_level()


def test_structured_log_contains_only_explicit_safe_fields(capsys) -> None:
    configure_structured_logging(logging.DEBUG)
    log_event(
        logging.INFO,
        "trace_finished",
        trace_id=TRACE_ID,
        status="failed",
        duration_ms=4,
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["event"] == "trace_finished"
    assert payload["trace_id"] == TRACE_ID
    assert set(payload) == {
        "timestamp",
        "level",
        "event",
        "trace_id",
        "status",
        "duration_ms",
    }


def test_structured_logging_filters_below_configured_level(capsys) -> None:
    configure_structured_logging(logging.WARNING)

    log_event(
        logging.INFO,
        "trace_started",
        trace_id=TRACE_ID,
        status="started",
    )

    assert capsys.readouterr().err == ""


def test_reconfiguring_structured_logging_replaces_handler() -> None:
    logger = logging.getLogger("cdy_agent.observability")

    configure_structured_logging(logging.INFO)
    first_handler = logger.handlers[0]
    configure_structured_logging(logging.DEBUG)

    assert len(logger.handlers) == 1
    assert logger.handlers[0] is not first_handler
    assert isinstance(logger.handlers[0].formatter, type(first_handler.formatter))


@pytest.mark.parametrize(
    "unsafe_field",
    ["prompt", "reply", "arguments", "result", "api_key", "environment", "message"],
)
def test_structured_log_rejects_unsafe_fields(unsafe_field: str) -> None:
    configure_structured_logging(logging.DEBUG)
    fields = {
        "trace_id": TRACE_ID,
        "status": "failed",
        "duration_ms": 4,
        unsafe_field: "secret-value",
    }

    with pytest.raises(ValueError, match="fields"):
        log_event(logging.INFO, "trace_finished", **fields)


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        ("trace_started", {"trace_id": TRACE_ID, "status": "started"}),
        (
            "trace_finished",
            {"trace_id": TRACE_ID, "status": "succeeded", "duration_ms": 4},
        ),
        (
            "model_call_finished",
            {
                "trace_id": TRACE_ID,
                "span_id": SPAN_ID,
                "status": "succeeded",
                "duration_ms": 2,
            },
        ),
        (
            "tool_call_finished",
            {
                "trace_id": TRACE_ID,
                "span_id": SPAN_ID,
                "status": "failed",
                "duration_ms": 3,
            },
        ),
    ],
)
def test_structured_log_accepts_only_complete_event_schemas(
    capsys,
    event: str,
    fields: dict[str, object],
) -> None:
    configure_structured_logging(logging.DEBUG)

    log_event(logging.INFO, event, **fields)

    payload = json.loads(capsys.readouterr().err)
    assert set(payload) == {"timestamp", "level", "event", *fields}


def test_structured_log_rejects_unknown_events_and_missing_fields() -> None:
    with pytest.raises(ValueError, match="event"):
        log_event(logging.INFO, "prompt_received", prompt="secret")
    with pytest.raises(ValueError, match="fields"):
        log_event(logging.INFO, "trace_finished", trace_id=TRACE_ID)


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("trace_id", "sk-secret-api-key"),
        ("status", "secret exception message"),
        ("duration_ms", {"arguments": {"path": "/secret"}}),
    ],
)
def test_structured_log_rejects_payloads_in_safe_field_slots(
    field: str,
    unsafe_value: object,
) -> None:
    fields: dict[str, object] = {
        "trace_id": TRACE_ID,
        "status": "failed",
        "duration_ms": 4,
    }
    fields[field] = unsafe_value

    with pytest.raises(ValueError, match="field"):
        log_event(logging.INFO, "trace_finished", **fields)
