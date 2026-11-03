"""Product search reads everything the agent reads about a product.

``search_tsv`` held the name and the description, while the text the agent sees and the
in-memory catalog behind the evals also hold the price and the attributes (``describe_product``
in ``vitrina_catalog.text``). "¿La tienen en rojo?" found a product there and missed it here
when only an attribute said red. The new text has the same fields, attribute names included.
The price is written as "precio 85 000": the words the query side reads "$85.000" as.

Replacing a generated column fills it for every existing row, in this migration's transaction.
It rewrites ``products`` under an exclusive lock, so the table is unavailable until it commits.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "to_tsvector('spanish', f_unaccent(coalesce(name, '') || ' ' || coalesce(description, '')))"
)
AFTER = (
    "to_tsvector('spanish', f_unaccent("
    "f_product_search_text(name, description, price_cop, attributes)))"
)


def _rebuild(expression: str) -> None:
    op.drop_index("ix_products_search_tsv", "products")
    op.drop_column("products", "search_tsv")
    op.add_column(
        "products",
        sa.Column("search_tsv", postgresql.TSVECTOR, sa.Computed(expression, persisted=True)),
    )
    op.create_index("ix_products_search_tsv", "products", ["search_tsv"], postgresql_using="gin")


def upgrade() -> None:
    op.execute(
        r"""CREATE FUNCTION f_product_search_text(
            product_name text, product_description text, price integer, product_attributes jsonb
        ) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path FROM CURRENT
        RETURN coalesce(product_name, '') || ' ' || coalesce(product_description, '')
            || ' precio ' || regexp_replace(price::text, '(\d)(?=(\d{3})+$)', '\1 ', 'g')
            || ' ' || CASE WHEN jsonb_typeof(product_attributes) = 'object' THEN coalesce(
                (SELECT string_agg(pair.k || ' ' || coalesce(pair.v, ''), ' ')
                 FROM jsonb_each_text(product_attributes) AS pair(k, v)),
                '') ELSE '' END"""
    )
    _rebuild(AFTER)


def downgrade() -> None:
    _rebuild(BEFORE)
    op.execute("DROP FUNCTION f_product_search_text(text, text, integer, jsonb)")
