"""Seed demo users, products, orders, logistics, and knowledge.

Idempotent: existing users/products/orders are skipped. Run with:

    python -m scripts.seed_demo

Set ``SEED_INDEX_KNOWLEDGE=0`` to skip PDF indexing (useful when you only need
business data and do not want to wait for the 1800-page policy PDFs).
"""

from __future__ import annotations

import asyncio
import os
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

# username -> role; every demo account uses the password ``password123``.
DEMO_USERS = (
    ("customer", "customer"),
    ("customer2", "customer"),
    ("customer3", "customer"),
    ("admin", "admin"),
    ("admin2", "admin"),
)

PRODUCTS = [
    ("EB-PRO", "声动 Pro 无线蓝牙耳机", "299.00", 50, "Pro", "曜石黑"),
    ("TH-450", "恒温智能保温杯", "88.50", 120, "450ml", "雾灰"),
    ("PB-10000", "疾充 10000mAh 便携充电宝", "129.00", 200, "10000mAh", "白色"),
    ("AP-320", "清呼吸 家用空气净化器", "899.00", 30, "320", "白色"),
    ("KB-87", "静音机械键盘 87 键", "459.00", 40, "87键", "深空灰"),
    ("SW-2", "轻氧智能手表 2 代", "1299.00", 25, "2代", "曜石黑"),
    ("SSD-1T", "疾速 1TB 移动固态硬盘", "699.00", 60, "1TB", "银色"),
    ("TB-200", "声波电动牙刷", "199.00", 150, "200", "薄荷绿"),
    ("HM-300", "迷你桌面加湿器", "79.00", 180, "300ml", "米白"),
    ("HP-500", "头戴式主动降噪耳机", "599.00", 35, "500", "黑色"),
]

ORDER_DEFS = [
    {
        "order_no": "ORD-20261117-0001",
        "username": "customer",
        "status": "运输中",
        "items": [("EB-PRO", 1, "299.00")],
        "logistics": [
            ("已揽收", "上海仓", "商家已揽收", 0),
            ("运输中", "杭州转运中心", "快件运输中", 2),
        ],
    },
    {
        "order_no": "ORD-20261110-0015",
        "username": "customer",
        "status": "已签收",
        "items": [("TH-450", 2, "88.50"), ("PB-10000", 1, "129.00")],
        "logistics": [
            ("已揽收", "上海仓", "商家已揽收", 0),
            ("运输中", "杭州转运中心", "快件运输中", 1),
            ("已签收", "收货地址", "本人签收", 4),
        ],
    },
    {
        "order_no": "ORD-20261118-0002",
        "username": "customer2",
        "status": "待发货",
        "items": [("SW-2", 1, "1299.00"), ("TB-200", 1, "199.00")],
        "logistics": [
            ("已下单", "上海仓", "仓库正在配货", 0),
        ],
    },
    {
        "order_no": "ORD-20261112-0021",
        "username": "customer2",
        "status": "已签收",
        "items": [("KB-87", 1, "459.00"), ("HM-300", 1, "79.00")],
        "logistics": [
            ("已揽收", "上海仓", "商家已揽收", 0),
            ("运输中", "苏州转运中心", "快件运输中", 1),
            ("已签收", "收货地址", "门卫代收", 3),
        ],
    },
    {
        "order_no": "ORD-20261119-0003",
        "username": "customer3",
        "status": "运输中",
        "items": [("SSD-1T", 1, "699.00")],
        "logistics": [
            ("已揽收", "深圳仓", "商家已揽收", 0),
            ("运输中", "广州转运中心", "快件运输中", 1),
        ],
    },
    {
        "order_no": "ORD-20261105-0033",
        "username": "customer3",
        "status": "已完成",
        "items": [("AP-320", 1, "899.00"), ("HP-500", 1, "599.00")],
        "logistics": [
            ("已揽收", "上海仓", "商家已揽收", 0),
            ("运输中", "杭州转运中心", "快件运输中", 1),
            ("已签收", "收货地址", "本人签收", 3),
        ],
    },
]


async def _seed_users(session) -> dict[str, User]:
    users: dict[str, User] = {}
    for username, role in DEMO_USERS:
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
    for sku, name, price, stock, model, color in PRODUCTS:
        existing = (
            await session.execute(select(Product).where(Product.sku == sku))
        ).scalar_one_or_none()
        if existing is None:
            existing = Product(
                sku=sku,
                name=name,
                model=model,
                color=color,
                price=Decimal(price),
                stock=stock,
                description="",
            )
            session.add(existing)
            await session.flush()
        elif not existing.model and not existing.color:
            existing.model = model
            existing.color = color
        products[sku] = existing
    await session.commit()
    return products


async def _seed_orders(
    session,
    users: dict[str, User],
    products: dict[str, Product],
) -> dict[str, Order]:
    orders: dict[str, Order] = {}
    now = datetime.now(timezone.utc)
    for spec in ORDER_DEFS:
        existing = (
            await session.execute(
                select(Order).where(Order.order_no == spec["order_no"])
            )
        ).scalar_one_or_none()
        if existing is not None:
            orders[spec["order_no"]] = existing
            continue
        customer = users[spec["username"]]
        order = Order(
            order_no=spec["order_no"],
            user_id=customer.id,
            status=spec["status"],
            total_amount=Decimal(
                sum(float(unit) * qty for _, qty, unit in spec["items"])
            ).quantize(Decimal("0.01")),
        )
        session.add(order)
        await session.flush()
        for sku, qty, unit in spec["items"]:
            product = products[sku]
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name=product.name,
                    model=product.model,
                    color=product.color,
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
    # Keep the order snapshot aligned when the demo product metadata is
    # introduced or corrected after the order was first seeded.
    products_by_id = {product.id: product for product in products.values()}
    items = list((await session.execute(select(OrderItem))).scalars().all())
    for item in items:
        product = products_by_id.get(item.product_id)
        if product is not None and (not item.model or not item.color):
            item.model = product.model
            item.color = product.color
    await session.commit()
    return orders


async def _seed_after_sales(session, orders: dict[str, Order]) -> None:
    specs = [
        {
            "order_no": "ORD-20261110-0015",
            "service_type": "退货退款",
            "status": "已完成",
            "refund_amount": "88.50",
            "reason": "商品破损",
        },
        {
            "order_no": "ORD-20261112-0021",
            "service_type": "换货",
            "status": "处理中",
            "refund_amount": "0.00",
            "reason": "键帽磨损",
        },
    ]
    for spec in specs:
        order = orders[spec["order_no"]]
        existing = (
            await session.execute(
                select(AfterSale).where(
                    AfterSale.order_id == order.id,
                    AfterSale.service_type == spec["service_type"],
                    AfterSale.reason == spec["reason"],
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                AfterSale(
                    order_id=order.id,
                    service_type=spec["service_type"],
                    status=spec["status"],
                    refund_amount=Decimal(spec["refund_amount"]),
                    reason=spec["reason"],
                )
            )
    await session.commit()


async def main() -> int:
    settings = get_settings()
    container = build_container(settings)
    factory = create_session_factory(settings.database_url)
    index_knowledge = os.getenv("SEED_INDEX_KNOWLEDGE", "1") == "1"
    result = None
    async with factory() as session:
        users = await _seed_users(session)
        products = await _seed_products(session)
        orders = await _seed_orders(session, users, products)
        await _seed_after_sales(session, orders)
        if index_knowledge:
            result = await index_registered_pdfs(
                session,
                container.providers.embeddings,
                PDF_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                embedding_model=settings.embedding_model,
            )

    print(
        f"seeded users={len(users)} products={len(products)} "
        f"orders={len(orders)} knowledge_documents="
        f"{result.documents_seen if result else 0} "
        f"indexed={result.documents_indexed if result else 0} "
        f"skipped={result.documents_skipped if result else 0} "
        f"versions={result.versions_published if result else 0} "
        f"chunks={result.chunks_indexed if result else 0} "
        f"errors={len(result.errors) if result else 0}"
    )
    if result:
        for error in result.errors:
            print(f"  ERROR {error}", file=sys.stderr)
    return 1 if result and result.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
