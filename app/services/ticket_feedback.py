"""Customer-facing ticket status messages and post-ticket feedback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import ChatMessage, HandoffTicket, User
from app.db.repositories import chat as chat_repo

FEEDBACK_ACTION = "ticket_feedback"
FEEDBACK_PROMPT = "如果愿意，可以为这次服务打个分、留下不超过 50 字的评价；也可以直接跳过。"


def can_rate(ticket: HandoffTicket) -> bool:
    """Only completed customer-facing tickets can be rated."""
    return ticket.kind == "human" and ticket.status == "resolved"


def feedback_action(ticket: HandoffTicket) -> dict[str, Any]:
    return {
        "type": FEEDBACK_ACTION,
        "ticket_id": ticket.id,
        "can_rate": can_rate(ticket),
        "rated": ticket.rated_at is not None,
        "rating": ticket.rating,
        "comment": ticket.rating_comment,
    }


def feedback_payload(ticket: HandoffTicket) -> dict[str, Any]:
    return {
        "ticket_id": ticket.id,
        "can_rate": can_rate(ticket),
        "rated": ticket.rated_at is not None,
        "rating": ticket.rating,
        "comment": ticket.rating_comment,
        "rated_at": ticket.rated_at,
    }


async def add_status_notice(
    session: AsyncSession, ticket: HandoffTicket, content: str
) -> None:
    """Add a customer-visible system notice for a human ticket only."""
    if ticket.kind != "human":
        return
    await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "system",
        content,
    )


async def add_final_feedback(
    session: AsyncSession,
    ticket: HandoffTicket,
    content: str = "本次人工服务已结束，工单已处理完成，智能体继续为您服务。",
) -> None:
    """Attach the one-time rating card to the final customer-facing notice."""
    if not can_rate(ticket):
        return
    await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "system",
        f"{content} {FEEDBACK_PROMPT}",
        actions=[feedback_action(ticket)],
    )


def mark_resolved(
    ticket: HandoffTicket,
    *,
    resolved_by_id: int | None,
    business_status: str = "已解决",
    close_reason: str = "resolved_by_agent",
    resolution_note: str | None = None,
) -> None:
    ticket.status = "resolved"
    ticket.resolved_at = datetime.now(timezone.utc)
    ticket.resolved_by_id = resolved_by_id
    ticket.business_status = business_status
    ticket.close_reason = close_reason
    if resolution_note is not None:
        ticket.resolution_note = resolution_note


async def save_feedback(
    session: AsyncSession,
    *,
    ticket: HandoffTicket,
    customer: User,
    rating: int | None,
    comment: str | None,
) -> HandoffTicket:
    if ticket.user_id != customer.id:
        raise PermissionError("ticket does not belong to customer")
    if not can_rate(ticket):
        raise ValueError("ticket is not ready for feedback")
    if ticket.rating is not None or ticket.rated_at is not None:
        raise ValueError("ticket already rated")
    ticket.rating = rating
    ticket.rating_comment = (comment or "").strip() or None
    ticket.rated_at = datetime.now(timezone.utc)
    await session.flush()

    # Keep the rating card's persisted action in sync so a page refresh shows
    # the submitted state instead of an empty form.
    result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == ticket.conversation_id,
            ChatMessage.role == "system",
        )
        .order_by(ChatMessage.id.desc())
        .limit(10)
    )
    for message in result.scalars().all():
        actions = list(message.actions or [])
        changed = False
        for index, action in enumerate(actions):
            if (
                isinstance(action, dict)
                and action.get("type") == FEEDBACK_ACTION
                and action.get("ticket_id") == ticket.id
            ):
                updated = dict(action)
                updated.update(
                    can_rate=can_rate(ticket),
                    rated=ticket.rated_at is not None,
                    rating=ticket.rating,
                    comment=ticket.rating_comment,
                )
                actions[index] = updated
                changed = True
        if changed:
            message.actions = actions
            flag_modified(message, "actions")
            break

    await session.flush()
    await session.refresh(ticket)
    return ticket
