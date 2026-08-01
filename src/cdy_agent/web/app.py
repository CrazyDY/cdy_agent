"""FastAPI composition for the authenticated local Web interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint
from starlette.staticfiles import StaticFiles

from cdy_agent.memory import ConversationStore
from cdy_agent.web.auth import BrowserCapability
from cdy_agent.web.sessions import create_sessions_router
from cdy_agent.web.socket import register_turn_socket

_STATIC_DIRECTORY = Path(__file__).with_name("static")
_MISSING_ASSETS = {
    "code": "web_assets_missing",
    "message": "Web assets are unavailable.",
    "retryable": True,
}


class TurnCoordinatorLike(Protocol):
    """The only turn-coordinator state the HTTP session routes require."""

    @property
    def busy(self) -> bool:
        """Whether a process-wide Agent turn is active."""


@dataclass(frozen=True)
class WebSettings:
    """Display-safe configuration resolved before the Web app starts."""

    workspace: Path
    model: str
    api_mode: Literal["responses", "chat_completions"]


@dataclass(frozen=True)
class WebDependencies:
    """Runtime dependencies supplied by the CLI composition layer."""

    auth: BrowserCapability
    conversation_store: ConversationStore
    turn_coordinator: TurnCoordinatorLike


def create_web_app(settings: WebSettings, dependencies: WebDependencies) -> FastAPI:
    """Create the authenticated local Web application."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_browser_session(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if _is_capability_exchange(request):
            return await call_next(request)
        if request.url.path == "/api" or request.url.path.startswith(
            "/api/"
        ) or request.url.path == "/assets" or request.url.path.startswith("/assets/"):
            try:
                dependencies.auth.require_http(request)
            except HTTPException as error:
                return JSONResponse(
                    status_code=error.status_code, content={"detail": error.detail}
                )
        return await call_next(request)

    @app.get("/")
    def root(request: Request) -> Response:
        exchanged = dependencies.auth.exchange(request)
        if exchanged is not None:
            return exchanged
        dependencies.auth.require_http(request)
        index = _STATIC_DIRECTORY / "index.html"
        if not index.is_file():
            return JSONResponse(status_code=503, content=_MISSING_ASSETS)
        return FileResponse(index)

    app.include_router(create_sessions_router(settings, dependencies))
    register_turn_socket(app, dependencies.auth, dependencies.turn_coordinator)  # type: ignore[arg-type]
    app.mount(
        "/assets",
        StaticFiles(directory=_STATIC_DIRECTORY / "assets", check_dir=False),
        name="assets",
    )
    return app


def _is_capability_exchange(request: Request) -> bool:
    return (
        request.method == "GET"
        and request.url.path == "/"
        and "access_token" in request.query_params
    )
