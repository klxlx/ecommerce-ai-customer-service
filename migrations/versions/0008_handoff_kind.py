"""Distinguish human handoff tickets from knowledge-gap tickets.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TICKETS = "handoff_tickets"


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if "kind" not in _columns(TICKETS):
        op.add_column(
            TICKETS,
            sa.Column(
                "kind",
                sa.String(length=24),
                nullable=False,
                server_default="human",
            ),
        )


def downgrade() -> None:
    if "kind" in _columns(TICKETS):
        op.drop_column(TICKETS, "kind")
