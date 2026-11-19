"""Chat API integration tests with fake providers."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import (
    AfterSale,
    AgentRun,
    ChatMessage,
    Conversation,
    HandoffTicket,
    ToolCallLog,
)
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.graph import nodes
from app.main import create_app
from app.rag.retriever import Providers
from app.schemas.knowledge import RetrievalResult
from tests.fakes import FakeEmbeddings, FakeReranker
from tests.helpers import create_after_sale, create_order, create_product, create_user


class ScriptedChat:
    """Returns a router decision JSON based on the user message content."""

    def __init__(self, route_map: dict[str, dict]):
        self.route_map = route_map
        self.calls = []

    async def complete(self, messages):
        self.calls.append(messages)
        content = messages[-1]["content"] if messages else ""
        for key, decision in self.route_map.items():
            if key in content:
                return json.dumps(decision, ensure_ascii=False)
        return json.dumps({"intent": "chit_chat", "slots": {}}, ensure_ascii=False)


class SequenceChat:
    """Returns scripted completions in order and records the prompts."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[list[dict]] = []

    async def complete(self, messages):
        self.calls.append(messages)
        index = len(self.calls) - 1
        return self.responses[index] if index < len(self.responses) else ""


def _make_container(chat):
    return Container(
        chat=chat,
        providers=Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
        settings=get_settings(),
    )


async def _login(client, username, password="password123"):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _build_client(container):
    app = create_app(settings=get_settings(), session_factory=create_session_factory(get_settings().database_url))
    app.dependency_overrides[get_container] = lambda: container
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _load_run_and_tool_call(trace_id):
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        run = await session.get(AgentRun, trace_id)
        tool_call = (
            await session.execute(
                select(ToolCallLog).where(ToolCallLog.agent_run_id == trace_id)
            )
        ).scalar_one()
        return run, tool_call


@pytest.mark.anyio
async def test_conversation_ownership_is_enforced(db_session):
    alice = await create_user(db_session, "alice")
    bob = await create_user(db_session, "bob")
    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as alice_client:
        alice_token = await _login(alice_client, "alice")
        resp = await alice_client.post("/api/conversations", json={}, headers=_auth(alice_token))
        conv_id = resp.json()["id"]

    async with await _build_client(container) as bob_client:
        bob_token = await _login(bob_client, "bob")
        # Bob cannot read Alice's conversation.
        denied = await bob_client.get(
            f"/api/conversations/{conv_id}/messages", headers=_auth(bob_token)
        )
        assert denied.status_code == 404


@pytest.mark.anyio
async def test_new_conversation_uses_assistant_name_as_default_title(
    db_session,
):
    await create_user(db_session, "default-title-owner")
    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "default-title-owner")
        response = await client.post(
            "/api/conversations",
            json={},
            headers=_auth(token),
        )

    assert response.status_code == 201
    assert response.json()["title"] == "智能客服"


@pytest.mark.anyio
async def test_greeting_flow_persists_messages(db_session):
    await create_user(db_session, "alice")
    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "alice")
        conv = (await client.post("/api/conversations", json={}, headers=_auth(token))).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )
        body = resp.json()
        assert resp.status_code == 200
        assert "购物客服" in body["answer"]
        assert body["intent"] == "greeting"

        messages = (await client.get(f"/api/conversations/{conv['id']}/messages", headers=_auth(token))).json()
        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant"]


@pytest.mark.anyio
async def test_after_sale_clarification_persists_and_resumes_with_order_number(
    db_session,
):
    owner = await create_user(db_session, "after-sale-resume-owner")
    await create_order(
        db_session,
        owner.id,
        "ORD-6002",
        status="已完成",
        total="199.00",
        items=[("耳机", 1, "199.00")],
    )
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "after-sale-resume-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        first = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我要退款"},
            headers=_auth(token),
        )
        first_body = first.json()
        second = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "ORD-6002"},
            headers=_auth(token),
        )
        second_body = second.json()

    assert first_body["intent"] == "clarification"
    assert "订单号" in first_body["answer"]
    assert second_body["intent"] == "after_sale"
    assert second_body["intent"] != "chit_chat"
    assert "ORD-6002" in second_body["answer"]

    conversation = (
        await db_session.execute(
            select(Conversation).where(Conversation.id == conv["id"])
        )
    ).scalar_one()
    await db_session.refresh(conversation)
    assert conversation.slots["order_no"] == "ORD-6002"
    assert conversation.slots["after_sale_action"] == "new_request"
    assert "pending_intent" not in conversation.slots


@pytest.mark.anyio
async def test_price_clarification_resumes_when_product_name_is_supplied(
    db_session,
):
    await create_user(db_session, "price-resume-owner")
    await create_product(
        db_session,
        "SKU-RESUME-HEADPHONE",
        "蓝牙耳机",
        price="299.00",
        stock=12,
    )
    route_map = {
        "查询价格": {"intent": "price_stock", "slots": {"product": ""}},
    }
    container = _make_container(ScriptedChat(route_map))

    async with await _build_client(container) as client:
        token = await _login(client, "price-resume-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        first = (
            await client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "查询价格"},
                headers=_auth(token),
            )
        ).json()
        second = (
            await client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "蓝牙耳机"},
                headers=_auth(token),
            )
        ).json()

    assert first["intent"] == "clarification"
    assert second["intent"] == "price_stock"
    assert "¥299.00" in second["answer"]
    assert "库存 12 件" in second["answer"]


@pytest.mark.anyio
async def test_successful_business_turn_resets_old_guard_counters(db_session):
    await create_user(db_session, "counter-reset-owner")
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "counter-reset-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        conversation = await db_session.get(Conversation, conv["id"])
        conversation.chit_chat_streak = 2
        conversation.clarification_attempts = 2
        await db_session.commit()

        response = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )
        assert response.status_code == 200

    await db_session.refresh(conversation)
    assert conversation.chit_chat_streak == 0
    assert conversation.clarification_attempts == 0


@pytest.mark.anyio
async def test_order_query_uses_authenticated_user(db_session):
    owner = await create_user(db_session, "owner")
    await create_order(db_session, owner.id, "ORD-5001", status="运输中", items=[("耳机", 1, "299.00")])
    route_map = {"ORD-5001": {"intent": "order", "slots": {"order_no": "ORD-5001"}}}
    container = _make_container(ScriptedChat(route_map))
    async with await _build_client(container) as client:
        token = await _login(client, "owner")
        conv = (await client.post("/api/conversations", json={}, headers=_auth(token))).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我的订单 ORD-5001 到哪里了"},
            headers=_auth(token),
        )
        body = resp.json()
        assert "运输中" in body["answer"]
        assert body["trace_id"] is not None


@pytest.mark.anyio
async def test_history_survives_new_app_instance(db_session):
    await create_user(db_session, "alice")
    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "alice")
        conv = (await client.post("/api/conversations", json={}, headers=_auth(token))).json()
        await client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "你好"}, headers=_auth(token))
        conv_id = conv["id"]

    # A brand new app instance (simulating restart) still sees the history.
    async with await _build_client(container) as client2:
        token2 = await _login(client2, "alice")
        convs = (await client2.get("/api/conversations", headers=_auth(token2))).json()
        assert any(c["id"] == conv_id for c in convs)
        messages = (await client2.get(f"/api/conversations/{conv_id}/messages", headers=_auth(token2))).json()
        assert len(messages) == 2


@pytest.mark.anyio
async def test_customer_can_soft_delete_own_conversation_and_cannot_continue_it(
    db_session,
):
    await create_user(db_session, "delete-conversation-owner")
    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "delete-conversation-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        sent = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )
        assert sent.status_code == 200

        deleted = await client.delete(
            f"/api/conversations/{conv['id']}",
            headers=_auth(token),
        )
        assert deleted.status_code == 204

        listed = await client.get("/api/conversations", headers=_auth(token))
        assert listed.json() == []
        hidden_messages = await client.get(
            f"/api/conversations/{conv['id']}/messages",
            headers=_auth(token),
        )
        blocked_send = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "还在吗"},
            headers=_auth(token),
        )
        assert hidden_messages.status_code == 404
        assert blocked_send.status_code == 404

    conversation = (
        await db_session.execute(
            select(Conversation).where(Conversation.id == conv["id"])
        )
    ).scalar_one()
    messages = (
        await db_session.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == conv["id"])
        )
    ).scalars().all()
    runs = (
        await db_session.execute(
            select(AgentRun).where(AgentRun.conversation_id == conv["id"])
        )
    ).scalars().all()
    assert conversation.deleted_at is not None
    assert len(messages) == 2
    assert len(runs) == 1


@pytest.mark.anyio
async def test_explicit_handoff_creates_ticket(db_session):
    await create_user(db_session, "alice")
    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "alice")
        conv = (await client.post("/api/conversations", json={}, headers=_auth(token))).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "转人工"},
            headers=_auth(token),
        )
        body = resp.json()
        assert body["handoff"] is not None
        assert body["handoff"]["status"] == "open"

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (await session.execute(select(HandoffTicket))).scalars().all()
        assert len(tickets) == 1
        runs = (await session.execute(select(AgentRun))).scalars().all()
        assert len(runs) == 1


@pytest.mark.anyio
async def test_knowledge_not_found_creates_handoff_ticket(
    db_session, monkeypatch
):
    await create_user(db_session, "knowledge-not-found-owner")

    async def empty_retrieve(*args, **kwargs):
        return RetrievalResult()

    monkeypatch.setattr(nodes, "retrieve_knowledge", empty_retrieve)
    container = _make_container(
        ScriptedChat(
            {
                "七天无理由": {
                    "intent": "policy_knowledge",
                    "slots": {},
                }
            }
        )
    )

    async with await _build_client(container) as client:
        token = await _login(client, "knowledge-not-found-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "七天无理由退货需要满足什么条件？"},
            headers=_auth(token),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["intent"] == "handoff"
    assert body["handoff"]["status"] == "open"
    assert "人工客服" in body["answer"]

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        ticket = await session.get(HandoffTicket, body["handoff"]["id"])
    assert ticket is not None
    assert ticket.evidence["tool"]["knowledge"] == {
        "kb_type": "policy",
        "status": "not_found",
    }


@pytest.mark.anyio
async def test_knowledge_empty_model_answer_creates_handoff_ticket(
    db_session, monkeypatch
):
    await create_user(db_session, "knowledge-empty-answer-owner")

    async def found_retrieve(*args, **kwargs):
        return RetrievalResult(
            parent_ids=[42],
            context="耳机保修期为一年，非人为损坏可申请保修。",
        )

    monkeypatch.setattr(nodes, "retrieve_knowledge", found_retrieve)
    container = _make_container(
        SequenceChat(
            [
                json.dumps(
                    {"intent": "product_knowledge", "slots": {}},
                    ensure_ascii=False,
                ),
                "   ",
            ]
        )
    )

    async with await _build_client(container) as client:
        token = await _login(client, "knowledge-empty-answer-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "这款耳机的保修期是多久？"},
            headers=_auth(token),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["intent"] == "handoff"
    assert body["handoff"]["status"] == "open"
    assert body["sources"] == []
    assert "人工客服" in body["answer"]

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        ticket = await session.get(HandoffTicket, body["handoff"]["id"])
    assert ticket is not None
    assert ticket.evidence["tool"]["knowledge"] == {
        "kb_type": "product",
        "status": "empty_answer",
    }


@pytest.mark.anyio
async def test_knowledge_retrieval_error_is_sanitized_in_response_and_ticket(
    db_session, monkeypatch
):
    await create_user(db_session, "knowledge-error-owner")

    async def failing_retrieve(*args, **kwargs):
        raise RuntimeError(
            "SELECT * FROM knowledge; password=secret; "
            "postgresql://user:secret@db/internal"
        )

    monkeypatch.setattr(nodes, "retrieve_knowledge", failing_retrieve)
    container = _make_container(
        ScriptedChat(
            {
                "会员积分": {
                    "intent": "policy_knowledge",
                    "slots": {},
                }
            }
        )
    )

    async with await _build_client(container) as client:
        token = await _login(client, "knowledge-error-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "会员积分规则是什么？"},
            headers=_auth(token),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["intent"] == "handoff"
    assert body["handoff"]["status"] == "open"
    serialized = json.dumps(body, ensure_ascii=False)
    assert "SELECT" not in serialized
    assert "password" not in serialized
    assert "postgresql://" not in serialized

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        ticket = await session.get(HandoffTicket, body["handoff"]["id"])
    assert ticket is not None
    assert ticket.evidence["tool"]["knowledge"] == {
        "kb_type": "policy",
        "status": "retrieval_failed",
    }
    serialized_ticket = json.dumps(ticket.evidence, ensure_ascii=False)
    assert "SELECT" not in serialized_ticket
    assert "password" not in serialized_ticket
    assert "postgresql://" not in serialized_ticket


@pytest.mark.anyio
async def test_after_sale_progress_returns_facts_without_threshold_handoff(
    db_session,
):
    owner = await create_user(db_session, "after-sale-api-owner")
    order = await create_order(
        db_session,
        owner.id,
        "ORD-6001",
        status="已完成",
        total="899.00",
        items=[("耳机", 1, "899.00")],
    )
    await create_after_sale(
        db_session,
        order.id,
        service_type="退款",
        status="已完成",
        refund_amount="899.00",
        reason="价保退款",
    )
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "after-sale-api-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我的订单 ORD-6001 退款进度"},
            headers=_auth(token),
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["intent"] == "after_sale"
        assert body["handoff"] is None
        assert "退款" in body["answer"]
        assert "899.00" in body["answer"]
        assert "价保退款" in body["answer"]
        assert body["after_sale"]["status"] == "has_records"
        assert body["after_sale"]["order_no"] == "ORD-6001"
        assert body["after_sale"]["order_status"] == "已完成"
        assert body["after_sale"]["service_type"] == "退款"
        assert body["after_sale"]["handoff_required"] is False
        assert body["after_sale"]["records"] == [
            {
                "id": body["after_sale"]["records"][0]["id"],
                "service_type": "退款",
                "status": "已完成",
                "refund_amount": "899.00",
                "reason": "价保退款",
                "created_at": body["after_sale"]["records"][0]["created_at"],
            }
        ]

        messages = (
            await client.get(
                f"/api/conversations/{conv['id']}/messages",
                headers=_auth(token),
            )
        ).json()
        assert messages[-1]["after_sale"] == body["after_sale"]

    run, tool_call = await _load_run_and_tool_call(body["trace_id"])
    assert run.trace["intent"] == "after_sale"
    assert run.trace["tool_evidence"]["after_sale"][0]["status"] == "已完成"
    assert run.trace["tool_evidence"]["order"]["status"] == "已完成"
    assert run.trace["tool_evidence"]["threshold"]["applied"] is False
    assert tool_call.tool_name == "after_sale"
    assert tool_call.result["threshold"]["exceeded"] is False

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (await session.execute(select(HandoffTicket))).scalars().all()
        assert tickets == []
        after_sales = (await session.execute(select(AfterSale))).scalars().all()
        assert len(after_sales) == 1


@pytest.mark.anyio
async def test_after_sale_progress_without_records_returns_explicit_empty_state(
    db_session,
):
    owner = await create_user(db_session, "after-sale-empty-owner")
    await create_order(
        db_session,
        owner.id,
        "ORD-7001",
        status="已完成",
        items=[("耳机", 1, "199.00")],
    )
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "after-sale-empty-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我的订单 ORD-7001 售后进度"},
            headers=_auth(token),
        )
        body = resp.json()

    assert resp.status_code == 200
    assert body["after_sale"] == {
        "status": "no_records",
        "order_no": "ORD-7001",
        "order_status": "已完成",
        "service_type": "售后",
        "records": [],
        "handoff_required": False,
    }


@pytest.mark.anyio
async def test_after_sale_unauthorized_and_missing_orders_are_indistinguishable(
    db_session,
):
    owner = await create_user(db_session, "protected-order-owner")
    viewer = await create_user(db_session, "order-viewer")
    await create_order(
        db_session,
        owner.id,
        "ORD-7101",
        status="已完成",
        items=[("耳机", 1, "299.00")],
    )
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "order-viewer")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        unauthorized = (
            await client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "我的订单 ORD-7101 退款进度"},
                headers=_auth(token),
            )
        ).json()
        missing = (
            await client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "我的订单 ORD-7102 退款进度"},
                headers=_auth(token),
            )
        ).json()

    assert unauthorized["answer"] == missing["answer"]
    assert unauthorized["intent"] == missing["intent"] == "after_sale"
    assert unauthorized["handoff"] == missing["handoff"] is None
    for body in (unauthorized, missing):
        assert body["after_sale"]["status"] == "not_available"
        assert body["after_sale"]["order_status"] is None
        assert body["after_sale"]["records"] == []
        assert body["after_sale"]["handoff_required"] is False
    assert {
        key: value
        for key, value in unauthorized["after_sale"].items()
        if key != "order_no"
    } == {
        key: value
        for key, value in missing["after_sale"].items()
        if key != "order_no"
    }


@pytest.mark.anyio
async def test_after_sale_query_error_is_sanitized_and_requires_handoff(
    db_session, monkeypatch
):
    owner = await create_user(db_session, "after-sale-error-owner")
    await create_order(
        db_session,
        owner.id,
        "ORD-7201",
        status="已完成",
        items=[("耳机", 1, "199.00")],
    )

    async def failing_lookup(*args, **kwargs):
        raise RuntimeError(
            "SELECT * FROM after_sales; password=secret; "
            "postgresql://user:secret@db/internal"
        )

    monkeypatch.setattr(nodes, "lookup_after_sales", failing_lookup)
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "after-sale-error-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我的订单 ORD-7201 售后进度"},
            headers=_auth(token),
        )
        body = resp.json()

    assert resp.status_code == 200
    assert body["after_sale"] == {
        "status": "query_error",
        "order_no": "ORD-7201",
        "order_status": "已完成",
        "service_type": "售后",
        "records": [],
        "handoff_required": True,
    }
    assert body["handoff"]["status"] == "open"
    serialized = json.dumps(body, ensure_ascii=False)
    assert "SELECT" not in serialized
    assert "password" not in serialized
    assert "postgresql://" not in serialized


@pytest.mark.anyio
async def test_after_sale_refund_above_threshold_creates_ticket_with_trace(
    db_session,
):
    owner = await create_user(db_session, "refund-threshold-owner")
    await create_order(
        db_session,
        owner.id,
        "ORD-6002",
        status="已完成",
        total="620.00",
        items=[("耳机", 1, "620.00")],
    )
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "refund-threshold-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我要退款订单 ORD-6002 金额 500.01 元"},
            headers=_auth(token),
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["intent"] == "handoff"
        assert body["handoff"] is not None
        assert body["handoff"]["status"] == "open"

    run, tool_call = await _load_run_and_tool_call(body["trace_id"])
    threshold = run.trace["tool_evidence"]["threshold"]
    assert threshold == {
        "applied": True,
        "amount": "500.01",
        "limit": "500.00",
        "exceeded": True,
    }
    assert "500.00" in run.trace["handoff_reason"]
    assert tool_call.tool_name == "after_sale"
    assert tool_call.result["threshold"]["exceeded"] is True

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (await session.execute(select(HandoffTicket))).scalars().all()
        assert len(tickets) == 1
        assert tickets[0].status == "open"
