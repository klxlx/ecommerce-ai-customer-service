"""Unit tests for graph guard nodes (slots, chit-chat, clarification, handoff)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.graph import nodes
from app.graph.state import AgentState, CHIT_CHAT_LIMIT, MAX_CLARIFICATION_ATTEMPTS
from app.schemas.after_sales import AfterSaleFact
from app.schemas.catalog import AmbiguousProduct, ProductFact, ProductRef
from app.schemas.knowledge import RetrievalResult
from app.schemas.orders import OrderFact, OrderItemFact
from tests.fakes import FakeChat


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
        "providers": None,
    }
    base.update(overrides)
    return base


def _order_fact(
    *,
    order_no: str = "ORD-AFTER-1",
    status: str = "已完成",
    total_amount: str = "620.00",
) -> OrderFact:
    return OrderFact(
        order_no=order_no,
        status=status,
        total_amount=Decimal(total_amount),
        created_at=datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc),
        items=[
            OrderItemFact(
                product_name="耳机",
                quantity=1,
                unit_price=Decimal(total_amount),
            )
        ],
        logistics=[],
    )


def _after_sale_fact(
    *,
    service_type: str = "退货退款",
    status: str = "已完成",
    refund_amount: str | None = "88.50",
    reason: str = "商品破损",
) -> AfterSaleFact:
    created_at = datetime(2026, 11, 11, 10, 20, tzinfo=timezone.utc)
    return AfterSaleFact(
        id=1,
        service_type=service_type,
        status=status,
        refund_amount=Decimal(refund_amount) if refund_amount is not None else None,
        reason=reason,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.anyio
async def test_order_missing_slot_clarifies(monkeypatch):
    async def fake_order(session, user_id, order_no):
        raise AssertionError("should not be called")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    result = await nodes.order_node(_state(slots={}))
    assert result["intent"] == "clarification"
    assert "订单号" in result["answer"]


@pytest.mark.anyio
async def test_order_found_returns_status(monkeypatch):
    fact = OrderFact(
        order_no="ORD-1",
        status="运输中",
        total_amount=Decimal("299.00"),
        created_at=datetime.now(timezone.utc),
        items=[OrderItemFact(product_name="耳机", quantity=1, unit_price=Decimal("299.00"))],
        logistics=[],
    )

    async def fake_order(session, user_id, order_no):
        assert order_no == "ORD-1"
        return fact

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    result = await nodes.order_node(_state(slots={"order_no": "ORD-1"}))
    assert "运输中" in result["answer"]
    assert result["tool_evidence"]["order"]["order_no"] == "ORD-1"


@pytest.mark.anyio
async def test_order_list_returns_all_owned_orders(monkeypatch):
    facts = [
        _order_fact(order_no="ORD-1", status="运输中", total_amount="299.00"),
        _order_fact(order_no="ORD-2", status="已完成", total_amount="199.00"),
    ]

    async def fake_orders(session, user_id):
        assert user_id == 1
        return facts

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("specific order lookup should not be called")

    monkeypatch.setattr(nodes, "list_owned_orders", fake_orders)
    monkeypatch.setattr(nodes, "get_order_and_logistics", fail_if_called)

    result = await nodes.order_node(
        _state(
            user_message="我的订单有哪些？",
            slots={"query_scope": "list"},
        )
    )

    assert "ORD-1" in result["answer"]
    assert "ORD-2" in result["answer"]
    assert len(result["tool_evidence"]["orders"]) == 2


@pytest.mark.anyio
async def test_order_not_found_is_non_enumerating(monkeypatch):
    async def fake_order(session, user_id, order_no):
        return None

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    result = await nodes.order_node(_state(slots={"order_no": "ORD-X"}))
    assert "未查询到该订单" in result["answer"]
    assert "handoff" not in result


@pytest.mark.anyio
async def test_order_tool_failure_creates_handoff(monkeypatch):
    async def fake_order(session, user_id, order_no):
        raise RuntimeError("db down")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    result = await nodes.order_node(_state(slots={"order_no": "ORD-1"}))
    assert result["intent"] == "handoff"
    assert result["handoff"]["reason"] == "工具查询失败"


@pytest.mark.anyio
async def test_catalog_ambiguous_requires_clarification(monkeypatch):
    ambiguous = AmbiguousProduct(
        candidates=[ProductRef(sku="A", name="声动 Pro"), ProductRef(sku="B", name="声动 Lite")]
    )

    async def fake_catalog(session, sku_or_name):
        return ambiguous

    monkeypatch.setattr(nodes, "get_product_price_stock", fake_catalog)
    result = await nodes.catalog_node(_state(slots={"product": "声动"}))
    assert result["intent"] == "clarification"


@pytest.mark.anyio
async def test_catalog_fact_returns_price_stock(monkeypatch):
    fact = ProductFact(sku="SKU-1", name="耳机", price=Decimal("299.00"), stock=10, description="")

    async def fake_catalog(session, sku_or_name):
        return fact

    monkeypatch.setattr(nodes, "get_product_price_stock", fake_catalog)
    result = await nodes.catalog_node(_state(slots={"product": "耳机"}))
    assert "299.00" in result["answer"]
    assert result["tool_evidence"]["product"]["stock"] == 10


@pytest.mark.anyio
async def test_knowledge_retrieval_failure_creates_sanitized_handoff(monkeypatch):
    async def fake_retrieve(session, query, kb_type, providers, recall_k=12, rerank_k=5):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(nodes, "retrieve_knowledge", fake_retrieve)
    result = await nodes.knowledge_node(_state(intent="product_knowledge"))

    assert result["intent"] == "knowledge_gap"
    assert result["handoff"]["reason"] == "knowledge_unavailable"
    assert result["tool_evidence"]["knowledge"] == {
        "kb_type": "product",
        "status": "retrieval_failed",
    }
    assert "学习内容" in result["answer"]
    assert "embedding down" not in result["answer"]
    assert "embedding down" not in str(result["handoff"])


@pytest.mark.anyio
async def test_knowledge_empty_result_creates_handoff(monkeypatch):
    async def fake_retrieve(session, query, kb_type, providers, recall_k=12, rerank_k=5):
        return RetrievalResult()

    monkeypatch.setattr(nodes, "retrieve_knowledge", fake_retrieve)
    result = await nodes.knowledge_node(_state(intent="product_knowledge"))

    assert result["intent"] == "knowledge_gap"
    assert result["handoff"]["reason"] == "knowledge_unavailable"
    assert result["tool_evidence"]["knowledge"]["status"] == "not_found"
    assert "学习内容" in result["answer"]


@pytest.mark.anyio
async def test_knowledge_empty_answer_creates_handoff(monkeypatch):
    async def fake_retrieve(session, query, kb_type, providers, recall_k=12, rerank_k=5):
        return RetrievalResult(
            parent_ids=[7],
            context="商品保修期为一年，非人为损坏可申请保修。",
        )

    monkeypatch.setattr(nodes, "retrieve_knowledge", fake_retrieve)
    result = await nodes.knowledge_node(
        _state(
            intent="product_knowledge",
            chat=FakeChat("   "),
        )
    )

    assert result["intent"] == "knowledge_gap"
    assert result["handoff"]["reason"] == "knowledge_unavailable"
    assert result["tool_evidence"]["knowledge"]["status"] == "empty_answer"
    assert result["sources"] == []
    assert "学习内容" in result["answer"]


@pytest.mark.anyio
async def test_knowledge_follow_up_includes_previous_user_question(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_retrieve(session, query, kb_type, providers, recall_k=12, rerank_k=5):
        seen["query"] = query
        return RetrievalResult(
            parent_ids=[9],
            context="七天无理由退货需保持商品、吊牌和包装完好。",
        )

    monkeypatch.setattr(nodes, "retrieve_knowledge", fake_retrieve)
    result = await nodes.knowledge_node(
        _state(
            intent="policy_knowledge",
            user_message="那需要保留吊牌吗？",
            history=[
                {"role": "user", "content": "七天无理由退货有什么要求？"},
                {"role": "assistant", "content": "需要保持商品完好。"},
            ],
            chat=FakeChat("需要保留吊牌，并保持商品和原包装完好。"),
        )
    )

    assert "七天无理由退货有什么要求" in seen["query"]
    assert "那需要保留吊牌吗" in seen["query"]
    assert result["answer"] == "需要保留吊牌，并保持商品和原包装完好。"


@pytest.mark.anyio
async def test_standalone_knowledge_question_excludes_unrelated_history(
    monkeypatch,
):
    async def fake_retrieve(session, query, kb_type, providers, recall_k=12, rerank_k=5):
        return RetrievalResult(
            parent_ids=[9],
            context="签收后七天内可申请无理由退货，商品需保持完好。",
        )

    chat = FakeChat("签收后七天内可申请无理由退货，商品需保持完好。")
    monkeypatch.setattr(nodes, "retrieve_knowledge", fake_retrieve)
    await nodes.knowledge_node(
        _state(
            intent="policy_knowledge",
            user_message="退货规则",
            history=[
                {
                    "role": "user",
                    "content": "查询订单 ORD-20261117-0001 信息",
                },
                {
                    "role": "assistant",
                    "content": "您的订单 ORD-20261117-0001 正在运输中。",
                },
            ],
            chat=chat,
        )
    )

    assert len(chat.calls) == 1
    prompt_text = str(chat.calls[0])
    assert "ORD-20261117-0001" not in prompt_text


@pytest.mark.anyio
async def test_chitchat_limit_then_guidance():
    first = await nodes.chitchat_node(_state(chit_chat_streak=0))
    assert first["chit_chat_streak"] == 1

    second = await nodes.chitchat_node(_state(chit_chat_streak=1))
    assert second["chit_chat_streak"] == 2

    third = await nodes.chitchat_node(_state(chit_chat_streak=2))
    assert third["chit_chat_streak"] == CHIT_CHAT_LIMIT  # capped, not incremented
    assert "转人工" in third["answer"] or "业务" in third["answer"]


@pytest.mark.anyio
async def test_repeated_clarification_triggers_handoff():
    result = await nodes.clarification_node(
        _state(clarification_attempts=MAX_CLARIFICATION_ATTEMPTS - 1)
    )
    assert result["intent"] == "handoff"
    assert result["handoff"]["reason"] == "多次澄清失败"


@pytest.mark.anyio
async def test_after_sale_missing_order_slot_clarifies(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("tools should not be called without an order number")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fail_if_called)
    monkeypatch.setattr(nodes, "lookup_after_sales", fail_if_called)

    result = await nodes.after_sale_node(
        _state(user_message="我要退款", slots={"after_sale_action": "new_request"})
    )

    assert result["intent"] == "clarification"
    assert "订单号" in result["answer"]


@pytest.mark.anyio
async def test_after_sale_missing_order_is_non_enumerating(monkeypatch):
    async def fake_order(session, user_id, order_no):
        return None

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("after-sales lookup requires an owned order")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    monkeypatch.setattr(nodes, "lookup_after_sales", fail_if_called)

    result = await nodes.after_sale_node(
        _state(
            user_message="我的订单 ORD-PRIVATE 退款进度",
            slots={"order_no": "ORD-PRIVATE", "after_sale_action": "progress"},
        )
    )

    assert "未查询到该订单" in result["answer"]
    assert "handoff" not in result
    assert result["after_sale_context"] == {
        "status": "not_available",
        "order_no": "ORD-PRIVATE",
        "order_status": None,
        "service_type": "退款",
        "records": [],
        "handoff_required": False,
    }


@pytest.mark.anyio
async def test_after_sale_progress_lists_record_facts(monkeypatch):
    fact = _after_sale_fact()

    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no)

    async def fake_after_sales(session, user_id, order_no):
        return [fact]

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    monkeypatch.setattr(nodes, "lookup_after_sales", fake_after_sales)

    result = await nodes.after_sale_node(
        _state(
            user_message="我的订单 ORD-AFTER-1 退款进度",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "progress",
            },
        )
    )

    assert "退货退款" in result["answer"]
    assert "已完成" in result["answer"]
    assert "88.50" in result["answer"]
    assert "商品破损" in result["answer"]
    assert "2026-11-11 10:20" in result["answer"]
    assert result["tool_evidence"]["after_sale"][0]["status"] == "已完成"
    assert result["tool_evidence"]["order"]["status"] == "已完成"
    assert result["after_sale_context"]["status"] == "has_records"
    assert result["after_sale_context"]["records"][0]["refund_amount"] == "88.50"


@pytest.mark.anyio
async def test_after_sale_progress_without_records_gives_conservative_guidance(
    monkeypatch,
):
    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no)

    async def fake_after_sales(session, user_id, order_no):
        return []

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    monkeypatch.setattr(nodes, "lookup_after_sales", fake_after_sales)

    result = await nodes.after_sale_node(
        _state(
            user_message="我的订单 ORD-AFTER-1 售后进度",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "progress",
            },
        )
    )

    assert "未查询到售后记录" in result["answer"]
    assert "订单详情" in result["answer"]
    assert result["tool_evidence"]["after_sale"] == []
    assert result["after_sale_context"]["status"] == "no_records"


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["待发货", "运输中"])
async def test_new_return_request_does_not_promise_direct_return_in_restricted_status(
    monkeypatch, status
):
    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no, status=status)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("new requests do not query historical after-sales")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    monkeypatch.setattr(nodes, "lookup_after_sales", fail_if_called)

    result = await nodes.after_sale_node(
        _state(
            user_message="我要退掉订单 ORD-AFTER-1",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "new_request",
            },
        )
    )

    assert "可以直接退货退款" not in result["answer"]
    assert "无法直接办理退货退款" in result["answer"]
    assert result["tool_evidence"]["order"]["status"] == status


@pytest.mark.anyio
async def test_new_refund_at_threshold_does_not_handoff(monkeypatch):
    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no, total_amount="620.00")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)

    result = await nodes.after_sale_node(
        _state(
            user_message="我要退款 500 元",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "new_request",
                "refund_amount": "500.00",
            },
        )
    )

    assert "handoff" not in result
    assert result["tool_evidence"]["threshold"] == {
        "applied": True,
        "amount": "500.00",
        "limit": "500.00",
        "exceeded": False,
    }


@pytest.mark.anyio
async def test_new_refund_above_threshold_hands_off(monkeypatch):
    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no, total_amount="620.00")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)

    result = await nodes.after_sale_node(
        _state(
            user_message="我要退款 500.01 元",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "new_request",
                "refund_amount": "500.01",
            },
        )
    )

    assert result["intent"] == "handoff"
    assert "500" in result["handoff"]["reason"]
    assert result["tool_evidence"]["threshold"]["amount"] == "500.01"
    assert result["tool_evidence"]["threshold"]["exceeded"] is True


@pytest.mark.anyio
async def test_new_refund_without_amount_uses_order_paid_amount(monkeypatch):
    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no, total_amount="499.00")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)

    result = await nodes.after_sale_node(
        _state(
            user_message="我要退款",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "new_request",
            },
        )
    )

    assert "handoff" not in result
    assert result["tool_evidence"]["threshold"]["amount"] == "499.00"
    assert result["tool_evidence"]["threshold"]["applied"] is True


@pytest.mark.anyio
async def test_historical_refund_amount_does_not_trigger_threshold(monkeypatch):
    fact = _after_sale_fact(
        service_type="退款",
        status="已完成",
        refund_amount="899.00",
        reason="价保退款",
    )

    async def fake_order(session, user_id, order_no):
        return _order_fact(order_no=order_no, total_amount="899.00")

    async def fake_after_sales(session, user_id, order_no):
        return [fact]

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    monkeypatch.setattr(nodes, "lookup_after_sales", fake_after_sales)

    result = await nodes.after_sale_node(
        _state(
            user_message="我的订单 ORD-AFTER-1 退款进度",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "progress",
            },
        )
    )

    assert "handoff" not in result
    assert result["tool_evidence"]["threshold"]["applied"] is False
    assert result["tool_evidence"]["threshold"]["exceeded"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("failing_tool", ["order", "after_sale"])
async def test_after_sale_tool_failure_is_sanitized_and_hands_off(
    monkeypatch, failing_tool
):
    async def fake_order(session, user_id, order_no):
        if failing_tool == "order":
            raise RuntimeError("db down")
        return _order_fact(order_no=order_no)

    async def fake_after_sales(session, user_id, order_no):
        raise RuntimeError("db down")

    monkeypatch.setattr(nodes, "get_order_and_logistics", fake_order)
    monkeypatch.setattr(nodes, "lookup_after_sales", fake_after_sales)

    result = await nodes.after_sale_node(
        _state(
            user_message="我的订单 ORD-AFTER-1 退款进度",
            slots={
                "order_no": "ORD-AFTER-1",
                "after_sale_action": "progress",
            },
        )
    )

    assert result["intent"] == "handoff"
    assert "db down" not in result["answer"]
    assert "转接人工" in result["answer"]
    assert result["after_sale_context"]["status"] == "query_error"
    assert result["after_sale_context"]["handoff_required"] is True
