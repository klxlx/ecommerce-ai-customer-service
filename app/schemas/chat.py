"""Chat and conversation schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AfterSaleRecordOut(BaseModel):
    id: int
    service_type: str
    status: str
    refund_amount: str | None = None
    reason: str
    created_at: datetime


class AfterSaleContextOut(BaseModel):
    status: Literal["has_records", "no_records", "not_available", "query_error"]
    order_no: str
    order_status: str | None = None
    service_type: str
    records: list[AfterSaleRecordOut] = Field(default_factory=list)
    handoff_required: bool = False


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    intent: str | None = None
    sources: list | None = None
    after_sale: AfterSaleContextOut | None = Field(
        default=None, validation_alias="after_sale_context"
    )
    created_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    answer: str
    intent: str | None = None
    sources: list = Field(default_factory=list)
    trace_id: int | None = None
    handoff: dict | None = None
    after_sale: AfterSaleContextOut | None = None
    conversation_id: int
    message_id: int
