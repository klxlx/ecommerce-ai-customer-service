"""Health endpoint tests (database mocked via dependency override)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_session


class _FakeSession:
    async def execute(self, _stmt):
        return None


async def _ok_session_override():
    yield _FakeSession()


class _BrokenSession(_FakeSession):
    async def execute(self, _stmt):
        raise RuntimeError("db down")


async def _broken_session_override():
    yield _BrokenSession()


async def _get(app, path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_health_reports_ready(app):
    app.dependency_overrides[get_session] = _ok_session_override
    resp = await _get(app, "/health")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert set(body["models"]) == {"chat", "embedding"}


@pytest.mark.anyio
async def test_health_reports_db_unavailable(app):
    app.dependency_overrides[get_session] = _broken_session_override
    resp = await _get(app, "/health")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["database"] == "unavailable"
    assert resp.json()["status"] == "degraded"
