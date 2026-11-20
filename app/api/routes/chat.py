"""Conversation and chat routes."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
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

logger = logging.getLogger(__name__)
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
    await chat_repo.add_message(
        session,
        conversation.id,
        "assistant",
        chat_service.NEW_CONVERSATION_GREETING,
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


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: int,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(rate_limit_chat),
    session: AsyncSession = Depends(get_session),
    container=Depends(get_container),
):
    conversation = await chat_repo.get_conversation_owned(
        session, conversation_id, user.id
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    async def event_stream():
        queue: asyncio.Queue[str] = asyncio.Queue()
        streamed = False
        streamed_parts: list[str] = []
        heartbeat_seconds = max(
            1.0,
            float(request.app.state.settings.sse_heartbeat_seconds),
        )
        last_emit = time.monotonic()

        # Flush headers immediately so gateways and proxies do not buffer the
        # stream until the first model token arrives.
        yield ": connected\n\n"

        async def emit_token(text: str) -> None:
            nonlocal streamed
            if not text:
                return
            streamed = True
            streamed_parts.append(text)
            await queue.put(text)

        task = asyncio.create_task(
            chat_service.run_chat(
                session,
                user,
                container,
                conversation_id,
                payload.content,
                metrics=request.app.state.metrics,
                token_sink=emit_token,
            )
        )
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    token = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_emit >= heartbeat_seconds:
                        # SSE comment frame: clients ignore it, gateways see
                        # traffic and keep the connection open.
                        yield ": heartbeat\n\n"
                        last_emit = time.monotonic()
                    continue
                last_emit = time.monotonic()
                yield _sse("token", {"text": token})

            result = await task
            if result is None:
                yield _sse("error", {"detail": "会话不存在"})
                return

            # Tools and fixed templates return a complete string; replay it in
            # small chunks so the customer still sees a typing effect.
            if not streamed:
                for chunk in _answer_chunks(result.answer):
                    yield _sse("token", {"text": chunk})
                    await asyncio.sleep(0.012)
            else:
                emitted = "".join(streamed_parts).strip()
                final = result.answer.strip()
                if emitted != final:
                    # The graph streams speculatively; ticket dedupe/limit or a
                    # provider failure can replace the final answer. The client
                    # must replace the bubble rather than append a second one.
                    yield _sse("replace", {"text": result.answer})

            yield _sse("done", result.model_dump(mode="json"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("streaming chat turn failed")
            yield _sse("error", {"detail": "回复生成失败，请稍后重试"})
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _answer_chunks(text: str, size: int = 3):
    for index in range(0, len(text), size):
        yield text[index : index + size]


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
