"""Async engine and session management.

The session factory is created by the application factory and stored on the app
state so tests can bind to a dedicated database without relying on a global
module-level engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Build an async session factory bound to ``database_url``."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session.

    The session factory is attached to the app state by ``create_app``.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
