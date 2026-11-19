"""Add soft-delete timestamps for conversations and knowledge documents.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    for table in ("conversations", "knowledge_documents"):
        if "deleted_at" not in _columns(table):
            op.add_column(
                table,
                sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            )


def downgrade() -> None:
    for table in ("knowledge_documents", "conversations"):
        if "deleted_at" in _columns(table):
            op.drop_column(table, "deleted_at")
