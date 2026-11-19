"""Admin operational-log API integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import AgentRun, ChatMessage, Conversation, ToolCallLog
from tests.helpers import create_user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username: str) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["token"]


async def _seed_run(
    db_session,
    *,
    user_id: int,
    intent: str | None,
    trace: dict,
    created_at: datetime,
    tool_name: str | None = None,
    additional_tool_names: tuple[str, ...] = (),
    after_sale_context: dict | None = None,
) -> AgentRun:
    conversation = Conversation(user_id=user_id, title="日志测试会话")
    db_session.add(conversation)
    await db_session.flush()
    run = AgentRun(
        conversation_id=conversation.id,
        intent=intent,
        trace=trace,
        created_at=created_at,
    )
    db_session.add(run)
    await db_session.flush()
    if tool_name is not None:
        db_session.add(
            ToolCallLog(
                agent_run_id=run.id,
                tool_name=tool_name,
                arguments={
                    "order_no": "SECRET-ORDER",
                    "access_token": "secret-token",
                },
                result={
                    "raw_result": "secret-tool-result",
                    "handoff_reason": trace.get("handoff_reason"),
                },
                created_at=created_at,
            )
        )
    for index, additional_tool_name in enumerate(
        additional_tool_names,
        start=1,
    ):
        db_session.add(
            ToolCallLog(
                agent_run_id=run.id,
                tool_name=additional_tool_name,
                arguments={"secret": "never-returned"},
                result={"secret": "never-returned"},
                created_at=created_at + timedelta(seconds=index),
            )
        )
    if after_sale_context is not None or tool_name is not None:
        db_session.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="secret-answer-content",
                intent=intent,
                sources=trace.get("sources") or [],
                trace_id=run.id,
                after_sale_context=after_sale_context,
                created_at=created_at,
            )
        )
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.anyio
async def test_admin_logs_reject_unauthenticated_and_customer_tokens(
    db_session,
    client,
):
    await create_user(db_session, "logs-customer", role="customer")
    token = await _login(client, "logs-customer")

    unauthenticated = await client.get("/api/admin/logs")
    forbidden = await client.get(
        "/api/admin/logs",
        headers=_auth(token),
    )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403


@pytest.mark.anyio
async def test_admin_logs_are_paginated_newest_first_and_redacted(
    db_session,
    client,
):
    await create_user(db_session, "logs-admin", role="admin")
    customer = await create_user(db_session, "logs-owner", role="customer")
    base = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
    oldest = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="after_sale",
        created_at=base,
        tool_name="after_sale",
        after_sale_context={"status": "has_records"},
        trace={
            "intent": "after_sale",
            "slots": {"order_no": "SECRET-SLOT"},
            "sources": [],
            "tool_evidence": {
                "threshold": {
                    "applied": True,
                    "amount": "500.01",
                    "limit": "500.00",
                    "exceeded": True,
                }
            },
            "handoff_reason": "threshold_exceeded",
        },
    )
    middle = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="knowledge",
        created_at=base + timedelta(minutes=10),
        tool_name="knowledge",
        trace={
            "intent": "knowledge",
            "slots": {"question": "SECRET-SLOT"},
            "sources": [
                {
                    "title": "HUAWEI Pura 70 用户指南",
                    "source_uri": r"C:\private\guide.pdf",
                    "page_start": 6,
                    "page_end": 8,
                    "checksum": "secret-checksum",
                    "content": "secret-source-content",
                }
            ],
            "tool_evidence": {"kb_type": "product", "parent_ids": [9]},
            "handoff_reason": None,
        },
    )
    newest = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="order",
        created_at=base + timedelta(minutes=20),
        tool_name="order",
        trace={
            "intent": "order",
            "slots": {"order_no": "SECRET-SLOT"},
            "sources": [],
            "tool_evidence": {
                "order": {"order_no": "SECRET-ORDER", "status": "已发货"}
            },
            "handoff_reason": None,
        },
    )
    token = await _login(client, "logs-admin")

    response = await client.get(
        "/api/admin/logs",
        params={"limit": 2, "offset": 0},
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert [item["run_id"] for item in body["items"]] == [
        newest.id,
        middle.id,
    ]

    knowledge_item = body["items"][1]
    assert set(knowledge_item) == {
        "run_id",
        "conversation_id",
        "user_id",
        "intent",
        "tool_name",
        "retrieval_result",
        "source_count",
        "sources",
        "after_sale_status",
        "threshold_applied",
        "threshold_amount",
        "threshold_limit",
        "threshold_exceeded",
        "has_handoff",
        "handoff_reason",
        "run_status",
        "created_at",
    }
    assert knowledge_item["sources"] == [
        {
            "title": "HUAWEI Pura 70 用户指南",
            "page_start": 6,
            "page_end": 8,
        }
    ]
    assert knowledge_item["retrieval_result"] == "hit"
    assert knowledge_item["run_status"] == "success"

    second_page = await client.get(
        "/api/admin/logs",
        params={"limit": 2, "offset": 2},
        headers=_auth(token),
    )
    assert [item["run_id"] for item in second_page.json()["items"]] == [
        oldest.id
    ]
    assert second_page.json()["total"] == 3

    serialized = response.text
    for forbidden in (
        "SECRET-SLOT",
        "SECRET-ORDER",
        "secret-token",
        "secret-tool-result",
        "secret-answer-content",
        r"C:\private\guide.pdf",
        "secret-checksum",
        "secret-source-content",
    ):
        assert forbidden not in serialized


@pytest.mark.anyio
async def test_admin_log_filters_apply_before_pagination_and_keep_total(
    db_session,
    client,
):
    await create_user(db_session, "filters-admin", role="admin")
    customer_one = await create_user(
        db_session,
        "filters-customer-one",
        role="customer",
    )
    customer_two = await create_user(
        db_session,
        "filters-customer-two",
        role="customer",
    )
    base = datetime(2026, 11, 18, 4, 0, tzinfo=timezone.utc)
    knowledge_hit = await _seed_run(
        db_session,
        user_id=customer_one.id,
        intent="knowledge",
        created_at=base,
        tool_name="knowledge",
        trace={
            "intent": "knowledge",
            "sources": [{"title": "使用说明", "page_start": 2}],
            "tool_evidence": {"kb_type": "product", "parent_ids": [1]},
            "handoff_reason": None,
        },
    )
    handoff_after_sale = await _seed_run(
        db_session,
        user_id=customer_one.id,
        intent="after_sale",
        created_at=base + timedelta(minutes=10),
        tool_name="after_sale",
        after_sale_context={"status": "has_records"},
        trace={
            "intent": "after_sale",
            "sources": [{"title": "售后规则", "page_start": 3}],
            "tool_evidence": {
                "kb_type": "policy",
                "threshold": {
                    "applied": True,
                    "amount": "620.00",
                    "limit": "500.00",
                    "exceeded": True,
                },
            },
            "handoff_reason": "threshold_exceeded",
        },
    )
    knowledge_empty = await _seed_run(
        db_session,
        user_id=customer_two.id,
        intent="knowledge",
        created_at=base + timedelta(minutes=20),
        tool_name="knowledge",
        after_sale_context={"status": "not_available"},
        trace={
            "intent": "knowledge",
            "sources": [],
            "tool_evidence": {"kb_type": "policy", "parent_ids": []},
            "handoff_reason": None,
        },
    )
    failed_provider = await _seed_run(
        db_session,
        user_id=customer_two.id,
        intent="handoff",
        created_at=base + timedelta(minutes=30),
        tool_name="order",
        after_sale_context={"status": "query_error"},
        trace={
            "intent": "handoff",
            "sources": [],
            "tool_evidence": {
                "provider_failure": {
                    "provider": "dashscope",
                    "category": "timeout",
                }
            },
            "handoff_reason": "provider_failure",
        },
    )
    unknown_history = await _seed_run(
        db_session,
        user_id=customer_two.id,
        intent=None,
        created_at=base + timedelta(minutes=40),
        trace={},
    )
    token = await _login(client, "filters-admin")

    async def matching_ids(**params) -> list[int]:
        response = await client.get(
            "/api/admin/logs",
            params=params,
            headers=_auth(token),
        )
        assert response.status_code == 200
        return [item["run_id"] for item in response.json()["items"]]

    assert await matching_ids(intent="knowledge") == [
        knowledge_empty.id,
        knowledge_hit.id,
    ]
    assert await matching_ids(tool_name="after_sale") == [
        handoff_after_sale.id
    ]
    assert await matching_ids(retrieval_result="hit") == [
        handoff_after_sale.id,
        knowledge_hit.id,
    ]
    assert await matching_ids(retrieval_result="empty") == [
        knowledge_empty.id
    ]
    assert await matching_ids(after_sale_status="has_records") == [
        handoff_after_sale.id
    ]
    assert await matching_ids(threshold_applied=True) == [
        handoff_after_sale.id
    ]
    assert await matching_ids(threshold_exceeded=True) == [
        handoff_after_sale.id
    ]
    assert await matching_ids(has_handoff=True) == [
        failed_provider.id,
        handoff_after_sale.id,
    ]
    assert await matching_ids(user_id=customer_one.id) == [
        handoff_after_sale.id,
        knowledge_hit.id,
    ]
    assert await matching_ids(
        conversation_id=knowledge_empty.conversation_id
    ) == [knowledge_empty.id]
    assert await matching_ids(
        start_at=(base + timedelta(minutes=15)).isoformat(),
        end_at=(base + timedelta(minutes=25)).isoformat(),
    ) == [knowledge_empty.id]

    combined = await client.get(
        "/api/admin/logs",
        params={
            "intent": "knowledge",
            "retrieval_result": "empty",
            "limit": 1,
            "offset": 0,
        },
        headers=_auth(token),
    )
    assert combined.status_code == 200
    assert combined.json()["total"] == 1
    assert combined.json()["items"][0]["run_id"] == knowledge_empty.id

    unknown_result = await client.get(
        "/api/admin/logs",
        params={"run_status": "unknown"},
        headers=_auth(token),
    )
    assert unknown_result.json()["total"] == 1
    assert unknown_result.json()["items"][0]["run_id"] == unknown_history.id


@pytest.mark.anyio
async def test_admin_log_run_status_precedence_and_threshold_values(
    db_session,
    client,
):
    await create_user(db_session, "status-admin", role="admin")
    customer = await create_user(db_session, "status-owner", role="customer")
    base = datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)
    failed = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="after_sale",
        created_at=base,
        tool_name="after_sale",
        after_sale_context={"status": "query_error"},
        trace={
            "intent": "after_sale",
            "sources": [],
            "tool_evidence": {"provider_failure": {"category": "timeout"}},
            "handoff_reason": "threshold_exceeded",
        },
    )
    handoff = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="handoff",
        created_at=base + timedelta(minutes=1),
        tool_name="handoff",
        trace={
            "intent": "handoff",
            "sources": [],
            "tool_evidence": {},
            "handoff_reason": "用户请求人工服务",
        },
    )
    success = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="order",
        created_at=base + timedelta(minutes=2),
        tool_name="order",
        trace={
            "intent": "order",
            "sources": [],
            "tool_evidence": {"order": {"status": "已发货"}},
            "handoff_reason": None,
        },
    )
    unknown = await _seed_run(
        db_session,
        user_id=customer.id,
        intent=None,
        created_at=base + timedelta(minutes=3),
        trace={},
    )
    token = await _login(client, "status-admin")

    response = await client.get("/api/admin/logs", headers=_auth(token))

    assert response.status_code == 200
    items = {item["run_id"]: item for item in response.json()["items"]}
    assert items[failed.id]["run_status"] == "failed"
    assert items[failed.id]["threshold_amount"] is None
    assert items[failed.id]["has_handoff"] is True
    assert items[handoff.id]["run_status"] == "handoff"
    assert items[handoff.id]["handoff_reason"] == "user_request"
    assert items[success.id]["run_status"] == "success"
    assert items[unknown.id]["run_status"] == "unknown"
    assert items[unknown.id]["tool_name"] == "none"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"retrieval_result": "partial"},
        {"run_status": "running"},
        {
            "start_at": "2026-11-18T08:00:00Z",
            "end_at": "2026-11-18T07:00:00Z",
        },
    ],
)
async def test_admin_logs_reject_invalid_query_boundaries(
    db_session,
    client,
    params,
):
    await create_user(db_session, "invalid-logs-admin", role="admin")
    token = await _login(client, "invalid-logs-admin")

    response = await client.get(
        "/api/admin/logs",
        params=params,
        headers=_auth(token),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_admin_logs_use_latest_tool_call_without_duplicating_runs(
    db_session,
    client,
):
    await create_user(db_session, "latest-tool-admin", role="admin")
    customer = await create_user(
        db_session,
        "latest-tool-owner",
        role="customer",
    )
    run = await _seed_run(
        db_session,
        user_id=customer.id,
        intent="knowledge",
        created_at=datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc),
        tool_name="knowledge",
        additional_tool_names=("order",),
        trace={
            "intent": "knowledge",
            "sources": [],
            "tool_evidence": {"kb_type": "product"},
            "handoff_reason": None,
        },
    )
    token = await _login(client, "latest-tool-admin")

    latest = await client.get(
        "/api/admin/logs",
        params={"tool_name": "order"},
        headers=_auth(token),
    )
    older = await client.get(
        "/api/admin/logs",
        params={"tool_name": "knowledge"},
        headers=_auth(token),
    )

    assert latest.status_code == 200
    assert latest.json()["total"] == 1
    assert len(latest.json()["items"]) == 1
    assert latest.json()["items"][0]["run_id"] == run.id
    assert latest.json()["items"][0]["tool_name"] == "order"
    assert older.status_code == 200
    assert older.json()["total"] == 0
