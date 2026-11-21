"""Seed 20 manual-test orders for the demo customer account.

Run with:
    .\\.venv\\Scripts\\python.exe -m scripts.seed_customer1_orders

The script is idempotent: orders already present are skipped, and items/logistics
are only created together with a new order. Amounts include 5.00, 9.99, 10.00,
10.01 and larger values so the refund threshold can be exercised manually.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    LogisticsEvent,
    Order,
    OrderItem,
    Product,
    User,
)
from app.db.session import create_session_factory

CUSTOMER_USERNAME = "customer"

ORDER_SPECS = [
    ("ORD-20261121-0101", "已完成", [("HM-300", 1, "5.00")]),
    ("ORD-20261121-0102", "已完成", [("HM-300", 1, "9.99")]),
    ("ORD-20261121-0103", "已完成", [("HM-300", 1, "10.00")]),
    ("ORD-20261121-0104", "已完成", [("HM-300", 1, "10.01")]),
    ("ORD-20261121-0105", "已完成", [("HM-300", 1, "19.90")]),
    ("ORD-20261121-0106", "已完成", [("HM-300", 1, "20.00")]),
    ("ORD-20261121-0107", "已完成", [("HM-300", 1, "49.99")]),
    ("ORD-20261121-0108", "已完成", [("HM-300", 1, "79.00")]),
    ("ORD-20261121-0109", "已完成", [("TH-450", 1, "88.50")]),
    ("ORD-20261121-0110", "已完成", [("PB-10000", 1, "129.00")]),
    ("ORD-20261121-0111", "已签收", [("TB-200", 1, "199.00")]),
    ("ORD-20261121-0112", "已签收", [("EB-PRO", 1, "299.00")]),
    ("ORD-20261121-0113", "已签收", [("KB-87", 1, "459.00")]),
    ("ORD-20261121-0114", "已签收", [("HP-500", 1, "599.00")]),
    ("ORD-20261121-0115", "已签收", [("SSD-1T", 1, "699.00")]),
    ("ORD-20261121-0116", "已签收", [("AP-320", 1, "899.00")]),
    ("ORD-20261121-0117", "运输中", [("SW-2", 1, "1299.00")]),
    (
        "ORD-20261121-0118",
        "运输中",
        [("SSD-1T", 1, "699.00"), ("HM-300", 1, "79.00")],
    ),
    (
        "ORD-20261121-0119",
        "待发货",
        [("PB-10000", 1, "129.00"), ("TB-200", 1, "199.00")],
    ),
    (
        "ORD-20261121-0120",
        "待发货",
        [("KB-87", 1, "459.00"), ("TH-450", 1, "88.50")],
    ),
]


def _events_for(status: str, now: datetime) -> list[tuple]:
    if status in {"已完成", "已签收"}:
        return [
            (now - timedelta(days=5), "已下单", "上海仓", "仓库已接单"),
            (now - timedelta(days=3), "已揽收", "上海仓", "商家已揽收"),
            (now - timedelta(days=1), "已签收", "收货地址", "本人签收"),
        ]
    if status == "运输中":
        return [
            (now - timedelta(days=2), "已揽收", "上海仓", "商家已揽收"),
            (now - timedelta(days=1), "运输中", "杭州转运中心", "快件运输中"),
        ]
    return [(now - timedelta(days=1), "已下单", "上海仓", "仓库正在配货")]


async def main() -> None:
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        customer = (
            await session.execute(
                select(User).where(User.username == CUSTOMER_USERNAME)
            )
        ).scalar_one_or_none()
        if customer is None:
            raise SystemExit(f"用户 {CUSTOMER_USERNAME} 不存在")
        products = {
            product.sku: product
            for product in (
                await session.execute(select(Product))
            ).scalars().all()
        }
        created = 0
        skipped = 0
        for order_no, status, item_specs in ORDER_SPECS:
            existing = (
                await session.execute(
                    select(Order).where(Order.order_no == order_no)
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue
            total = sum(
                Decimal(unit_price) * quantity
                for _, quantity, unit_price in item_specs
            )
            order = Order(
                order_no=order_no,
                user_id=customer.id,
                status=status,
                total_amount=total,
            )
            session.add(order)
            await session.flush()
            for sku, quantity, unit_price in item_specs:
                product = products[sku]
                session.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        product_name=product.name,
                        model=product.model,
                        color=product.color,
                        quantity=quantity,
                        unit_price=Decimal(unit_price),
                    )
                )
            for event_time, event_status, location, description in _events_for(
                status, now
            ):
                session.add(
                    LogisticsEvent(
                        order_id=order.id,
                        event_time=event_time,
                        status=event_status,
                        location=location,
                        description=description,
                    )
                )
            created += 1
        await session.commit()

    print(
        f"customer={customer.id} created={created} skipped={skipped} "
        f"total_specs={len(ORDER_SPECS)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
