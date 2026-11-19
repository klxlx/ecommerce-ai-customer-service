"""Catalog tool: query product price and stock from PostgreSQL."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import catalog as catalog_repo
from app.schemas.catalog import AmbiguousProduct, ProductFact


async def get_product_price_stock(
    session: AsyncSession, sku_or_name: str
) -> ProductFact | AmbiguousProduct | None:
    """Query the current price and stock of a product by SKU or name.

    This data lives only in the ``products`` table and is never indexed.
    """
    return await catalog_repo.get_product_price_stock(session, sku_or_name)
