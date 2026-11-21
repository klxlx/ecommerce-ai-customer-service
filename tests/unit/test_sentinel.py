"""Unit tests for the asynchronous answer sentinel."""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.graph.builder import Container
from app.services import sentinel


class SentinelChat:
    def __init__(self, response: str, delay: float = 0):
        self.response = response
        self.delay = delay
        self.calls = []

    async def complete(self, messages, model=None):
        self.calls.append((messages, model))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response


def _container(chat, **overrides):
    settings = Settings(
        _env_file=None,
        SENTINEL_ENABLED=True,
        SENTINEL_TIMEOUT_SECONDS=0.02,
        ROUTER_MODEL="qwen-plus",
        **overrides,
    )
    return Container(chat=chat, providers=None, settings=settings)


@pytest.mark.anyio
async def test_flags_unsupported_policy_promise():
    chat = SentinelChat(
        '{"supported": false, "policy_risk": true, '
        '"unsupported_claims": ["承诺一定退款"], "reason": "资料未支持"}'
    )
    verdict = await sentinel.check_answer(
        _container(chat),
        question="能退款吗",
        answer="放心，我们保证一定退款。",
        intent="after_sale",
        sources=[],
        tool_evidence={},
    )
    assert verdict.checked is True
    assert verdict.flagged is True
    assert verdict.policy_risk is True
    assert verdict.unsupported_claims == ["承诺一定退款"]
    assert chat.calls[0][1] == "qwen-plus"


@pytest.mark.anyio
async def test_uses_router_model_by_default():
    chat = SentinelChat(
        '{"supported": true, "policy_risk": false, '
        '"unsupported_claims": [], "reason": ""}'
    )
    verdict = await sentinel.check_answer(
        _container(chat),
        question="电池容量",
        answer="电池容量是 5000mAh。",
        intent="product_knowledge",
        sources=[],
        tool_evidence={"capacity": "5000mAh"},
    )
    assert verdict.checked is True
    assert verdict.flagged is False
    assert chat.calls[0][1] == "qwen-plus"


@pytest.mark.anyio
async def test_invalid_json_and_timeout_fail_open():
    invalid = await sentinel.check_answer(
        _container(SentinelChat("not json")),
        question="电池容量",
        answer="5000mAh",
        intent="product_knowledge",
        sources=[],
        tool_evidence={},
    )
    assert invalid.checked is False
    assert invalid.flagged is False

    slow = await sentinel.check_answer(
        _container(SentinelChat('{"supported": false}', delay=0.05)),
        question="电池容量",
        answer="5000mAh",
        intent="product_knowledge",
        sources=[],
        tool_evidence={},
    )
    assert slow.checked is False
    assert slow.error == "timeout"
    assert slow.flagged is False


@pytest.mark.anyio
async def test_non_business_intent_is_skipped():
    verdict = await sentinel.check_answer(
        _container(SentinelChat("{}")),
        question="你好",
        answer="你好呀",
        intent="greeting",
        sources=[],
        tool_evidence={},
    )
    assert verdict.checked is False
