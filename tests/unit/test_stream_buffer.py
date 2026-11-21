"""Streaming behavior for knowledge answers with a machine-readable gap marker."""

from __future__ import annotations

import pytest

from app.graph import nodes
from app.graph.state import AgentState


class StreamingChat:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def stream(self, messages, model=None):
        for chunk in self.chunks:
            yield chunk

    async def complete(self, messages, model=None):
        return "".join(self.chunks)


def _state(chat: StreamingChat, emitted: list[str]) -> AgentState:
    async def sink(text: str) -> None:
        emitted.append(text)

    return {
        "user_message": "退款政策",
        "user_id": 1,
        "history": [],
        "slots": {},
        "sources": [],
        "tool_evidence": {},
        "chit_chat_streak": 0,
        "clarification_attempts": 0,
        "chat": chat,
        "session": None,
        "providers": None,
        "stream": sink,
    }


@pytest.mark.anyio
async def test_gap_marker_is_validated_before_any_token_is_emitted():
    emitted: list[str] = []
    chat = StreamingChat(["这是一段完整答案。", "[[KNOWLEDGE_GAP]]"])

    answer = await nodes._complete_answer(
        _state(chat, emitted),
        [],
        sentinel="[[KNOWLEDGE_GAP]]",
    )

    assert "[[KNOWLEDGE_GAP]]" in answer
    assert emitted == []


@pytest.mark.anyio
async def test_normal_answer_is_streamed_after_validation():
    emitted: list[str] = []
    chat = StreamingChat(["这是", "一段", "完整答案。"])

    answer = await nodes._complete_answer(
        _state(chat, emitted),
        [],
        sentinel="[[KNOWLEDGE_GAP]]",
    )

    assert answer == "这是一段完整答案。"
    assert "".join(emitted) == answer


@pytest.mark.anyio
async def test_emotional_customer_gets_empathy_before_business_answer():
    emitted: list[str] = []
    chat = StreamingChat(["亲，您好～蓝牙版本为 5.3。"])

    answer = await nodes._complete_answer(
        _state(chat, emitted),
        [],
        sentinel="[[KNOWLEDGE_GAP]]",
        empathy_text="烦死了，你们这个耳机蓝牙版本到底是多少？",
    )

    assert answer.startswith("非常抱歉")
    assert "蓝牙版本为 5.3" in answer
    assert "".join(emitted) == answer
