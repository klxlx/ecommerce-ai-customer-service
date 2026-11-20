"""Shared PostgreSQL fixtures for integration and end-to-end tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import create_session_factory

TABLES = [
    "tool_call_logs",
    "agent_runs",
    "return_analyses",
    "semantic_cache",
    "handoff_tickets",
    "chat_messages",
    "logistics_events",
    "order_items",
    "after_sales",
    "orders",
    "auth_sessions",
    "conversations",
    "products",
    "knowledge_ingestion_jobs",
    "knowledge_document_versions",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_parents",
    "knowledge_sources",
    "users",
]


def _admin_url(database_url: str) -> URL:
    return make_url(database_url).set(database="postgres")


def _ensure_test_database(database_url: str) -> None:
    target = make_url(database_url)
    if not target.database:
        raise ValueError("DATABASE_URL must include a database name")

    engine = sa.create_engine(
        _admin_url(database_url),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target.database},
        ).scalar()
        if not exists:
            quoted_name = target.database.replace('"', '""')
            conn.execute(sa.text(f'CREATE DATABASE "{quoted_name}"'))
    engine.dispose()


def _reset_schema(database_url: str) -> None:
    engine = sa.create_engine(
        database_url,
        connect_args={"connect_timeout": 5},
    )
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.commit()
    engine.dispose()

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


async def _truncate_database(database_url: str) -> None:
    engine = create_async_engine(
        database_url,
        connect_args={"connect_timeout": 5},
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )
    await engine.dispose()


@pytest.fixture(scope="session")
def _database(settings) -> None:
    _ensure_test_database(settings.database_url)
    _reset_schema(settings.database_url)
    yield


@pytest.fixture()
async def _truncate_tables():
    await _truncate_database(get_settings().database_url)


@pytest.fixture()
async def db_session(settings, _database) -> AsyncIterator:
    factory = create_session_factory(settings.database_url)
    try:
        async with factory() as session:
            yield session
    finally:
        await _truncate_database(settings.database_url)
