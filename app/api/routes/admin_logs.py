"""Administrator operational-log routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limit_admin
from app.db.models import User
from app.db.repositories import operational_log as log_repo
from app.db.session import get_session
from app.schemas.operational_log import OperationalLogPage, OperationalLogQuery

router = APIRouter()


@router.get("/logs", response_model=OperationalLogPage)
async def list_logs(
    query: OperationalLogQuery = Depends(),
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    if query.start_at and query.end_at and query.start_at > query.end_at:
        raise HTTPException(
            status_code=422,
            detail="start_at must not be later than end_at",
        )
    items, total = await log_repo.list_operational_logs(session, query)
    return OperationalLogPage(
        items=items,
        total=total,
        limit=query.limit,
        offset=query.offset,
    )
