"""Handoff service: queue, live console, claim, reply, and release."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, HandoffTicket, User
from app.db.repositories import auth as auth_repo
from app.db.repositories import chat as chat_repo
from app.db.repositories import handoff as handoff_repo


class HandoffError(Exception):
    """Base class for handoff state errors mapped to HTTP responses."""


class HandoffNotFound(HandoffError):
    """The ticket does not exist."""


class HandoffConflict(HandoffError):
    """The ticket has already been claimed by another administrator."""

    def __init__(self, assignee_id: int | None, assignee_name: str = "") -> None:
        super().__init__("handoff already claimed")
        self.assignee_id = assignee_id
        self.assignee_name = assignee_name


class HandoffForbidden(HandoffError):
    """The caller is not the administrator who owns this ticket."""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _elapsed_seconds(value: datetime | None, now: datetime) -> int:
    moment = _utc(value)
    if moment is None:
        return 0
    return max(0, int((now - moment).total_seconds()))


async def create_ticket(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
    summary: str,
    slots: dict,
    recent_messages: list,
    evidence: dict,
) -> HandoffTicket:
    return await handoff_repo.create_handoff(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
    )


async def list_tickets(
    session: AsyncSession, status: str | None = None
) -> list[HandoffTicket]:
    return await handoff_repo.list_handoffs(session, status)


async def get_ticket(
    session: AsyncSession, handoff_id: int
) -> HandoffTicket | None:
    return await handoff_repo.get_handoff(session, handoff_id)


async def get_active_ticket(
    session: AsyncSession, conversation_id: int
) -> HandoffTicket | None:
    return await handoff_repo.get_active_for_conversation(
        session, conversation_id
    )


async def live_sessions(session: AsyncSession) -> list[dict]:
    """Serialize the active queue for the human-agent console."""
    tickets = await handoff_repo.list_active(session)
    if not tickets:
        return []

    user_ids = {ticket.user_id for ticket in tickets}
    user_ids.update(
        ticket.assignee_id for ticket in tickets if ticket.assignee_id
    )
    users = await auth_repo.get_users_by_ids(session, sorted(user_ids))
    conversation_ids = [ticket.conversation_id for ticket in tickets]
    latest = await handoff_repo.latest_messages(session, conversation_ids)
    counts = await handoff_repo.message_counts(session, conversation_ids)

    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for ticket in tickets:
        customer = users.get(ticket.user_id)
        assignee = users.get(ticket.assignee_id) if ticket.assignee_id else None
        last = latest.get(ticket.conversation_id)
        items.append(
            {
                "id": ticket.id,
                "conversation_id": ticket.conversation_id,
                "user_id": ticket.user_id,
                "username": customer.username if customer else "",
                "status": ticket.status,
                "assignee_id": ticket.assignee_id,
                "assignee_name": assignee.username if assignee else "",
                "summary": ticket.summary,
                "created_at": ticket.created_at,
                "claimed_at": ticket.claimed_at,
                "waiting_seconds": _elapsed_seconds(ticket.created_at, now),
                "last_message": last.content if last else "",
                "last_message_role": last.role if last else "",
                "last_message_at": last.created_at if last else None,
                "message_count": counts.get(ticket.conversation_id, 0),
            }
        )
    return items


async def claim(
    session: AsyncSession, handoff_id: int, admin: User
) -> HandoffTicket:
    """Claim a waiting ticket for ``admin`` or raise a typed error."""
    claimed = await handoff_repo.claim(session, handoff_id, admin.id)
    if not claimed:
        ticket = await handoff_repo.get_handoff(session, handoff_id)
        if ticket is None:
            raise HandoffNotFound
        assignee = (
            await auth_repo.get_user_by_id(session, ticket.assignee_id)
            if ticket.assignee_id
            else None
        )
        raise HandoffConflict(
            ticket.assignee_id,
            assignee.username if assignee else "",
        )

    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:  # pragma: no cover - guarded by the update above
        raise HandoffNotFound
    await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "system",
        "人工客服已接入，请稍候。",
    )
    await session.refresh(ticket)
    return ticket


def _require_owner(ticket: HandoffTicket | None, admin: User) -> HandoffTicket:
    if ticket is None:
        raise HandoffNotFound
    if ticket.status != "in_progress" or ticket.assignee_id != admin.id:
        raise HandoffForbidden
    return ticket


async def reply(
    session: AsyncSession, handoff_id: int, admin: User, content: str
) -> ChatMessage:
    """Append a human-agent reply without ending the session.

    Replying to a still-waiting ticket implicitly claims it first, so the
    queue detail page and the live console share one consistent rule: only the
    administrator who owns the ticket can write to the customer.
    """
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is not None and (
        ticket.status == "open"
        or (ticket.status == "in_progress" and ticket.assignee_id is None)
    ):
        ticket = await claim(session, handoff_id, admin)
    ticket = _require_owner(ticket, admin)
    message = await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "admin",
        content,
    )
    await session.refresh(ticket)
    return message


async def release(
    session: AsyncSession,
    handoff_id: int,
    admin: User,
    note: str,
) -> HandoffTicket:
    """Close the session and hand the conversation back to the agent."""
    ticket = _require_owner(
        await handoff_repo.get_handoff(session, handoff_id), admin
    )
    now = datetime.now(timezone.utc)
    ticket.status = "resolved"
    ticket.resolved_at = now
    ticket.close_reason = "returned_to_agent"
    ticket.resolution_note = note

    await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "system",
        "人工服务已结束，智能体继续为您服务。",
    )

    conversation = await chat_repo.get_conversation(
        session, ticket.conversation_id
    )
    if conversation is not None:
        slots = dict(conversation.slots or {})
        slots["last_handoff"] = {
            "ticket_id": ticket.id,
            "note": note,
            "resolved_at": now.isoformat(),
        }
        conversation.slots = slots

    await session.flush()
    await session.refresh(ticket)
    return ticket


async def update_status(
    session: AsyncSession, handoff_id: int, status: str
) -> HandoffTicket | None:
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    return await handoff_repo.update_status(session, ticket, status)


async def list_messages(
    session: AsyncSession,
    handoff_id: int,
    after_id: int | None = None,
) -> list[ChatMessage] | None:
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    return await chat_repo.list_messages(
        session, ticket.conversation_id, after_id=after_id
    )


async def reply_and_return_to_agent(
    session: AsyncSession, handoff_id: int, content: str
) -> tuple[ChatMessage, HandoffTicket] | None:
    """Legacy queue endpoint: reply once and close the ticket.

    Kept for the audit queue; the live console uses :func:`reply` followed by
    :func:`release` so a session can span many messages.
    """
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    message = await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "admin",
        content,
    )
    await handoff_repo.update_status(session, ticket, "resolved")
    return message, ticket
