"""Read-only after-sales facts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AfterSaleFact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_type: str
    status: str
    refund_amount: Decimal | None
    reason: str
    created_at: datetime
    updated_at: datetime
