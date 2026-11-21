"""Order and logistics domain schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class OrderItemFact(BaseModel):
    product_name: str
    model: str = ""
    color: str = ""
    quantity: int
    unit_price: Decimal


class LogisticsEventFact(BaseModel):
    event_time: datetime
    status: str
    location: str
    description: str


class OrderFact(BaseModel):
    order_no: str
    status: str
    total_amount: Decimal
    created_at: datetime
    items: list[OrderItemFact]
    logistics: list[LogisticsEventFact]
