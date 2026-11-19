"""Seed demo users, products, orders, logistics, and knowledge.

Idempotent: existing users/products/orders are skipped. Run with:

    python -m scripts.seed_demo

Set ``MOCK_PROVIDERS=1`` to index knowledge without an embedding API key.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select

from app.bootstrap import build_container
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import (
    AfterSale,
    LogisticsEvent,
    Order,
    OrderItem,
    Product,
    User,
)
from app.db.session import create_session_factory
from app.rag.indexer import index_registered_pdfs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_REGISTRY_PATH = PROJECT_ROOT / "data" / "knowledge" / "pdf_sources.json"

PRODUCTS = [
    ("EB-PRO", "声动 Pro 无线蓝牙耳机", "299.00", 50),
    ("TH-450", "恒温智能保温杯", "88.50", 120),
    ("PB-10000", "疾充 10000mAh 便携充电宝", "129.00", 200),
    ("AP-320", "清呼吸 家用空气净化器", "899.00", 30),
]


async def _seed_users(session) -> dict[str, User]:
    users: dict[str, User] = {}
    for username, role in (("customer", "customer"), ("admin", "admin")):
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is None:
            existing = User(
                username=username,
                password_hash=hash_password("password123"),
                role=role,
                is_active=True,
            )
            session.add(existing)
            await session.flush()
        users[username] = existing
    await session.commit()
    return users


async def _seed_products(session) -> dict[str, Product]:
    products: dict[str, Product] = {}
    for sku, name, price, stock in PRODUCTS:
        existing = (
            await session.execute(select(Product).where(Product.sku == sku))
        ).scalar_one_or_none()
        if existing is None:
            existing = Product(
                sku=sku,
                name=name,
                price=Decimal(price),
                stock=stock,
                description="",
            )
            session.add(existing)
            await session.flush()
        products[sku] = existing
    await session.commit()
    return products


async def _seed_orders(
    session,
    customer: User,
    products: dict[str, Product],
) -> dict[str, Order]:
    orders: dict[str, Order] = {}
    order_defs = [
        {
            "order_no": "ORD-20261117-0001",
            "status": "运输中",
            "items": [("EB-PRO", 1, "299.00")],
            "logistics": [
                ("已揽收", "上海仓", "商家已揽收", 0),
                ("运输中", "杭州转运中心", "快件运输中", 2),
            ],
        },
        {
            "order_no": "ORD-20261110-0015",
            "status": "已签收",
            "items": [("TH-450", 2, "88.50"), ("PB-10000", 1, "129.00")],
            "logistics": [
                ("已揽收", "上海仓", "商家已揽收", 0),
                ("运输中", "杭州转运中心", "快件运输中", 1),
                ("已签收", "收货地址", "本人签收", 4),
            ],
        },
    ]
    for spec in order_defs:
        existing = (
            await session.execute(
                select(Order).where(Order.order_no == spec["order_no"])
            )
        ).scalar_one_or_none()
        if existing is not None:
            orders[spec["order_no"]] = existing
            continue
        order = Order(
            order_no=spec["order_no"],
            user_id=customer.id,
            status=spec["status"],
            total_amount=Decimal(
                sum(
                    float(unit) * qty
                    for _, qty, unit in spec["items"]
                )
            ).quantize(Decimal("0.01")),
        )
        session.add(order)
        await session.flush()
        now = datetime.now(timezone.utc)
        for sku, qty, unit in spec["items"]:
            product = products[sku]
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name=product.name,
                    quantity=qty,
                    unit_price=Decimal(unit),
                )
            )
        for status, location, desc, hours_ago in spec["logistics"]:
            session.add(
                LogisticsEvent(
                    order_id=order.id,
                    event_time=now - timedelta(hours=hours_ago),
                    status=status,
                    location=location,
                    description=desc,
                )
            )
        orders[spec["order_no"]] = order
    await session.commit()
    return orders


async def _seed_after_sales(session, orders: dict[str, Order]) -> None:
    order = orders["ORD-20261110-0015"]
    existing = (
        await session.execute(
            select(AfterSale).where(
                AfterSale.order_id == order.id,
                AfterSale.service_type == "退货退款",
                AfterSale.reason == "商品破损",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            AfterSale(
                order_id=order.id,
                service_type="退货退款",
                status="已完成",
                refund_amount=Decimal("88.50"),
                reason="商品破损",
            )
        )
    await session.commit()


async def main() -> int:
    settings = get_settings()
    container = build_container(settings)
    factory = create_session_factory(settings.database_url)
    async with factory() as session:
        users = await _seed_users(session)
        products = await _seed_products(session)
        orders = await _seed_orders(session, users["customer"], products)
        await _seed_after_sales(session, orders)
        result = await index_registered_pdfs(
            session,
            container.providers.embeddings,
            PDF_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            embedding_model=settings.embedding_model,
        )

    print(
        f"seeded users={len(users)} products={len(products)} "
        f"knowledge documents={result.documents_seen} "
        f"indexed={result.documents_indexed} skipped={result.documents_skipped} "
        f"versions={result.versions_published} chunks={result.chunks_indexed} "
        f"errors={len(result.errors)}"
    )
    for error in result.errors:
        print(f"  ERROR {error}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
