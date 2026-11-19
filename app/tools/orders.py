"""Order tool: query order status and logistics, scoped to the caller."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import orders as orders_repo
from app.schemas.orders import OrderFact


async def get_order_and_logistics(
    session: AsyncSession, user_id: int, order_no: str
) -> OrderFact | None:
    """Query an owned order with items and logistics.

    ``user_id`` originates from the authenticated session, never from model
    output.
    """
    return await orders_repo.get_order_and_logistics(session, user_id, order_no)


async def list_owned_orders(
    session: AsyncSession,
    user_id: int,
) -> list[OrderFact]:
    """List all orders owned by the authenticated customer."""
    return await orders_repo.list_owned_orders(session, user_id)
