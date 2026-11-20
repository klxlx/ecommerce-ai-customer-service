"""End-to-end demo flow using mock providers against a seeded database."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import HandoffTicket
from app.db.session import create_session_factory
from app.graph.container import Container
from app.main import create_app
from app.providers.mock import MockChat, MockEmbeddings, MockReranker
from app.rag.retriever import Providers
from scripts import seed_demo


async def _build_client() -> AsyncClient:
    settings = get_settings()
    container = Container(
        chat=MockChat(),
        providers=Providers(embeddings=MockEmbeddings(), rerank=MockReranker()),
        settings=settings,
    )
    app = create_app(
        settings=settings,
        session_factory=create_session_factory(settings.database_url),
    )
    app.dependency_overrides[get_container] = lambda: container
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client, username, password="password123"):
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def seeded(db_session):
    users = await seed_demo._seed_users(db_session)
    products = await seed_demo._seed_products(db_session)
    await seed_demo._seed_orders(db_session, users, products)
    yield


@pytest.mark.anyio
async def test_demo_credentials_and_greeting(seeded, db_session):
    async with await _build_client() as client:
        token = await _login(client, "customer")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert "购物客服" in resp.json()["answer"]


@pytest.mark.anyio
async def test_demo_order_query(seeded, db_session):
    async with await _build_client() as client:
        token = await _login(client, "customer")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "查询订单 ORD-20261117-0001 的物流"},
            headers=_auth(token),
        )
        body = resp.json()
        assert resp.status_code == 200
        assert "运输中" in body["answer"]


@pytest.mark.anyio
async def test_demo_handoff_creates_ticket(seeded, db_session):
    async with await _build_client() as client:
        token = await _login(client, "customer")
        conv = (
            await client.post("/api/conversations", json={}, headers=_auth(token))
        ).json()
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "转人工"},
            headers=_auth(token),
        )
        assert resp.json()["handoff"] is not None

        admin_token = await _login(client, "admin")
        tickets = await client.get(
            "/api/admin/handoffs", headers=_auth(admin_token)
        )
        assert tickets.status_code == 200
        assert len(tickets.json()) >= 1
