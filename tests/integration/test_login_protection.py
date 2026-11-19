"""Login failure-window and temporary-lock integration tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.session import create_session_factory
from app.main import create_app
from tests.helpers import create_user


def _build_client():
    settings = get_settings().model_copy(
        update={
            "login_failure_limit": 3,
            "login_failure_window_seconds": 60,
            "login_lock_seconds": 45,
        }
    )
    app = create_app(
        settings=settings,
        session_factory=create_session_factory(settings.database_url),
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client, username, password):
    return await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


@pytest.mark.anyio
async def test_repeated_failures_return_stable_locked_response(db_session):
    await create_user(db_session, "alice", "pw123")
    async with _build_client() as client:
        first = await _login(client, "alice", "wrong")
        second = await _login(client, "alice", "wrong")
        locked = await _login(client, "alice", "wrong")
        still_locked = await _login(client, "alice", "pw123")

    assert first.status_code == 401
    assert second.status_code == 401
    assert locked.status_code == 429
    assert locked.headers["X-Error-Code"] == "AUTH_TEMPORARILY_LOCKED"
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.json() == still_locked.json()
    assert "密码" not in locked.json()["detail"]


@pytest.mark.anyio
async def test_missing_wrong_and_inactive_users_share_failure_response(db_session):
    await create_user(db_session, "alice", "pw123")
    await create_user(db_session, "inactive", "pw123", active=False)
    async with _build_client() as client:
        wrong = await _login(client, "alice", "wrong")
        missing = await _login(client, "ghost", "wrong")
        inactive = await _login(client, "inactive", "pw123")

    assert wrong.status_code == missing.status_code == inactive.status_code == 401
    assert wrong.json() == missing.json() == inactive.json()
    assert (
        wrong.headers["X-Error-Code"]
        == missing.headers["X-Error-Code"]
        == inactive.headers["X-Error-Code"]
        == "AUTH_INVALID_CREDENTIALS"
    )


@pytest.mark.anyio
async def test_successful_login_clears_previous_failures(db_session):
    await create_user(db_session, "alice", "pw123")
    async with _build_client() as client:
        assert (await _login(client, "alice", "wrong")).status_code == 401
        assert (await _login(client, "alice", "wrong")).status_code == 401
        assert (await _login(client, "alice", "pw123")).status_code == 200
        after_success_1 = await _login(client, "alice", "wrong")
        after_success_2 = await _login(client, "alice", "wrong")

    assert after_success_1.status_code == 401
    assert after_success_2.status_code == 401
    assert after_success_2.headers["X-Error-Code"] == "AUTH_INVALID_CREDENTIALS"

