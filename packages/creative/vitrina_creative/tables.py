"""Tables the creative module owns, mirroring migration 0001 (data-model.md §1).

Only the columns this module reads and writes are declared; foreign keys stay in the migration,
because modules meet through public interfaces, not through each other's tables (Art. VI.3).
"""

import sqlalchemy as sa

from vitrina_core.db import metadata

assets = sa.Table(
    "assets",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("seller_id", sa.Uuid, nullable=False),
    sa.Column("product_id", sa.Uuid, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("storage_key", sa.Text, nullable=False),
    sa.Column("width", sa.Integer),
    sa.Column("height", sa.Integer),
    sa.Column("duration_s", sa.Float),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)
