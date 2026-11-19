"""Handoff ticket schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HandoffStatus = Literal["open", "in_progress", "resolved"]


class HandoffUpdate(BaseModel):
    status: HandoffStatus


class HandoffReplyCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class HandoffTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    user_id: int
    status: str
    summary: str
    slots: dict = Field(default_factory=dict)
    recent_messages: list = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
