"""Process-local browser capability authentication for the local Web server."""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field
from urllib.parse import quote

from fastapi import HTTPException, Request, WebSocket, WebSocketException, status
from fastapi.responses import RedirectResponse
from starlette.datastructures import Headers

COOKIE_NAME = "cdy_agent_web"
LOOPBACK_HOST = "127.0.0.1"
_FORBIDDEN_DETAIL = "Forbidden."


@dataclass(frozen=True, repr=False)
class BrowserCapability:
    """A non-persistent secret that grants one local browser session."""

    secret: str
    host: str
    port: int
    _exchange_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    _consumed: bool = field(default=False, init=False, repr=False, compare=False)

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
        self._require_host(request.headers)
        tokens = request.query_params.getlist("access_token")
        if not tokens:
            return None
        with self._exchange_lock:
            if (
                self._consumed
                or len(tokens) != 1
                or not hmac.compare_digest(tokens[0], self.secret)
            ):
                self._forbid_http()
            object.__setattr__(self, "_consumed", True)

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
        self._require_host(request.headers)
        self._require_cookie(request.headers, websocket=False)

    def require_websocket(self, websocket: WebSocket) -> None:
        """Reject non-local or unauthenticated WebSocket upgrades with policy violation."""
        if (
            self._header_values(websocket.headers, "host") != [self._expected_host]
            or self._header_values(websocket.headers, "origin") != [self.origin]
        ):
            self._forbid_websocket()
        self._require_cookie(websocket.headers, websocket=True)

    def _require_host(self, headers: Headers) -> None:
        if self._header_values(headers, "host") != [self._expected_host]:
            self._forbid_http()

    def _require_cookie(self, headers: Headers, *, websocket: bool) -> None:
        cookie_values = self._capability_cookie_values(headers)
        if len(cookie_values) != 1 or not hmac.compare_digest(cookie_values[0], self.secret):
            if websocket:
                self._forbid_websocket()
            self._forbid_http()

    @staticmethod
    def _header_values(headers: Headers, name: str) -> list[str]:
        expected_name = name.encode("ascii")
        return [
            value.decode("latin-1")
            for header_name, value in headers.raw
            if header_name.lower() == expected_name
        ]

    def _capability_cookie_values(self, headers: Headers) -> list[str]:
        cookie_headers = self._header_values(headers, "cookie")
        if len(cookie_headers) != 1:
            return []
        values: list[str] = []
        for fragment in cookie_headers[0].split(";"):
            name, separator, value = fragment.strip().partition("=")
            if name == COOKIE_NAME and separator:
                values.append(value.strip())
        return values

    @staticmethod
    def _forbid_http() -> None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN_DETAIL)

    @staticmethod
    def _forbid_websocket() -> None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
