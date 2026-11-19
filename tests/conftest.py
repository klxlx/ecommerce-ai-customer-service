"""Shared pytest fixtures (database-independent).

The test database URL is pinned before any application import so unit tests can
build the app without touching a real database. Integration fixtures that need
PostgreSQL live in ``tests/integration/conftest.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://cs_user:cs_password@localhost:5432/cs_test",
)

# Async psycopg requires the selector event loop on Windows, not the default
# proactor loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.db.session import create_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture()
def app(settings: Settings):
    factory = create_session_factory(settings.database_url)
    return create_app(settings=settings, session_factory=factory)


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"
