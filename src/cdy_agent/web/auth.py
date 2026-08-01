"""Process-local browser capability authentication for the local Web server."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import HTTPException, Request, WebSocket, WebSocketException, status
from fastapi.responses import RedirectResponse

COOKIE_NAME = "cdy_agent_web"
LOOPBACK_HOST = "127.0.0.1"
_FORBIDDEN_DETAIL = "Forbidden."


@dataclass(frozen=True, repr=False)
class BrowserCapability:
    """A non-persistent secret that grants one local browser session."""

    secret: str
    host: str
    port: int

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise ValueError("Browser capability host must be the IPv4 loopback address.")
        if not 1 <= self.port <= 65535:
            raise ValueError("Browser capability port must be between 1 and 65535.")

    @classmethod
    def create(cls, host: str, port: int) -> BrowserCapability:
        """Create a new process-local capability without persisting its secret."""
        return cls(secrets.token_urlsafe(32), host, port)

    @classmethod
    def from_secret(cls, secret: str, host: str, port: int) -> BrowserCapability:
        """Construct a capability from a fixed secret for deterministic tests."""
        return cls(secret, host, port)

    @property
    def origin(self) -> str:
        """Return the only allowed browser origin."""
        return f"http://{self.host}:{self.port}"

    @property
    def launch_url(self) -> str:
        """Return the one-time launch URL used to exchange the capability."""
        return f"{self.origin}/?access_token={quote(self.secret, safe='')}"

    @property
    def _expected_host(self) -> str:
        return f"{self.host}:{self.port}"

    def exchange(self, request: Request) -> RedirectResponse | None:
        """Exchange one valid query capability for a strict session cookie."""
        self._require_host(request.headers.get("host"))
        tokens = request.query_params.getlist("access_token")
        if not tokens:
            return None
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self.secret):
            self._forbid_http()

        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=COOKIE_NAME,
            value=self.secret,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    def require_http(self, request: Request) -> None:
        """Reject HTTP requests outside the local authenticated browser session."""
        self._require_host(request.headers.get("host"))
        self._require_cookie(request.cookies.get(COOKIE_NAME), websocket=False)

    def require_websocket(self, websocket: WebSocket) -> None:
        """Reject non-local or unauthenticated WebSocket upgrades with policy violation."""
        if (
            websocket.headers.get("host") != self._expected_host
            or websocket.headers.get("origin") != self.origin
        ):
            self._forbid_websocket()
        self._require_cookie(websocket.cookies.get(COOKIE_NAME), websocket=True)

    def _require_host(self, host: str | None) -> None:
        if host != self._expected_host:
            self._forbid_http()

    def _require_cookie(self, cookie: str | None, *, websocket: bool) -> None:
        if cookie is None or not hmac.compare_digest(cookie, self.secret):
            if websocket:
                self._forbid_websocket()
            self._forbid_http()

    @staticmethod
    def _forbid_http() -> None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN_DETAIL)

    @staticmethod
    def _forbid_websocket() -> None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
