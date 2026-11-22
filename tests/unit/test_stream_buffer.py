"""Streaming behavior for knowledge answers with a machine-readable gap marker."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import resilience
from app.core.errors import ProviderConnectionError, ProviderTimeoutError
from app.graph import nodes
from app.graph.state import AgentState


@pytest.fixture(autouse=True)
def reset_circuits():
    resilience._circuits.clear()
    yield
    resilience._circuits.clear()


class StreamingChat:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def stream(self, messages, model=None):
        for chunk in self.chunks:
            yield chunk

    async def complete(self, messages, model=None):
        return "".join(self.chunks)


class FlakyStreamingChat:
    """Each stream call consumes one scripted attempt.

    An attempt is either a list of chunks or an exception raised before any
    chunk. ``complete`` records fallback calls and returns a fixed answer.
    """

    def __init__(
        self,
        attempts: list[list[str] | Exception | tuple[list[str], Exception]],
        *,
        fallback: str = "",
        complete_error: Exception | None = None,
    ) -> None:
        self.attempts = attempts
        self.fallback = fallback
        self.complete_error = complete_error
        self.stream_calls = 0
        self.complete_calls = 0

    async def stream(self, messages, model=None):
        index = self.stream_calls
        self.stream_calls += 1
        if index >= len(self.attempts):
            raise ProviderTimeoutError("chat", "no scripted stream attempt left")
        attempt = self.attempts[index]
        if isinstance(attempt, Exception):
            raise attempt
        error: Exception | None = None
        chunks: list[str]
        if isinstance(attempt, tuple):
            chunks, error = attempt
        else:
            chunks = attempt
        for chunk in chunks:
            yield chunk
        if error is not None:
            raise error

    async def complete(self, messages, model=None):
        self.complete_calls += 1
        if self.complete_error is not None:
            raise self.complete_error
        return self.fallback


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


@pytest.mark.anyio
async def test_stream_retries_before_first_token_and_does_not_call_complete():
    emitted: list[str] = []
    chat = FlakyStreamingChat(
        [ProviderConnectionError("chat", "connection reset"), ["完整答案"]],
        fallback="不应使用的兜底答案",
    )

    answer = await nodes._complete_answer(_state(chat, emitted), [])

    assert answer == "完整答案"
    assert emitted == ["完整答案"]
    assert chat.stream_calls == 2
    assert chat.complete_calls == 0


@pytest.mark.anyio
async def test_partial_stream_uses_complete_without_replaying_the_stream():
    emitted: list[str] = []
    chat = FlakyStreamingChat(
        [(["半截答案"], ProviderTimeoutError("chat", "stream idle timeout")),],
        fallback="完整答案",
    )

    answer = await nodes._complete_answer(_state(chat, emitted), [])

    assert answer == "完整答案"
    assert emitted == ["半截答案"]
    assert chat.stream_calls == 1
    assert chat.complete_calls == 1


@pytest.mark.anyio
async def test_sentinel_buffer_retries_without_concatenating_partial_text():
    emitted: list[str] = []
    chat = FlakyStreamingChat(
        [
            (
                ["半截答案"],
                ProviderTimeoutError("chat", "stream idle timeout"),
            ),
            ["完整答案"],
        ],
        fallback="不应使用的兜底答案",
    )

    answer = await nodes._complete_answer(
        _state(chat, emitted),
        [],
        sentinel="[[KNOWLEDGE_GAP]]",
    )

    assert answer == "完整答案"
    assert emitted == ["完整答案"]
    assert chat.stream_calls == 2
    assert chat.complete_calls == 0


@pytest.mark.anyio
async def test_complete_fallback_succeeds_after_stream_retries_exhausted():
    emitted: list[str] = []
    chat = FlakyStreamingChat(
        [
            ProviderTimeoutError("chat", "first"),
            ProviderTimeoutError("chat", "second"),
            ProviderTimeoutError("chat", "third"),
        ],
        fallback="完整答案",
    )

    answer = await nodes._complete_answer(_state(chat, emitted), [])

    assert answer == "完整答案"
    assert emitted == ["完整答案"]
    assert chat.stream_calls == 3
    assert chat.complete_calls == 1


@pytest.mark.anyio
async def test_disabled_stream_retry_keeps_the_original_stream_error():
    emitted: list[str] = []
    chat = FlakyStreamingChat(
        [(["半截答案"], ProviderTimeoutError("chat", "stream idle timeout"))],
        fallback="不应使用的兜底答案",
    )
    state = _state(chat, emitted)
    state["settings"] = SimpleNamespace(
        stream_retry_enabled=False,
        stream_partial_fallback_enabled=True,
        chat_attempt_timeout_seconds=30.0,
        chat_total_budget_seconds=60.0,
        provider_max_retries=2,
        provider_retry_backoff_seconds=0.0,
    )

    with pytest.raises(ProviderTimeoutError):
        await nodes._complete_answer(state, [])

    assert emitted == ["半截答案"]
    assert chat.stream_calls == 1
    assert chat.complete_calls == 0


@pytest.mark.anyio
async def test_complete_failure_propagates_after_stream_retries_exhausted():
    emitted: list[str] = []
    chat = FlakyStreamingChat(
        [
            ProviderTimeoutError("chat", "first"),
            ProviderTimeoutError("chat", "second"),
            ProviderTimeoutError("chat", "third"),
        ],
        complete_error=ProviderTimeoutError("chat", "fallback failed"),
    )

    with pytest.raises(ProviderTimeoutError):
        await nodes._complete_answer(_state(chat, emitted), [])

    assert emitted == []
    assert chat.complete_calls == 1
