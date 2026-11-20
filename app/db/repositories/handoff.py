"""Repository for handoff tickets."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, HandoffTicket


async def create_handoff(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
    summary: str,
    slots: dict,
    recent_messages: list,
    evidence: dict,
    status: str = "open",
    kind: str = "human",
    question_norm: str = "",
    question_embedding: list[float] | None = None,
    occurrence_count: int = 1,
    last_seen_at: datetime | None = None,
) -> HandoffTicket:
    ticket = HandoffTicket(
        conversation_id=conversation_id,
        user_id=user_id,
        status=status,
        kind=kind,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
        question_norm=question_norm,
        question_embedding=question_embedding,
        occurrence_count=occurrence_count,
        last_seen_at=last_seen_at,
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def list_unresolved_for_user(
    session: AsyncSession, user_id: int
) -> list[HandoffTicket]:
    """Return every ticket still occupying the user's unresolved quota."""
    result = await session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.user_id == user_id,
            HandoffTicket.status.in_(("open", "in_progress")),
        )
        .order_by(HandoffTicket.created_at.asc(), HandoffTicket.id.asc())
    )
    return list(result.scalars().all())


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
            HandoffTicket.kind == "human",
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


async def list_active(session: AsyncSession) -> list[HandoffTicket]:
    """Return tickets that still need a human: waiting or being handled."""
    result = await session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.kind == "human",
            HandoffTicket.status.in_(("open", "in_progress")),
        )
        .order_by(HandoffTicket.created_at.asc())
    )
    return list(result.scalars().all())


async def claim(
    session: AsyncSession, handoff_id: int, admin_id: int
) -> bool:
    """Atomically move an ``open`` ticket to ``in_progress``.

    Returns ``False`` when the ticket is missing or already claimed by someone
    else, so two administrators can never share one conversation.
    """
    result = await session.execute(
        update(HandoffTicket)
        .where(
            HandoffTicket.id == handoff_id,
            HandoffTicket.kind == "human",
            or_(
                HandoffTicket.status == "open",
                and_(
                    HandoffTicket.status == "in_progress",
                    HandoffTicket.assignee_id.is_(None),
                ),
            )
        )
        .values(
            status="in_progress",
            assignee_id=admin_id,
            claimed_at=datetime.now(timezone.utc),
        )
    )
    return result.rowcount == 1


async def latest_messages(
    session: AsyncSession, conversation_ids: list[int]
) -> dict[int, ChatMessage]:
    """Return the newest message per conversation for the live session list."""
    if not conversation_ids:
        return {}
    ranked = (
        select(
            ChatMessage.id.label("message_id"),
            func.row_number()
            .over(
                partition_by=ChatMessage.conversation_id,
                order_by=ChatMessage.id.desc(),
            )
            .label("row_number"),
        )
        .where(ChatMessage.conversation_id.in_(conversation_ids))
        .subquery()
    )
    result = await session.execute(
        select(ChatMessage)
        .join(ranked, ChatMessage.id == ranked.c.message_id)
        .where(ranked.c.row_number == 1)
    )
    return {
        message.conversation_id: message
        for message in result.scalars().all()
    }


async def message_counts(
    session: AsyncSession, conversation_ids: list[int]
) -> dict[int, int]:
    if not conversation_ids:
        return {}
    result = await session.execute(
        select(ChatMessage.conversation_id, func.count(ChatMessage.id))
        .where(ChatMessage.conversation_id.in_(conversation_ids))
        .group_by(ChatMessage.conversation_id)
    )
    return {conversation_id: count for conversation_id, count in result.all()}
