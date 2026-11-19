"""Repository for handoff tickets."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HandoffTicket


async def create_handoff(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
    summary: str,
    slots: dict,
    recent_messages: list,
    evidence: dict,
    status: str = "open",
) -> HandoffTicket:
    ticket = HandoffTicket(
        conversation_id=conversation_id,
        user_id=user_id,
        status=status,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def list_handoffs(
    session: AsyncSession, status: str | None = None
) -> list[HandoffTicket]:
    stmt = select(HandoffTicket).order_by(HandoffTicket.created_at.desc())
    if status is not None:
        stmt = stmt.where(HandoffTicket.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_handoff(
    session: AsyncSession, handoff_id: int
) -> HandoffTicket | None:
    return await session.get(HandoffTicket, handoff_id)


async def get_active_for_conversation(
    session: AsyncSession, conversation_id: int
) -> HandoffTicket | None:
    result = await session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.conversation_id == conversation_id,
            HandoffTicket.status.in_(("open", "in_progress")),
        )
        .order_by(HandoffTicket.created_at.desc(), HandoffTicket.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def update_status(
    session: AsyncSession, ticket: HandoffTicket, status: str
) -> HandoffTicket:
    ticket.status = status
    await session.flush()
    # Reload the server-side onupdate timestamp so callers can serialize it.
    await session.refresh(ticket)
    return ticket
