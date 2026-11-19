"""Read-only after-sales tool scoped by the authenticated user."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import after_sales as after_sales_repo
from app.schemas.after_sales import AfterSaleFact


async def lookup_after_sales(
    session: AsyncSession,
    user_id: int,
    order_no: str,
) -> list[AfterSaleFact] | None:
    """Query after-sales facts for an order owned by the authenticated user."""
    return await after_sales_repo.lookup_after_sales(session, user_id, order_no)
