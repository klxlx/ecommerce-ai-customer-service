"""Track the administrator who owns a handoff ticket and how it was closed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TICKETS = "handoff_tickets"
ASSIGNEE_FK = "fk_handoff_tickets_assignee_id_users"
ASSIGNEE_INDEX = "ix_handoff_tickets_assignee_id"


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table)}


def _foreign_keys(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_foreign_keys(table)
        if constraint.get("name")
    }


def upgrade() -> None:
    columns = _columns(TICKETS)
    if "assignee_id" not in columns:
        op.add_column(
            TICKETS,
            sa.Column("assignee_id", sa.Integer(), nullable=True),
        )
    if "claimed_at" not in columns:
        op.add_column(
            TICKETS,
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "resolved_at" not in columns:
        op.add_column(
            TICKETS,
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "close_reason" not in columns:
        op.add_column(
            TICKETS,
            sa.Column(
                "close_reason",
                sa.String(length=24),
                nullable=False,
                server_default="",
            ),
        )
    if "resolution_note" not in columns:
        op.add_column(
            TICKETS,
            sa.Column(
                "resolution_note",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )

    foreign_keys = _foreign_keys(TICKETS)
    if ASSIGNEE_FK not in foreign_keys:
        op.create_foreign_key(
            ASSIGNEE_FK,
            TICKETS,
            "users",
            ["assignee_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if ASSIGNEE_INDEX not in _indexes(TICKETS):
        op.create_index(ASSIGNEE_INDEX, TICKETS, ["assignee_id"])


def downgrade() -> None:
    if ASSIGNEE_INDEX in _indexes(TICKETS):
        op.drop_index(ASSIGNEE_INDEX, table_name=TICKETS)
    if ASSIGNEE_FK in _foreign_keys(TICKETS):
        op.drop_constraint(ASSIGNEE_FK, TICKETS, type_="foreignkey")

    columns = _columns(TICKETS)
    for column in (
        "resolution_note",
        "close_reason",
        "resolved_at",
        "claimed_at",
        "assignee_id",
    ):
        if column in columns:
            op.drop_column(TICKETS, column)
