"""Repository for conversations, messages, and agent runs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRun, ChatMessage, Conversation, ToolCallLog


async def create_conversation(
    session: AsyncSession, user_id: int, title: str = "新对话"
) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title)
    session.add(conversation)
    await session.flush()
    return conversation


async def list_conversations(
    session: AsyncSession, user_id: int
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
        .order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_conversation(
    session: AsyncSession, conversation_id: int
) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def get_conversation_owned(
    session: AsyncSession, conversation_id: int, user_id: int
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def soft_delete_owned_conversation(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
) -> bool:
    conversation = await get_conversation_owned(session, conversation_id, user_id)
    if conversation is None:
        return False
    conversation.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def list_messages(
    session: AsyncSession,
    conversation_id: int,
    after_id: int | None = None,
    limit: int | None = None,
) -> list[ChatMessage]:
    statement = select(ChatMessage).where(
        ChatMessage.conversation_id == conversation_id
    )
    if after_id is not None:
        statement = statement.where(ChatMessage.id > after_id)
    statement = statement.order_by(ChatMessage.id)
    if limit is not None:
        statement = statement.limit(limit)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def add_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    intent: str | None = None,
    sources: list | None = None,
    actions: list | None = None,
    trace_id: int | None = None,
    after_sale_context: dict | None = None,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        intent=intent,
        sources=sources,
        actions=actions,
        trace_id=trace_id,
        after_sale_context=after_sale_context,
    )
    session.add(message)
    await session.flush()
    return message


async def update_conversation_state(
    session: AsyncSession,
    conversation: Conversation,
    slots: dict | None,
    chit_chat_streak: int | None,
    clarification_attempts: int | None,
) -> None:
    if slots is not None:
        conversation.slots = slots
    if chit_chat_streak is not None:
        conversation.chit_chat_streak = chit_chat_streak
    if clarification_attempts is not None:
        conversation.clarification_attempts = clarification_attempts


async def create_agent_run(
    session: AsyncSession,
    conversation_id: int,
    intent: str | None,
    trace: dict,
) -> AgentRun:
    run = AgentRun(conversation_id=conversation_id, intent=intent, trace=trace)
    session.add(run)
    await session.flush()
    return run


async def create_tool_call_log(
    session: AsyncSession,
    agent_run_id: int,
    tool_name: str,
    arguments: dict,
    result: dict,
) -> ToolCallLog:
    log = ToolCallLog(
        agent_run_id=agent_run_id,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
    )
    session.add(log)
    await session.flush()
    return log
