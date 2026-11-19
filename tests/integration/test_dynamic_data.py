"""Dynamic catalog (price/stock) integration tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Product
from app.schemas.catalog import AmbiguousProduct, ProductFact
from app.tools.catalog import get_product_price_stock
from tests.helpers import create_product


@pytest.mark.anyio
async def test_price_stock_reflect_db_updates_without_reindexing(db_session):
    product = await create_product(db_session, "SKU-001", "无线蓝牙耳机", price="100.00", stock=10)

    fact = await get_product_price_stock(db_session, "SKU-001")
    assert isinstance(fact, ProductFact)
    assert fact.price == Decimal("100.00")
    assert fact.stock == 10

    # Mutate price/stock directly in the database.
    product.price = Decimal("150.00")
    product.stock = 5
    await db_session.commit()

    fact2 = await get_product_price_stock(db_session, "SKU-001")
    assert fact2.price == Decimal("150.00")
    assert fact2.stock == 5


@pytest.mark.anyio
async def test_lookup_by_exact_sku(db_session):
    await create_product(db_session, "SKU-009", "恒温保温杯", price="88.50", stock=3)
    fact = await get_product_price_stock(db_session, "SKU-009")
    assert isinstance(fact, ProductFact)
    assert fact.name == "恒温保温杯"


@pytest.mark.anyio
async def test_ambiguous_name_requires_clarification(db_session):
    await create_product(db_session, "SKU-A", "声动 Pro 无线蓝牙耳机", price="299.00", stock=4)
    await create_product(db_session, "SKU-B", "声动 Lite 无线蓝牙耳机", price="199.00", stock=8)
    result = await get_product_price_stock(db_session, "声动")
    assert isinstance(result, AmbiguousProduct)
    assert {c.sku for c in result.candidates} == {"SKU-A", "SKU-B"}


@pytest.mark.anyio
async def test_unknown_product_returns_none(db_session):
    result = await get_product_price_stock(db_session, "不存在的商品")
    assert result is None
