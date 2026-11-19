"""Administrator handoff queue routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limit_admin
from app.db.models import User
from app.db.session import get_session
from app.schemas.chat import ChatMessageOut
from app.schemas.handoff import (
    HandoffReplyCreate,
    HandoffTicketOut,
    HandoffUpdate,
)
from app.services import handoff as handoff_service

router = APIRouter()


@router.get("/handoffs", response_model=list[HandoffTicketOut])
async def list_handoffs(
    status_filter: str | None = Query(default=None, alias="status"),
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    tickets = await handoff_service.list_tickets(session, status_filter)
    return [HandoffTicketOut.model_validate(t) for t in tickets]


@router.get("/handoffs/{handoff_id}", response_model=HandoffTicketOut)
async def get_handoff(
    handoff_id: int,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    ticket = await handoff_service.get_ticket(session, handoff_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return HandoffTicketOut.model_validate(ticket)


@router.patch("/handoffs/{handoff_id}", response_model=HandoffTicketOut)
async def update_handoff(
    handoff_id: int,
    payload: HandoffUpdate,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    ticket = await handoff_service.update_status(session, handoff_id, payload.status)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    await session.commit()
    return HandoffTicketOut.model_validate(ticket)


@router.get(
    "/handoffs/{handoff_id}/messages",
    response_model=list[ChatMessageOut],
)
async def list_handoff_messages(
    handoff_id: int,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    messages = await handoff_service.list_messages(session, handoff_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return [ChatMessageOut.model_validate(message) for message in messages]


@router.post(
    "/handoffs/{handoff_id}/messages",
    response_model=ChatMessageOut,
)
async def reply_to_handoff(
    handoff_id: int,
    payload: HandoffReplyCreate,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await handoff_service.reply_and_return_to_agent(
        session, handoff_id, payload.content
    )
    if result is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    message, _ticket = result
    await session.commit()
    return ChatMessageOut.model_validate(message)
