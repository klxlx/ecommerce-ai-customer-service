"""Conversation and chat routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_current_user, rate_limit_chat
from app.db.models import User
from app.db.repositories import chat as chat_repo
from app.db.session import get_session
from app.schemas.chat import (
    ChatMessageOut,
    ChatResponse,
    ConversationCreate,
    ConversationOut,
    MessageCreate,
)
from app.services import chat as chat_service

router = APIRouter()


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    conversation = await chat_repo.create_conversation(
        session,
        user.id,
        title=payload.title or chat_service.DEFAULT_CONVERSATION_TITLE,
    )
    await session.commit()
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    conversations = await chat_repo.list_conversations(session, user.id)
    return [ConversationOut.model_validate(c) for c in conversations]


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    deleted = await chat_repo.soft_delete_owned_conversation(
        session,
        conversation_id,
        user.id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    await session.commit()


@router.get("/conversations/{conversation_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    conversation = await chat_repo.get_conversation_owned(
        session, conversation_id, user.id
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await chat_repo.list_messages(session, conversation_id)
    return [ChatMessageOut.model_validate(m) for m in messages]


@router.post("/conversations/{conversation_id}/messages", response_model=ChatResponse)
async def send_message(
    conversation_id: int,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(rate_limit_chat),
    session: AsyncSession = Depends(get_session),
    container=Depends(get_container),
):
    result = await chat_service.run_chat(
        session,
        user,
        container,
        conversation_id,
        payload.content,
        metrics=request.app.state.metrics,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return result
