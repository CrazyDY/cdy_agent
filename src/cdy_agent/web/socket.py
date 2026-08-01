"""Authenticated WebSocket transport for local streaming Agent turns."""

from __future__ import annotations

import asyncio
from json import JSONDecodeError

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from cdy_agent.web.auth import BrowserCapability
from cdy_agent.web.schemas import (
    ApprovalResolve,
    ProtocolError,
    ServerBusy,
    TurnCancel,
    TurnStart,
    parse_client_event,
)
from cdy_agent.web.turns import ActiveTurn, ServerBusyError, TurnCoordinator


def register_turn_socket(
    app: FastAPI, auth: BrowserCapability, coordinator: TurnCoordinator
) -> None:
    """Register the authenticated, single-turn WebSocket endpoint."""

    @app.websocket("/api/turns")
    async def turn_socket(websocket: WebSocket) -> None:
        auth.require_websocket(websocket)
        await websocket.accept()

        active_turn: ActiveTurn | None = None
        sender: asyncio.Task[None] | None = None
        receiver = asyncio.create_task(websocket.receive_json())
        try:
            while True:
                waiting: set[asyncio.Task[object]] = {receiver}
                if sender is not None:
                    waiting.add(sender)  # type: ignore[arg-type]
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

                if sender is not None and sender in done:
                    try:
                        await sender
                    except Exception:  # noqa: BLE001 - a failed sender ends this socket
                        return
                    sender = None
                    active_turn = None

                if receiver not in done:
                    continue
                try:
                    payload = receiver.result()
                except WebSocketDisconnect:
                    return
                except (JSONDecodeError, TypeError, ValueError):
                    if not await _send_protocol_error(websocket):
                        return
                    receiver = asyncio.create_task(websocket.receive_json())
                    continue

                receiver = asyncio.create_task(websocket.receive_json())
                try:
                    event = parse_client_event(payload)
                except ValidationError:
                    if not await _send_protocol_error(websocket):
                        return
                    continue

                if isinstance(event, TurnStart):
                    try:
                        active_turn = await coordinator.start(event)
                    except ServerBusyError:
                        if not await _send_event(
                            websocket, ServerBusy(type="server.busy")
                        ):
                            return
                        continue
                    sender = asyncio.create_task(_send_turn_events(websocket, active_turn))
                    continue

                if active_turn is None or event.turn_id != active_turn.turn_id:
                    if not await _send_protocol_error(websocket):
                        return
                    continue

                if isinstance(event, TurnCancel):
                    active_turn.cancel()
                    continue

                if isinstance(event, ApprovalResolve):
                    try:
                        active_turn.resolve_approval(event)
                    except ValueError:
                        if not await _send_protocol_error(websocket):
                            return
        finally:
            receiver.cancel()
            await _await_cancelled(receiver)
            if active_turn is not None:
                active_turn.cancel()
                await active_turn.wait_stopped()
            if sender is not None:
                sender.cancel()
                await _await_cancelled(sender)


async def _send_turn_events(websocket: WebSocket, turn: ActiveTurn) -> None:
    """Forward one turn's ordered event queue until its terminal event."""
    try:
        while True:
            event = await turn.next_event()
            await websocket.send_json(event.model_dump(mode="json"))
            if event.type in {"turn.completed", "turn.failed", "turn.cancelled"}:
                return
    except Exception:
        turn.cancel()
        await turn.wait_stopped()
        raise


async def _send_event(websocket: WebSocket, event: object) -> bool:
    try:
        websocket_event = event
        await websocket.send_json(websocket_event.model_dump(mode="json"))  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - socket send failures are transport failures
        return False
    return True


async def _send_protocol_error(websocket: WebSocket) -> bool:
    return await _send_event(
        websocket,
        ProtocolError(type="protocol.error", message="Invalid WebSocket event."),
    )


async def _await_cancelled(task: asyncio.Task[object]) -> None:
    try:
        await task
    except (asyncio.CancelledError, WebSocketDisconnect):
        return
