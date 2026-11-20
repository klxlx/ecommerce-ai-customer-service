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


class HandoffReleaseRequest(BaseModel):
    """Conclusion written back to the conversation before returning to AI."""

    note: str = Field(min_length=1, max_length=500)


class HandoffLiveSessionOut(BaseModel):
    id: int
    conversation_id: int
    user_id: int
    username: str
    status: HandoffStatus
    kind: str = "human"
    category: str = "general"
    assignee_id: int | None = None
    assignee_name: str = ""
    summary: str = ""
    created_at: datetime
    claimed_at: datetime | None = None
    waiting_seconds: int = 0
    last_message: str = ""
    last_message_role: str = ""
    last_message_at: datetime | None = None
    message_count: int = 0


class HandoffTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    user_id: int
    status: str
    kind: str = "human"
    category: str = "general"
    summary: str
    slots: dict = Field(default_factory=dict)
    recent_messages: list = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    assignee_id: int | None = None
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    close_reason: str = ""
    resolution_note: str = ""
    created_at: datetime
    updated_at: datetime
