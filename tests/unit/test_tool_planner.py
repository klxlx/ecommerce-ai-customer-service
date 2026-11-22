"""Bounded order planner loop contracts."""

from __future__ import annotations

import pytest

from app.graph import tool_planner as planner_module
from app.graph.tool_planner import run_order_tool_loop
from app.providers.chat import ChatToolCall, ChatToolResponse
from app.tools.runtime import ToolResult


class FakePlannerChat:
    def __init__(self, responses: list[ChatToolResponse]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def complete_with_tools(
        self, messages, tools, model=None, tool_choice="auto"
    ):
        self.calls.append(list(messages))
        if not self._responses:
            return ChatToolResponse()
        return self._responses.pop(0)


def _detail_call(order_no: str = "ORD-5001") -> ChatToolResponse:
    return ChatToolResponse(
        tool_calls=[
            ChatToolCall(
                id="call_1",
                name="order_detail",
                arguments={"order_no": order_no},
                raw_arguments=f'{{"order_no": "{order_no}"}}',
            )
        ]
    )


@pytest.fixture
def _settings():
    class Settings:
        fc_max_steps = 2
        fc_planner_model_name = "qwen-flash"
        fc_allowed_tools = "order_list,order_detail"

    return Settings()


@pytest.mark.anyio
async def test_planner_executes_one_tool_then_answers(monkeypatch, _settings):
    async def fake_execute(name, **kwargs):
        return ToolResult(
            tool=name,
            status="ok",
            data={"order_no": "ORD-5001", "status": "运输中"},
            evidence={"tool": name, "status": "ok", "attempts": 1},
        )

    monkeypatch.setattr(planner_module, "execute_tool", fake_execute)
    chat = FakePlannerChat([_detail_call(), ChatToolResponse()])
    plan = await run_order_tool_loop(
        chat=chat,
        settings=_settings,
        messages=[{"role": "user", "content": "查 ORD-5001"}],
        session=None,
        user_id=7,
    )
    assert plan.fallback_reason is None
    assert plan.steps == 2
    assert plan.evidence["order"]["order_no"] == "ORD-5001"
    assert plan.tool_calls[0]["name"] == "order_detail"
    assert chat.calls[1][-2]["role"] == "assistant"
    assert chat.calls[1][-1]["role"] == "tool"


@pytest.mark.anyio
async def test_planner_stops_at_max_steps(monkeypatch, _settings):
    async def fake_execute(name, **kwargs):
        return ToolResult(
            tool=name,
            status="ok",
            data={"order_no": "ORD-5001"},
            evidence={"tool": name, "status": "ok", "attempts": 1},
        )

    monkeypatch.setattr(planner_module, "execute_tool", fake_execute)
    chat = FakePlannerChat([_detail_call(), _detail_call("ORD-5002")])
    plan = await run_order_tool_loop(
        chat=chat,
        settings=_settings,
        messages=[{"role": "user", "content": "查订单"}],
        session=None,
        user_id=7,
    )
    assert plan.steps == 2
    assert len(plan.tool_calls) == 2
    assert plan.fallback_reason is None


@pytest.mark.anyio
async def test_planner_rejects_unregistered_tool_and_falls_back(monkeypatch, _settings):
    chat = FakePlannerChat(
        [
            ChatToolResponse(
                tool_calls=[
                    ChatToolCall(
                        id="call_bad",
                        name="refund_write",
                        arguments={},
                    )
                ]
            )
        ]
    )
    plan = await run_order_tool_loop(
        chat=chat,
        settings=_settings,
        messages=[{"role": "user", "content": "退款"}],
        session=None,
        user_id=7,
    )
    assert plan.fallback_reason == "unregistered_tool"
    assert plan.tool_calls == []


@pytest.mark.anyio
async def test_planner_without_tool_call_falls_back(monkeypatch, _settings):
    chat = FakePlannerChat([ChatToolResponse(content="请提供订单号")])
    plan = await run_order_tool_loop(
        chat=chat,
        settings=_settings,
        messages=[{"role": "user", "content": "查订单"}],
        session=None,
        user_id=7,
    )
    assert plan.fallback_reason == "no_tool_call"
