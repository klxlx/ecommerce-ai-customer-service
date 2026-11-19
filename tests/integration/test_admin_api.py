"""Admin API integration tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import AgentRun, ChatMessage, Conversation, HandoffTicket
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.main import create_app
from app.rag.retriever import Providers
from tests.fakes import FakeChat, FakeEmbeddings, FakeReranker
from tests.helpers import create_user


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username, password="password123"):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["token"]


async def _seed_ticket(db_session, user_id, status="open", summary="测试工单"):
    conversation = Conversation(user_id=user_id, title="测试会话")
    db_session.add(conversation)
    await db_session.flush()
    ticket = HandoffTicket(
        conversation_id=conversation.id,
        user_id=user_id,
        status=status,
        summary=summary,
        slots={"order_no": "ORD-1"},
        recent_messages=[{"role": "user", "content": "转人工"}],
        evidence={"sources": [], "tool": {}},
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


@pytest.mark.anyio
async def test_admin_routes_reject_unauthenticated(client):
    resp = await client.get("/api/admin/overview")
    assert resp.status_code == 404
    resp = await client.get("/api/admin/handoffs")
    assert resp.status_code == 401
    resp = await client.get("/api/admin/knowledge")
    assert resp.status_code == 401
    resp = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "商品知识",
            "kb_type": "product",
            "content": "这是静态商品说明。",
        },
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_admin_routes_reject_customer_token(db_session, client):
    customer = await create_user(db_session, "cust", role="customer")
    ticket = await _seed_ticket(db_session, customer.id)
    token = await _login(client, "cust")
    handoffs = await client.get("/api/admin/handoffs", headers=_auth(token))
    assert handoffs.status_code == 403
    detail = await client.get(
        f"/api/admin/handoffs/{ticket.id}", headers=_auth(token)
    )
    assert detail.status_code == 403
    update = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "resolved"},
        headers=_auth(token),
    )
    assert update.status_code == 403
    messages = await client.get(
        f"/api/admin/handoffs/{ticket.id}/messages",
        headers=_auth(token),
    )
    assert messages.status_code == 403
    reply = await client.post(
        f"/api/admin/handoffs/{ticket.id}/messages",
        json={"content": "人工回复"},
        headers=_auth(token),
    )
    assert reply.status_code == 403
    knowledge = await client.get("/api/admin/knowledge", headers=_auth(token))
    assert knowledge.status_code == 403
    add_knowledge = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "商品知识",
            "kb_type": "product",
            "content": "这是静态商品说明。",
        },
        headers=_auth(token),
    )
    assert add_knowledge.status_code == 403


@pytest.mark.anyio
async def test_admin_handoff_list_filter(db_session, client):
    admin = await create_user(db_session, "admin1", role="admin")
    customer = await create_user(db_session, "cust1", role="customer")
    await _seed_ticket(db_session, customer.id, status="open", summary="A")
    await _seed_ticket(db_session, customer.id, status="resolved", summary="B")
    token = await _login(client, "admin1")

    all_resp = await client.get("/api/admin/handoffs", headers=_auth(token))
    assert len(all_resp.json()) == 2

    open_resp = await client.get("/api/admin/handoffs", params={"status": "open"}, headers=_auth(token))
    bodies = open_resp.json()
    assert len(bodies) == 1
    assert bodies[0]["summary"] == "A"


@pytest.mark.anyio
async def test_admin_handoff_detail_and_status_update(db_session, client):
    admin = await create_user(db_session, "admin1", role="admin")
    customer = await create_user(db_session, "cust1", role="customer")
    ticket = await _seed_ticket(db_session, customer.id, status="open", summary="详情")
    token = await _login(client, "admin1")

    detail = await client.get(f"/api/admin/handoffs/{ticket.id}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["summary"] == "详情"
    assert detail.json()["slots"]["order_no"] == "ORD-1"

    patch = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "in_progress"},
        headers=_auth(token),
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "in_progress"


@pytest.mark.anyio
async def test_admin_handoff_not_found(db_session, client):
    await create_user(db_session, "admin1", role="admin")
    token = await _login(client, "admin1")
    resp = await client.get("/api/admin/handoffs/99999", headers=_auth(token))
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_admin_reply_returns_conversation_to_agent(db_session, client):
    admin = await create_user(db_session, "admin-reply-user", role="admin")
    customer = await create_user(db_session, "cust-reply-user")
    ticket = await _seed_ticket(
        db_session,
        customer.id,
        status="in_progress",
        summary="价格问题需要人工确认",
    )
    db_session.add_all(
        [
            ChatMessage(
                conversation_id=ticket.conversation_id,
                role="user",
                content="这个订单可以便宜一点吗？",
            ),
            ChatMessage(
                conversation_id=ticket.conversation_id,
                role="assistant",
                content="已为您转接人工客服。",
                intent="handoff",
            ),
        ]
    )
    await db_session.commit()
    token = await _login(client, "admin-reply-user")

    history = await client.get(
        f"/api/admin/handoffs/{ticket.id}/messages",
        headers=_auth(token),
    )
    assert history.status_code == 200
    assert [message["role"] for message in history.json()] == [
        "user",
        "assistant",
    ]

    reply = await client.post(
        f"/api/admin/handoffs/{ticket.id}/messages",
        json={"content": "可以为您申请一张 20 元优惠券。"},
        headers=_auth(token),
    )
    assert reply.status_code == 200
    assert reply.json()["role"] == "admin"
    assert reply.json()["content"] == "可以为您申请一张 20 元优惠券。"

    await db_session.refresh(ticket)
    assert ticket.status == "resolved"
    runs = (
        await db_session.execute(
            select(AgentRun).where(
                AgentRun.conversation_id == ticket.conversation_id
            )
        )
    ).scalars().all()
    assert runs == []


@pytest.mark.anyio
async def test_manual_handoff_pauses_agent_until_admin_replies(
    db_session,
):
    admin = await create_user(db_session, "admin-manual-mode", role="admin")
    customer = await create_user(db_session, "cust-manual-mode")
    ticket = await _seed_ticket(
        db_session,
        customer.id,
        status="in_progress",
        summary="人工处理中",
    )
    container = Container(
        chat=FakeChat(),
        providers=Providers(
            embeddings=FakeEmbeddings(),
            rerank=FakeReranker(),
        ),
        settings=get_settings(),
    )
    app = create_app(
        settings=get_settings(),
        session_factory=create_session_factory(get_settings().database_url),
    )
    app.dependency_overrides[get_container] = lambda: container

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        customer_token = await _login(api, "cust-manual-mode")
        during_handoff = await api.post(
            f"/api/conversations/{ticket.conversation_id}/messages",
            json={"content": "我再补充一下退款原因。"},
            headers=_auth(customer_token),
        )
        assert during_handoff.status_code == 200

        runs = (
            await db_session.execute(
                select(AgentRun).where(
                    AgentRun.conversation_id == ticket.conversation_id
                )
            )
        ).scalars().all()
        assert runs == []

        admin_token = await _login(api, "admin-manual-mode")
        reply = await api.post(
            f"/api/admin/handoffs/{ticket.id}/messages",
            json={"content": "收到，我来继续处理。"},
            headers=_auth(admin_token),
        )
        assert reply.status_code == 200

        after_reply = await api.post(
            f"/api/conversations/{ticket.conversation_id}/messages",
            json={"content": "你好"},
            headers=_auth(customer_token),
        )
        assert after_reply.status_code == 200

        runs = (
            await db_session.execute(
                select(AgentRun).where(
                    AgentRun.conversation_id == ticket.conversation_id
                )
            )
        ).scalars().all()
        assert len(runs) == 1
