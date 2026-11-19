"""Demo after-sales seed is stable and idempotent."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import AfterSale, Order


@pytest.mark.anyio
async def test_demo_after_sale_seed_is_idempotent(db_session):
    from scripts.seed_demo import (
        _seed_after_sales,
        _seed_orders,
        _seed_products,
        _seed_users,
    )

    users = await _seed_users(db_session)
    products = await _seed_products(db_session)
    orders = await _seed_orders(db_session, users["customer"], products)

    assert set(orders) == {
        "ORD-20261117-0001",
        "ORD-20261110-0015",
    }

    await _seed_after_sales(db_session, orders)
    await _seed_after_sales(db_session, orders)

    rows = (
        await db_session.execute(
            select(AfterSale)
            .join(Order, Order.id == AfterSale.order_id)
            .where(Order.order_no == "ORD-20261110-0015")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].service_type == "退货退款"
    assert rows[0].status == "已完成"
    assert rows[0].refund_amount == Decimal("88.50")
    assert rows[0].reason == "商品破损"

    total = await db_session.scalar(select(func.count()).select_from(AfterSale))
    assert total == 1
