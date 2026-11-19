"""Add versioned, traceable knowledge documents.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18

The first migration uses the current SQLAlchemy metadata, so it may create the
new tables and columns directly. This migration therefore has two jobs:

* upgrade an older 0003 database that still has the legacy source/parent/chunk
  schema;
* safely accept a database whose 0001 already created the complete new schema.

Existing legacy rows are deliberately left in place. Version-aware retrieval
ignores rows without a published document version.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

DOCUMENTS = "knowledge_documents"
VERSIONS = "knowledge_document_versions"
JOBS = "knowledge_ingestion_jobs"
SOURCES = "knowledge_sources"
PARENTS = "knowledge_parents"
CHUNKS = "knowledge_chunks"

DOCUMENT_COLUMNS = frozenset(
    {
        "id",
        "source_key",
        "title",
        "kb_type",
        "source_uri",
        "applicability",
        "effective_date",
        "current_version_id",
        "created_at",
        "updated_at",
    }
)
VERSION_COLUMNS = frozenset(
    {
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
    }
)
JOB_COLUMNS = frozenset(
    {
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
    }
)
SOURCE_COLUMNS = frozenset(
    {"id", "name", "source_uri", "kb_type", "checksum", "created_at", "updated_at"}
)
PARENT_COLUMNS = frozenset(
    {
        "id",
        "source_id",
        "parent_index",
        "content",
        "meta",
        "created_at",
        "updated_at",
    }
)
CHUNK_COLUMNS = frozenset(
    {
        "id",
        "parent_id",
        "chunk_index",
        "content",
        "embedding",
        "created_at",
        "updated_at",
    }
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table)}


def _foreign_keys(table: str) -> list[dict[str, object]]:
    return _inspector().get_foreign_keys(table)


def _foreign_key_matches(
    foreign_key: dict[str, object],
    local_columns: list[str],
    remote_table: str,
    remote_columns: list[str],
) -> bool:
    return (
        list(foreign_key.get("constrained_columns") or []) == list(local_columns)
        and foreign_key.get("referred_table") == remote_table
        and list(foreign_key.get("referred_columns") or []) == list(remote_columns)
    )


def _has_foreign_key(
    table: str,
    local_columns: list[str],
    remote_table: str,
    remote_columns: list[str],
) -> bool:
    return any(
        _foreign_key_matches(
            foreign_key,
            local_columns,
            remote_table,
            remote_columns,
        )
        for foreign_key in _foreign_keys(table)
    )


def _has_unique_constraint(table: str, name: str) -> bool:
    return any(
        constraint.get("name") == name
        for constraint in _inspector().get_unique_constraints(table)
    )


def _has_index(table: str, name: str) -> bool:
    return name in _index_names(table)


def _add_column_if_missing(table: str, name: str, column: sa.Column) -> None:
    if name not in _columns(table):
        op.add_column(table, column)


def _add_fk_if_missing(
    table: str,
    name: str,
    local_columns: list[str],
    remote_table: str,
    remote_columns: list[str],
    *,
    ondelete: str,
) -> None:
    if not _has_foreign_key(
        table,
        local_columns,
        remote_table,
        remote_columns,
    ):
        op.create_foreign_key(
            name,
            table,
            remote_table,
            local_columns,
            remote_columns,
            ondelete=ondelete,
        )


def _drop_index_if_exists(table: str, name: str) -> None:
    if name in _index_names(table):
        op.drop_index(name, table_name=table)


def _ensure_one_published_index() -> None:
    name = "uq_knowledge_document_versions_one_published"
    # Keep the predicate explicit so both the ORM-created schema and the
    # legacy-schema upgrade converge on the same PostgreSQL definition.
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{name}"'))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX "
            "uq_knowledge_document_versions_one_published "
            "ON knowledge_document_versions (document_id) "
            "WHERE (status = 'published')"
        )
    )


def _add_index_if_missing(
    table: str,
    name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if not _has_index(table, name):
        op.create_index(name, table, columns, unique=unique)


def _require_columns(table: str, required: frozenset[str]) -> None:
    missing = required - _columns(table)
    if missing:
        raise RuntimeError(
            f"{table} exists but is missing required columns: {sorted(missing)}"
        )


def _create_documents() -> None:
    op.create_table(
        DOCUMENTS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("kb_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=500), nullable=False),
        sa.Column("applicability", sa.Text(), server_default="", nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_documents"),
        sa.UniqueConstraint(
            "source_key",
            name="uq_knowledge_documents_source_key",
        ),
    )
    op.create_index("ix_knowledge_documents_kb_type", DOCUMENTS, ["kb_type"])


def _create_versions() -> None:
    op.create_table(
        VERSIONS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("overlap", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("pdf_file", sa.String(length=500), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("extraction_strategy", sa.String(length=32), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("parent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            [f"{DOCUMENTS}.id"],
            name="fk_knowledge_document_versions_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_document_versions"),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_knowledge_document_versions_document_number",
        ),
        sa.UniqueConstraint(
            "document_id",
            "checksum",
            "chunk_size",
            "overlap",
            "embedding_model",
            name="uq_knowledge_document_versions_config",
        ),
    )
    op.create_index(
        "ix_knowledge_document_versions_document_id",
        VERSIONS,
        ["document_id"],
    )
    op.create_index("ix_knowledge_document_versions_status", VERSIONS, ["status"])


def _create_jobs() -> None:
    op.create_table(
        JOBS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="running",
            nullable=False,
        ),
        sa.Column("parent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_reason", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            [f"{DOCUMENTS}.id"],
            name="fk_knowledge_ingestion_jobs_document_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            [f"{VERSIONS}.id"],
            name="fk_knowledge_ingestion_jobs_document_version_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_ingestion_jobs"),
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_document_id",
        JOBS,
        ["document_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_document_version_id",
        JOBS,
        ["document_version_id"],
    )
    op.create_index("ix_knowledge_ingestion_jobs_status", JOBS, ["status"])


def _ensure_version_indexes() -> None:
    _add_index_if_missing(
        VERSIONS,
        "ix_knowledge_document_versions_document_id",
        ["document_id"],
    )
    _add_index_if_missing(
        VERSIONS,
        "ix_knowledge_document_versions_status",
        ["status"],
    )
    _ensure_one_published_index()


def _ensure_document_constraints() -> None:
    if not _has_unique_constraint(
        DOCUMENTS, "uq_knowledge_documents_source_key"
    ):
        op.create_unique_constraint(
            "uq_knowledge_documents_source_key",
            DOCUMENTS,
            ["source_key"],
        )
    _add_index_if_missing(
        DOCUMENTS,
        "ix_knowledge_documents_kb_type",
        ["kb_type"],
    )
    _add_fk_if_missing(
        DOCUMENTS,
        "fk_knowledge_documents_current_version_id",
        ["current_version_id"],
        VERSIONS,
        ["id"],
        ondelete="SET NULL",
    )


def _ensure_job_indexes() -> None:
    _add_index_if_missing(
        JOBS,
        "ix_knowledge_ingestion_jobs_document_id",
        ["document_id"],
    )
    _add_index_if_missing(
        JOBS,
        "ix_knowledge_ingestion_jobs_document_version_id",
        ["document_version_id"],
    )
    _add_index_if_missing(
        JOBS,
        "ix_knowledge_ingestion_jobs_status",
        ["status"],
    )


def _ensure_legacy_tables_and_add_version_columns() -> None:
    tables = _tables()
    for table, required in (
        (SOURCES, SOURCE_COLUMNS),
        (PARENTS, PARENT_COLUMNS),
        (CHUNKS, CHUNK_COLUMNS),
    ):
        if table not in tables:
            raise RuntimeError(f"{table} is required but does not exist")
        _require_columns(table, required)

    _add_column_if_missing(
        SOURCES,
        "document_id",
        sa.Column("document_id", sa.Integer(), nullable=True),
    )
    _add_fk_if_missing(
        SOURCES,
        "fk_knowledge_sources_document_id",
        ["document_id"],
        DOCUMENTS,
        ["id"],
        ondelete="SET NULL",
    )
    _add_index_if_missing(
        SOURCES,
        "ix_knowledge_sources_document_id",
        ["document_id"],
    )

    _add_column_if_missing(
        PARENTS,
        "document_version_id",
        sa.Column("document_version_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        PARENTS,
        "page_start",
        sa.Column("page_start", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        PARENTS,
        "page_end",
        sa.Column("page_end", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        PARENTS,
        "section_path",
        sa.Column(
            "section_path",
            sa.Text(),
            server_default="",
            nullable=False,
        ),
    )
    _add_fk_if_missing(
        PARENTS,
        "fk_knowledge_parents_document_version_id",
        ["document_version_id"],
        VERSIONS,
        ["id"],
        ondelete="CASCADE",
    )
    _add_index_if_missing(
        PARENTS,
        "ix_knowledge_parents_document_version_id",
        ["document_version_id"],
    )

    _add_column_if_missing(
        CHUNKS,
        "document_version_id",
        sa.Column("document_version_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        CHUNKS,
        "page_start",
        sa.Column("page_start", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        CHUNKS,
        "page_end",
        sa.Column("page_end", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        CHUNKS,
        "section_path",
        sa.Column(
            "section_path",
            sa.Text(),
            server_default="",
            nullable=False,
        ),
    )
    _add_fk_if_missing(
        CHUNKS,
        "fk_knowledge_chunks_document_version_id",
        ["document_version_id"],
        VERSIONS,
        ["id"],
        ondelete="CASCADE",
    )
    _add_index_if_missing(
        CHUNKS,
        "ix_knowledge_chunks_document_version_id",
        ["document_version_id"],
    )


def upgrade() -> None:
    tables = _tables()

    if DOCUMENTS in tables:
        _require_columns(DOCUMENTS, DOCUMENT_COLUMNS)
    else:
        _create_documents()
        tables.add(DOCUMENTS)

    if VERSIONS in tables:
        _require_columns(VERSIONS, VERSION_COLUMNS)
    else:
        _create_versions()
        tables.add(VERSIONS)

    _ensure_document_constraints()

    if JOBS in tables:
        _require_columns(JOBS, JOB_COLUMNS)
    else:
        _create_jobs()

    _ensure_version_indexes()
    _ensure_job_indexes()
    _ensure_legacy_tables_and_add_version_columns()


def _drop_fk_if_exists(
    table: str,
    local_columns: list[str],
    remote_table: str,
    remote_columns: list[str],
) -> None:
    for foreign_key in _foreign_keys(table):
        if _foreign_key_matches(
            foreign_key,
            local_columns,
            remote_table,
            remote_columns,
        ):
            name = foreign_key.get("name")
            if name:
                op.drop_constraint(str(name), table, type_="foreignkey")
            return


def _drop_version_columns() -> None:
    tables = _tables()
    if SOURCES in tables:
        _drop_index_if_exists(SOURCES, "ix_knowledge_sources_document_id")
        _drop_fk_if_exists(
            SOURCES,
            ["document_id"],
            DOCUMENTS,
            ["id"],
        )
        if "document_id" in _columns(SOURCES):
            op.drop_column(SOURCES, "document_id")

    for table, index_name, remote_table in (
        (
            PARENTS,
            "ix_knowledge_parents_document_version_id",
            VERSIONS,
        ),
        (
            CHUNKS,
            "ix_knowledge_chunks_document_version_id",
            VERSIONS,
        ),
    ):
        if table not in tables:
            continue
        _drop_index_if_exists(table, index_name)
        _drop_fk_if_exists(
            table,
            ["document_version_id"],
            remote_table,
            ["id"],
        )
        for column in ("document_version_id", "page_start", "page_end", "section_path"):
            if column in _columns(table):
                op.drop_column(table, column)


def downgrade() -> None:
    _drop_version_columns()

    tables = _tables()
    if DOCUMENTS in tables:
        _drop_fk_if_exists(
            DOCUMENTS,
            ["current_version_id"],
            VERSIONS,
            ["id"],
        )
    for table in (JOBS, VERSIONS, DOCUMENTS):
        if table in tables:
            op.drop_table(table)
