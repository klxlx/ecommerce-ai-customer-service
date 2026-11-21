"""Handoff ticket schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HandoffStatus = Literal["open", "in_progress", "resolved"]


class HandoffUpdate(BaseModel):
    status: HandoffStatus


class HandoffReplyCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    action: Literal["return_request"] | None = None


class HandoffFeedbackCreate(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def require_one_value(self):
        if self.rating is None and not (self.comment or "").strip():
            raise ValueError("请至少选择星级或填写评价")
        return self


class HandoffFeedbackOut(BaseModel):
    ticket_id: int
    can_rate: bool
    rated: bool
    rating: int | None = None
    comment: str | None = None
    rated_at: datetime | None = None


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
    assignee_name: str = ""
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by_id: int | None = None
    resolved_by_name: str = ""
    business_status: str = ""
    rating: int | None = None
    rating_comment: str | None = None
    rated_at: datetime | None = None
    close_reason: str = ""
    resolution_note: str = ""
    created_at: datetime
    updated_at: datetime
