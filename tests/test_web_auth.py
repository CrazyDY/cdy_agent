from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cdy_agent.web.auth import BrowserCapability


def auth_test_app(auth: BrowserCapability) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def root(request: Request) -> object:
        exchanged = auth.exchange(request)
        if exchanged is not None:
            return exchanged
        auth.require_http(request)
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        auth.require_websocket(websocket)
        await websocket.accept()
        await websocket.close()

    return app


def test_exchange_sets_cookie_and_redirects_clean_url() -> None:
    """Dropping the exchange would leak the one-time capability in the browser URL."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))

    response = client.get(
        "/?access_token=fixed-secret",
        headers={"host": "127.0.0.1:8000"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "Path=/" in response.headers["set-cookie"]
    assert "fixed-secret" not in response.headers["location"]


def test_exchange_rejects_wrong_token_without_exposing_the_secret() -> None:
    """Accepting another token would grant local browser access to an attacker."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))

    response = client.get(
        "/?access_token=wrong-secret",
        headers={"host": "127.0.0.1:8000"},
    )

    assert response.status_code == 403
    assert "fixed-secret" not in response.text
    assert "wrong-secret" not in response.text


def test_exchange_rejects_a_non_loopback_host() -> None:
    """Trusting a forwarded or alternate Host would widen the capability scope."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))

    response = client.get(
        "/?access_token=fixed-secret",
        headers={"host": "localhost:8000"},
    )

    assert response.status_code == 403
    assert "fixed-secret" not in response.text


def test_http_request_requires_the_exchanged_cookie() -> None:
    """Removing cookie validation would make every loopback request authenticated."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))

    response = client.get("/", headers={"host": "127.0.0.1:8000"})

    assert response.status_code == 403
    assert "fixed-secret" not in response.text


def test_websocket_rejects_wrong_origin_with_policy_violation() -> None:
    """Relaxing Origin validation would allow a hostile page to open a local socket."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))
    client.cookies.set("cdy_agent_web", "fixed-secret")

    with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(
        "/ws",
        headers={"host": "127.0.0.1:8000", "origin": "http://evil.example"},
    ):
        pass

    assert error.value.code == 1008


def test_token_comparison_uses_constant_time_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing compare_digest with equality would make token matching timing-sensitive."""
    calls: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("cdy_agent.web.auth.hmac.compare_digest", compare_digest)
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))

    response = client.get(
        "/?access_token=fixed-secret",
        headers={"host": "127.0.0.1:8000"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [("fixed-secret", "fixed-secret")]


def test_capability_repr_never_discloses_its_secret() -> None:
    """A dataclass-generated repr would expose the browser capability in diagnostics."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)

    assert "fixed-secret" not in repr(auth)
