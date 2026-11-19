"""Role-specific model routing and chitchat behavior."""

from __future__ import annotations

import pytest

from app.agents.registry import build_agent_registry
from app.core.config import Settings, get_settings
from app.graph import nodes
from app.graph.state import AgentState
from app.schemas.knowledge import RetrievalResult


def test_default_chitchat_model_uses_active_small_model():
    assert Settings.model_fields["chitchat_model"].default == "qwen-flash"


class RecordingChat:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.models: list[str | None] = []

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> str:
        self.calls.append(messages)
        self.models.append(model)
        if not self.responses:
            return ""
        return self.responses.pop(0)


class RecordingWeather:
    def __init__(self, result: str = "晴，26℃", error: Exception | None = None):
        self.result = result
        self.error = error
        self.cities: list[str] = []

    async def query(self, city: str) -> str:
        self.cities.append(city)
        if self.error is not None:
            raise self.error
        return self.result


def _registry():
    settings = get_settings().model_copy(
        update={
            "router_model": "router-tier",
            "knowledge_model": "knowledge-tier",
            "chitchat_model": "chitchat-tier",
        }
    )
    return build_agent_registry(settings)


def _state(**overrides) -> AgentState:
    base: AgentState = {
        "user_message": "",
        "user_id": 1,
        "history": [],
        "slots": {},
        "sources": [],
        "tool_evidence": {},
        "chit_chat_streak": 0,
        "clarification_attempts": 0,
        "chat": RecordingChat(),
        "agents": _registry(),
        "session": None,
        "providers": None,
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_router_uses_router_tier_and_fixed_intent_prompt():
    chat = RecordingChat('{"intent": "order", "slots": {"order_no": "ORD-1"}}')

    result = await nodes.route_node(
        _state(user_message="查询订单 ORD-1", chat=chat)
    )

    assert result["intent"] == "order"
    assert chat.models == ["router-tier"]
    assert "意图识别器" in chat.calls[0][0]["content"]


@pytest.mark.anyio
async def test_knowledge_uses_highest_tier_and_answer_prompt(monkeypatch):
    async def fake_retrieve(*args, **kwargs):
        return RetrievalResult(
            parent_ids=[9],
            context="签收后七天内可申请无理由退货，商品需保持完好。",
        )

    monkeypatch.setattr(nodes, "retrieve_knowledge", fake_retrieve)
    chat = RecordingChat("签收后七天内可申请无理由退货，请保持商品完好。")

    result = await nodes.knowledge_node(
        _state(
            intent="policy_knowledge",
            user_message="七天无理由退货规则是什么",
            chat=chat,
        )
    )

    assert result["answer"].startswith("签收后七天内")
    assert chat.models == ["knowledge-tier"]
    assert "专业的电商客服助手" in chat.calls[0][0]["content"]


@pytest.mark.anyio
async def test_chitchat_uses_small_tier_and_caps_reply_at_200_characters():
    chat = RecordingChat("客" * 260)

    result = await nodes.chitchat_node(
        _state(user_message="今天心情怎么样", chat=chat)
    )

    assert chat.models == ["chitchat-tier"]
    assert len(result["answer"]) <= 200
    prompt = chat.calls[0][0]["content"]
    assert "闲聊" in prompt
    assert "200" in prompt


@pytest.mark.anyio
async def test_chitchat_time_request_uses_local_time_tool():
    chat = RecordingChat("现在是 2026 年 11 月 19 日 14 点 30 分。")

    result = await nodes.chitchat_node(
        _state(
            user_message="时间",
            chat=chat,
            local_time=lambda: {
                "timezone": "Asia/Shanghai",
                "date": "2026-11-19",
                "time": "14:30:00",
                "weekday": "星期四",
                "text": "2026年11月19日 14:30:00（星期四，Asia/Shanghai）",
            },
        )
    )

    assert result["tool_evidence"]["local_time"]["date"] == "2026-11-19"
    assert "2026年11月19日 14:30:00" in result["answer"]
    assert "商品" in result["answer"]
    assert chat.calls == []


@pytest.mark.anyio
async def test_chitchat_weather_request_uses_mcp_tool_with_city():
    weather = RecordingWeather("晴，26℃")
    chat = RecordingChat("广州今天晴，约 26℃。您还需要咨询商品或订单吗？")

    result = await nodes.chitchat_node(
        _state(
            user_message="广州天气",
            chat=chat,
            weather=weather,
        )
    )

    assert weather.cities == ["广州"]
    assert result["tool_evidence"]["weather"]["status"] == "success"
    assert "晴，26℃" in chat.calls[0][0]["content"]
    assert "商品" in chat.calls[0][0]["content"]


@pytest.mark.anyio
async def test_chitchat_weather_without_city_asks_and_skips_tools_and_llm():
    weather = RecordingWeather()
    chat = RecordingChat("不应调用")

    result = await nodes.chitchat_node(
        _state(
            user_message="今天天气怎么样？",
            chat=chat,
            weather=weather,
        )
    )

    assert weather.cities == []
    assert chat.calls == []
    assert "城市" in result["answer"]
    assert "商品" in result["answer"]


@pytest.mark.anyio
async def test_chitchat_weather_failure_is_honest_and_skips_llm():
    weather = RecordingWeather(error=RuntimeError("weather mcp unavailable"))
    chat = RecordingChat("上海今天晴朗。")

    result = await nodes.chitchat_node(
        _state(
            user_message="上海天气怎么样？",
            chat=chat,
            weather=weather,
        )
    )

    assert chat.calls == []
    assert "暂时查不到" in result["answer"]
    assert "商品" in result["answer"]
    assert "晴朗" not in result["answer"]
    assert result["tool_evidence"]["weather"]["status"] == "unavailable"

