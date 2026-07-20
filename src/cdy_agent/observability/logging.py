from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

LOGGER = logging.getLogger("cdy_agent.observability")
LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
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
        payload.update(
            {
                key: value
                for key, value in getattr(record, "safe_fields", {}).items()
                if key not in {"timestamp", "level", "event"}
            }
        )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def resolve_log_level() -> int:
    configured = os.getenv("CDY_AGENT_LOG_LEVEL", "WARNING").strip().upper()
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
    LOGGER.log(level, event, extra={"safe_fields": fields})
