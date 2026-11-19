"""Repository for order and logistics lookup, always scoped by user id."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LogisticsEvent, Order, OrderItem
from app.schemas.orders import LogisticsEventFact, OrderFact, OrderItemFact


def _to_order_fact(
    order: Order,
    items: list[OrderItem],
    logistics: list[LogisticsEvent],
) -> OrderFact:
    return OrderFact(
        order_no=order.order_no,
        status=order.status,
        total_amount=order.total_amount,
        created_at=order.created_at,
        items=[
            OrderItemFact(
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in items
        ],
        logistics=[
            LogisticsEventFact(
                event_time=event.event_time,
                status=event.status,
                location=event.location,
                description=event.description,
            )
            for event in logistics
        ],
    )


async def get_order_and_logistics(
    session: AsyncSession, user_id: int, order_no: str
) -> OrderFact | None:
    """Return an owned order with items and logistics, or ``None``.

    The ``user_id`` is always supplied by the authenticated session, never from
    model-generated tool arguments. A missing or unauthorized order returns the
    same ``None`` so ownership is never enumerable.
    """
    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        return None

    items = (
        await session.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    ).scalars().all()
    logistics = (
        await session.execute(
            select(LogisticsEvent)
            .where(LogisticsEvent.order_id == order.id)
            .order_by(LogisticsEvent.event_time)
        )
    ).scalars().all()

    return _to_order_fact(order, list(items), list(logistics))


async def list_owned_orders(
    session: AsyncSession,
    user_id: int,
) -> list[OrderFact]:
    """Return all orders owned by the authenticated user, newest first."""
    orders = list(
        (
            await session.execute(
                select(Order)
                .where(Order.user_id == user_id)
                .order_by(Order.created_at.desc(), Order.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not orders:
        return []

    order_ids = [order.id for order in orders]
    items = list(
        (
            await session.execute(
                select(OrderItem)
                .where(OrderItem.order_id.in_(order_ids))
                .order_by(OrderItem.id)
            )
        )
        .scalars()
        .all()
    )
    items_by_order: dict[int, list[OrderItem]] = {
        order_id: [] for order_id in order_ids
    }
    for item in items:
        items_by_order[item.order_id].append(item)

    return [
        _to_order_fact(order, items_by_order[order.id], [])
        for order in orders
    ]
