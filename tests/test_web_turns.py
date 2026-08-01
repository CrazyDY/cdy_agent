from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest

from cdy_agent.conversation import Message
from cdy_agent.memory import ConversationStore, ConversationStoreError
from cdy_agent.run_control import RunControl
from cdy_agent.tools.base import ConfirmationDecision, ConfirmationRequest
from cdy_agent.web.schemas import ApprovalResolve, TurnStart
from cdy_agent.web.turns import (
    ConfirmationBroker,
    ServerBusyError,
    TurnCoordinator,
    TurnDependencies,
)


@dataclass
class FakeStreamingAgent:
    chunks: tuple[str, ...] = ()
    reply: str = "Hello"
    histories: list[tuple[Message, ...]] = field(default_factory=list)

    def run_stream(
        self,
        messages: tuple[Message, ...],
        on_text: object,
        recorder: object | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: object | None = None,
    ) -> str:
        self.histories.append(messages)
        assert callable(on_text)
        for chunk in self.chunks:
            if run_control is not None:
                run_control.raise_if_cancelled()
            on_text(chunk)
        return self.reply


class ConfirmingAgent:
    def __init__(self, confirm: object, *, allow_always: bool) -> None:
        self._confirm = confirm
        self.allow_always = allow_always
        self.decisions: list[ConfirmationDecision] = []

    def run_stream(
        self,
        messages: tuple[Message, ...],
        on_text: object,
        recorder: object | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: object | None = None,
    ) -> str:
        assert callable(self._confirm)
        decision = self._confirm(
            ConfirmationRequest(
                tool_name="shell",
                arguments={"private": "do-not-send"},
                description="Run the requested command?",
                allow_always=self.allow_always,
            )
        )
        assert isinstance(decision, ConfirmationDecision)
        self.decisions.append(decision)
        return "confirmed"


class CancellationBlockingAgent:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def run_stream(
        self,
        messages: tuple[Message, ...],
        on_text: object,
        recorder: object | None = None,
        *,
        run_control: RunControl | None = None,
        event_sink: object | None = None,
    ) -> str:
        self.started.set()
        self.release.wait(timeout=2)
        assert run_control is not None
        run_control.raise_if_cancelled()
        return "unreachable"


class RecordingConversationStore(ConversationStore):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.actions: list[str] = []

    def append_turn(self, session_id: str, user: Message, assistant: Message) -> object:
        summary = super().append_turn(session_id, user, assistant)
        self.actions.append("append_turn")
        return summary


class FailingConversationStore:
    def load(self, session_id: str) -> object:
        raise AssertionError("A new turn must not load history.")

    def append_turn(self, session_id: str, user: Message, assistant: Message) -> None:
        raise ConversationStoreError("private persistence detail")

    def list_summaries(self) -> tuple[object, ...]:
        return ()


class FailingTraceStore:
    def append(self, record: object) -> None:
        raise RuntimeError("private trace detail")


async def collect_until_terminal(turn: object) -> list[object]:
    events = []
    while True:
        event = await turn.next_event()
        events.append(event)
        if event.type in {"turn.completed", "turn.failed", "turn.cancelled"}:
            return events


def make_coordinator(
    *,
    agent: object,
    conversations: object,
    confirmations: ConfirmationBroker | None = None,
    traces: object | None = None,
) -> TurnCoordinator:
    broker = confirmations or ConfirmationBroker()
    return TurnCoordinator(
        TurnDependencies(
            agent=agent,
            confirmations=broker,
            conversations=conversations,
            traces=traces,
            model="test-model",
            api_mode="responses",
            pricing=None,
        )
    )


def turn_start(prompt: str = "Hello", session_id: str | None = None) -> TurnStart:
    return TurnStart(type="turn.start", prompt=prompt, session_id=session_id)


def test_turn_module_starts_with_a_completed_stream_persisted_before_terminal(
    tmp_path: Path,
) -> None:
    """Dropping persistence ordering would expose a completed but unsaved reply."""

    async def scenario() -> None:
        store = RecordingConversationStore(tmp_path)
        coordinator = make_coordinator(
            agent=FakeStreamingAgent(("Hel", "lo")), conversations=store
        )

        turn = await coordinator.start(turn_start("Hi"))
        events = await collect_until_terminal(turn)

        assert [event.type for event in events] == [
            "turn.accepted",
            "assistant.delta",
            "assistant.delta",
            "turn.completed",
        ]
        assert events[-1].assistant_message == "Hello"
        assert store.actions == ["append_turn"]
        assert store.load(turn.session_id).messages == (
            Message("user", "Hi"),
            Message("assistant", "Hello"),
        )
        assert events[-1].conversation.message_count == 2
        await turn.wait_stopped()
        assert coordinator.busy is False

    asyncio.run(scenario())


def test_resumed_turn_uses_exact_stored_history_before_the_new_message(tmp_path: Path) -> None:
    """Replacing stored history with a summary would lose model context."""

    async def scenario() -> None:
        store = RecordingConversationStore(tmp_path)
        session_id = str(uuid4())
        store.append_turn(session_id, Message("user", "old"), Message("assistant", "reply"))
        agent = FakeStreamingAgent(reply="new reply")
        coordinator = make_coordinator(agent=agent, conversations=store)

        events = await collect_until_terminal(
            await coordinator.start(turn_start("new", session_id))
        )

        assert events[-1].type == "turn.completed"
        assert agent.histories == [
            (
                Message("user", "old"),
                Message("assistant", "reply"),
                Message("user", "new"),
            )
        ]
        assert [item.content for item in store.load(session_id).messages] == [
            "old",
            "reply",
            "new",
            "new reply",
        ]

    asyncio.run(scenario())


def test_resumed_turn_preserves_stored_message_whitespace(tmp_path: Path) -> None:
    """Re-normalizing persisted history would silently alter the model context."""

    async def scenario() -> None:
        store = RecordingConversationStore(tmp_path)
        session_id = str(uuid4())
        store.append_turn(
            session_id,
            Message("user", " leading and trailing "),
            Message("assistant", " reply with spaces "),
        )
        agent = FakeStreamingAgent(reply="next")
        coordinator = make_coordinator(agent=agent, conversations=store)

        await collect_until_terminal(await coordinator.start(turn_start("new", session_id)))

        assert agent.histories == [
            (
                Message("user", " leading and trailing "),
                Message("assistant", " reply with spaces "),
                Message("user", "new"),
            )
        ]

    asyncio.run(scenario())


def test_completed_turn_is_not_blocked_by_an_unrelated_corrupt_summary(
    tmp_path: Path,
) -> None:
    """A post-commit global summary scan could report failure after saving a turn."""

    async def scenario() -> None:
        store = RecordingConversationStore(tmp_path)
        corrupt_id = str(uuid4())
        store.append_turn(corrupt_id, Message("user", "old"), Message("assistant", "reply"))
        database = tmp_path / ".cdy-agent" / "cdy-agent.sqlite3"
        import sqlite3

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE messages SET role = 'user' WHERE session_id = ? AND sequence = 1",
                (corrupt_id,),
            )
        coordinator = make_coordinator(agent=FakeStreamingAgent(), conversations=store)

        events = await collect_until_terminal(await coordinator.start(turn_start("new")))

        assert events[-1].type == "turn.completed"
        assert events[-1].conversation.message_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "decision",
    [
        ConfirmationDecision.DENY,
        ConfirmationDecision.ALLOW_ONCE,
        ConfirmationDecision.ALLOW_ALWAYS,
    ],
)
def test_confirmation_returns_the_exact_allowed_decision(
    tmp_path: Path, decision: ConfirmationDecision
) -> None:
    """Coercing decisions would widen or ignore the tool confirmation policy."""

    async def scenario() -> None:
        broker = ConfirmationBroker()
        agent = ConfirmingAgent(broker.confirm, allow_always=True)
        coordinator = make_coordinator(
            agent=agent, conversations=RecordingConversationStore(tmp_path), confirmations=broker
        )
        turn = await coordinator.start(turn_start())
        required = await turn.next_event()
        assert required.type == "turn.accepted"
        required = await turn.next_event()
        assert required.type == "approval.required"
        assert required.description == "Run the requested command?"
        assert required.allow_always is True
        assert not hasattr(required, "arguments")

        turn.resolve_approval(
            ApprovalResolve(
                type="approval.resolve",
                turn_id=turn.turn_id,
                approval_id=required.approval_id,
                decision=decision,
            )
        )
        events = await collect_until_terminal(turn)

        assert events[-1].type == "turn.completed"
        assert agent.decisions == [decision]

    asyncio.run(scenario())


def test_approval_rejects_stale_double_and_disallowed_always_decisions(tmp_path: Path) -> None:
    """Accepting a stale or widened approval could execute a different command."""

    async def scenario() -> None:
        broker = ConfirmationBroker()
        agent = ConfirmingAgent(broker.confirm, allow_always=False)
        coordinator = make_coordinator(
            agent=agent, conversations=RecordingConversationStore(tmp_path), confirmations=broker
        )
        turn = await coordinator.start(turn_start())
        await turn.next_event()
        required = await turn.next_event()

        with pytest.raises(ValueError):
            turn.resolve_approval(
                ApprovalResolve(
                    type="approval.resolve",
                    turn_id=str(uuid4()),
                    approval_id=required.approval_id,
                    decision=ConfirmationDecision.ALLOW_ONCE,
                )
            )
        with pytest.raises(ValueError):
            turn.resolve_approval(
                ApprovalResolve(
                    type="approval.resolve",
                    turn_id=turn.turn_id,
                    approval_id=required.approval_id,
                    decision=ConfirmationDecision.ALLOW_ALWAYS,
                )
            )

        resolution = ApprovalResolve(
            type="approval.resolve",
            turn_id=turn.turn_id,
            approval_id=required.approval_id,
            decision=ConfirmationDecision.ALLOW_ONCE,
        )
        turn.resolve_approval(resolution)
        with pytest.raises(ValueError):
            turn.resolve_approval(resolution)
        assert (await collect_until_terminal(turn))[-1].type == "turn.completed"

    asyncio.run(scenario())


def test_cancel_during_confirmation_wakes_worker_without_persisting(tmp_path: Path) -> None:
    """A sleeping confirmation callback would retain the active lock forever."""

    async def scenario() -> None:
        broker = ConfirmationBroker()
        store = RecordingConversationStore(tmp_path)
        coordinator = make_coordinator(
            agent=ConfirmingAgent(broker.confirm, allow_always=True),
            conversations=store,
            confirmations=broker,
        )
        turn = await coordinator.start(turn_start())
        await turn.next_event()
        required = await turn.next_event()

        turn.cancel()
        events = await collect_until_terminal(turn)
        await turn.wait_stopped()

        assert required.type == "approval.required"
        assert events == [events[0]]
        assert events[0].type == "turn.cancelled"
        assert store.list_summaries() == ()
        assert coordinator.busy is False

    asyncio.run(scenario())


def test_cancel_keeps_server_busy_until_the_worker_has_terminated(tmp_path: Path) -> None:
    """Clearing busy on request would allow a second worker to race the first."""

    async def scenario() -> None:
        agent = CancellationBlockingAgent()
        coordinator = make_coordinator(agent=agent, conversations=RecordingConversationStore(tmp_path))
        turn = await coordinator.start(turn_start())
        await asyncio.to_thread(agent.started.wait, 1)

        turn.cancel()
        assert coordinator.busy is True
        agent.release.set()
        events = await collect_until_terminal(turn)
        await turn.wait_stopped()

        assert events[-1].type == "turn.cancelled"
        assert coordinator.busy is False

    asyncio.run(scenario())


def test_cancelling_the_supervisor_keeps_busy_until_the_worker_exits(tmp_path: Path) -> None:
    """Cancelling the asyncio supervisor must not abandon its worker thread."""

    async def scenario() -> None:
        agent = CancellationBlockingAgent()
        coordinator = make_coordinator(agent=agent, conversations=RecordingConversationStore(tmp_path))
        turn = await coordinator.start(turn_start())
        await asyncio.to_thread(agent.started.wait, 1)
        assert turn._stopped_task is not None

        turn._stopped_task.cancel()
        await asyncio.sleep(0)

        assert coordinator.busy is True
        agent.release.set()
        await turn.wait_stopped()
        assert coordinator.busy is False

    asyncio.run(scenario())


def test_persistence_failure_never_emits_completed_or_saves_partial_turn() -> None:
    """Publishing completion after a failed append would make later reloads inconsistent."""

    async def scenario() -> None:
        coordinator = make_coordinator(
            agent=FakeStreamingAgent(reply="unsaved"), conversations=FailingConversationStore()
        )
        turn = await coordinator.start(turn_start())
        events = await collect_until_terminal(turn)

        assert [event.type for event in events] == ["turn.accepted", "turn.failed"]
        assert events[-1].code == "conversation_store_error"
        assert "private persistence detail" not in events[-1].message
        await turn.wait_stopped()
        assert coordinator.busy is False

    asyncio.run(scenario())


def test_trace_write_failure_does_not_change_a_successful_turn(tmp_path: Path) -> None:
    """Observability failures must not turn a completed Agent result into an error."""

    async def scenario() -> None:
        store = RecordingConversationStore(tmp_path)
        coordinator = make_coordinator(
            agent=FakeStreamingAgent(reply="saved"),
            conversations=store,
            traces=FailingTraceStore(),
        )
        turn = await coordinator.start(turn_start())
        events = await collect_until_terminal(turn)

        assert events[-1].type == "turn.completed"
        assert store.load(turn.session_id).messages[-1] == Message("assistant", "saved")

    asyncio.run(scenario())


def test_second_start_is_rejected_while_the_first_turn_is_active(tmp_path: Path) -> None:
    """Dropping the process-wide guard would permit concurrent workspace mutation."""

    async def scenario() -> None:
        agent = CancellationBlockingAgent()
        coordinator = make_coordinator(agent=agent, conversations=RecordingConversationStore(tmp_path))
        first = await coordinator.start(turn_start())
        await asyncio.to_thread(agent.started.wait, 1)

        with pytest.raises(ServerBusyError):
            await coordinator.start(turn_start("second"))

        first.cancel()
        agent.release.set()
        await first.wait_stopped()

    asyncio.run(scenario())


def test_terminal_event_is_delivered_only_after_the_active_turn_is_cleared(
    tmp_path: Path,
) -> None:
    """A terminal event observed before cleanup would make an immediate retry fail."""

    async def scenario() -> None:
        coordinator = make_coordinator(
            agent=FakeStreamingAgent(), conversations=RecordingConversationStore(tmp_path)
        )
        first = await coordinator.start(turn_start())
        events = await collect_until_terminal(first)

        assert events[-1].type == "turn.completed"
        assert coordinator.busy is False
        second = await coordinator.start(turn_start("second"))
        second.cancel()
        await second.wait_stopped()

    asyncio.run(scenario())
