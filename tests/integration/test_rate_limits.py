"""Chat and admin API rate-limit integration tests."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.main import create_app
from app.rag.retriever import Providers
from tests.fakes import FakeEmbeddings, FakeReranker
from tests.helpers import create_user


class JsonChat:
    async def complete(self, messages):
        return json.dumps({"intent": "chit_chat", "slots": {}})


def _container():
    return Container(
        chat=JsonChat(),
        providers=Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
        settings=get_settings(),
    )


def _build_client(**overrides):
    settings = get_settings().model_copy(update=overrides)
    app = create_app(
        settings=settings,
        session_factory=create_session_factory(settings.database_url),
    )
    container = _container()
    app.dependency_overrides[get_container] = lambda: container
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username):
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["token"]


@pytest.mark.anyio
async def test_chat_rate_limit_is_per_authenticated_user(db_session):
    await create_user(db_session, "alice")
    async with _build_client(
        chat_rate_limit_requests=1,
        chat_rate_limit_window_seconds=60,
    ) as client:
        token = await _login(client, "alice")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        first = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )
        limited = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "再问一次"},
            headers=_auth(token),
        )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["X-Error-Code"] == "CHAT_RATE_LIMITED"
    assert limited.headers["Retry-After"] == "60"
    assert "过于频繁" in limited.json()["detail"]


@pytest.mark.anyio
async def test_admin_bucket_is_independent_from_chat_bucket(db_session):
    await create_user(db_session, "admin1", role="admin")
    async with _build_client(
        chat_rate_limit_requests=1,
        chat_rate_limit_window_seconds=60,
        admin_rate_limit_requests=1,
        admin_rate_limit_window_seconds=60,
    ) as client:
        token = await _login(client, "admin1")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        chat = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "你好"},
            headers=_auth(token),
        )
        first_admin = await client.get(
            "/api/admin/handoffs", headers=_auth(token)
        )
        limited_admin = await client.get(
            "/api/admin/handoffs", headers=_auth(token)
        )

    assert chat.status_code == 200
    assert first_admin.status_code == 200
    assert limited_admin.status_code == 429
    assert limited_admin.headers["X-Error-Code"] == "ADMIN_RATE_LIMITED"
    assert limited_admin.headers["Retry-After"] == "60"

