"""Repository for product price and stock (SQL only, never vectorized)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product
from app.schemas.catalog import AmbiguousProduct, ProductFact, ProductRef


async def get_product_price_stock(
    session: AsyncSession, sku_or_name: str
) -> ProductFact | AmbiguousProduct | None:
    """Resolve a product by exact SKU or by name.

    Returns ``None`` when no product matches and an ``AmbiguousProduct`` when a
    name matches more than one product so the caller can ask for clarification.
    """
    by_sku = (
        await session.execute(select(Product).where(Product.sku == sku_or_name))
    ).scalar_one_or_none()
    if by_sku is not None:
        return _to_fact(by_sku)

    by_name = (
        await session.execute(
            select(Product).where(Product.name.ilike(f"%{sku_or_name}%"))
        )
    ).scalars().all()
    if len(by_name) == 1:
        return _to_fact(by_name[0])
    if len(by_name) > 1:
        return AmbiguousProduct(
            candidates=[ProductRef(sku=p.sku, name=p.name) for p in by_name]
        )
    return None


def _to_fact(product: Product) -> ProductFact:
    return ProductFact(
        sku=product.sku,
        name=product.name,
        price=product.price,
        stock=product.stock,
        description=product.description,
    )
