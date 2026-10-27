"""Tables the catalog owns, mirroring migrations 0001 and 0003 (data-model.md §1)."""

from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

from vitrina_core.db import metadata


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


products = sa.Table(
    "products",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("price_cop", sa.Integer, nullable=False),
    sa.Column("stock", sa.Integer),
    sa.Column("attributes", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("source", sa.Text, nullable=False, server_default="manual"),
    sa.Column(
        "amounts_cop", ARRAY(sa.Integer), nullable=False, server_default=sa.text("'{}'::integer[]")
    ),
    sa.Column("embedding", Vector(1024)),
    sa.Column("image_embedding", Vector(768)),
    sa.Column(
        "search_tsv",
        TSVECTOR,
        sa.Computed(
            "to_tsvector('spanish', f_unaccent(coalesce(name, '') || ' ' || coalesce(description, '')))",
            persisted=True,
        ),
    ),
    *_timestamps(),
)

facts = sa.Table(
    "facts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("source", sa.Text),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column(
        "amounts_cop", ARRAY(sa.Integer), nullable=False, server_default=sa.text("'{}'::integer[]")
    ),
    sa.Column("embedding", Vector(1024)),
    sa.Column(
        "search_tsv",
        TSVECTOR,
        sa.Computed("""to_tsvector('spanish', f_unaccent("text"))""", persisted=True),
    ),
    *_timestamps(),
)

reservations = sa.Table(
    "reservations",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("conversation_id", sa.Uuid),
    sa.Column(
        "product_id", sa.Uuid, sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("variant", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="reserved"),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)
