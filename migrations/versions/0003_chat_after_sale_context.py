"""Persist the read-only after-sales snapshot shown in chat history.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLE_NAME = "chat_messages"
COLUMN_NAME = "after_sale_context"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        return

    columns = {
        column["name"]: column for column in inspector.get_columns(TABLE_NAME)
    }
    existing = columns.get(COLUMN_NAME)
    if existing is None:
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
        return

    column_type = existing["type"]
    if not isinstance(column_type, postgresql.JSONB) or not existing["nullable"]:
        raise RuntimeError(
            f"{TABLE_NAME}.{COLUMN_NAME} exists but must be nullable jsonb"
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME in columns:
        op.drop_column(TABLE_NAME, COLUMN_NAME)
