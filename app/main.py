"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Async psycopg cannot run on the Proactor loop that uvicorn selects by default
# on Windows; the selector loop is required for local runs. No-op elsewhere.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.metrics import MetricsRegistry
from app.core.rate_limit import LoginGuard, SlidingWindowRateLimiter
from app.db.session import create_session_factory

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app(
    settings: Settings | None = None,
    session_factory=None,
) -> FastAPI:
    """Build the FastAPI application with its dependencies.

    ``settings`` and ``session_factory`` may be supplied by tests to bind the
    application to a dedicated database.
    """
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    app = FastAPI(
        title="E-commerce Customer Service",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.state.settings = resolved_settings
    app.state.session_factory = session_factory or create_session_factory(
        resolved_settings.database_url
    )
    app.state.metrics = MetricsRegistry()
    app.state.login_guard = LoginGuard(
        max_failures=resolved_settings.login_failure_limit,
        window_seconds=resolved_settings.login_failure_window_seconds,
        lock_seconds=resolved_settings.login_lock_seconds,
    )
    app.state.chat_rate_limiter = SlidingWindowRateLimiter(
        limit=resolved_settings.chat_rate_limit_requests,
        window_seconds=resolved_settings.chat_rate_limit_window_seconds,
    )
    app.state.admin_rate_limiter = SlidingWindowRateLimiter(
        limit=resolved_settings.admin_rate_limit_requests,
        window_seconds=resolved_settings.admin_rate_limit_window_seconds,
    )

    # The single-page app talks to the API from the same origin in production;
    # allow local development origins for the Vite dev server.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    _register_routes(app)
    _mount_frontend(app)

    return app


def _register_routes(app: FastAPI) -> None:
    """Register API routers, lazily imported to avoid import-time coupling."""
    from app.api.routes import (
        admin_knowledge,
        admin_logs,
        auth,
        chat,
        handoffs,
        returns,
        weather_mcp,
    )

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(returns.router, prefix="/api", tags=["returns"])
    app.include_router(
        admin_knowledge.router,
        prefix="/api/admin",
        tags=["admin-knowledge"],
    )
    app.include_router(
        admin_logs.router,
        prefix="/api/admin",
        tags=["admin-logs"],
    )
    app.include_router(handoffs.router, prefix="/api/admin", tags=["handoffs"])
    app.include_router(weather_mcp.router, prefix="/mcp/weather", tags=["mcp"])


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA and fall back to index.html for client routes."""
    if not (FRONTEND_DIST / "index.html").exists():
        return
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
            raise HTTPException(status_code=404)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


app = create_app()
