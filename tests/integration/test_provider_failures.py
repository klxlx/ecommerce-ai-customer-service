"""Provider failure mapping and safe chat degradation."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.core.errors import ProviderAuthenticationError
from app.db.models import AgentRun, HandoffTicket
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.main import create_app
from app.rag.retriever import Providers
from tests.fakes import FakeEmbeddings, FakeReranker
from tests.helpers import create_user


class FailingChat:
    async def complete(self, messages):
        raise ProviderAuthenticationError("chat", "invalid api key")


def _build_client():
    settings = get_settings()
    app = create_app(
        settings=settings,
        session_factory=create_session_factory(settings.database_url),
    )
    container = Container(
        chat=FailingChat(),
        providers=Providers(embeddings=FakeEmbeddings(), rerank=FakeReranker()),
        settings=settings,
    )
    app.dependency_overrides[get_container] = lambda: container
    return app, AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.anyio
async def test_provider_failure_returns_safe_answer_and_creates_ticket(db_session):
    await create_user(db_session, "alice")
    app, client = _build_client()
    async with client:
        token = (
            await client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "password123"},
            )
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        conversation = (
            await client.post("/api/conversations", json={}, headers=headers)
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "查询订单状态"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "handoff"
    assert body["handoff"]["status"] == "open"
    assert "人工客服" in body["answer"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert "invalid api key" not in serialized
    assert "ProviderAuthenticationError" not in serialized

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        tickets = (await session.execute(select(HandoffTicket))).scalars().all()
        runs = (await session.execute(select(AgentRun))).scalars().all()
        assert len(tickets) == 1
        assert len(runs) == 1
        assert runs[0].trace["tool_evidence"]["provider_failure"] == {
            "provider": "chat",
            "category": "authentication",
            "retryable": False,
        }

    snapshot = app.state.metrics.snapshot()
    assert snapshot["counters"]["chat.failure.total"]["value"] == 1
    assert snapshot["counters"]["provider.degraded.total"][
        "provider=chat,reason=authentication"
    ] == 1

