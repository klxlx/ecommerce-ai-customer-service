"""Photo return/exchange request schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

DamageLevel = Literal["轻微", "中等", "严重", "完全损坏"]
ReturnReason = Literal[
    "7天无理由",
    "质量问题",
    "运输破损",
    "发错货",
    "缺件",
    "其他",
]
DamageType = Literal["屏幕裂", "外壳磕碰", "缺件", "错发", "未知"]


class DetectedObjectOut(BaseModel):
    label: str
    label_zh: str
    confidence: float
    box: list[float] = Field(default_factory=list)


class ReturnAnalysisOut(BaseModel):
    analysis_id: int
    image_id: str
    passed: bool
    confidence: float
    product: str = ""
    product_class: str = ""
    model: str = ""
    color: str = ""
    accessories: list[str] = Field(default_factory=list)
    damage_type: DamageType = "未知"
    detected_objects: list[DetectedObjectOut] = Field(default_factory=list)
    message: str = ""
    provider: str = ""


class ReturnOrderItemOut(BaseModel):
    order_item_id: int
    sku: str
    product_name: str
    model: str = ""
    color: str = ""
    quantity: int
    unit_price: Decimal


class ReturnOrderContextOut(BaseModel):
    order_no: str
    status: str
    items: list[ReturnOrderItemOut]


class ReturnSubmitIn(BaseModel):
    conversation_id: int
    analysis_id: int
    order_no: str = Field(min_length=4, max_length=64)
    order_item_id: int
    damage_level: DamageLevel
    damage_type: DamageType
    reason: ReturnReason
    accessories: list[str] = Field(default_factory=list, max_length=20)
    return_address: str = Field(min_length=5, max_length=300)
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    confirmed: bool


class ReturnSubmitOut(BaseModel):
    ticket_id: int
    duplicate: bool
    answer: str
    conversation_id: int
    message_id: int
