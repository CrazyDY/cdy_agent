from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdy_agent.memory import ConversationStore
from cdy_agent.web.app import WebDependencies, WebSettings, create_web_app
from cdy_agent.web.auth import BrowserCapability


@dataclass
class StubCoordinator:
    busy: bool = False


@pytest.fixture
def app_client(tmp_path: Path) -> TestClient:
    capability = BrowserCapability.from_secret(
        "fixed-secret", host="127.0.0.1", port=8000
    )
    app = create_web_app(
        WebSettings(workspace=tmp_path, model="safe-model", api_mode="responses"),
        WebDependencies(
            auth=capability,
            conversation_store=ConversationStore(tmp_path),
            turn_coordinator=StubCoordinator(),
        ),
    )
    return TestClient(app)


def test_root_exchanges_capability_before_asset_lookup(app_client: TestClient) -> None:
    """Blocking the exchange in middleware would leave the capability in the URL."""
    response = app_client.get(
        "/?access_token=fixed-secret",
        headers={"host": "127.0.0.1:8000"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "HttpOnly" in response.headers["set-cookie"]


def test_root_requires_cookie_and_returns_safe_missing_asset_error(
    app_client: TestClient,
) -> None:
    """A backend-only package must not serve the root without browser authentication."""
    forbidden = app_client.get("/", headers={"host": "127.0.0.1:8000"})
    app_client.cookies.set("cdy_agent_web", "fixed-secret")
    missing = app_client.get("/", headers={"host": "127.0.0.1:8000"})

    assert forbidden.status_code == 403
    assert missing.status_code == 503
    assert missing.json() == {
        "code": "web_assets_missing",
        "message": "Web assets are unavailable.",
        "retryable": True,
    }


@pytest.mark.parametrize("path", ["/api/bootstrap", "/assets/app.js"])
def test_api_and_assets_require_exact_host_and_cookie(
    app_client: TestClient, path: str
) -> None:
    """Relaxing middleware authentication would expose local session data cross-origin."""
    unauthenticated = app_client.get(path, headers={"host": "127.0.0.1:8000"})
    app_client.cookies.set("cdy_agent_web", "fixed-secret")
    wrong_host = app_client.get(path, headers={"host": "localhost:8000"})

    assert unauthenticated.status_code == 403
    assert wrong_host.status_code == 403
