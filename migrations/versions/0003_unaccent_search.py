"""Accent-insensitive keyword search for catalog retrieval (T020, plan.md §4.1).

Customers write "envio a medellin"; facts say "Envío a Medellín". ``unaccent`` is STABLE, so
generated columns cannot call it directly: ``f_unaccent`` pins the dictionary and is
declared IMMUTABLE, the usual Postgres recipe. ``search_tsv`` is rebuilt on top of it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPRESSIONS = {
    "products": "coalesce(name, '') || ' ' || coalesce(description, '')",
    "facts": '"text"',
}


def _rebuild(table: str, expression: str) -> None:
    op.drop_index(f"ix_{table}_search_tsv", table)
    op.drop_column(table, "search_tsv")
    op.add_column(
        table,
        sa.Column("search_tsv", postgresql.TSVECTOR, sa.Computed(expression, persisted=True)),
    )
    op.create_index(f"ix_{table}_search_tsv", table, ["search_tsv"], postgresql_using="gin")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions")
    op.execute(
        """CREATE FUNCTION f_unaccent(value text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        SET search_path FROM CURRENT
        RETURN unaccent('unaccent', value)"""
    )
    for table, expression in EXPRESSIONS.items():
        _rebuild(table, f"to_tsvector('spanish', f_unaccent({expression}))")


def downgrade() -> None:
    for table, expression in EXPRESSIONS.items():
        _rebuild(table, f"to_tsvector('spanish', {expression})")
    op.execute("DROP FUNCTION IF EXISTS f_unaccent(text)")
