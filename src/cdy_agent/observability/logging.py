from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from uuid import UUID

LOGGER = logging.getLogger("cdy_agent.observability")
LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
EVENT_FIELDS = {
    "trace_started": frozenset({"trace_id", "status"}),
    "trace_finished": frozenset({"trace_id", "status", "duration_ms"}),
    "model_call_finished": frozenset(
        {"trace_id", "span_id", "status", "duration_ms"}
    ),
    "tool_call_finished": frozenset(
        {"trace_id", "span_id", "status", "duration_ms"}
    ),
}
EVENT_STATUSES = {
    "trace_started": frozenset({"started"}),
    "trace_finished": frozenset({"succeeded", "failed"}),
    "model_call_finished": frozenset({"succeeded", "failed"}),
    "tool_call_finished": frozenset({"succeeded", "failed"}),
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": record.msg,
        }
        payload.update(getattr(record, "safe_fields", {}))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def resolve_log_level() -> int:
    configured = os.getenv("CDY_AGENT_LOG_LEVEL", "WARNING")
    if configured not in LEVELS:
        raise ValueError(
            "CDY_AGENT_LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR."
        )
    return LEVELS[configured]


def configure_structured_logging(level: int) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    LOGGER.handlers[:] = [handler]
    LOGGER.setLevel(level)
    LOGGER.propagate = False


def log_event(level: int, event: str, **fields: object) -> None:
    expected_fields = EVENT_FIELDS.get(event)
    if expected_fields is None:
        raise ValueError("Unsupported observability event.")
    if set(fields) != expected_fields:
        raise ValueError(f"{event} has invalid structured logging fields.")
    if not _safe_field_values(event, fields):
        raise ValueError(f"{event} has invalid structured logging field values.")
    LOGGER.log(level, event, extra={"safe_fields": fields})


def _safe_field_values(event: str, fields: dict[str, object]) -> bool:
    identifiers = [
        value for key, value in fields.items() if key in {"trace_id", "span_id"}
    ]
    if any(not _canonical_uuid(value) for value in identifiers):
        return False
    if fields["status"] not in EVENT_STATUSES[event]:
        return False
    duration = fields.get("duration_ms")
    return duration is None or (
        isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0
    )


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False
