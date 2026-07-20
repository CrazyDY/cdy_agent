from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .logging import log_event
from .models import ModelCallSpan, TokenUsage, ToolCallSpan, TraceRecord
from .pricing import Pricing, estimate_cost


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class _OpenSpan:
    span_id: str
    sequence: int
    started: float
    tool_name: str | None = None


class TraceRecorder:
    def __init__(
        self,
        command: str,
        model: str,
        api_mode: str,
        session_id: str | None = None,
        pricing: Pricing | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
        now: Callable[[], str] = utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if command not in {"ask", "chat"}:
            raise ValueError("Unsupported trace command.")
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError("Unsupported trace API mode.")
        if not model.strip():
            raise ValueError("Trace model must not be empty.")
        if command == "ask" and session_id is not None:
            raise ValueError("ask traces cannot have a session ID.")
        if command == "chat":
            try:
                if session_id is None or str(UUID(session_id)) != session_id:
                    raise ValueError
            except ValueError:
                raise ValueError(
                    "chat traces require a complete session UUID."
                ) from None
        self.trace_id = str(uuid_factory())
        self.command = command
        self.model = model
        self.api_mode = api_mode
        self.session_id = session_id
        self._pricing = pricing
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._started_at = now()
        self._started = clock()
        self._model_calls: list[ModelCallSpan] = []
        self._tool_calls: list[ToolCallSpan] = []
        self._next_model_sequence = 1
        self._next_tool_sequence = 1
        self._finished = False
        log_event(
            logging.INFO,
            "trace_started",
            trace_id=self.trace_id,
            status="started",
        )

    def start_model_call(self) -> _OpenSpan:
        token = _OpenSpan(
            str(self._uuid_factory()),
            self._next_model_sequence,
            self._clock(),
        )
        self._next_model_sequence += 1
        return token

    def finish_model_call(
        self,
        token: _OpenSpan,
        usage: TokenUsage | None,
        error: Exception | None = None,
    ) -> None:
        duration = self._duration(token.started)
        status = "failed" if error is not None else "succeeded"
        error_type = type(error).__name__ if error is not None else None
        self._model_calls.append(
            ModelCallSpan(
                token.span_id,
                token.sequence,
                duration,
                status,
                error_type,
                usage,
            )
        )
        log_event(
            logging.DEBUG,
            "model_call_finished",
            trace_id=self.trace_id,
            span_id=token.span_id,
            status=status,
            duration_ms=duration,
        )

    def start_tool_call(self, tool_name: str) -> _OpenSpan:
        token = _OpenSpan(
            str(self._uuid_factory()),
            self._next_tool_sequence,
            self._clock(),
            tool_name,
        )
        self._next_tool_sequence += 1
        return token

    def finish_tool_call(
        self,
        token: _OpenSpan,
        *,
        ok: bool,
        error_type: str | None = None,
    ) -> None:
        if token.tool_name is None:
            raise ValueError("Tool span token is invalid.")
        duration = self._duration(token.started)
        status = "succeeded" if ok else "failed"
        normalized_error = None if ok else (error_type or "tool_error")
        self._tool_calls.append(
            ToolCallSpan(
                token.span_id,
                token.sequence,
                token.tool_name,
                duration,
                status,
                normalized_error,
            )
        )
        log_event(
            logging.DEBUG,
            "tool_call_finished",
            trace_id=self.trace_id,
            span_id=token.span_id,
            status=status,
            duration_ms=duration,
        )

    def finish(self, error: Exception | None = None) -> TraceRecord:
        if self._finished:
            raise RuntimeError("Trace recorder is already finished.")
        self._finished = True
        model_calls = tuple(sorted(self._model_calls, key=lambda span: span.sequence))
        tool_calls = tuple(sorted(self._tool_calls, key=lambda span: span.sequence))
        known = [
            span.usage for span in model_calls if span.usage is not None
        ]
        usage = (
            None
            if not known
            else TokenUsage(
                sum(item.input_tokens for item in known),
                sum(item.output_tokens for item in known),
            )
        )
        duration = self._duration(self._started)
        status = "failed" if error is not None else "succeeded"
        record = TraceRecord(
            1,
            self.trace_id,
            self._started_at,
            duration,
            self.command,
            status,
            self.model,
            self.api_mode,
            self.session_id,
            type(error).__name__ if error is not None else None,
            usage,
            estimate_cost(usage, self._pricing) if usage is not None else None,
            model_calls,
            tool_calls,
        )
        log_event(
            logging.INFO,
            "trace_finished",
            trace_id=self.trace_id,
            status=status,
            duration_ms=duration,
        )
        return record

    def _duration(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))
