"""Schema migration tests: the initial migration creates every required table."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import create_session_factory

REQUIRED_TABLES = {
    "users",
    "auth_sessions",
    "conversations",
    "chat_messages",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_ingestion_jobs",
    "knowledge_sources",
    "knowledge_parents",
    "knowledge_chunks",
    "products",
    "orders",
    "order_items",
    "after_sales",
    "logistics_events",
    "agent_runs",
    "tool_call_logs",
    "handoff_tickets",
}


def _alembic_config() -> AlembicConfig:
    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def _reset_schema_to_head() -> None:
    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    command.upgrade(_alembic_config(), "head")


async def _table_columns(table_name: str) -> set[str]:
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table_name"
            ),
            {"table_name": table_name},
        )
        return {row[0] for row in rows.fetchall()}


async def _table_indexes(table_name: str) -> set[tuple[str, ...]]:
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :table_name"
            ),
            {"table_name": table_name},
        )
        return {
            (row.indexname, row.indexdef)
            for row in rows.fetchall()
        }


@pytest.mark.anyio
async def test_initial_migration_creates_all_tables():
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        existing = {r[0] for r in rows.fetchall()}
    missing = REQUIRED_TABLES - existing
    assert not missing, f"missing tables: {missing}"


@pytest.mark.anyio
async def test_knowledge_chunks_parent_id_is_non_nullable():
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        row = await session.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'knowledge_chunks' AND column_name = 'parent_id'"
            )
        )
        is_nullable = row.scalar()
    assert is_nullable == "NO"


@pytest.mark.anyio
async def test_embedding_column_is_vector_1024():
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        row = await session.execute(
            text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding'"
            )
        )
        udt = row.scalar()
    assert udt == "vector"


@pytest.mark.anyio
async def test_versioned_knowledge_schema_is_traceable_and_guarded():
    document_columns = await _table_columns("knowledge_documents")
    assert {
        "id",
        "source_key",
        "title",
        "kb_type",
        "source_uri",
        "applicability",
        "effective_date",
        "current_version_id",
        "source_kind",
        "original_filename",
        "deleted_at",
        "created_at",
        "updated_at",
    } <= document_columns

    version_columns = await _table_columns("knowledge_document_versions")
    assert {
        "id",
        "document_id",
        "version_number",
        "checksum",
        "chunk_size",
        "overlap",
        "status",
        "pdf_file",
        "page_count",
        "extraction_strategy",
        "embedding_model",
        "parent_count",
        "chunk_count",
        "failure_reason",
        "published_at",
        "created_at",
        "updated_at",
    } <= version_columns

    job_columns = await _table_columns("knowledge_ingestion_jobs")
    assert {
        "id",
        "document_id",
        "document_version_id",
        "status",
        "parent_count",
        "chunk_count",
        "failure_reason",
        "started_at",
        "finished_at",
        "created_at",
    } <= job_columns

    source_columns = await _table_columns("knowledge_sources")
    assert "document_id" in source_columns
    parent_columns = await _table_columns("knowledge_parents")
    assert {
        "document_version_id",
        "page_start",
        "page_end",
        "section_path",
    } <= parent_columns
    chunk_columns = await _table_columns("knowledge_chunks")
    assert {
        "document_version_id",
        "page_start",
        "page_end",
        "section_path",
    } <= chunk_columns

    version_indexes = await _table_indexes("knowledge_document_versions")
    assert any(
        name == "uq_knowledge_document_versions_one_published"
        and "UNIQUE INDEX" in definition
        and "(document_id)" in definition
        and "published" in definition
        for name, definition in version_indexes
    )


@pytest.mark.anyio
async def test_after_sales_uses_order_ownership_columns():
    columns = await _table_columns("after_sales")
    assert {
        "id",
        "order_id",
        "service_type",
        "status",
        "refund_amount",
        "reason",
        "created_at",
        "updated_at",
    } <= columns
    assert "user_id" not in columns


@pytest.mark.anyio
async def test_chat_messages_store_nullable_after_sale_snapshot():
    columns = await _table_columns("chat_messages")
    assert "after_sale_context" in columns

    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        row = await session.execute(
            text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name = 'chat_messages' "
                "AND column_name = 'after_sale_context'"
            )
        )
        is_nullable, data_type = row.one()
    assert is_nullable == "YES"
    assert data_type == "jsonb"


@pytest.mark.anyio
async def test_soft_delete_columns_are_nullable_timestamps():
    factory = create_session_factory(get_settings().database_url)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, is_nullable, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND column_name = 'deleted_at' "
                    "AND table_name IN ('conversations', 'knowledge_documents')"
                )
            )
        ).fetchall()
    assert {row.table_name for row in rows} == {
        "conversations",
        "knowledge_documents",
    }
    assert all(row.is_nullable == "YES" for row in rows)
    assert all(row.data_type == "timestamp with time zone" for row in rows)


@pytest.mark.anyio
async def test_upgrade_0006_backfills_knowledge_file_origins():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.downgrade(config, "0005")
        engine = sa.create_engine(get_settings().database_url)
        with engine.begin() as connection:
            manual_document_id = connection.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(source_key, title, kb_type, source_uri, applicability) "
                    "VALUES ('legacy-manual', '旧手工知识', 'policy', "
                    "'manual://legacy-manual', '') RETURNING id"
                )
            ).scalar_one()
            manual_version_id = connection.execute(
                text(
                    "INSERT INTO knowledge_document_versions "
                    "(document_id, version_number, checksum, chunk_size, "
                    "overlap, status, pdf_file, page_count, "
                    "extraction_strategy, embedding_model, parent_count, "
                    "chunk_count, failure_reason) "
                    "VALUES (:document_id, 1, :checksum, 400, 80, 'published', "
                    "'manual://legacy-manual', 0, 'manual', 'test-1024', 0, 0, '') "
                    "RETURNING id"
                ),
                {
                    "document_id": manual_document_id,
                    "checksum": "b" * 64,
                },
            ).scalar_one()
            connection.execute(
                text(
                    "UPDATE knowledge_documents SET current_version_id = :version_id "
                    "WHERE id = :document_id"
                ),
                {
                    "version_id": manual_version_id,
                    "document_id": manual_document_id,
                },
            )

            pdf_document_id = connection.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(source_key, title, kb_type, source_uri, applicability) "
                    "VALUES ('legacy-pdf', '旧 PDF 资料', 'product', "
                    "'official-guide.pdf', '') RETURNING id"
                )
            ).scalar_one()
            pdf_version_id = connection.execute(
                text(
                    "INSERT INTO knowledge_document_versions "
                    "(document_id, version_number, checksum, chunk_size, "
                    "overlap, status, pdf_file, page_count, "
                    "extraction_strategy, embedding_model, parent_count, "
                    "chunk_count, failure_reason) "
                    "VALUES (:document_id, 1, :checksum, 400, 80, 'published', "
                    "'official-guide.pdf', 10, 'jd_clauses', 'test-1024', "
                    "0, 0, '') "
                    "RETURNING id"
                ),
                {
                    "document_id": pdf_document_id,
                    "checksum": "c" * 64,
                },
            ).scalar_one()
            connection.execute(
                text(
                    "UPDATE knowledge_documents SET current_version_id = :version_id "
                    "WHERE id = :document_id"
                ),
                {
                    "version_id": pdf_version_id,
                    "document_id": pdf_document_id,
                },
            )
        engine.dispose()

        command.upgrade(config, "head")

        factory = create_session_factory(get_settings().database_url)
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT source_key, source_kind, original_filename "
                        "FROM knowledge_documents ORDER BY source_key"
                    )
                )
            ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("legacy-manual", "manual", ""),
            ("legacy-pdf", "pdf", "official-guide.pdf"),
        ]
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_empty_schema_upgrades_to_head_and_creates_after_sales_index():
    _reset_schema_to_head()
    try:
        indexes = await _table_indexes("after_sales")
        assert any(
            name == "ix_after_sales_order_id" and "(order_id)" in definition
            for name, definition in indexes
        )
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_upgrade_to_0002_skips_after_sales_created_by_0001():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.downgrade(config, "base")
        command.upgrade(config, "0001")
        assert {
            "id",
            "order_id",
            "service_type",
            "status",
            "refund_amount",
            "reason",
            "created_at",
            "updated_at",
        } <= await _table_columns("after_sales")

        command.upgrade(config, "head")
        assert "order_id" in await _table_columns("after_sales")
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_upgrade_to_0002_recreates_table_deleted_at_0001():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.downgrade(config, "base")
        command.upgrade(config, "0001")
        engine = sa.create_engine(get_settings().database_url)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE after_sales CASCADE"))
        engine.dispose()

        command.upgrade(config, "head")
        assert "order_id" in await _table_columns("after_sales")
        indexes = await _table_indexes("after_sales")
        assert any(name == "ix_after_sales_order_id" for name, _ in indexes)
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_repeated_upgrade_to_head_is_idempotent():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert "order_id" in await _table_columns("after_sales")
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_upgrade_0004_adds_versioning_to_legacy_knowledge_schema():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.downgrade(config, "base")
        command.upgrade(config, "0001")
        engine = sa.create_engine(get_settings().database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TABLE IF EXISTS knowledge_ingestion_jobs, "
                    "knowledge_document_versions, knowledge_documents CASCADE"
                )
            )
            for table_name, columns in {
                "knowledge_sources": ("document_id",),
                "knowledge_parents": (
                    "document_version_id",
                    "page_start",
                    "page_end",
                    "section_path",
                ),
                "knowledge_chunks": (
                    "document_version_id",
                    "page_start",
                    "page_end",
                    "section_path",
                ),
            }.items():
                for column in columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"DROP COLUMN IF EXISTS {column} CASCADE"
                        )
                    )
        engine.dispose()

        command.upgrade(config, "head")
        command.upgrade(config, "head")

        assert {
            "knowledge_documents",
            "knowledge_document_versions",
            "knowledge_ingestion_jobs",
        } <= REQUIRED_TABLES
        assert "document_version_id" in await _table_columns("knowledge_parents")
        assert "document_version_id" in await _table_columns("knowledge_chunks")
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_upgrade_0004_rejects_incomplete_document_table():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.downgrade(config, "0003")
        engine = sa.create_engine(get_settings().database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE knowledge_documents ("
                    "id INTEGER PRIMARY KEY, "
                    "title VARCHAR(200) NOT NULL)"
                )
            )
        engine.dispose()

        with pytest.raises(
            RuntimeError,
            match="knowledge_documents exists but is missing required columns",
        ):
            command.upgrade(config, "head")
    finally:
        _reset_schema_to_head()


@pytest.mark.anyio
async def test_upgrade_to_0002_rejects_existing_incomplete_table():
    _reset_schema_to_head()
    try:
        config = _alembic_config()
        command.downgrade(config, "base")
        command.upgrade(config, "0001")
        engine = sa.create_engine(get_settings().database_url)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE after_sales CASCADE"))
            connection.execute(
                text(
                    "CREATE TABLE after_sales ("
                    "id INTEGER PRIMARY KEY, "
                    "order_id INTEGER NOT NULL)"
                )
            )
        engine.dispose()

        with pytest.raises(
            RuntimeError,
            match="after_sales exists but is missing required columns",
        ):
            command.upgrade(config, "head")
    finally:
        _reset_schema_to_head()
