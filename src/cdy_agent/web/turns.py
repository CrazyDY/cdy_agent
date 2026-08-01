"""Coordinate one authenticated, streaming Web Agent turn at a time."""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from cdy_agent.agent import AgentEventSink
from cdy_agent.conversation import Conversation, Message
from cdy_agent.memory import ConversationStore, ConversationSummary
from cdy_agent.observability import Pricing, TraceRecorder, TraceStore
from cdy_agent.run_control import AgentRunCancelled, RunControl
from cdy_agent.tools.base import ConfirmationDecision, ConfirmationRequest
from cdy_agent.web.errors import map_web_error
from cdy_agent.web.schemas import (
    ApprovalRequired,
    ApprovalResolve,
    AssistantDelta,
    ConversationSummaryResponse,
    ServerEvent,
    ToolStatus,
    TurnAccepted,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStart,
)
from cdy_agent.web.sessions import summary_response

_SAFE_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}\Z")


class ServerBusyError(RuntimeError):
    """Raised when an attempt is made to start a second concurrent Web turn."""


class StreamingAgent(Protocol):
    """The streaming portion of the Agent boundary used by Web turns."""

    def run_stream(
        self,
        messages: Sequence[Message],
        on_text: Callable[[str], None],
        recorder: TraceRecorder | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: AgentEventSink | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class TurnDependencies:
    """Startup-resolved dependencies retained for all Web turns."""

    agent: StreamingAgent
    confirmations: ConfirmationBroker
    conversations: ConversationStore
    traces: TraceStore | None
    model: str
    api_mode: str
    pricing: Pricing | None


@dataclass
class _PendingApproval:
    approval_id: str
    request: ConfirmationRequest
    decision: ConfirmationDecision | None = None


class ConfirmationBroker:
    """Bridge synchronous tool confirmation callbacks to an active Web turn."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._coordinator: TurnCoordinator | None = None
        self._active: ActiveTurn | None = None

    def attach(self, coordinator: TurnCoordinator) -> None:
        """Attach the one coordinator that owns this process-wide broker."""
        with self._lock:
            if self._coordinator is not None:
                raise RuntimeError("Confirmation broker is already attached.")
            self._coordinator = coordinator

    def activate(self, turn: ActiveTurn) -> None:
        """Make an accepted turn available to its synchronous Agent callback."""
        with self._lock:
            if self._active is not None:
                raise RuntimeError("A confirmation turn is already active.")
            self._active = turn

    def deactivate(self, turn: ActiveTurn) -> None:
        """Remove a terminated turn after its worker cleanup has completed."""
        with self._lock:
            if self._active is turn:
                self._active = None

    def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Publish one approval request and wait for its exact browser decision."""
        with self._lock:
            turn = self._active
        if turn is None:
            raise AgentRunCancelled("Agent run was cancelled.")
        return turn._confirm(request)


class _WebEventSink:
    """Publish sanitized tool lifecycle signals from the Agent worker."""

    def __init__(self, turn: ActiveTurn) -> None:
        self._turn = turn

    def tool_started(self, name: str) -> None:
        self._publish(name, "started")

    def tool_finished(
        self, name: str, ok: bool, error_type: str | None
    ) -> None:
        self._publish(name, "finished")

    def _publish(self, name: str, phase: str) -> None:
        safe_name = name if _SAFE_TOOL_NAME.fullmatch(name) else "tool"
        self._turn._publish(
            ToolStatus(
                type="tool.status",
                turn_id=self._turn.turn_id,
                name=safe_name,
                phase=phase,  # type: ignore[arg-type]
                label=f"Tool {phase}",
            )
        )


class ActiveTurn:
    """One worker-backed turn and its async event and confirmation bridges."""

    def __init__(
        self,
        request: TurnStart,
        dependencies: TurnDependencies,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.turn_id = str(uuid4())
        self.session_id = request.session_id or str(uuid4())
        self._request = request
        self._dependencies = dependencies
        self._loop = loop
        self._events: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self._run_control = RunControl()
        self._approval_condition = threading.Condition()
        self._pending_approval: _PendingApproval | None = None
        self._terminal_lock = threading.Lock()
        self._terminal_emitted = False
        self._stopped_task: asyncio.Task[None] | None = None

    @classmethod
    def create(
        cls,
        request: TurnStart,
        dependencies: TurnDependencies,
        loop: asyncio.AbstractEventLoop,
    ) -> ActiveTurn:
        """Allocate a new ephemeral or resumed conversation turn."""
        return cls(request, dependencies, loop)

    def start(
        self, on_stopped: Callable[[ActiveTurn], Awaitable[None]]
    ) -> None:
        """Emit acceptance before starting the blocking streaming worker."""
        self._events.put_nowait(
            TurnAccepted(
                type="turn.accepted", turn_id=self.turn_id, session_id=self.session_id
            )
        )
        self._stopped_task = asyncio.create_task(self._run_and_stop(on_stopped))

    async def next_event(self) -> ServerEvent:
        """Wait for the next ordered server event for this turn."""
        return await self._events.get()

    def resolve_approval(self, event: ApprovalResolve) -> None:
        """Resolve only the exact, still-pending approval once."""
        if event.turn_id != self.turn_id:
            raise ValueError("Approval does not belong to this turn.")
        with self._approval_condition:
            pending = self._pending_approval
            if pending is None or pending.approval_id != event.approval_id:
                raise ValueError("Approval is not pending.")
            if pending.decision is not None:
                raise ValueError("Approval was already resolved.")
            if (
                event.decision is ConfirmationDecision.ALLOW_ALWAYS
                and not pending.request.allow_always
            ):
                raise ValueError("Always-allow is not available for this approval.")
            pending.decision = event.decision
            self._approval_condition.notify_all()

    def cancel(self) -> None:
        """Cooperatively cancel model, process, and confirmation work."""
        self._run_control.cancel()
        self._wake_confirmation()

    async def wait_stopped(self) -> None:
        """Wait until worker termination and coordinator cleanup are complete."""
        if self._stopped_task is not None:
            await asyncio.shield(self._stopped_task)

    async def _run_and_stop(
        self, on_stopped: Callable[[ActiveTurn], Awaitable[None]]
    ) -> None:
        worker = asyncio.create_task(asyncio.to_thread(self._run_worker))
        try:
            terminal = await self._wait_for_worker(worker)
        except Exception as error:  # noqa: BLE001 - boundary emits a safe error only
            mapped = map_web_error(error)
            terminal = TurnFailed(
                type="turn.failed",
                turn_id=self.turn_id,
                code=mapped.code,
                message=mapped.message,
                retryable=mapped.retryable,
            )
        finally:
            self._clear_pending_approval()
            await self._wait_for_cleanup(on_stopped)
        self._emit_terminal(terminal)

    async def _wait_for_worker(
        self,
        worker: asyncio.Task[TurnCompleted | TurnFailed | TurnCancelled],
    ) -> TurnCompleted | TurnFailed | TurnCancelled:
        while True:
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                self.cancel()

    async def _wait_for_cleanup(
        self, on_stopped: Callable[[ActiveTurn], Awaitable[None]]
    ) -> None:
        cleanup = asyncio.create_task(on_stopped(self))
        while True:
            try:
                await asyncio.shield(cleanup)
                return
            except asyncio.CancelledError:
                self.cancel()

    def _run_worker(self) -> TurnCompleted | TurnFailed | TurnCancelled:
        recorder = self._new_recorder()
        try:
            self._run_control.raise_if_cancelled()
            history: tuple[Message, ...] = ()
            if self._request.session_id is not None:
                stored = self._dependencies.conversations.load(self.session_id)
                history = stored.messages
            conversation = Conversation()
            user = conversation.append("user", self._request.prompt)
            reply = self._dependencies.agent.run_stream(
                (*history, user),
                self._on_text,
                recorder,
                run_control=self._run_control,
                event_sink=_WebEventSink(self),
            )
            self._run_control.raise_if_cancelled()
            assistant = conversation.append("assistant", reply)
            self._run_control.raise_if_cancelled()
            summary = self._dependencies.conversations.append_turn(
                self.session_id, user, assistant
            )
            self._finish_trace(recorder)
            return TurnCompleted(
                type="turn.completed",
                turn_id=self.turn_id,
                assistant_message=assistant.content,
                conversation=self._summary_response(summary),
            )
        except AgentRunCancelled as error:
            self._finish_trace(recorder, error)
            return TurnCancelled(type="turn.cancelled", turn_id=self.turn_id)
        except Exception as error:  # noqa: BLE001 - boundary emits a safe error only
            self._finish_trace(recorder, error)
            mapped = map_web_error(error)
            if self._run_control.cancelled:
                return TurnCancelled(type="turn.cancelled", turn_id=self.turn_id)
            return TurnFailed(
                type="turn.failed",
                turn_id=self.turn_id,
                code=mapped.code,
                message=mapped.message,
                retryable=mapped.retryable,
            )

    def _new_recorder(self) -> TraceRecorder | None:
        try:
            return TraceRecorder(
                "chat",
                self._dependencies.model,
                self._dependencies.api_mode,
                session_id=self.session_id,
                pricing=self._dependencies.pricing,
            )
        except Exception:  # noqa: BLE001 - tracing is strictly best effort
            return None

    def _finish_trace(
        self, recorder: TraceRecorder | None, error: Exception | None = None
    ) -> None:
        if recorder is None or self._dependencies.traces is None:
            return
        try:
            record = recorder.finish(error)
            self._dependencies.traces.append(record)
        except Exception:  # noqa: BLE001 - tracing is strictly best effort
            return

    @staticmethod
    def _summary_response(summary: ConversationSummary) -> ConversationSummaryResponse:
        return summary_response(summary)

    def _on_text(self, delta: str) -> None:
        if delta:
            self._publish(
                AssistantDelta(
                    type="assistant.delta", turn_id=self.turn_id, delta=delta
                )
            )

    def _confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        self._run_control.raise_if_cancelled()
        pending = _PendingApproval(str(uuid4()), request)
        with self._approval_condition:
            self._run_control.raise_if_cancelled()
            if self._pending_approval is not None:
                raise RuntimeError("Another approval is already pending.")
            self._pending_approval = pending
            self._publish(
                ApprovalRequired(
                    type="approval.required",
                    turn_id=self.turn_id,
                    approval_id=pending.approval_id,
                    description=request.description,
                    allow_always=request.allow_always,
                )
            )
            while pending.decision is None:
                self._run_control.raise_if_cancelled()
                self._approval_condition.wait(timeout=0.05)
            self._pending_approval = None
            return pending.decision

    def _wake_confirmation(self) -> None:
        with self._approval_condition:
            self._approval_condition.notify_all()

    def _clear_pending_approval(self) -> None:
        with self._approval_condition:
            self._pending_approval = None
            self._approval_condition.notify_all()

    def _publish(self, event: ServerEvent) -> None:
        try:
            self._loop.call_soon_threadsafe(self._events.put_nowait, event)
        except RuntimeError:
            return

    def _emit_terminal(self, event: TurnCompleted | TurnFailed | TurnCancelled) -> None:
        with self._terminal_lock:
            if self._terminal_emitted:
                return
            self._terminal_emitted = True
        self._publish(event)


class TurnCoordinator:
    """Own process-wide active-turn state without holding a lock during work."""

    def __init__(self, dependencies: TurnDependencies) -> None:
        self._dependencies = dependencies
        self._active: ActiveTurn | None = None
        self._state_lock = asyncio.Lock()
        dependencies.confirmations.attach(self)

    @property
    def busy(self) -> bool:
        """Whether the one global worker remains live or is still cleaning up."""
        return self._active is not None

    async def start(self, request: TurnStart) -> ActiveTurn:
        """Accept one request, or reject it while an earlier worker is active."""
        async with self._state_lock:
            if self._active is not None:
                raise ServerBusyError("Another turn is already running.")
            turn = ActiveTurn.create(
                request, self._dependencies, asyncio.get_running_loop()
            )
            self._active = turn
            try:
                self._dependencies.confirmations.activate(turn)
                turn.start(self._clear_when_stopped)
            except Exception:
                self._dependencies.confirmations.deactivate(turn)
                self._active = None
                raise
            return turn

    async def _clear_when_stopped(self, turn: ActiveTurn) -> None:
        async with self._state_lock:
            if self._active is turn:
                self._dependencies.confirmations.deactivate(turn)
                self._active = None
