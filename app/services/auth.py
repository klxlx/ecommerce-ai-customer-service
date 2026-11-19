"""Authentication service: login, logout, and session creation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_session_token,
    hash_session_token,
    verify_password,
)
from app.db.models import User
from app.db.repositories import auth as auth_repo


async def authenticate(
    session: AsyncSession, username: str, password: str
) -> User | None:
    """Return the matching active user, or None on any failure."""
    user = await auth_repo.get_user_by_username(session, username)
    if user is None:
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def login(
    session: AsyncSession, username: str, password: str
) -> tuple[str, User] | None:
    """Authenticate and create an opaque session, returning (token, user)."""
    user = await authenticate(session, username, password)
    if user is None:
        return None
    token = create_session_token()
    ttl = get_settings().session_ttl_hours
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl)
    await auth_repo.create_session(
        session, user.id, hash_session_token(token), expires_at
    )
    await session.commit()
    return token, user


async def logout(session: AsyncSession, token_hash: str) -> None:
    await auth_repo.delete_session(session, token_hash)
    await session.commit()
