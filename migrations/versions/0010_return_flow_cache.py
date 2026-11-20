"""Add photo return intake, product attributes, and semantic cache.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSION = 1024


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade() -> None:
    product_columns = _columns("products")
    if "model" not in product_columns:
        op.add_column(
            "products",
            sa.Column(
                "model",
                sa.String(length=120),
                nullable=False,
                server_default="",
            ),
        )
    if "color" not in product_columns:
        op.add_column(
            "products",
            sa.Column(
                "color",
                sa.String(length=80),
                nullable=False,
                server_default="",
            ),
        )

    item_columns = _columns("order_items")
    if "model" not in item_columns:
        op.add_column(
            "order_items",
            sa.Column(
                "model",
                sa.String(length=120),
                nullable=False,
                server_default="",
            ),
        )
    if "color" not in item_columns:
        op.add_column(
            "order_items",
            sa.Column(
                "color",
                sa.String(length=80),
                nullable=False,
                server_default="",
            ),
        )
    op.execute(
        """
        UPDATE order_items AS oi
        SET model = p.model, color = p.color
        FROM products AS p
        WHERE oi.product_id = p.id
          AND (oi.model = '' OR oi.color = '')
        """
    )

    message_columns = _columns("chat_messages")
    if "actions" not in message_columns:
        op.add_column(
            "chat_messages",
            sa.Column("actions", sa.JSON(), nullable=True),
        )

    ticket_columns = _columns("handoff_tickets")
    if "category" not in ticket_columns:
        op.add_column(
            "handoff_tickets",
            sa.Column(
                "category",
                sa.String(length=32),
                nullable=False,
                server_default="general",
            ),
        )

    existing_tables = _tables()
    if "return_analyses" not in existing_tables:
        op.create_table(
            "return_analyses",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "conversation_id",
                sa.Integer(),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("image_id", sa.String(length=64), nullable=False, unique=True),
            sa.Column("image_path", sa.String(length=500), nullable=False),
            sa.Column(
                "result",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "confidence",
                sa.Float(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "passed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "failure_reason",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
        )
        op.create_index(
            "ix_return_analyses_user_id",
            "return_analyses",
            ["user_id"],
        )
        op.create_index(
            "ix_return_analyses_conversation_id",
            "return_analyses",
            ["conversation_id"],
        )

    if "semantic_cache" not in existing_tables:
        op.create_table(
            "semantic_cache",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("question_norm", sa.String(length=512), nullable=False),
            sa.Column(
                "kb_type",
                sa.String(length=32),
                nullable=False,
                server_default="policy",
            ),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column(
                "sources",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            ),
            sa.Column(
                "embedding",
                Vector(EMBEDDING_DIMENSION),
                nullable=False,
            ),
            sa.Column(
                "hit_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.UniqueConstraint(
                "question_norm",
                "kb_type",
                name="uq_semantic_cache_question_kb",
            ),
        )
        op.create_index(
            "ix_semantic_cache_question_norm",
            "semantic_cache",
            ["question_norm"],
        )
        op.create_index(
            "ix_semantic_cache_embedding",
            "semantic_cache",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    existing_tables = _tables()
    if "semantic_cache" in existing_tables:
        op.drop_index("ix_semantic_cache_embedding", table_name="semantic_cache")
        op.drop_index(
            "ix_semantic_cache_question_norm", table_name="semantic_cache"
        )
        op.drop_table("semantic_cache")
    if "return_analyses" in existing_tables:
        op.drop_index(
            "ix_return_analyses_conversation_id", table_name="return_analyses"
        )
        op.drop_index("ix_return_analyses_user_id", table_name="return_analyses")
        op.drop_table("return_analyses")

    if "category" in _columns("handoff_tickets"):
        op.drop_column("handoff_tickets", "category")
    if "actions" in _columns("chat_messages"):
        op.drop_column("chat_messages", "actions")

    item_columns = _columns("order_items")
    for column in ("color", "model"):
        if column in item_columns:
            op.drop_column("order_items", column)
    product_columns = _columns("products")
    for column in ("color", "model"):
        if column in product_columns:
            op.drop_column("products", column)
