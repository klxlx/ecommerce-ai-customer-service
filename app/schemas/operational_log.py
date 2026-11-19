"""Schemas for the administrator operational-log view."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RetrievalResult = Literal["hit", "empty", "not_applicable"]
AfterSaleStatus = Literal[
    "has_records",
    "no_records",
    "not_available",
    "query_error",
]
RunStatus = Literal["success", "handoff", "failed", "unknown"]


class OperationalLogQuery(BaseModel):
    intent: str | None = Field(default=None, max_length=32)
    tool_name: str | None = Field(default=None, max_length=64)
    retrieval_result: RetrievalResult | None = None
    after_sale_status: AfterSaleStatus | None = None
    threshold_applied: bool | None = None
    threshold_exceeded: bool | None = None
    has_handoff: bool | None = None
    run_status: RunStatus | None = None
    user_id: int | None = Field(default=None, ge=1)
    conversation_id: int | None = Field(default=None, ge=1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

class OperationalLogSource(BaseModel):
    title: str
    page_start: int | None
    page_end: int | None


class OperationalLogItem(BaseModel):
    run_id: int
    conversation_id: int
    user_id: int
    intent: str | None
    tool_name: str
    retrieval_result: RetrievalResult
    source_count: int
    sources: list[OperationalLogSource]
    after_sale_status: AfterSaleStatus | None
    threshold_applied: bool
    threshold_amount: str | None
    threshold_limit: str | None
    threshold_exceeded: bool
    has_handoff: bool
    handoff_reason: str | None
    run_status: RunStatus
    created_at: datetime


class OperationalLogPage(BaseModel):
    items: list[OperationalLogItem]
    total: int
    limit: int
    offset: int
