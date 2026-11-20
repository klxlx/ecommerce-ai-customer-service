"""Integration tests for the curated pgvector FAQ cache."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import SemanticCache
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.main import create_app
from app.rag.retriever import Providers
from app.services import semantic_cache as cache_service
from tests.fakes import FakeEmbeddings, FakeReranker
from tests.helpers import create_user


class CountingChat:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, model=None):
        self.calls += 1
        return ""


async def _login(client, username):
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_cache_hit_skips_graph_and_returns_curated_answer(db_session):
    user = await create_user(db_session, "cache-hit-user")
    embeddings = FakeEmbeddings()
    entry = {
        "question": "七天无理由退货规则是什么？",
        "answer": "请在订单详情页查看最新退换货规则，以页面展示为准。",
        "kb_type": "policy",
        "sources": [
            {
                "title": "退换货规则",
                "source_uri": "",
                "kb_type": "policy",
                "parent_id": 0,
                "chunk_id": 0,
                "score": 0.0,
            }
        ],
    }
    imported = await cache_service.import_entries(
        db_session, [entry], embeddings
    )
    await db_session.commit()
    assert imported == 1

    chat = CountingChat()
    container = Container(
        chat=chat,
        providers=Providers(embeddings=embeddings, rerank=FakeReranker()),
        settings=get_settings(),
    )
    app = create_app(
        settings=get_settings(),
        session_factory=create_session_factory(get_settings().database_url),
    )
    app.dependency_overrides[get_container] = lambda: container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "cache-hit-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        response = await client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "七天无理由退货规则是什么？"},
            headers=_auth(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cached"] is True
        assert body["answer"] == entry["answer"]
        assert body["intent"] == "policy_knowledge"

    assert chat.calls == 0
    cache = (
        await db_session.execute(
            select(SemanticCache).where(SemanticCache.id == 1)
        )
    ).scalar_one()
    assert cache.hit_count == 1


@pytest.mark.anyio
async def test_dynamic_order_question_never_uses_cache(db_session):
    user = await create_user(db_session, "cache-dynamic-user")
    embeddings = FakeEmbeddings()
    await cache_service.import_entries(
        db_session,
        [
            {
                "question": "退款进度怎么查？",
                "answer": "请在订单详情页查看。",
                "kb_type": "policy",
                "sources": [],
            }
        ],
        embeddings,
    )
    await db_session.commit()

    hit = await cache_service.lookup(
        db_session,
        "我的订单退款进度到哪了？",
        embeddings,
        get_settings(),
    )
    assert hit is None


@pytest.mark.anyio
async def test_cache_entry_is_cleared_on_knowledge_change(db_session):
    embeddings = FakeEmbeddings()
    await cache_service.import_entries(
        db_session,
        [
            {
                "question": "保修政策是什么？",
                "answer": "以商品页面为准。",
                "kb_type": "policy",
                "sources": [],
            }
        ],
        embeddings,
    )
    await db_session.commit()
    removed = await cache_service.clear(db_session)
    await db_session.commit()
    assert removed == 1
    assert await cache_service.count(db_session) == 0
