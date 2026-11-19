"""Test helper functions for seeding data."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    AfterSale,
    LogisticsEvent,
    Order,
    OrderItem,
    Product,
    User,
)


async def create_user(
    session: AsyncSession,
    username: str,
    password: str = "password123",
    role: str = "customer",
    active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=active,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def create_product(
    session: AsyncSession,
    sku: str,
    name: str,
    price: str | Decimal = "100.00",
    stock: int = 10,
    description: str = "",
) -> Product:
    product = Product(
        sku=sku,
        name=name,
        price=Decimal(price),
        stock=stock,
        description=description,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def create_order(
    session: AsyncSession,
    user_id: int,
    order_no: str,
    status: str = "已发货",
    total: str | Decimal = "199.00",
    items: list[tuple[str, int, str]] | None = None,
    logistics: list[dict] | None = None,
) -> Order:
    order = Order(
        order_no=order_no,
        user_id=user_id,
        status=status,
        total_amount=Decimal(total),
    )
    session.add(order)
    await session.flush()
    for product_name, quantity, unit_price in items or []:
        product = Product(
            sku=f"SKU-{order_no}-{product_name}",
            name=product_name,
            price=Decimal(unit_price),
            stock=100,
        )
        session.add(product)
        await session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product_name,
                quantity=quantity,
                unit_price=Decimal(unit_price),
            )
        )
    for event in logistics or []:
        session.add(
            LogisticsEvent(
                order_id=order.id,
                event_time=event.get("event_time", datetime.now(timezone.utc)),
                status=event.get("status", "已发货"),
                location=event.get("location", ""),
                description=event.get("description", ""),
            )
        )
    await session.commit()
    await session.refresh(order)
    return order


async def create_after_sale(
    session: AsyncSession,
    order_id: int,
    *,
    service_type: str = "退款",
    status: str = "处理中",
    refund_amount: str | Decimal | None = None,
    reason: str = "",
) -> AfterSale:
    after_sale = AfterSale(
        order_id=order_id,
        service_type=service_type,
        status=status,
        refund_amount=Decimal(refund_amount) if refund_amount is not None else None,
        reason=reason,
    )
    session.add(after_sale)
    await session.commit()
    await session.refresh(after_sale)
    return after_sale
