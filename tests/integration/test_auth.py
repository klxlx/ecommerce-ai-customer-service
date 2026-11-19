"""Authentication, role, and ownership integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import hash_session_token
from app.db.models import AuthSession, User
from tests.helpers import create_user


async def _login(client, username: str, password: str):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.mark.anyio
async def test_login_returns_token_and_role(db_session, client):
    await create_user(db_session, "alice", "pw123", role="customer")
    resp = await _login(client, "alice", "pw123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "customer"


@pytest.mark.anyio
async def test_login_wrong_password_and_missing_user_are_identical(db_session, client):
    await create_user(db_session, "alice", "pw123")
    wrong = await _login(client, "alice", "nope")
    missing = await _login(client, "ghost", "whatever")
    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert wrong.json() == missing.json()


@pytest.mark.anyio
async def test_login_inactive_user_rejected(db_session, client):
    await create_user(db_session, "alice", "pw123", active=False)
    resp = await _login(client, "alice", "pw123")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_me_requires_valid_token(db_session, client):
    await create_user(db_session, "alice", "pw123")
    token = (await _login(client, "alice", "pw123")).json()["token"]
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"

    no_auth = await client.get("/api/auth/me")
    assert no_auth.status_code == 401


@pytest.mark.anyio
async def test_logout_revokes_token(db_session, client):
    await create_user(db_session, "alice", "pw123")
    token = (await _login(client, "alice", "pw123")).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.post("/api/auth/logout", headers=headers)
    assert resp.status_code == 204
    after = await client.get("/api/auth/me", headers=headers)
    assert after.status_code == 401


@pytest.mark.anyio
async def test_expired_token_rejected(db_session, client):
    user = await create_user(db_session, "alice", "pw123")
    expired = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token("expired-token-value"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(expired)
    await db_session.commit()
    resp = await client.get(
        "/api/auth/me", headers={"Authorization": "Bearer expired-token-value"}
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_raw_token_and_password_not_stored(db_session, client):
    await create_user(db_session, "alice", "pw123")
    token = (await _login(client, "alice", "pw123")).json()["token"]
    sessions = (await db_session.execute(select(AuthSession))).scalars().all()
    assert len(sessions) == 1
    assert token not in sessions[0].token_hash
    assert len(sessions[0].token_hash) == 64

    users = (await db_session.execute(select(User))).scalars().all()
    assert "pw123" not in users[0].password_hash
