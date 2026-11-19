"""After-sales fact table created through a guarded migration.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLE_NAME = "after_sales"
INDEX_NAME = "ix_after_sales_order_id"
REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "order_id",
        "service_type",
        "status",
        "refund_amount",
        "reason",
        "created_at",
        "updated_at",
    }
)


def _create_table() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("service_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
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
            ["order_id"],
            ["orders.id"],
            name="fk_after_sales_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_after_sales"),
    )
    op.create_index(INDEX_NAME, TABLE_NAME, ["order_id"])


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME not in inspector.get_table_names():
        _create_table()
        return

    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            f"{TABLE_NAME} exists but is missing required columns: {sorted(missing)}"
        )

    indexed_columns = {
        tuple(index["column_names"]) for index in inspector.get_indexes(TABLE_NAME)
    }
    if ("order_id",) not in indexed_columns:
        op.create_index(INDEX_NAME, TABLE_NAME, ["order_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE_NAME in inspector.get_table_names():
        op.drop_table(TABLE_NAME)
