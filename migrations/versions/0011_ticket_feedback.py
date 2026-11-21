"""Ticket feedback, completion attribution, and customer-facing status.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("handoff_tickets")
    if "resolved_by_id" not in columns:
        op.add_column(
            "handoff_tickets",
            sa.Column(
                "resolved_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_handoff_tickets_resolved_by_id",
            "handoff_tickets",
            ["resolved_by_id"],
        )
    if "business_status" not in columns:
        op.add_column(
            "handoff_tickets",
            sa.Column(
                "business_status",
                sa.String(length=32),
                nullable=False,
                server_default="",
            ),
        )
    if "rating" not in columns:
        op.add_column(
            "handoff_tickets",
            sa.Column("rating", sa.Integer(), nullable=True),
        )
    if "rating_comment" not in columns:
        op.add_column(
            "handoff_tickets",
            sa.Column("rating_comment", sa.String(length=50), nullable=True),
        )
    if "rated_at" not in columns:
        op.add_column(
            "handoff_tickets",
            sa.Column("rated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    columns = _columns("handoff_tickets")
    for column in ("rated_at", "rating_comment", "rating", "business_status"):
        if column in columns:
            op.drop_column("handoff_tickets", column)
    if "resolved_by_id" in columns:
        op.drop_index(
            "ix_handoff_tickets_resolved_by_id", table_name="handoff_tickets"
        )
        op.drop_column("handoff_tickets", "resolved_by_id")
