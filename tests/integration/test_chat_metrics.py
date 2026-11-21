"""Chat-level metrics for successful, after-sale, and handoff flows."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import ToolCallLog
from app.db.session import create_session_factory
from app.graph import nodes
from app.graph.container import Container
from app.main import create_app
from app.rag.retriever import Providers
from tests.fakes import FakeChat, FakeEmbeddings, FakeReranker
from tests.helpers import create_order, create_user


def _build_app(chat):
    settings = get_settings()
    app = create_app(
        settings=settings,
        session_factory=create_session_factory(settings.database_url),
    )
    container = Container(
        chat=chat,
        providers=Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
        settings=settings,
    )
    app.dependency_overrides[get_container] = lambda: container
    return app, AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


async def _login_and_create_conversation(client: AsyncClient, username: str):
    token = (
        await client.post(
            "/api/auth/login",
            json={"username": username, "password": "password123"},
        )
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    conversation = (
        await client.post("/api/conversations", json={}, headers=headers)
    ).json()
    return headers, conversation["id"]


@pytest.mark.anyio
async def test_successful_chat_records_request_success_latency_and_tool(db_session):
    await create_user(db_session, "metrics-greeting")
    app, client = _build_app(FakeChat())
    async with client:
        headers, conversation_id = await _login_and_create_conversation(
            client, "metrics-greeting"
        )
        response = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "你好"},
            headers=headers,
        )

    assert response.status_code == 200
    snapshot = app.state.metrics.snapshot()
    assert snapshot["counters"]["chat.request.total"]["value"] == 1
    assert snapshot["counters"]["chat.success.total"]["value"] == 1
    assert snapshot["counters"]["tool.call.total"][
        "tool=greeting,outcome=success"
    ] == 1
    assert snapshot["timings"]["chat.latency_ms"]["count"] == 1


@pytest.mark.anyio
async def test_after_sale_progress_records_query_result(db_session):
    user = await create_user(db_session, "metrics-after-sale")
    await create_order(
        db_session,
        user.id,
        "ORD-6101",
        status="已签收",
        total="399.00",
    )
    app, client = _build_app(FakeChat())
    async with client:
        headers, conversation_id = await _login_and_create_conversation(
            client, "metrics-after-sale"
        )
        response = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "查询订单 ORD-6101 的退款进度"},
            headers=headers,
        )

    assert response.status_code == 200
    snapshot = app.state.metrics.snapshot()
    assert snapshot["counters"]["after_sale.query.total"][
        "outcome=no_records"
    ] == 1


@pytest.mark.anyio
async def test_refund_threshold_records_trigger_and_handoff(db_session):
    user = await create_user(db_session, "metrics-threshold")
    await create_order(
        db_session,
        user.id,
        "ORD-6102",
        status="已签收",
        total="600.00",
    )
    app, client = _build_app(FakeChat())
    async with client:
        headers, conversation_id = await _login_and_create_conversation(
            client, "metrics-threshold"
        )
        response = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "我要退款，订单号 ORD-6102，退款金额 600 元"},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["intent"] == "handoff"
    snapshot = app.state.metrics.snapshot()
    assert snapshot["counters"]["after_sale.refund_threshold.total"][
        "outcome=exceeded"
    ] == 1
    assert snapshot["counters"]["handoff.created.total"][
        "reason=threshold_exceeded"
    ] == 1


@pytest.mark.anyio
async def test_knowledge_unavailable_records_stable_handoff_reason(
    db_session, monkeypatch
):
    await create_user(db_session, "metrics-knowledge-miss")

    async def empty_retrieve(*args, **kwargs):
        return SimpleNamespace(is_empty=True)

    monkeypatch.setattr(nodes, "retrieve_knowledge", empty_retrieve)
    app, client = _build_app(
        FakeChat(
            json.dumps(
                {"intent": "policy_knowledge", "slots": {}},
                ensure_ascii=False,
            )
        )
    )
    async with client:
        headers, conversation_id = await _login_and_create_conversation(
            client, "metrics-knowledge-miss"
        )
        response = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "会员积分规则是什么"},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["intent"] == "knowledge_gap"
    snapshot = app.state.metrics.snapshot()
    assert snapshot["counters"]["handoff.created.total"][
        "reason=knowledge_unavailable"
    ] == 1


@pytest.mark.anyio
async def test_knowledge_unavailable_tool_trace_keeps_knowledge_source(
    db_session, monkeypatch
):
    await create_user(db_session, "metrics-knowledge-tool")

    async def failing_retrieve(*args, **kwargs):
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(nodes, "retrieve_knowledge", failing_retrieve)
    app, client = _build_app(
        FakeChat(
            json.dumps(
                {"intent": "product_knowledge", "slots": {}},
                ensure_ascii=False,
            )
        )
    )
    async with client:
        headers, conversation_id = await _login_and_create_conversation(
            client, "metrics-knowledge-tool"
        )
        response = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "这款耳机支持降噪吗"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    snapshot = app.state.metrics.snapshot()
    assert snapshot["counters"]["tool.call.total"][
        "tool=knowledge,outcome=failure"
    ] == 1

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tool_call = (
            await session.execute(
                select(ToolCallLog).where(
                    ToolCallLog.agent_run_id == body["trace_id"]
                )
            )
        ).scalar_one()
    assert tool_call.tool_name == "knowledge"
