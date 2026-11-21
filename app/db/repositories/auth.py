"""Repository for users and auth sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthSession, User


async def get_user_by_username(
    session: AsyncSession, username: str
) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_users_by_ids(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, User]:
    if not user_ids:
        return {}
    result = await session.execute(
        select(User).where(User.id.in_(user_ids))
    )
    return {user.id: user for user in result.scalars().all()}


async def create_session(
    session: AsyncSession, user_id: int, token_hash: str, expires_at: datetime
) -> AuthSession:
    auth_session = AuthSession(
        user_id=user_id, token_hash=token_hash, expires_at=expires_at
    )
    session.add(auth_session)
    await session.flush()
    return auth_session


async def get_session_by_token_hash(
    session: AsyncSession, token_hash: str
) -> AuthSession | None:
    result = await session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def delete_session(session: AsyncSession, token_hash: str) -> bool:
    auth_session = await get_session_by_token_hash(session, token_hash)
    if auth_session is None:
        return False
    await session.delete(auth_session)
    await session.flush()
    return True
