"""Handoff service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, HandoffTicket
from app.db.repositories import chat as chat_repo
from app.db.repositories import handoff as handoff_repo


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


async def update_status(
    session: AsyncSession, handoff_id: int, status: str
) -> HandoffTicket | None:
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    return await handoff_repo.update_status(session, ticket, status)


async def list_messages(
    session: AsyncSession, handoff_id: int
) -> list[ChatMessage] | None:
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    return await chat_repo.list_messages(session, ticket.conversation_id)


async def reply_and_return_to_agent(
    session: AsyncSession, handoff_id: int, content: str
) -> tuple[ChatMessage, HandoffTicket] | None:
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
