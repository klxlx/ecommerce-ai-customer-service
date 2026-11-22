"""Integration coverage for the read-only order Function Calling pilot."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import AgentRun
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.main import create_app
from app.providers.chat import ChatToolCall, ChatToolResponse
from app.rag.retriever import Providers
from tests.fakes import FakeEmbeddings, FakeReranker
from tests.helpers import create_order, create_user


class OrderToolChat:
    """Scripted router + one function call + final answer."""

    def __init__(self, route: dict, plan: list[ChatToolResponse], answer: str):
        self.route = route
        self.plan = list(plan)
        self.answer = answer
        self.complete_calls = 0
        self.plan_calls = 0

    async def complete(self, messages, model=None):
        self.complete_calls += 1
        if self.complete_calls == 1:
            return json.dumps(self.route, ensure_ascii=False)
        return self.answer

    async def complete_with_tools(
        self, messages, tools, model=None, tool_choice="auto"
    ):
        self.plan_calls += 1
        if self.plan_calls <= len(self.plan):
            return self.plan[self.plan_calls - 1]
        return ChatToolResponse()


def _call(name: str, arguments: dict) -> ChatToolResponse:
    return ChatToolResponse(
        tool_calls=[
            ChatToolCall(
                id=f"call_{name}",
                name=name,
                arguments=arguments,
                raw_arguments=json.dumps(arguments, ensure_ascii=False),
            )
        ]
    )


def _settings():
    return get_settings().model_copy(
        update={
            "fc_readonly_enabled": True,
            "fc_max_steps": 2,
            "sentinel_enabled": False,
        }
    )


def _container(chat):
    settings = _settings()
    return Container(
        chat=chat,
        providers=Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
        settings=settings,
    )


async def _client(container):
    settings = container.settings
    app = create_app(
        settings=settings,
        session_factory=create_session_factory(settings.database_url),
    )
    app.dependency_overrides[get_container] = lambda: container
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


async def _login(client, username):
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _load_run(trace_id: str):
    factory = create_session_factory(_settings().database_url)
    async with factory() as session:
        return await session.get(AgentRun, trace_id)


@pytest.mark.anyio
async def test_order_list_uses_native_tool_call_and_records_evidence(db_session):
    user = await create_user(db_session, "fc-list-user")
    await create_order(
        db_session,
        user.id,
        "ORD-7001",
        status="已发货",
        items=[("测试商品A", 1, "99.00")],
    )
    await create_order(
        db_session,
        user.id,
        "ORD-7002",
        status="待发货",
        items=[("测试商品B", 2, "50.00")],
    )
    chat = OrderToolChat(
        route={
            "intent": "order",
            "slots": {"query_scope": "list"},
        },
        plan=[_call("order_list", {}), ChatToolResponse()],
        answer="亲，您当前共有 2 笔订单，分别是 ORD-7001 和 ORD-7002。",
    )
    container = _container(chat)
    async with await _client(container) as client:
        token = await _login(client, "fc-list-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "我的订单有哪些"},
            headers=_auth(token),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "order"
    assert "2 笔订单" in body["answer"]
    run = await _load_run(body["trace_id"])
    assert run is not None
    trace = run.trace
    assert trace["fc"]["fallback_reason"] is None
    assert trace["fc"]["tool_calls"][0]["name"] == "order_list"
    assert len(trace["tool_evidence"]["orders"]) == 2


@pytest.mark.anyio
async def test_order_detail_uses_native_tool_call(db_session):
    user = await create_user(db_session, "fc-detail-user")
    await create_order(
        db_session,
        user.id,
        "ORD-7101",
        status="运输中",
        items=[("测试商品C", 1, "120.00")],
        logistics=[
            {
                "status": "运输中",
                "location": "广州分拨中心",
                "description": "快件已发出",
            }
        ],
    )
    chat = OrderToolChat(
        route={
            "intent": "order",
            "slots": {"order_no": "ORD-7101"},
        },
        plan=[_call("order_detail", {"order_no": "ORD-7101"})],
        answer="您的订单 ORD-7101 当前状态为运输中，快件已从广州分拨中心发出。",
    )
    container = _container(chat)
    async with await _client(container) as client:
        token = await _login(client, "fc-detail-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "帮我查 ORD-7101"},
            headers=_auth(token),
        )
    assert response.status_code == 200
    body = response.json()
    assert "运输中" in body["answer"]
    run = await _load_run(body["trace_id"])
    trace = run.trace
    assert trace["fc"]["fallback_reason"] is None
    assert trace["tool_evidence"]["order"]["order_no"] == "ORD-7101"


@pytest.mark.anyio
async def test_planner_without_tool_call_falls_back_to_deterministic_order_node(
    db_session,
):
    user = await create_user(db_session, "fc-fallback-user")
    await create_order(
        db_session,
        user.id,
        "ORD-7201",
        status="已发货",
        items=[("测试商品D", 1, "88.00")],
    )
    chat = OrderToolChat(
        route={
            "intent": "order",
            "slots": {"order_no": "ORD-7201"},
        },
        plan=[ChatToolResponse(content="请提供订单号")],
        answer="不应被使用",
    )
    container = _container(chat)
    async with await _client(container) as client:
        token = await _login(client, "fc-fallback-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "查 ORD-7201"},
            headers=_auth(token),
        )
    body = response.json()
    assert "ORD-7201" in body["answer"]
    assert "不应被使用" not in body["answer"]
    run = await _load_run(body["trace_id"])
    assert run.trace["fc"]["fallback_reason"] == "no_tool_call"


@pytest.mark.anyio
async def test_unowned_order_is_not_enumerated_and_falls_back(db_session):
    owner = await create_user(db_session, "fc-owner")
    other = await create_user(db_session, "fc-other")
    await create_order(
        db_session,
        owner.id,
        "ORD-7301",
        status="已发货",
        items=[("测试商品E", 1, "60.00")],
    )
    chat = OrderToolChat(
        route={
            "intent": "order",
            "slots": {"order_no": "ORD-7301"},
        },
        plan=[_call("order_detail", {"order_no": "ORD-7301"})],
        answer="不应暴露",
    )
    container = _container(chat)
    async with await _client(container) as client:
        token = await _login(client, "fc-other")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "查 ORD-7301"},
            headers=_auth(token),
        )
    body = response.json()
    assert "未查询到该订单" in body["answer"]
    assert "ORD-7301" not in body["answer"]
    run = await _load_run(body["trace_id"])
    assert run.trace["fc"]["fallback_reason"] == "not_found"
