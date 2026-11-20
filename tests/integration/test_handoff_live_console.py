"""Integration tests for the live human-agent console."""

from __future__ import annotations

import pytest

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


async def _seed_ticket(db_session, user_id, status="open"):
    conversation = Conversation(user_id=user_id, title="转人工会话")
    db_session.add(conversation)
    await db_session.flush()
    ticket = HandoffTicket(
        conversation_id=conversation.id,
        user_id=user_id,
        status=status,
        summary="顾客要求人工处理退款",
        slots={"order_no": "ORD-DEMO-1"},
        recent_messages=[],
        evidence={"sources": [], "tool": {}},
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
