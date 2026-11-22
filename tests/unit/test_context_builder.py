"""Focused unit tests for deterministic context assembly."""

from __future__ import annotations

from types import SimpleNamespace

from app.context.budget import select_recent_turns, trim_history_to_chars
from app.context.builder import build_context_pack
from app.context.formatters import format_slots, format_tool_facts
from app.prompts.answer import build_answer_pack, build_order_tool_answer_pack
from app.prompts.chitchat import build_chitchat_pack
from app.prompts.router import build_router_pack
from app.prompts.tool_planner import build_tool_planner_pack


def _settings(**overrides):
    values = {
        "context_engine_enabled": True,
        "context_max_chars": 4000,
        "context_history_turns": 6,
        "context_history_max_chars": 1200,
        "context_tool_facts_max_chars": 600,
        "context_slots_max_chars": 200,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _history(turns: int = 8) -> list[dict]:
    messages: list[dict] = []
    for index in range(turns):
        messages.append({"role": "user", "content": f"第{index + 1}轮问题"})
        messages.append(
            {"role": "assistant", "content": f"第{index + 1}轮回答"}
        )
    return messages


def _order_evidence() -> dict:
    return {
        "orders": [
            {
                "order_no": "ORD-1001",
                "status": "已发货",
                "total_amount": "99.00",
                "created_at": "2026-08-01T10:00:00",
                "items": [
                    {"product_name": "测试商品A", "quantity": 1},
                    {"product_name": "测试商品B", "quantity": 2},
                ],
                "logistics": [
                    {
                        "status": "运输中",
                        "location": "上海",
                        "description": "已发出",
                        "event_time": "2026-08-02T09:30:00",
                    }
                ],
            }
        ]
    }


def test_order_answer_pack_is_structured_and_bounded():
    pack = build_order_tool_answer_pack(
        _history(8),
        "我的订单到哪了",
        _order_evidence(),
        slots={"order_no": "ORD-1001", "query_scope": "list"},
        settings=_settings(context_history_max_chars=500),
    )

    system = pack.to_messages()[0]["content"]
    assert "订单 1 笔" in system
    assert "ORD-1001" in system
    assert "状态「已发货」" in system
    assert "最新物流：运输中（上海）已发出" in system
    assert "当前会话槽位" in system
    assert "order_no: ORD-1001" in system
    assert '{"orders"' not in system
    assert pack.to_messages()[-1] == {
        "role": "user",
        "content": "我的订单到哪了",
    }
    assert pack.manifest.history_turns <= 6
    assert pack.manifest.history_messages_dropped > 0
    assert pack.manifest.tool_sections == ("orders",)
    assert pack.manifest.total_chars <= 4000


def test_knowledge_pack_orders_reference_tool_and_protocol():
    pack = build_answer_pack(
        _history(2),
        "退货规则是什么",
        "【测试资料】七天无理由退货。",
        slots={"product": "测试商品A"},
        settings=_settings(),
    )

    system = pack.to_messages()[0]["content"]
    assert system.index("参考资料") < system.index("当前会话槽位")
    assert system.index("当前会话槽位") < system.rindex("知识缺口协议")
    assert "product: 测试商品A" in system
    assert pack.manifest.purpose == "knowledge_answer"


def test_router_pack_uses_readable_slots_and_recent_history():
    pack = build_router_pack(
        _history(8),
        "这个订单呢",
        slots={"order_no": "ORD-1001", "pending_intent": "order"},
        settings=_settings(context_history_turns=2),
    )

    system = pack.to_messages()[0]["content"]
    assert "- order_no: ORD-1001" in system
    assert "- pending_intent: order" in system
    assert '"order_no": "ORD-1001"' not in system
    assert pack.manifest.history_turns == 2
    assert pack.manifest.history_messages == 4


def test_tool_planner_pack_does_not_include_business_evidence():
    pack = build_tool_planner_pack(
        _history(2),
        "查 ORD-1001",
        slots={"order_no": "ORD-1001"},
        settings=_settings(),
    )

    system = pack.to_messages()[0]["content"]
    assert "只读订单工具" in system
    assert "- order_no: ORD-1001" in system
    assert "订单业务数据" not in system
    assert len(pack.to_messages()) == 6


def test_chitchat_pack_formats_weather_without_json():
    pack = build_chitchat_pack(
        _history(2),
        "上海天气怎么样",
        {
            "weather": {
                "status": "success",
                "city": "上海",
                "data": "晴，26℃",
            }
        },
        settings=_settings(),
    )

    system = pack.to_messages()[0]["content"]
    assert "天气：上海；晴，26℃" in system
    assert '{"weather"' not in system
    assert pack.manifest.tool_sections == ("weather",)
    assert pack.manifest.purpose == "chitchat_answer"


def test_slots_hide_internal_and_structured_values():
    formatted = format_slots(
        {
            "order_no": "ORD-1001",
            "pending_slots": {"order_no": "SHOULD-NOT-APPEAR"},
            "last_handoff": {"ticket_id": 9},
            "query_scope": "list",
        }
    )

    assert "order_no: ORD-1001" in formatted.text
    assert "query_scope: list" in formatted.text
    assert "SHOULD-NOT-APPEAR" not in formatted.text
    assert "last_handoff" not in formatted.text
    assert formatted.keys == ("order_no", "query_scope")


def test_tool_facts_ignore_unknown_sections_without_dumping_json():
    formatted = format_tool_facts(
        {
            "orders": [],
            "mystery": {"secret": "SHOULD-NOT-APPEAR"},
            "threshold": {"amount": "99.00"},
        }
    )

    assert "当前账号没有订单记录" in formatted.text
    assert "SHOULD-NOT-APPEAR" not in formatted.text
    assert formatted.sections == ("orders",)
    assert "mystery" in formatted.ignored_sections
    assert "threshold" in formatted.ignored_sections


def test_history_trimming_drops_oldest_messages_first():
    history = [
        {"role": "user", "content": "old-question"},
        {"role": "assistant", "content": "old-answer"},
        {"role": "user", "content": "new-question"},
        {"role": "assistant", "content": "new-answer"},
    ]
    selected, dropped = trim_history_to_chars(history, max_chars=30)

    assert dropped == 2
    assert selected == [
        {"role": "user", "content": "new-question"},
        {"role": "assistant", "content": "new-answer"},
    ]


def test_recent_turns_keep_user_message_with_its_answer():
    history = _history(3)
    selected, dropped = select_recent_turns(history, max_turns=1)

    assert dropped == 4
    assert selected == [
        {"role": "user", "content": "第3轮问题"},
        {"role": "assistant", "content": "第3轮回答"},
    ]


def test_recent_turns_drop_leading_assistant_greeting():
    history = [
        {"role": "assistant", "content": "你好呀"},
        {"role": "user", "content": "查订单"},
        {"role": "assistant", "content": "请提供订单号"},
    ]
    selected, dropped = select_recent_turns(history, max_turns=2)

    assert dropped == 1
    assert selected == [
        {"role": "user", "content": "查订单"},
        {"role": "assistant", "content": "请提供订单号"},
    ]


def test_tiny_budget_never_drops_system_or_current_question():
    pack = build_context_pack(
        purpose="order_answer",
        system_prompt="系统规则必须保留",
        history=_history(3),
        user_message="当前问题必须保留",
        tool_evidence=_order_evidence(),
        settings=_settings(context_max_chars=1000, context_history_max_chars=1),
    )

    messages = pack.to_messages()
    assert messages[0]["content"].startswith("系统规则必须保留")
    assert messages[-1]["content"] == "当前问题必须保留"
    assert pack.manifest.truncated is True
    assert pack.manifest.history_messages == 0


def test_legacy_order_prompt_still_accepts_plain_text():
    from app.prompts.answer import build_order_tool_answer_messages

    messages = build_order_tool_answer_messages(
        _history(2),
        "查订单",
        "订单 ORD-1001：已发货",
    )

    assert "订单 ORD-1001：已发货" in messages[0]["content"]
    assert "当前会话槽位" not in messages[0]["content"]
