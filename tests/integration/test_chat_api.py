"""Chat API integration tests with fake providers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.core.errors import ProviderTimeoutError
from app.core.resilience import ResiliencePolicy
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


class ResilientStreamChat:
    """Routes once, then streams an answer with scripted failures.

    ``attempts`` entries are either an exception (fail before the first chunk),
    a list of chunks, or a ``(chunks, error)`` tuple (emit then fail).
    """

    def __init__(
        self,
        attempts: list[list[str] | Exception | tuple[list[str], Exception]],
        *,
        fallback: str = "",
        route_intent: str = "policy_knowledge",
    ) -> None:
        self.attempts = attempts
        self.fallback = fallback
        self.route_intent = route_intent
        self.stream_calls = 0
        self.complete_calls = 0

    async def complete(self, messages):
        self.complete_calls += 1
        if self.complete_calls == 1:
            return json.dumps(
                {"intent": self.route_intent, "slots": {}},
                ensure_ascii=False,
            )
        return self.fallback

    async def stream(self, messages, model=None):
        index = self.stream_calls
        self.stream_calls += 1
        if index >= len(self.attempts):
            raise ProviderTimeoutError("chat", "no stream attempt left")
        attempt = self.attempts[index]
        if isinstance(attempt, Exception):
            raise attempt
        error: Exception | None = None
        if isinstance(attempt, tuple):
            chunks, error = attempt
        else:
            chunks = attempt
        for chunk in chunks:
            yield chunk
        if error is not None:
            raise error

    @property
    def stream_policy(self) -> ResiliencePolicy:
        return ResiliencePolicy(
            attempt_timeout_seconds=2.0,
            total_budget_seconds=10.0,
            max_retries=2,
            backoff_seconds=0.0,
        )


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
        assert roles == ["assistant", "user", "assistant"]


@pytest.mark.anyio
async def test_streaming_endpoint_emits_tokens_and_done(db_session):
    await create_user(db_session, "stream-owner")
    container = _make_container(
        ScriptedChat({"你好": {"intent": "greeting", "slots": {}}})
    )
    async with await _build_client(container) as client:
        token = await _login(client, "stream-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages/stream",
            json={"content": "你好"},
            headers=_auth(token),
        )

    assert resp.status_code == 200
    assert ": connected" in resp.text
    assert "event: token" in resp.text
    assert "event: done" in resp.text
    done = next(
        json.loads(line.removeprefix("data:"))
        for line in resp.text.splitlines()
        if line.startswith("data:") and '"message_id"' in line
    )
    assert done["intent"] == "greeting"
    assert "购物客服" in done["answer"]


@pytest.mark.anyio
async def test_knowledge_gap_ticket_does_not_block_next_turn(
    db_session, monkeypatch
):
    await create_user(db_session, "knowledge-gap-followup-owner")

    async def empty_retrieve(*args, **kwargs):
        return RetrievalResult()

    monkeypatch.setattr(nodes, "retrieve_knowledge", empty_retrieve)
    container = _make_container(
        ScriptedChat(
            {
                "积分": {"intent": "policy_knowledge", "slots": {}},
                "你好": {"intent": "greeting", "slots": {}},
            }
        )
    )

    async with await _build_client(container) as client:
        token = await _login(client, "knowledge-gap-followup-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        first = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "会员积分规则是什么？"},
            headers=_auth(token),
        )
        second = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )

    assert first.json()["intent"] == "knowledge_gap"
    assert second.json()["intent"] == "greeting"
    assert "消息已发送给人工客服" not in second.json()["answer"]


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
        total="9.99",
        items=[("耳机", 1, "9.99")],
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
    assert "9.99" in second_body["answer"]

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
        assert len(messages) == 3


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
    assert len(messages) == 3
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
    assert body["intent"] == "knowledge_gap"
    assert body["handoff"] is None
    assert "学习内容" in body["answer"]

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        ticket = (
            await session.execute(
                select(HandoffTicket).where(
                    HandoffTicket.conversation_id == conversation["id"]
                )
            )
        ).scalar_one()
    assert ticket.kind == "knowledge_gap"
    assert ticket.status == "open"
    assert ticket.evidence["tool"]["knowledge"] == {
        "kb_type": "policy",
        "status": "not_found",
    }


@pytest.mark.anyio
async def test_active_handoff_acknowledges_emotional_message(db_session):
    user = await create_user(db_session, "active-handoff-emotion-owner")
    conversation = Conversation(user_id=user.id, title="人工处理中")
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        HandoffTicket(
            conversation_id=conversation.id,
            user_id=user.id,
            status="open",
            kind="human",
            summary="历史售后问题",
            slots={},
            recent_messages=[],
            evidence={},
        )
    )
    await db_session.flush()
    await db_session.commit()

    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "active-handoff-emotion-owner")
        response = await client.post(
            f"/api/conversations/{conversation.id}/messages",
            json={"content": "我想打人"},
            headers=_auth(token),
        )

    body = response.json()
    assert response.status_code == 200
    assert "安全" in body["answer"]
    assert "这条消息已同步给人工客服" in body["answer"]
    assert "服务需求太多" not in body["answer"]

    rows = (
        await db_session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id)
        )
    ).all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[-1].content == body["answer"]
    assert body["message_id"] == rows[-1].id


@pytest.mark.anyio
async def test_streaming_active_handoff_persists_assistant_message(db_session):
    user = await create_user(db_session, "active-handoff-stream-owner")
    conversation = Conversation(user_id=user.id, title="人工处理中")
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        HandoffTicket(
            conversation_id=conversation.id,
            user_id=user.id,
            status="open",
            kind="human",
            summary="历史售后问题",
            slots={},
            recent_messages=[],
            evidence={},
        )
    )
    await db_session.flush()
    await db_session.commit()

    container = _make_container(ScriptedChat({}))
    async with await _build_client(container) as client:
        token = await _login(client, "active-handoff-stream-owner")
        resp = await client.post(
            f"/api/conversations/{conversation.id}/messages/stream",
            json={"content": "我想打人"},
            headers=_auth(token),
        )

    assert resp.status_code == 200
    assert "event: token" in resp.text
    assert "event: done" in resp.text
    done = next(
        json.loads(line.removeprefix("data:"))
        for line in resp.text.splitlines()
        if line.startswith("data:") and '"message_id"' in line
    )
    assert "安全" in done["answer"]

    rows = (
        await db_session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id)
        )
    ).all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[-1].content == done["answer"]
    assert done["message_id"] == rows[-1].id


@pytest.mark.anyio
async def test_third_unresolved_ticket_returns_limit_reply(
    db_session, monkeypatch
):
    user = await create_user(db_session, "ticket-limit-owner")
    old_a = Conversation(user_id=user.id, title="旧问题一")
    old_b = Conversation(user_id=user.id, title="旧问题二")
    db_session.add_all([old_a, old_b])
    await db_session.flush()
    db_session.add_all(
        [
            HandoffTicket(
                conversation_id=old_a.id,
                user_id=user.id,
                status="open",
                kind="human",
                summary="订单一直没发货",
                slots={},
                recent_messages=[],
                evidence={},
            ),
            HandoffTicket(
                conversation_id=old_b.id,
                user_id=user.id,
                status="open",
                kind="knowledge_gap",
                summary="怎么申请退款",
                slots={},
                recent_messages=[],
                evidence={},
            ),
        ]
    )
    await db_session.flush()
    await db_session.commit()

    async def empty_retrieve(*args, **kwargs):
        return RetrievalResult()

    monkeypatch.setattr(nodes, "retrieve_knowledge", empty_retrieve)
    container = _make_container(
        ScriptedChat(
            {
                "Mate 90": {
                    "intent": "policy_knowledge",
                    "slots": {},
                }
            }
        )
    )

    async with await _build_client(container) as client:
        token = await _login(client, "ticket-limit-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "Mate 90 的电池容量是多少？"},
            headers=_auth(token),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["handoff"] is None
    assert body["answer"] == nodes.KNOWLEDGE_GAP_REPLY

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (
            await session.execute(select(HandoffTicket))
        ).scalars().all()
        run = await session.get(AgentRun, body["trace_id"])
    assert len(tickets) == 3
    assert run.trace["ticket_policy"]["action"] == "created"


@pytest.mark.anyio
async def test_duplicate_knowledge_gap_does_not_create_second_ticket(
    db_session, monkeypatch
):
    user = await create_user(db_session, "ticket-dedupe-owner")
    old = Conversation(user_id=user.id, title="旧问题")
    db_session.add(old)
    await db_session.flush()
    db_session.add(
        HandoffTicket(
            conversation_id=old.id,
            user_id=user.id,
            status="open",
            kind="knowledge_gap",
            summary="Mate 90 的电池容量是多少？",
            slots={},
            recent_messages=[],
            evidence={},
            occurrence_count=1,
        )
    )
    await db_session.flush()
    await db_session.commit()

    async def empty_retrieve(*args, **kwargs):
        return RetrievalResult()

    monkeypatch.setattr(nodes, "retrieve_knowledge", empty_retrieve)
    container = _make_container(
        ScriptedChat(
            {
                "Mate 90": {
                    "intent": "policy_knowledge",
                    "slots": {},
                }
            }
        )
    )

    async with await _build_client(container) as client:
        token = await _login(client, "ticket-dedupe-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "Mate 90 电池容量"},
            headers=_auth(token),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["handoff"] is None
    assert "学习内容" in body["answer"]

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (
            await session.execute(select(HandoffTicket))
        ).scalars().all()
        run = await session.get(AgentRun, body["trace_id"])
    assert len(tickets) == 1
    assert tickets[0].occurrence_count == 2
    assert run.trace["ticket_policy"]["action"] == "reused"


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
    assert body["intent"] == "knowledge_gap"
    assert body["handoff"] is None
    assert body["sources"] == []
    assert "学习内容" in body["answer"]

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        ticket = (
            await session.execute(
                select(HandoffTicket).where(
                    HandoffTicket.conversation_id == conversation["id"]
                )
            )
        ).scalar_one()
    assert ticket.kind == "knowledge_gap"
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
    assert body["intent"] == "knowledge_gap"
    assert body["handoff"] is None
    serialized = json.dumps(body, ensure_ascii=False)
    assert "SELECT" not in serialized
    assert "password" not in serialized
    assert "postgresql://" not in serialized

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        ticket = (
            await session.execute(
                select(HandoffTicket).where(
                    HandoffTicket.conversation_id == conversation["id"]
                )
            )
        ).scalar_one()
    assert ticket.kind == "knowledge_gap"
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
async def test_after_sale_query_error_is_sanitized_and_creates_internal_ticket(
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
        "handoff_required": False,
    }
    assert body["handoff"] is None
    assert body["answer"] == nodes.KNOWLEDGE_GAP_REPLY
    assert body["intent"] == "knowledge_gap"
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
            json={"content": "我要退款订单 ORD-6002 金额 10.01 元"},
            headers=_auth(token),
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["intent"] == "handoff"
        assert body["handoff"] is not None
        assert body["handoff"]["status"] in {"open", "in_progress"}

    run, tool_call = await _load_run_and_tool_call(body["trace_id"])
    threshold = run.trace["tool_evidence"]["threshold"]
    assert threshold == {
        "applied": True,
        "amount": "10.01",
        "limit": "10.00",
        "exceeded": True,
        "auto_small_refund": False,
    }
    assert "10.00" in run.trace["handoff_reason"]
    assert tool_call.tool_name == "after_sale"
    assert tool_call.result["threshold"]["exceeded"] is True

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (await session.execute(select(HandoffTicket))).scalars().all()
        assert len(tickets) == 1
        assert tickets[0].status == "open"


@pytest.mark.anyio
async def test_small_refund_daily_limit_and_feedback_boundary(db_session):
    owner = await create_user(db_session, "small-refund-limit-owner")
    await create_order(
        db_session,
        owner.id,
        "ORD-9001",
        status="已完成",
        total="9.99",
        items=[("耳机", 1, "9.99")],
    )
    container = _make_container(ScriptedChat({}))

    async with await _build_client(container) as client:
        token = await _login(client, "small-refund-limit-owner")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()

        for _ in range(3):
            response = await client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "我要退款订单 ORD-9001 金额 9.99 元"},
                headers=_auth(token),
            )
            body = response.json()
            assert body["intent"] == "after_sale"
            assert body["handoff"]["status"] == "resolved"
            assert "9.99" in body["answer"]

        fourth = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我要退款订单 ORD-9001 金额 9.99 元"},
            headers=_auth(token),
        )
        fourth_body = fourth.json()
        assert fourth_body["intent"] == "handoff"
        assert fourth_body["handoff"]["status"] == "open"
        assert "次数已用完" in fourth_body["answer"]

        factory = create_session_factory(get_settings().database_url)
        async with factory() as session:
            auto_tickets = (
                await session.execute(
                    select(HandoffTicket).where(
                        HandoffTicket.close_reason == "auto_small_refund"
                    )
                )
            ).scalars().all()
            assert len(auto_tickets) == 3
            auto_ticket = auto_tickets[0]
            assert auto_ticket.status == "resolved"
            assert auto_ticket.business_status == "已解决"

        rated = await client.post(
            f"/api/conversations/{auto_ticket.conversation_id}/tickets/{auto_ticket.id}/feedback",
            json={"rating": 4, "comment": "小额退款处理很快。"},
            headers=_auth(token),
        )
        assert rated.status_code == 200
        assert rated.json()["rating"] == 4

        duplicate = await client.post(
            f"/api/conversations/{auto_ticket.conversation_id}/tickets/{auto_ticket.id}/feedback",
            json={"rating": 5},
            headers=_auth(token),
        )
        assert duplicate.status_code == 409


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line.removeprefix("data: "))))
            event = None
    return events


@pytest.mark.anyio
async def test_stream_retry_before_first_token_emits_one_answer(
    db_session, monkeypatch
):
    await create_user(db_session, "stream-retry-owner")

    async def found_retrieve(*args, **kwargs):
        return RetrievalResult(
            parent_ids=[7],
            context="签收后七天内可申请无理由退货，商品需保持完好。",
        )

    monkeypatch.setattr(nodes, "retrieve_knowledge", found_retrieve)
    chat = ResilientStreamChat(
        [
            ProviderTimeoutError("chat", "stream first token timeout"),
            ["签收后七天内可申请无理由退货。"],
        ]
    )
    container = _make_container(chat)
    container.agents = SimpleNamespace(
        router=None,
        knowledge=None,
        chitchat=SimpleNamespace(model=None, system_prompt=None),
    )

    async with await _build_client(container) as client:
        token = await _login(client, "stream-retry-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages/stream",
            json={"content": "七天无理由退货规则是什么？"},
            headers=_auth(token),
        )

    events = _sse_events(response.text)
    token_text = "".join(
        payload["text"] for event, payload in events if event == "token"
    )
    done = [payload for event, payload in events if event == "done"][-1]

    assert token_text == "签收后七天内可申请无理由退货。"
    assert done["answer"] == token_text
    assert not any(event == "replace" for event, _ in events)
    assert chat.stream_calls == 2


@pytest.mark.anyio
async def test_visible_partial_stream_is_replaced_by_complete_answer(
    db_session,
):
    await create_user(db_session, "stream-replace-owner")
    chat = ResilientStreamChat(
        [(["半截答案"], ProviderTimeoutError("chat", "stream idle timeout"))],
        fallback="完整答案。",
        route_intent="chit_chat",
    )
    container = _make_container(chat)
    container.agents = SimpleNamespace(
        router=None,
        chitchat=SimpleNamespace(model=None, system_prompt=None),
    )

    async with await _build_client(container) as client:
        token = await _login(client, "stream-replace-owner")
        conversation = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages/stream",
            json={"content": "陪我聊聊天"},
            headers=_auth(token),
        )

    events = _sse_events(response.text)
    token_text = "".join(
        payload["text"] for event, payload in events if event == "token"
    )
    replacements = [
        payload["text"] for event, payload in events if event == "replace"
    ]
    done = [payload for event, payload in events if event == "done"][-1]

    assert token_text == "半截答案"
    assert replacements == ["完整答案。"]
    assert done["answer"] == "完整答案。"
    assert chat.stream_calls == 1
