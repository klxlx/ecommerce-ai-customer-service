"""Unit tests for greeting/handoff guards and LLM intent routing."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.graph import nodes
from app.graph.builder import Container, build_graph, route_branch
from app.graph.router import (
    is_after_sale_progress_query,
    is_explicit_handoff,
    is_new_after_sale_request,
    parse_router_decision,
    route,
)
from app.graph.state import AgentState
from app.prompts.answer import build_answer_messages
from app.providers.mock import MockChat
from app.rag.retriever import Providers
from tests.fakes import FakeChat, FakeEmbeddings, FakeReranker


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
        "chat": FakeChat(),
        "session": None,
        "providers": Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
    }
    base.update(overrides)
    return base


def _graph() -> Container:
    return build_graph(
        Container(
            chat=FakeChat(),
            providers=Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
            settings=get_settings(),
        )
    )


@pytest.mark.anyio
async def test_greeting_returns_fixed_response_without_llm():
    chat = FakeChat()
    state = _state(user_message="你好", chat=chat)
    result = await nodes.route_node(state)
    assert result["intent"] == "greeting"
    assert chat.calls == []  # no LLM call


@pytest.mark.anyio
async def test_short_greeting_variants():
    for msg in ["在吗", "hello", "您好", "早上好"]:
        result = await nodes.route_node(_state(user_message=msg))
        assert result["intent"] == "greeting", msg


@pytest.mark.anyio
async def test_explicit_handoff_detection():
    for msg in ["转人工", "我要投诉", "找人工客服"]:
        result = await nodes.route_node(_state(user_message=msg))
        assert result["intent"] == "handoff", msg


@pytest.mark.anyio
async def test_router_returns_intent_and_slots():
    chat = FakeChat('{"intent": "order", "slots": {"order_no": "ORD-1"}}')
    result = await nodes.route_node(_state(user_message="查订单", chat=chat))
    assert result["intent"] == "order"
    assert result["slots"]["order_no"] == "ORD-1"


@pytest.mark.anyio
async def test_router_failure_with_explicit_order_number_routes_to_order():
    chat = FakeChat("这不是 JSON")
    result = await nodes.route_node(
        _state(user_message="查一下订单 ORD-20261118-0002", chat=chat)
    )

    assert result["intent"] == "order"
    assert result["slots"]["order_no"] == "ORD-20261118-0002"
    assert "query_scope" not in result["slots"]
    assert len(chat.calls) >= 1


@pytest.mark.anyio
async def test_router_marks_explicit_order_list_query():
    chat = FakeChat('{"intent": "order", "slots": {}}')
    result = await nodes.route_node(
        _state(user_message="我的订单有哪些？", chat=chat)
    )
    assert result["intent"] == "order"
    assert result["slots"]["query_scope"] == "list"
    assert "order_no" not in result["slots"]


@pytest.mark.anyio
async def test_plain_my_orders_clears_stale_order_and_routes_to_list():
    chat = FakeChat('{"intent": "order", "slots": {}}')
    result = await nodes.route_node(
        _state(
            user_message="我的订单",
            slots={"order_no": "ORD-20261118-0002"},
            chat=chat,
        )
    )

    assert result["intent"] == "order"
    assert result["slots"]["query_scope"] == "list"
    assert "order_no" not in result["slots"]
    assert len(chat.calls) == 1


@pytest.mark.anyio
async def test_plain_my_orders_phrase_is_not_confused_with_status_query():
    chat = FakeChat('{"intent": "order", "slots": {"order_no": "ORD-1"}}')
    result = await nodes.route_node(
        _state(user_message="我的订单 ORD-1 到哪了", chat=chat)
    )

    assert result["intent"] == "order"
    assert result["slots"]["order_no"] == "ORD-1"
    assert "query_scope" not in result["slots"]


@pytest.mark.anyio
async def test_router_malformed_output_falls_back_to_clarification():
    chat = FakeChat("这不是 JSON")
    result = await nodes.route_node(_state(user_message="你好呀", chat=chat))
    assert result["intent"] == "clarification"


@pytest.mark.anyio
async def test_router_invalid_intent_falls_back_to_clarification():
    chat = FakeChat('{"intent": "delete_order", "slots": {}}')
    result = await nodes.route_node(_state(user_message="删除订单", chat=chat))
    assert result["intent"] == "clarification"


class _PersonaThenJsonChat:
    """First turn answers like the customer-service persona, second emits JSON."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        self.calls.append(messages)
        if len(self.calls) == 1:
            return "亲，您好～您查询的订单不存在，请核对订单号。"
        return '{"intent":"order","slots":{"order_no":"ORD-20261118-0002"}}'


@pytest.mark.anyio
async def test_router_retries_once_after_persona_answer():
    chat = _PersonaThenJsonChat()

    decision = await route(
        chat,
        [],
        "查一下订单 ORD-20261118-0002",
        slots={"order_no": "ORD-20261121-0117"},
    )

    assert decision is not None
    assert decision.intent == "order"
    assert decision.slots["order_no"] == "ORD-20261118-0002"
    assert len(chat.calls) == 2
    assert "不要回答用户问题" in chat.calls[0][-1]["content"]
    assert "不要回答用户问题" in chat.calls[1][-1]["content"]


def test_route_branch_maps_intent():
    assert route_branch({"intent": "order"}) == "order"
    assert route_branch({"intent": "after_sale"}) == "after_sale"
    assert route_branch({"intent": "greeting"}) == "greeting"
    assert route_branch({}) == "clarification"


@pytest.mark.anyio
async def test_compiled_graph_routes_greeting_to_answer():
    graph = _graph()
    state = _state(user_message="你好")
    out = await graph.ainvoke(state)
    assert out["intent"] == "greeting"
    assert "购物客服" in out["answer"]


def test_after_sale_classifiers_prioritize_specific_requests():
    assert is_new_after_sale_request("我要退款") is True
    assert is_after_sale_progress_query("我要退款") is False
    assert is_explicit_handoff("我要退款") is False
    assert is_after_sale_progress_query("我的订单 ORD-1 退款到哪了") is True
    assert is_new_after_sale_request("我的订单 ORD-1 退款到哪了") is False
    assert is_new_after_sale_request("我要退掉订单 ORD-1") is True
    assert is_after_sale_progress_query("七天无理由退货规则是什么") is False
    assert is_new_after_sale_request("七天无理由退货规则是什么") is False


def test_router_parses_after_sale_intent_and_slots():
    decision = parse_router_decision(
        '{"intent": "after_sale", "slots": {'
        '"order_no": "ORD-1", "refund_amount": "500.01", '
        '"after_sale_action": "new_request"}}'
    )
    assert decision is not None
    assert decision.intent == "after_sale"
    assert decision.slots["order_no"] == "ORD-1"
    assert decision.slots["refund_amount"] == "500.01"
    assert decision.slots["after_sale_action"] == "new_request"


@pytest.mark.anyio
async def test_route_node_forces_refund_request_to_after_sale_without_llm():
    chat = FakeChat('{"intent": "policy_knowledge", "slots": {}}')
    result = await nodes.route_node(_state(user_message="我要退款", chat=chat))
    assert result["intent"] == "after_sale"
    assert chat.calls == []


@pytest.mark.anyio
async def test_pending_after_sale_continues_when_order_number_is_supplied():
    chat = FakeChat('{"intent": "chit_chat", "slots": {}}')
    state = _state(
        user_message="ORD-6002",
        chat=chat,
        slots={
            "pending_intent": "after_sale",
            "pending_slots": {
                "after_sale_action": "new_request",
                "service_type": "退款",
            },
            "missing_slot": "order_no",
        },
    )

    result = await nodes.route_node(state)

    assert result["intent"] == "after_sale"
    assert result["slots"]["order_no"] == "ORD-6002"
    assert result["slots"]["after_sale_action"] == "new_request"
    assert result["slots"]["service_type"] == "退款"
    assert chat.calls == []


@pytest.mark.anyio
async def test_pending_price_query_continues_when_product_is_supplied():
    chat = FakeChat('{"intent": "product_knowledge", "slots": {}}')
    state = _state(
        user_message="蓝牙耳机",
        chat=chat,
        slots={
            "pending_intent": "price_stock",
            "pending_slots": {},
            "missing_slot": "product",
        },
    )

    result = await nodes.route_node(state)

    assert result["intent"] == "price_stock"
    assert result["slots"]["product"] == "蓝牙耳机"
    assert "pending_intent" not in result["slots"]
    assert chat.calls == []


@pytest.mark.anyio
async def test_mock_router_distinguishes_after_sale_from_general_policy():
    chat = MockChat()

    refund = await chat.complete(
        [
            {"role": "system", "content": "你是意图识别器"},
            {"role": "user", "content": "我要退款"},
        ]
    )
    progress = await chat.complete(
        [
            {"role": "system", "content": "你是意图识别器"},
            {"role": "user", "content": "退款进度"},
        ]
    )
    policy = await chat.complete(
        [
            {"role": "system", "content": "你是意图识别器"},
            {"role": "user", "content": "七天无理由退货规则是什么"},
        ]
    )

    assert parse_router_decision(refund).intent == "after_sale"
    assert parse_router_decision(progress).intent == "after_sale"
    assert parse_router_decision(policy).intent == "policy_knowledge"


@pytest.mark.anyio
async def test_mock_knowledge_answer_uses_retrieved_reference_content():
    chat = MockChat()

    answer = await chat.complete(
        [
            {"role": "system", "content": "你是专业的电商客服助手"},
            {
                "role": "user",
                "content": (
                    "参考资料：\n"
                    "签收后 7 天内可申请无理由退货，商品需保持完好。\n\n"
                    "业务数据：\n（无）\n\n"
                    "用户问题：七天无理由退货有什么要求？"
                ),
            },
        ]
    )

    assert "签收后 7 天内可申请无理由退货" in answer
    assert answer.startswith("结论：")


@pytest.mark.anyio
async def test_mock_knowledge_answer_is_concise_instead_of_dumping_reference():
    chat = MockChat()
    repeated_detail = (
        "商品需保持完好，不影响二次销售；赠品、配件、原包装需一并寄回；"
        "定制商品、已拆封的贴身用品和明确标注不支持无理由退货的商品除外。"
    )

    answer = await chat.complete(
        [
            {"role": "system", "content": "你是专业的电商客服助手"},
            {
                "role": "user",
                "content": (
                    "参考资料：\n"
                    "七天无理由退货规则：签收后 7 天内可提交申请。\n"
                    f"{repeated_detail * 8}\n\n"
                    "业务数据：\n（无）\n\n"
                    "用户问题：七天无理由退货有什么要求？"
                ),
            },
        ]
    )

    assert len(answer) <= 300
    assert "签收后 7 天内可提交申请" in answer
    assert "人工" in answer


def test_answer_messages_keep_reference_context_separate_from_question():
    messages = build_answer_messages(
        [
            {"role": "user", "content": "七天无理由退货有什么要求？"},
            {"role": "assistant", "content": "需要保持商品完好。"},
        ],
        "那需要保留吊牌吗？",
        "退货时需保持商品、吊牌和原包装完好。",
        '{"order_status": "已完成"}',
    )

    assert messages[-1] == {"role": "user", "content": "那需要保留吊牌吗？"}
    assert messages[1:3] == [
        {"role": "user", "content": "七天无理由退货有什么要求？"},
        {"role": "assistant", "content": "需要保持商品完好。"},
    ]
    system_context = "\n".join(
        message["content"] for message in messages if message["role"] == "system"
    )
    assert "退货时需保持商品、吊牌和原包装完好。" in system_context
    assert '{"order_status": "已完成"}' in system_context
