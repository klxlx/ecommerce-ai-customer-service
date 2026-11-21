"""Integration tests for the live human-agent console."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import ChatMessage, Conversation, HandoffTicket
from tests.helpers import create_user


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username, password="password123"):
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    return resp.json()["token"]


async def _seed_ticket(
    db_session,
    user_id,
    status="open",
    *,
    kind="human",
    category="general",
    evidence=None,
):
    conversation = Conversation(user_id=user_id, title="转人工会话")
    db_session.add(conversation)
    await db_session.flush()
    ticket = HandoffTicket(
        conversation_id=conversation.id,
        user_id=user_id,
        status=status,
        kind=kind,
        category=category,
        summary="顾客要求人工处理退款",
        slots={"order_no": "ORD-DEMO-1"},
        recent_messages=[],
        evidence=evidence or {"sources": [], "tool": {}},
    )
    db_session.add(ticket)
    db_session.add_all(
        [
            ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content="我要人工处理退货。",
            ),
            ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="已为您转接人工客服。",
                intent="handoff",
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


@pytest.mark.anyio
async def test_live_queue_claim_is_exclusive(db_session, client):
    admin1 = await create_user(db_session, "live-admin-1", role="admin")
    admin2 = await create_user(db_session, "live-admin-2", role="admin")
    customer = await create_user(db_session, "live-customer-1")
    ticket = await _seed_ticket(db_session, customer.id)

    token1 = await _login(client, "live-admin-1")
    token2 = await _login(client, "live-admin-2")

    live = await client.get("/api/admin/handoffs/live", headers=_auth(token1))
    assert live.status_code == 200
    session = live.json()[0]
    assert session["id"] == ticket.id
    assert session["username"] == "live-customer-1"
    assert session["status"] == "open"
    assert session["last_message"] == "已为您转接人工客服。"

    claim = await client.post(
        f"/api/admin/handoffs/{ticket.id}/claim",
        headers=_auth(token1),
    )
    assert claim.status_code == 200
    assert claim.json()["status"] == "in_progress"
    assert claim.json()["assignee_id"] == admin1.id

    conflict = await client.post(
        f"/api/admin/handoffs/{ticket.id}/claim",
        headers=_auth(token2),
    )
    assert conflict.status_code == 409
    assert "live-admin-1" in conflict.json()["detail"]

    live_after = await client.get(
        "/api/admin/handoffs/live", headers=_auth(token2)
    )
    session = live_after.json()[0]
    assert session["status"] == "in_progress"
    assert session["assignee_name"] == "live-admin-1"


@pytest.mark.anyio
async def test_reply_keeps_session_open_and_release_hands_back(
    db_session, client
):
    admin = await create_user(db_session, "live-admin-release", role="admin")
    customer = await create_user(db_session, "live-customer-release")
    ticket = await _seed_ticket(db_session, customer.id)

    token = await _login(client, "live-admin-release")
    claim = await client.post(
        f"/api/admin/handoffs/{ticket.id}/claim",
        headers=_auth(token),
    )
    assert claim.status_code == 200

    for content in ("我来看一下订单。", "已为您登记补发。"):
        reply = await client.post(
            f"/api/admin/handoffs/{ticket.id}/messages",
            json={"content": content},
            headers=_auth(token),
        )
        assert reply.status_code == 200
        assert reply.json()["role"] == "admin"

    await db_session.refresh(ticket)
    assert ticket.status == "in_progress"

    missing_note = await client.post(
        f"/api/admin/handoffs/{ticket.id}/release",
        json={"note": ""},
        headers=_auth(token),
    )
    assert missing_note.status_code == 422

    released = await client.post(
        f"/api/admin/handoffs/{ticket.id}/release",
        json={"note": "已登记补发，顾客同意。"},
        headers=_auth(token),
    )
    assert released.status_code == 200
    assert released.json()["status"] == "resolved"
    assert released.json()["close_reason"] == "returned_to_agent"

    await db_session.refresh(ticket)
    assert ticket.resolution_note == "已登记补发，顾客同意。"

    conversation = await db_session.get(Conversation, ticket.conversation_id)
    assert conversation is not None
    assert conversation.slots["last_handoff"]["ticket_id"] == ticket.id
    assert conversation.slots["last_handoff"]["note"] == "已登记补发，顾客同意。"

    await create_user(db_session, "live-admin-other", role="admin")
    other_token = await _login(client, "live-admin-other")
    after_release = await client.post(
        f"/api/admin/handoffs/{ticket.id}/messages",
        json={"content": "这条不应写入。"},
        headers=_auth(other_token),
    )
    assert after_release.status_code == 403


@pytest.mark.anyio
async def test_release_records_completion_and_adds_feedback_card(
    db_session, client
):
    admin = await create_user(db_session, "live-admin-feedback", role="admin")
    customer = await create_user(db_session, "live-customer-feedback")
    ticket = await _seed_ticket(db_session, customer.id)
    token = await _login(client, "live-admin-feedback")

    claim = await client.post(
        f"/api/admin/handoffs/{ticket.id}/claim", headers=_auth(token)
    )
    assert claim.status_code == 200
    released = await client.post(
        f"/api/admin/handoffs/{ticket.id}/release",
        json={"note": "已解决。"},
        headers=_auth(token),
    )
    assert released.status_code == 200
    assert released.json()["resolved_by_id"] == admin.id
    assert released.json()["resolved_by_name"] == "live-admin-feedback"
    assert released.json()["business_status"] == "已解决"

    await db_session.refresh(ticket)
    assert ticket.resolved_by_id == admin.id

    messages = (
        await db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == ticket.conversation_id)
            .order_by(ChatMessage.id.desc())
        )
    ).scalars().all()
    feedback = messages[0]
    assert feedback.role == "system"
    assert "本次人工服务已结束" in feedback.content
    assert any(
        action.get("type") == "ticket_feedback"
        for action in (feedback.actions or [])
    )


@pytest.mark.anyio
async def test_customer_can_rate_resolved_ticket_only_once(db_session, client):
    admin = await create_user(db_session, "live-admin-rate", role="admin")
    customer = await create_user(db_session, "live-customer-rate")
    ticket = await _seed_ticket(db_session, customer.id)
    admin_token = await _login(client, "live-admin-rate")
    customer_token = await _login(client, "live-customer-rate")

    assert (
        await client.post(
            f"/api/admin/handoffs/{ticket.id}/claim",
            headers=_auth(admin_token),
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/admin/handoffs/{ticket.id}/release",
            json={"note": "已处理。"},
            headers=_auth(admin_token),
        )
    ).status_code == 200

    rated = await client.post(
        f"/api/conversations/{ticket.conversation_id}/tickets/{ticket.id}/feedback",
        json={"rating": 5, "comment": "响应很快，处理清楚。"},
        headers=_auth(customer_token),
    )
    assert rated.status_code == 200
    assert rated.json()["rated"] is True
    assert rated.json()["rating"] == 5

    duplicate = await client.post(
        f"/api/conversations/{ticket.conversation_id}/tickets/{ticket.id}/feedback",
        json={"comment": "再评一次"},
        headers=_auth(customer_token),
    )
    assert duplicate.status_code == 409

    await db_session.refresh(ticket)
    assert ticket.rating == 5
    assert ticket.rating_comment == "响应很快，处理清楚。"
    assert ticket.rated_at is not None

    messages = (
        await db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == ticket.conversation_id)
            .order_by(ChatMessage.id.desc())
        )
    ).scalars().all()
    feedback_action = next(
        action
        for action in (messages[0].actions or [])
        if action.get("type") == "ticket_feedback"
    )
    assert feedback_action["rated"] is True
    assert feedback_action["rating"] == 5


@pytest.mark.anyio
async def test_internal_ticket_is_not_in_live_queue(db_session, client):
    admin = await create_user(db_session, "live-admin-internal", role="admin")
    customer = await create_user(db_session, "live-customer-internal")
    await _seed_ticket(db_session, customer.id, kind="internal")
    token = await _login(client, "live-admin-internal")

    live = await client.get("/api/admin/handoffs/live", headers=_auth(token))
    assert live.status_code == 200
    assert live.json() == []


@pytest.mark.anyio
async def test_return_popup_marks_ticket_and_exposes_action(db_session, client):
    admin = await create_user(db_session, "live-admin-return", role="admin")
    customer = await create_user(db_session, "live-customer-return")
    ticket = await _seed_ticket(db_session, customer.id)
    token = await _login(client, "live-admin-return")

    claim = await client.post(
        f"/api/admin/handoffs/{ticket.id}/claim", headers=_auth(token)
    )
    assert claim.status_code == 200
    reply = await client.post(
        f"/api/admin/handoffs/{ticket.id}/messages",
        json={"content": "请填写退货信息。", "action": "return_request"},
        headers=_auth(token),
    )
    assert reply.status_code == 200
    assert reply.json()["actions"] == [{"type": "return_request"}]

    await db_session.refresh(ticket)
    assert ticket.category == "return_exchange"
    assert ticket.business_status == "退换货"


@pytest.mark.anyio
async def test_refund_ticket_requires_claim_and_owner_to_resolve(
    db_session, client
):
    admin1 = await create_user(db_session, "live-admin-refund-1", role="admin")
    await create_user(db_session, "live-admin-refund-2", role="admin")
    customer = await create_user(db_session, "live-customer-refund")
    ticket = await _seed_ticket(db_session, customer.id, category="refund")

    token1 = await _login(client, "live-admin-refund-1")
    token2 = await _login(client, "live-admin-refund-2")

    claim = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "in_progress"},
        headers=_auth(token1),
    )
    assert claim.status_code == 200
    assert claim.json()["assignee_id"] == admin1.id
    assert claim.json()["assignee_name"] == "live-admin-refund-1"
    assert claim.json()["business_status"] == "处理中"

    detail = await client.get(
        f"/api/admin/handoffs/{ticket.id}", headers=_auth(token1)
    )
    assert detail.status_code == 200
    assert detail.json()["assignee_name"] == "live-admin-refund-1"

    wrong_owner = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "resolved"},
        headers=_auth(token2),
    )
    assert wrong_owner.status_code == 403

    resolved = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "resolved"},
        headers=_auth(token1),
    )
    assert resolved.status_code == 200
    assert resolved.json()["resolved_by_id"] == admin1.id


@pytest.mark.anyio
async def test_knowledge_gap_admin_can_mark_knowledge_patched(
    db_session, client
):
    admin = await create_user(db_session, "live-admin-gap", role="admin")
    customer = await create_user(db_session, "live-customer-gap")
    ticket = await _seed_ticket(
        db_session,
        customer.id,
        kind="knowledge_gap",
        evidence={"sources": [], "tool": {"knowledge": {"status": "insufficient_context"}}},
    )
    token = await _login(client, "live-admin-gap")

    before = (
        await db_session.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == ticket.conversation_id)
        )
    ).scalars().all()
    response = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "resolved"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["business_status"] == "知识已补全"
    assert body["resolved_by_name"] == "live-admin-gap"

    await db_session.refresh(ticket)
    after = (
        await db_session.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == ticket.conversation_id)
        )
    ).scalars().all()
    assert len(after) == len(before)


@pytest.mark.anyio
async def test_internal_tool_failure_admin_can_mark_fixed(db_session, client):
    admin = await create_user(db_session, "live-admin-internal-fix", role="admin")
    customer = await create_user(db_session, "live-customer-internal-fix")
    ticket = await _seed_ticket(
        db_session,
        customer.id,
        kind="internal",
        evidence={
            "sources": [],
            "tool": {},
            "internal_kind": "tool_failure",
        },
    )
    token = await _login(client, "live-admin-internal-fix")

    response = await client.patch(
        f"/api/admin/handoffs/{ticket.id}",
        json={"status": "resolved"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["business_status"] == "工具已修复"
    assert body["resolved_by_name"] == "live-admin-internal-fix"
    assert admin.id == body["resolved_by_id"]


@pytest.mark.anyio
async def test_stale_in_progress_ticket_without_assignee_can_be_reclaimed(
    db_session, client
):
    admin = await create_user(db_session, "live-admin-reclaim", role="admin")
    customer = await create_user(db_session, "live-customer-reclaim")
    ticket = await _seed_ticket(db_session, customer.id, status="in_progress")
    token = await _login(client, "live-admin-reclaim")

    response = await client.post(
        f"/api/admin/handoffs/{ticket.id}/claim",
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json()["assignee_id"] == admin.id
    assert response.json()["business_status"] == "处理中"
