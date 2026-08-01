from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdy_agent.conversation import Message
from cdy_agent.memory import ConversationNotFoundError, ConversationStore
from cdy_agent.web.app import WebDependencies, WebSettings, create_web_app
from cdy_agent.web.auth import BrowserCapability
from cdy_agent.web.errors import ServerBusyError
from cdy_agent.web.schemas import TurnStart
from cdy_agent.web.turns import ConfirmationBroker, TurnCoordinator, TurnDependencies

SESSION_ID = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"


@dataclass
class StubCoordinator:
    store: ConversationStore
    busy: bool = False

    def delete_session(self, session_id: str) -> None:
        if self.busy:
            raise ServerBusyError("Another turn is already running.")
        self.store.delete(session_id)


class BlockingDeleteStore(ConversationStore):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.delete_started = threading.Event()
        self.allow_delete = threading.Event()

    def delete(self, session_id: str) -> None:
        self.delete_started.set()
        assert self.allow_delete.wait(timeout=2)
        super().delete(session_id)


class RecordingAgent:
    def __init__(self) -> None:
        self.called = threading.Event()

    def run_stream(self, *args: object, **kwargs: object) -> str:
        self.called.set()
        return "new answer"


@pytest.fixture
def authenticated_client(tmp_path: Path) -> tuple[TestClient, ConversationStore, StubCoordinator]:
    store = ConversationStore(tmp_path)
    coordinator = StubCoordinator(store)
    capability = BrowserCapability.from_secret(
        "fixed-secret", host="127.0.0.1", port=8000
    )
    app = create_web_app(
        WebSettings(workspace=tmp_path, model="safe-model", api_mode="responses"),
        WebDependencies(
            auth=capability,
            conversation_store=store,
            turn_coordinator=coordinator,
        ),
    )
    client = TestClient(app)
    client.cookies.set("cdy_agent_web", "fixed-secret")
    return client, store, coordinator


def request(client: TestClient, method: str, path: str) -> object:
    return getattr(client, method)(path, headers={"host": "127.0.0.1:8000"})


def test_bootstrap_returns_only_display_safe_fields(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
) -> None:
    """Returning configuration internals would disclose secrets to the browser."""
    client, store, _ = authenticated_client
    store.append_turn(
        SESSION_ID,
        Message("user", "Hello"),
        Message("assistant", "Hi"),
    )

    response = request(client, "get", "/api/bootstrap")

    assert response.status_code == 200
    assert set(response.json()) == {
        "workspace_name",
        "workspace_path",
        "model",
        "api_mode",
        "busy",
        "conversations",
    }
    assert response.json()["model"] == "safe-model"
    assert response.json()["conversations"] == [
        {
            "id": SESSION_ID,
            "updated_at": response.json()["conversations"][0]["updated_at"],
            "message_count": 2,
            "preview": "Hello",
        }
    ]
    assert "OPENAI_API_KEY" not in response.text
    assert "system_prompt" not in response.text


def test_empty_bootstrap_does_not_create_conversation_database(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
    tmp_path: Path,
) -> None:
    """A read-only empty sidebar must not initialize persistent state."""
    client, _, _ = authenticated_client

    response = request(client, "get", "/api/bootstrap")

    assert response.status_code == 200
    assert response.json()["conversations"] == []
    assert not (tmp_path / ".cdy-agent").exists()


def test_session_load_returns_exact_persisted_messages(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
) -> None:
    """Loading must preserve store message order and omit storage implementation data."""
    client, store, _ = authenticated_client
    store.append_turn(
        SESSION_ID,
        Message("user", "Question"),
        Message("assistant", "Answer"),
    )

    response = request(client, "get", f"/api/sessions/{SESSION_ID}")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "created_at", "updated_at", "messages"}
    assert body["id"] == SESSION_ID
    assert body["messages"] == [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]


def test_session_routes_reject_noncanonical_uuid(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
) -> None:
    """Accepting UUID variants would bypass the store's canonical ID boundary."""
    client, _, _ = authenticated_client

    response = request(client, "get", "/api/sessions/52C809C6-6E55-4FF1-9220-E4F90A4F6774")

    assert response.status_code == 400
    assert response.json() == {
        "code": "invalid_conversation_id",
        "message": "Conversation ID must be a complete canonical UUID.",
        "retryable": False,
    }


def test_session_load_maps_missing_conversation_to_safe_error(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
) -> None:
    """Leaking a storage exception would expose local persistence details."""
    client, _, _ = authenticated_client

    response = request(client, "get", f"/api/sessions/{SESSION_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "code": "conversation_not_found",
        "message": "Conversation was not found.",
        "retryable": False,
    }


def test_session_delete_removes_saved_conversation(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
) -> None:
    """A successful delete must remove the exact persisted session."""
    client, store, _ = authenticated_client
    store.append_turn(
        SESSION_ID,
        Message("user", "Question"),
        Message("assistant", "Answer"),
    )

    response = request(client, "delete", f"/api/sessions/{SESSION_ID}")

    assert response.status_code == 204
    with pytest.raises(ConversationNotFoundError):
        store.load(SESSION_ID)


def test_session_delete_rejects_when_turn_is_busy(
    authenticated_client: tuple[TestClient, ConversationStore, StubCoordinator],
) -> None:
    """Deleting during a turn could race the coordinator's later persistence."""
    client, store, coordinator = authenticated_client
    store.append_turn(
        SESSION_ID,
        Message("user", "Question"),
        Message("assistant", "Answer"),
    )
    coordinator.busy = True

    response = request(client, "delete", f"/api/sessions/{SESSION_ID}")

    assert response.status_code == 409
    assert response.json() == {
        "code": "server_busy",
        "message": "Another turn is already running.",
        "retryable": True,
    }
    assert store.load(SESSION_ID).id == SESSION_ID


def test_session_delete_and_turn_start_share_one_atomic_boundary(tmp_path: Path) -> None:
    """A resumed turn must not start through a deletion already in progress."""

    async def scenario() -> None:
        store = BlockingDeleteStore(tmp_path)
        store.append_turn(
            SESSION_ID,
            Message("user", "Question"),
            Message("assistant", "Answer"),
        )
        agent = RecordingAgent()
        coordinator = TurnCoordinator(
            TurnDependencies(
                agent=agent,
                confirmations=ConfirmationBroker(),
                conversations=store,
                traces=None,
                model="test-model",
                api_mode="responses",
                pricing=None,
            )
        )
        capability = BrowserCapability.from_secret(
            "fixed-secret", host="127.0.0.1", port=8000
        )
        app = create_web_app(
            WebSettings(workspace=tmp_path, model="safe-model", api_mode="responses"),
            WebDependencies(
                auth=capability,
                conversation_store=store,
                turn_coordinator=coordinator,
            ),
        )
        client = TestClient(app)
        client.cookies.set("cdy_agent_web", "fixed-secret")
        delete_task = asyncio.create_task(
            asyncio.to_thread(request, client, "delete", f"/api/sessions/{SESSION_ID}")
        )
        await asyncio.to_thread(store.delete_started.wait, 1)

        start_task = asyncio.create_task(
            coordinator.start(
                TurnStart(type="turn.start", prompt="new question", session_id=SESSION_ID)
            )
        )
        probe = asyncio.Event()
        asyncio.get_running_loop().call_soon(probe.set)
        await probe.wait()
        start_crossed_delete = start_task.done()

        store.allow_delete.set()
        response = await delete_task
        turn = await start_task
        events = []
        while True:
            event = await turn.next_event()
            events.append(event)
            if event.type in {"turn.completed", "turn.failed", "turn.cancelled"}:
                break
        await turn.wait_stopped()

        assert start_crossed_delete is False
        assert response.status_code == 204
        assert events[-1].type == "turn.failed"
        assert agent.called.is_set() is False

    asyncio.run(scenario())
