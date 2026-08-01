from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketException
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest
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


def asgi_http_request(
    headers: list[tuple[str, str]], *, query: str = ""
) -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": query.encode("ascii"),
            "headers": [(name.encode("ascii"), value.encode("ascii")) for name, value in headers],
        }
    )


def asgi_websocket(headers: list[tuple[str, str]]) -> WebSocket:
    async def receive() -> dict[str, str]:
        return {"type": "websocket.connect"}

    async def send(message: object) -> None:
        return None

    return WebSocket(
        {
            "type": "websocket",
            "path": "/ws",
            "query_string": b"",
            "headers": [(name.encode("ascii"), value.encode("ascii")) for name, value in headers],
        },
        receive=receive,
        send=send,
    )


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


def test_exchanged_cookie_authenticates_a_clean_http_request() -> None:
    """Consuming a capability must not invalidate the browser session it creates."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))

    exchange = client.get(
        "/?access_token=fixed-secret",
        headers={"host": "127.0.0.1:8000"},
        follow_redirects=False,
    )
    response = client.get("/", headers={"host": "127.0.0.1:8000"})

    assert exchange.status_code == 303
    assert response.status_code == 200
    assert response.json() == {"ok": True}


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


def test_websocket_accepts_exact_host_origin_and_exchanged_cookie() -> None:
    """The strict checks must still permit the authenticated local browser."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    client = TestClient(auth_test_app(auth))
    exchange = client.get(
        "/?access_token=fixed-secret",
        headers={"host": "127.0.0.1:8000"},
        follow_redirects=False,
    )

    assert exchange.status_code == 303
    with client.websocket_connect(
        "/ws",
        headers={"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"},
    ):
        pass


@pytest.mark.parametrize(
    "headers",
    [
        [("host", "127.0.0.1:8000"), ("host", "127.0.0.1:8000")],
        [("host", "127.0.0.1:08000")],
        [("host", "[::1]:8000")],
        [("host", "127.0.0.1")],
    ],
)
def test_http_rejects_repeated_or_noncanonical_host_headers(
    headers: list[tuple[str, str]],
) -> None:
    """Reading only the first Host value would accept ambiguous routing metadata."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    request = asgi_http_request(headers + [("cookie", "cdy_agent_web=fixed-secret")])

    with pytest.raises(HTTPException) as error:
        auth.require_http(request)

    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        [
            ("host", "127.0.0.1:8000"),
            ("origin", "http://127.0.0.1:8000"),
            ("origin", "http://127.0.0.1:8000"),
        ],
        [("host", "127.0.0.1:8000"), ("origin", "HTTP://127.0.0.1:8000")],
        [("host", "127.0.0.1:8000"), ("origin", "http://[::1]:8000")],
        [("host", "127.0.0.1:8000"), ("origin", "http://127.0.0.1")],
    ],
)
def test_websocket_rejects_repeated_or_noncanonical_origin_headers(
    headers: list[tuple[str, str]],
) -> None:
    """Accepting an Origin variant would allow a non-exact browser origin."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    websocket = asgi_websocket(headers + [("cookie", "cdy_agent_web=fixed-secret")])

    with pytest.raises(WebSocketException) as error:
        auth.require_websocket(websocket)

    assert error.value.code == 1008


def test_http_rejects_repeated_capability_cookie() -> None:
    """Collapsing repeated cookie names would let a request choose its own credential."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    request = asgi_http_request(
        [
            ("host", "127.0.0.1:8000"),
            ("cookie", "cdy_agent_web=fixed-secret; cdy_agent_web=fixed-secret"),
        ]
    )

    with pytest.raises(HTTPException) as error:
        auth.require_http(request)

    assert error.value.status_code == 403


def test_capability_token_can_be_exchanged_only_once() -> None:
    """Leaving a valid query token reusable would preserve it in browser history."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    request = asgi_http_request(
        [("host", "127.0.0.1:8000")], query="access_token=fixed-secret"
    )

    first = auth.exchange(request)
    with pytest.raises(HTTPException) as error:
        auth.exchange(request)

    assert first is not None
    assert first.status_code == 303
    assert error.value.status_code == 403


def test_concurrent_capability_exchanges_allow_exactly_one_cookie() -> None:
    """A check-then-set race would issue sessions to both concurrent launch requests."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def exchange() -> None:
        request = asgi_http_request(
            [("host", "127.0.0.1:8000")], query="access_token=fixed-secret"
        )
        barrier.wait()
        try:
            response = auth.exchange(request)
            assert response is not None
            statuses.append(response.status_code)
        except HTTPException as error:
            statuses.append(error.status_code)

    first = threading.Thread(target=exchange)
    second = threading.Thread(target=exchange)
    first.start()
    second.start()
    first.join()
    second.join()

    assert sorted(statuses) == [303, 403]


def test_invalid_host_or_token_does_not_consume_capability() -> None:
    """An invalid request must not let another origin burn the launch capability."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=8000)

    with pytest.raises(HTTPException):
        auth.exchange(
            asgi_http_request(
                [("host", "localhost:8000")], query="access_token=fixed-secret"
            )
        )
    with pytest.raises(HTTPException):
        auth.exchange(
            asgi_http_request(
                [("host", "127.0.0.1:8000")], query="access_token=wrong-secret"
            )
        )

    response = auth.exchange(
        asgi_http_request(
            [("host", "127.0.0.1:8000")], query="access_token=fixed-secret"
        )
    )

    assert response is not None
    assert response.status_code == 303


def test_default_port_variants_are_not_equivalent_to_exact_authority() -> None:
    """Normalizing away the explicit port would weaken the pinned local authority."""
    auth = BrowserCapability.from_secret("fixed-secret", host="127.0.0.1", port=80)
    http_request = asgi_http_request(
        [("host", "127.0.0.1"), ("cookie", "cdy_agent_web=fixed-secret")]
    )
    websocket = asgi_websocket(
        [
            ("host", "127.0.0.1:80"),
            ("origin", "http://127.0.0.1"),
            ("cookie", "cdy_agent_web=fixed-secret"),
        ]
    )

    with pytest.raises(HTTPException) as http_error:
        auth.require_http(http_request)
    with pytest.raises(WebSocketException) as websocket_error:
        auth.require_websocket(websocket)

    assert http_error.value.status_code == 403
    assert websocket_error.value.code == 1008


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
