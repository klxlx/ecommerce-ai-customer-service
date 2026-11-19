"""Health and readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Report service, database, and model-provider readiness."""
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - exercised by integration test
        database = "unavailable"

    settings = request.app.state.settings
    models = {
        "chat": settings.chat_configured or settings.mock_providers,
        "embedding": settings.embedding_configured or settings.mock_providers,
    }
    status = "ok" if database == "ok" else "degraded"
    return {"status": status, "database": database, "models": models}
