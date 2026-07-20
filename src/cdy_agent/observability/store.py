from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from .models import TraceRecord


class TraceStoreError(RuntimeError):
    """Base error for workspace trace persistence failures."""


class TraceNotFoundError(TraceStoreError):
    """Raised when a requested trace does not exist."""


class TraceStore:
    """Append and query strict workspace-local JSONL traces."""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace / ".cdy-agent" / "traces.jsonl"

    def append(self, record: TraceRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                record.to_dict(), ensure_ascii=False, separators=(",", ":")
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        except OSError as exc:
            raise TraceStoreError("Could not write trace data.") from exc

    def list_traces(self) -> tuple[TraceRecord, ...]:
        records = self._read_all()
        return tuple(
            sorted(records, key=lambda record: record.started_at, reverse=True)
        )

    def get(self, trace_id: str) -> TraceRecord:
        try:
            if not isinstance(trace_id, str) or str(UUID(trace_id)) != trace_id:
                raise ValueError
        except ValueError:
            raise TraceStoreError("Trace ID must be a complete UUID.") from None
        for record in self._read_all():
            if record.trace_id == trace_id:
                return record
        raise TraceNotFoundError(f"Trace {trace_id} not found.")

    def _read_all(self) -> tuple[TraceRecord, ...]:
        if not self.path.exists():
            return ()
        records = []
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        payload = json.loads(line)
                        records.append(TraceRecord.from_dict(payload))
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise TraceStoreError(
                            f"Invalid trace data on line {line_number}."
                        ) from exc
        except TraceStoreError:
            raise
        except OSError as exc:
            raise TraceStoreError("Could not read trace data.") from exc
        return tuple(records)
