"""After-sales lookup scoped through order ownership."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AfterSale, Order
from app.schemas.after_sales import AfterSaleFact


async def lookup_after_sales(
    session: AsyncSession,
    user_id: int,
    order_no: str,
) -> list[AfterSaleFact] | None:
    """Return facts for an owned order, or ``None`` when it is not accessible."""
    order_id = (
        await session.execute(
            select(Order.id).where(
                Order.order_no == order_no,
                Order.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if order_id is None:
        return None

    rows = (
        await session.execute(
            select(AfterSale)
            .where(AfterSale.order_id == order_id)
            .order_by(AfterSale.created_at, AfterSale.id)
        )
    ).scalars().all()
    return [
        AfterSaleFact.model_validate(row, from_attributes=True)
        for row in rows
    ]
