"""Add per-user question fingerprint and unresolved-ticket metadata.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TICKETS = "handoff_tickets"
EMBEDDING_DIMENSION = 1024
INDEX = "ix_handoff_tickets_user_status"


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    columns = _columns(TICKETS)
    if "question_norm" not in columns:
        op.add_column(
            TICKETS,
            sa.Column(
                "question_norm",
                sa.String(length=512),
                nullable=False,
                server_default="",
            ),
        )
    if "question_embedding" not in columns:
        op.add_column(
            TICKETS,
            sa.Column(
                "question_embedding",
                Vector(EMBEDDING_DIMENSION),
                nullable=True,
            ),
        )
    if "occurrence_count" not in columns:
        op.add_column(
            TICKETS,
            sa.Column(
                "occurrence_count",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )
    if "last_seen_at" not in columns:
        op.add_column(
            TICKETS,
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
    if INDEX not in _indexes(TICKETS):
        op.create_index(INDEX, TICKETS, ["user_id", "status"], unique=False)


def downgrade() -> None:
    if INDEX in _indexes(TICKETS):
        op.drop_index(INDEX, table_name=TICKETS)
    columns = _columns(TICKETS)
    for column in ("last_seen_at", "occurrence_count", "question_embedding", "question_norm"):
        if column in columns:
            op.drop_column(TICKETS, column)
