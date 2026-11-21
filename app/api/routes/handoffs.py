"""Administrator handoff queue and live human-agent console routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limit_admin
from app.db.models import User
from app.db.session import get_session
from app.schemas.chat import ChatMessageOut
from app.schemas.handoff import (
    HandoffLiveSessionOut,
    HandoffReleaseRequest,
    HandoffReplyCreate,
    HandoffTicketOut,
    HandoffUpdate,
)
from app.services import handoff as handoff_service
from app.services import returns as return_service

router = APIRouter()


@router.get("/handoffs/live", response_model=list[HandoffLiveSessionOut])
async def list_live_sessions(
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    """Active sessions for the human-agent console (open + in_progress)."""
    return await handoff_service.live_sessions(session)


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


@router.post("/handoffs/{handoff_id}/claim", response_model=HandoffTicketOut)
async def claim_handoff(
    handoff_id: int,
    admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    """Take ownership of a waiting ticket; only one administrator can win."""
    try:
        ticket = await handoff_service.claim(session, handoff_id, admin)
    except handoff_service.HandoffNotFound:
        raise HTTPException(status_code=404, detail="工单不存在") from None
    except handoff_service.HandoffForbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="内部工单不能接入人工会话",
        ) from None
    except handoff_service.HandoffConflict as exc:
        name = exc.assignee_name or "其他客服"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该会话已由 {name} 接入",
        ) from None
    await session.commit()
    return HandoffTicketOut.model_validate(ticket)


@router.patch("/handoffs/{handoff_id}", response_model=HandoffTicketOut)
async def update_handoff(
    handoff_id: int,
    payload: HandoffUpdate,
    admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    ticket = await handoff_service.get_ticket(session, handoff_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")

    if payload.status == "in_progress":
        if ticket.kind != "human":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="内部工单不能接入人工会话",
            )
        if ticket.status == "resolved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="已解决的工单不能重新接入",
            )
        if (
            ticket.status == "in_progress"
            and ticket.assignee_id is not None
            and ticket.assignee_id != admin.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该工单已由其他客服接入",
            )
        if ticket.status != "in_progress" or ticket.assignee_id is None:
            try:
                ticket = await handoff_service.claim(session, handoff_id, admin)
            except handoff_service.HandoffConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"该工单已由 {exc.assignee_name or '其他客服'} 接入",
                ) from None
        await session.commit()
        return HandoffTicketOut.model_validate(ticket)

    if (
        payload.status == "resolved"
        and ticket.kind == "human"
        and ticket.category in {"return_exchange", "refund"}
        and (
            ticket.status != "in_progress" or ticket.assignee_id != admin.id
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="退换货/退款工单需先点击处理中接入，并由接入的管理员解决",
        )

    try:
        ticket = await handoff_service.update_status(
            session, handoff_id, payload.status, admin
        )
    except handoff_service.HandoffForbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该工单当前不由你接待",
        ) from None
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    await session.commit()
    return HandoffTicketOut.model_validate(ticket)


@router.get("/handoffs/{handoff_id}/return-image")
async def get_return_image(
    handoff_id: int,
    request: Request,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    path = await return_service.ticket_image_path(
        session,
        handoff_id,
        request.app.state.settings,
    )
    if path is None:
        raise HTTPException(status_code=404, detail="退换货图片不存在")
    return FileResponse(path, media_type="image/jpeg")


@router.get(
    "/handoffs/{handoff_id}/messages",
    response_model=list[ChatMessageOut],
)
async def list_handoff_messages(
    handoff_id: int,
    after_id: int | None = Query(default=None, ge=0),
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    messages = await handoff_service.list_messages(session, handoff_id, after_id)
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
    admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    """Send a reply while keeping the session open for more messages."""
    try:
        message = await handoff_service.reply(
            session, handoff_id, admin, payload.content, payload.action
        )
    except handoff_service.HandoffNotFound:
        raise HTTPException(status_code=404, detail="工单不存在") from None
    except handoff_service.HandoffConflict as exc:
        name = exc.assignee_name or "其他客服"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该会话已由 {name} 接入",
        ) from None
    except handoff_service.HandoffForbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该会话当前不由你接待",
        ) from None
    await session.commit()
    return ChatMessageOut.model_validate(message)


@router.post("/handoffs/{handoff_id}/release", response_model=HandoffTicketOut)
async def release_handoff(
    handoff_id: int,
    payload: HandoffReleaseRequest,
    admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    """Close the session, record the conclusion, and return to the agent."""
    try:
        ticket = await handoff_service.release(
            session, handoff_id, admin, payload.note
        )
    except handoff_service.HandoffNotFound:
        raise HTTPException(status_code=404, detail="工单不存在") from None
    except handoff_service.HandoffForbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该会话当前不由你接待",
        ) from None
    await session.commit()
    return HandoffTicketOut.model_validate(ticket)
