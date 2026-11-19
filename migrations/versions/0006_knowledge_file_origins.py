"""Track the source format and original filename of knowledge documents.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DOCUMENTS = "knowledge_documents"


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns(DOCUMENTS)
    if "source_kind" not in columns:
        op.add_column(
            DOCUMENTS,
            sa.Column(
                "source_kind",
                sa.String(length=16),
                nullable=False,
                server_default="unknown",
            ),
        )
    if "original_filename" not in columns:
        op.add_column(
            DOCUMENTS,
            sa.Column(
                "original_filename",
                sa.String(length=500),
                nullable=False,
                server_default="",
            ),
        )

    op.execute(
        sa.text(
            """
            UPDATE knowledge_documents AS document
            SET
                source_kind = CASE
                    WHEN version.extraction_strategy = 'manual'
                         OR document.source_uri LIKE 'manual://%'
                    THEN 'manual'
                    WHEN lower(version.pdf_file) LIKE '%.pdf'
                         OR lower(document.source_uri) LIKE '%.pdf'
                    THEN 'pdf'
                    ELSE 'unknown'
                END,
                original_filename = CASE
                    WHEN version.extraction_strategy <> 'manual'
                         AND (
                             lower(version.pdf_file) LIKE '%.pdf'
                             OR lower(document.source_uri) LIKE '%.pdf'
                         )
                    THEN document.source_uri
                    ELSE ''
                END
            FROM knowledge_document_versions AS version
            WHERE version.id = COALESCE(
                document.current_version_id,
                (
                    SELECT latest.id
                    FROM knowledge_document_versions AS latest
                    WHERE latest.document_id = document.id
                    ORDER BY latest.version_number DESC
                    LIMIT 1
                )
            )
            """
        )
    )


def downgrade() -> None:
    columns = _columns(DOCUMENTS)
    if "original_filename" in columns:
        op.drop_column(DOCUMENTS, "original_filename")
    if "source_kind" in columns:
        op.drop_column(DOCUMENTS, "source_kind")
