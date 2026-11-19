"""Catalog (price/stock) domain schemas."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ProductRef(BaseModel):
    sku: str
    name: str


class ProductFact(BaseModel):
    sku: str
    name: str
    price: Decimal
    stock: int
    description: str


class AmbiguousProduct(BaseModel):
    candidates: list[ProductRef]
