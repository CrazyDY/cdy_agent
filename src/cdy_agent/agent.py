from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .conversation import Message
from .observability import TraceRecorder
from .openai_client import FinalResponse, ModelResponse
from .run_control import RunControl
from .tools.base import ConfirmationCallback


class AgentLoopLimitError(RuntimeError):
    """Raised when an agent does not finish within its model-call budget."""


class AgentEventSink(Protocol):
    """Receive tool lifecycle status without tool arguments or results."""

    def tool_started(self, name: str) -> None: ...

    def tool_finished(
        self, name: str, ok: bool, error_type: str | None
    ) -> None: ...


def _invalidate_recorder(recorder: TraceRecorder) -> None:
    """Mark a broken recorder unusable without affecting the Agent result."""
    try:
        recorder.invalidate()
    except Exception:
        pass


def _raise_if_cancelled(control: RunControl | None) -> None:
    if control is not None:
        control.raise_if_cancelled()


class Agent:
    """Run a bounded, API-neutral model and tool interaction loop."""

    def __init__(
        self,
        gateway: Any,
        registry: Any,
        confirm: ConfirmationCallback,
        max_model_calls: int = 8,
        system_prompt: str | None = None,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if max_model_calls < 1:
            raise ValueError("max_model_calls must be at least 1.")
        self._gateway = gateway
        self._registry = registry
        self._confirm = confirm
        self._max_model_calls = max_model_calls
        self._system_message = _normalize_system_message(system_prompt)
        self._close_callback = close_callback
        self._closed = False

    def close(self) -> None:
        """Release optional runtime resources exactly once."""
        if self._closed:
            return
        self._closed = True
        if self._close_callback is not None:
            self._close_callback()

    def run(
        self,
        messages: Sequence[Message],
        recorder: TraceRecorder | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: AgentEventSink | None = None,
    ) -> str:
        def create_model_call(**kwargs: object) -> ModelResponse:
            if run_control is not None:
                return self._gateway.create(**kwargs, run_control=run_control)
            return self._gateway.create(**kwargs)

        return self._run_loop(
            messages,
            create_model_call,
            recorder,
            run_control=run_control,
            event_sink=event_sink,
        )

    def run_stream(
        self,
        messages: Sequence[Message],
        on_text: Callable[[str], None],
        recorder: TraceRecorder | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: AgentEventSink | None = None,
    ) -> str:
        def stream_model_call(**kwargs: object) -> ModelResponse:
            if run_control is not None:
                return self._gateway.stream(
                    on_text=on_text, **kwargs, run_control=run_control
                )
            return self._gateway.stream(on_text=on_text, **kwargs)

        return self._run_loop(
            messages,
            stream_model_call,
            recorder,
            run_control=run_control,
            event_sink=event_sink,
        )

    def _run_loop(
        self,
        messages: Sequence[Message],
        model_call: Callable[..., ModelResponse],
        recorder: TraceRecorder | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: AgentEventSink | None = None,
    ) -> str:
        if not messages:
            raise ValueError("Conversation history must not be empty.")

        continuation = None
        outputs: tuple[tuple[str, str], ...] = ()
        active_recorder = recorder
        for _ in range(self._max_model_calls):
            _raise_if_cancelled(run_control)
            model_span = None
            if active_recorder is not None:
                try:
                    model_span = active_recorder.start_model_call()
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            try:
                outcome = model_call(
                    messages=self._messages_with_system_prompt(messages),
                    tools=self._registry.definitions,
                    continuation=continuation,
                    tool_outputs=outputs,
                )
            except Exception as exc:
                if active_recorder is not None and model_span is not None:
                    try:
                        active_recorder.finish_model_call(model_span, None, exc)
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                raise
            _raise_if_cancelled(run_control)
            if active_recorder is not None and model_span is not None:
                try:
                    active_recorder.finish_model_call(model_span, outcome.usage)
                except Exception:
                    _invalidate_recorder(active_recorder)
                    active_recorder = None
            if isinstance(outcome, FinalResponse):
                return outcome.text
            completed_outputs = []
            for call in outcome.calls:
                _raise_if_cancelled(run_control)
                tool_span = None
                if active_recorder is not None:
                    try:
                        tool_span = active_recorder.start_tool_call(call.name)
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                if event_sink is not None:
                    event_sink.tool_started(call.name)
                _raise_if_cancelled(run_control)
                try:
                    if run_control is not None:
                        result = self._registry.execute(
                            call,
                            self._confirm,
                            run_control=run_control,
                        )
                    else:
                        result = self._registry.execute(call, self._confirm)
                except Exception as exc:
                    if active_recorder is not None and tool_span is not None:
                        try:
                            active_recorder.finish_tool_call(
                                tool_span,
                                ok=False,
                                error_type=type(exc).__name__,
                            )
                        except Exception:
                            _invalidate_recorder(active_recorder)
                            active_recorder = None
                    if event_sink is not None:
                        event_sink.tool_finished(
                            call.name,
                            ok=False,
                            error_type=type(exc).__name__,
                        )
                    raise
                if active_recorder is not None and tool_span is not None:
                    try:
                        active_recorder.finish_tool_call(
                            tool_span,
                            ok=result.ok,
                            error_type=None if result.ok else result.code,
                        )
                    except Exception:
                        _invalidate_recorder(active_recorder)
                        active_recorder = None
                if event_sink is not None:
                    event_sink.tool_finished(
                        call.name,
                        ok=result.ok,
                        error_type=None if result.ok else result.code,
                    )
                _raise_if_cancelled(run_control)
                completed_outputs.append((call.call_id, result.to_json()))
            outputs = tuple(completed_outputs)
            continuation = outcome.continuation

        raise AgentLoopLimitError(
            f"Agent exceeded the maximum of {self._max_model_calls} model calls."
        )

    def _messages_with_system_prompt(
        self, messages: Sequence[Message]
    ) -> tuple[Message, ...]:
        current = tuple(messages)
        if self._system_message is None:
            return current
        if current and current[0].role == "system":
            return (self._system_message, *current[1:])
        return (self._system_message, *current)


def _normalize_system_message(system_prompt: str | None) -> Message | None:
    if system_prompt is None:
        return None
    normalized_prompt = system_prompt.strip()
    if not normalized_prompt:
        return None
    return Message("system", normalized_prompt)
