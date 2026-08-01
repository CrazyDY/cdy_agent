from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from cdy_agent.tools.base import ConfirmationDecision
from cdy_agent.web.auth import BrowserCapability
from cdy_agent.web.schemas import (
    ApprovalResolve,
    AssistantDelta,
    TurnAccepted,
    TurnCancelled,
    TurnCompleted,
)
from cdy_agent.web.socket import _send_turn_events, register_turn_socket
from cdy_agent.web.turns import ServerBusyError


@dataclass
class _CompletedTurn:
    turn_id: str
    session_id: str

    def __post_init__(self) -> None:
        self._events = asyncio.Queue()
        self._events.put_nowait(
            TurnAccepted(
                type="turn.accepted", turn_id=self.turn_id, session_id=self.session_id
            )
        )
        self._events.put_nowait(
            AssistantDelta(type="assistant.delta", turn_id=self.turn_id, delta="Hi")
        )
        self._events.put_nowait(
            TurnCompleted(
                type="turn.completed",
                turn_id=self.turn_id,
                assistant_message="Hi",
                conversation={
                    "id": self.session_id,
                    "updated_at": "2026-08-01T00:00:00+00:00",
                    "message_count": 2,
                    "preview": "Hi",
                },
            )
        )

    async def next_event(self) -> object:
        return await self._events.get()

    def cancel(self) -> None:
        return None

    async def wait_stopped(self) -> None:
        return None


class _Coordinator:
    def __init__(self) -> None:
        self.started = []

    async def start(self, request: object) -> _CompletedTurn:
        self.started.append(request)
        return _CompletedTurn(str(uuid4()), str(uuid4()))


class _WaitingTurn:
    def __init__(self) -> None:
        self.turn_id = str(uuid4())
        self.session_id = str(uuid4())
        self._events: asyncio.Queue[object] = asyncio.Queue()
        self._events.put_nowait(
            TurnAccepted(
                type="turn.accepted", turn_id=self.turn_id, session_id=self.session_id
            )
        )
        self.cancel_calls = 0
        self.wait_stopped_calls = 0
        self.approvals: list[ApprovalResolve] = []
        self.approval_id = str(uuid4())
        self._cancelled = False

    async def next_event(self) -> object:
        return await self._events.get()

    def cancel(self) -> None:
        self.cancel_calls += 1
        if not self._cancelled:
            self._cancelled = True
            self._events.put_nowait(
                TurnCancelled(type="turn.cancelled", turn_id=self.turn_id)
            )

    async def wait_stopped(self) -> None:
        self.wait_stopped_calls += 1

    def resolve_approval(self, event: ApprovalResolve) -> None:
        if event.approval_id != self.approval_id:
            raise ValueError("Approval is not pending.")
        self.approvals.append(event)


class _SingleTurnCoordinator:
    def __init__(self, turn: _WaitingTurn) -> None:
        self.turn = turn
        self.started = []

    async def start(self, request: object) -> _WaitingTurn:
        self.started.append(request)
        if len(self.started) > 1:
            raise ServerBusyError()
        return self.turn


class _FakeWebSocket:
    def __init__(self, messages: list[object], *, origin: str = "http://127.0.0.1:8000") -> None:
        self.headers = Headers(
            {
                "host": "127.0.0.1:8000",
                "origin": origin,
                "cookie": "cdy_agent_web=fixed-secret",
            }
        )
        self._messages = asyncio.Queue()
        for message in messages:
            self._messages.put_nowait(message)
        self.sent: list[dict[str, object]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> object:
        await asyncio.sleep(0)
        message = await self._messages.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)


async def _run_socket_handler(
    websocket: _FakeWebSocket, coordinator: object
) -> None:
    capability = BrowserCapability.from_secret("fixed-secret", "127.0.0.1", 8000)
    app = FastAPI()
    register_turn_socket(app, capability, coordinator)  # type: ignore[arg-type]
    await app.routes[-1].endpoint(websocket)  # type: ignore[union-attr]


def _websocket_client(coordinator: object) -> TestClient:
    capability = BrowserCapability.from_secret("fixed-secret", "127.0.0.1", 8000)
    app = FastAPI()
    register_turn_socket(app, capability, coordinator)  # type: ignore[arg-type]
    client = TestClient(app)
    client.cookies.set("cdy_agent_web", "fixed-secret")
    return client


def _socket(client: TestClient):
    return client.websocket_connect(
        "/api/turns",
        headers={
            "host": "127.0.0.1:8000",
            "origin": "http://127.0.0.1:8000",
        },
    )


def test_start_sends_ordered_turn_events() -> None:
    """Dropping the sender or reordering its queue would corrupt streamed replies."""
    coordinator = _Coordinator()
    with _websocket_client(coordinator) as client, _socket(client) as socket:
        socket.send_json({"type": "turn.start", "prompt": "hello"})
        events = [socket.receive_json() for _ in range(3)]

    assert [event["type"] for event in events] == [
        "turn.accepted",
        "assistant.delta",
        "turn.completed",
    ]
    assert coordinator.started[0].prompt == "hello"


def test_protocol_errors_do_not_start_or_cancel_a_turn() -> None:
    """Parsing unknown input before dispatch prevents malformed data doing work."""
    turn = _WaitingTurn()
    coordinator = _SingleTurnCoordinator(turn)

    websocket = _FakeWebSocket(
        [
            {"type": "turn.start", "prompt": "hello", "extra": True},
            {"type": "not-a-real-event"},
            {"type": "turn.start", "prompt": "hello"},
            {"type": "turn.cancel", "turn_id": turn.turn_id},
            WebSocketDisconnect(),
        ]
    )

    asyncio.run(_run_socket_handler(websocket, coordinator))

    assert turn.cancel_calls == 1
    assert coordinator.started[0].prompt == "hello"
    assert [item["type"] for item in websocket.sent] == [
        "protocol.error",
        "protocol.error",
        "turn.accepted",
        "turn.cancelled",
    ]


def test_busy_cancel_and_approval_require_the_current_exact_turn() -> None:
    """Stale IDs must not cancel or approve an active tool request."""
    turn = _WaitingTurn()
    coordinator = _SingleTurnCoordinator(turn)

    websocket = _FakeWebSocket(
        [
            {"type": "turn.start", "prompt": "hello"},
            {"type": "turn.start", "prompt": "second"},
            {"type": "turn.cancel", "turn_id": str(uuid4())},
            {
                "type": "approval.resolve",
                "turn_id": turn.turn_id,
                "approval_id": str(uuid4()),
                "decision": "allow_once",
            },
            {
                "type": "approval.resolve",
                "turn_id": turn.turn_id,
                "approval_id": turn.approval_id,
                "decision": "deny",
            },
            {"type": "turn.cancel", "turn_id": turn.turn_id},
            WebSocketDisconnect(),
        ]
    )

    asyncio.run(_run_socket_handler(websocket, coordinator))

    assert turn.cancel_calls == 1
    assert len(turn.approvals) == 1
    assert turn.approvals[0].turn_id == turn.turn_id
    assert turn.approvals[0].decision is ConfirmationDecision.DENY
    assert [item["type"] for item in websocket.sent] == [
        "turn.accepted",
        "server.busy",
        "protocol.error",
        "protocol.error",
        "turn.cancelled",
    ]


def test_disconnect_cancels_and_waits_for_the_active_worker() -> None:
    """Closing a browser tab must not leave its Agent worker or lock alive."""
    turn = _WaitingTurn()
    coordinator = _SingleTurnCoordinator(turn)

    websocket = _FakeWebSocket(
        [{"type": "turn.start", "prompt": "hello"}, WebSocketDisconnect()]
    )

    asyncio.run(_run_socket_handler(websocket, coordinator))

    assert turn.cancel_calls == 1
    assert turn.wait_stopped_calls == 1


def test_wrong_origin_is_rejected_before_the_socket_is_accepted() -> None:
    """Relaxing upgrade authentication would let a web page drive local tools."""
    coordinator = _Coordinator()
    websocket = _FakeWebSocket([], origin="https://attacker.test")

    with pytest.raises(Exception) as error:
        asyncio.run(_run_socket_handler(websocket, coordinator))

    assert getattr(error.value, "code", None) == 1008
    assert websocket.accepted is False
    assert coordinator.started == []


def test_send_failure_cancels_and_waits_for_the_worker() -> None:
    """A failed sender must not leave a live worker after its browser is gone."""

    class FailingWebSocket:
        async def send_json(self, payload: object) -> None:
            raise RuntimeError("disconnected")

    async def scenario() -> None:
        turn = _WaitingTurn()
        with pytest.raises(RuntimeError, match="disconnected"):
            await _send_turn_events(FailingWebSocket(), turn)  # type: ignore[arg-type]
        assert turn.cancel_calls == 1
        assert turn.wait_stopped_calls == 1

    asyncio.run(scenario())


def test_binary_frame_protocol_error_keeps_the_socket_alive_and_cleans_up() -> None:
    """A binary-frame KeyError must not bypass cancellation of the active worker."""
    turn = _WaitingTurn()
    coordinator = _SingleTurnCoordinator(turn)
    websocket = _FakeWebSocket(
        [
            {"type": "turn.start", "prompt": "hello"},
            KeyError("text"),
            {"type": "turn.cancel", "turn_id": turn.turn_id},
            WebSocketDisconnect(),
        ]
    )

    async def scenario() -> None:
        await asyncio.wait_for(_run_socket_handler(websocket, coordinator), timeout=0.5)

    asyncio.run(scenario())

    assert [event["type"] for event in websocket.sent] == [
        "turn.accepted",
        "protocol.error",
        "turn.cancelled",
    ]
    assert turn.cancel_calls == 1


def test_unexpected_receive_failure_cleans_up_the_active_worker() -> None:
    """A receive failure must not prevent finally from stopping active work."""
    turn = _WaitingTurn()
    coordinator = _SingleTurnCoordinator(turn)
    websocket = _FakeWebSocket(
        [{"type": "turn.start", "prompt": "hello"}, RuntimeError("receive failed")]
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="receive failed"):
            await asyncio.wait_for(_run_socket_handler(websocket, coordinator), timeout=0.5)

    asyncio.run(scenario())

    assert turn.cancel_calls == 1
    assert turn.wait_stopped_calls == 1


def test_new_start_waits_for_the_previous_sender_to_finish() -> None:
    """Replacing a live sender could make two tasks write one WebSocket at once."""
    old_turn = _WaitingTurn()
    replacement_turn = _WaitingTurn()

    class ClearedCoordinator:
        def __init__(self) -> None:
            self.started: list[object] = []

        async def start(self, request: object) -> _WaitingTurn:
            self.started.append(request)
            return old_turn if len(self.started) == 1 else replacement_turn

    class OwnershipWebSocket(_FakeWebSocket):
        def __init__(self) -> None:
            super().__init__([{"type": "turn.start", "prompt": "first"}])

        async def send_json(self, payload: dict[str, object]) -> None:
            await super().send_json(payload)
            event_type = payload["type"]
            if event_type == "turn.accepted" and payload["turn_id"] == old_turn.turn_id:
                self._messages.put_nowait({"type": "turn.start", "prompt": "too soon"})
            elif event_type == "server.busy":
                old_turn._events.put_nowait(
                    TurnCompleted(
                        type="turn.completed",
                        turn_id=old_turn.turn_id,
                        assistant_message="old reply",
                        conversation={
                            "id": old_turn.session_id,
                            "updated_at": "2026-08-01T00:00:00+00:00",
                            "message_count": 2,
                            "preview": "old reply",
                        },
                    )
                )
            elif event_type == "turn.completed" and payload["turn_id"] == old_turn.turn_id:
                self._messages.put_nowait({"type": "turn.start", "prompt": "after terminal"})
            elif (
                event_type == "turn.accepted"
                and payload["turn_id"] == replacement_turn.turn_id
            ):
                self._messages.put_nowait(
                    {"type": "turn.cancel", "turn_id": replacement_turn.turn_id}
                )
                self._messages.put_nowait(WebSocketDisconnect())

    coordinator = ClearedCoordinator()
    websocket = OwnershipWebSocket()

    async def scenario() -> None:
        await asyncio.wait_for(_run_socket_handler(websocket, coordinator), timeout=0.5)

    asyncio.run(scenario())

    assert [request.prompt for request in coordinator.started] == ["first", "after terminal"]
    assert [event["type"] for event in websocket.sent] == [
        "turn.accepted",
        "server.busy",
        "turn.completed",
        "turn.accepted",
        "turn.cancelled",
    ]
