"""Tables the creative module owns, mirroring migration 0001 (data-model.md §1).

Only the columns this module reads and writes are declared; foreign keys stay in the migration,
because modules meet through public interfaces, not through each other's tables (Art. VI.3).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

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

creative_jobs = sa.Table(
    "creative_jobs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("seller_id", sa.Uuid, nullable=False),
    sa.Column("product_id", sa.Uuid, nullable=False),
    sa.Column("params", JSONB),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("stage", sa.Text, nullable=False),
    sa.Column("progress", sa.Numeric(4, 3), nullable=False),
    sa.Column("error", JSONB),
    sa.Column("brief_id", sa.Uuid),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)

creatives = sa.Table(
    "creatives",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("job_id", sa.Uuid, nullable=False),
    sa.Column("seller_id", sa.Uuid, nullable=False),
    sa.Column("product_id", sa.Uuid, nullable=False),
    sa.Column("format", sa.Text, nullable=False),
    sa.Column("storage_key", sa.Text),
    sa.Column("mask_key", sa.Text),
    sa.Column("source_asset_id", sa.Uuid),
    sa.Column("scene_prompt", sa.Text),
    sa.Column("verification", JSONB),
    sa.Column("provenance", JSONB),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)

copy_variants = sa.Table(
    "copy_variants",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("job_id", sa.Uuid, nullable=False),
    sa.Column("seller_id", sa.Uuid, nullable=False),
    sa.Column("hook", sa.Text, nullable=False),
    sa.Column("angle", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("brand_voice_score", sa.Numeric(2, 1)),
    sa.Column("brand_voice_judge", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)

# Ads (data-model.md ads): the database assigns code, wa_link and approved_at on approval
# (next_ad_code, ads_assign_code) and refuses an approved ad on a creative that did not pass.
ads = sa.Table(
    "ads",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("seller_id", sa.Uuid, nullable=False),
    sa.Column("product_id", sa.Uuid, nullable=False),
    sa.Column("creative_id", sa.Uuid, nullable=False),
    sa.Column("copy_variant_id", sa.Uuid, nullable=False),
    sa.Column("brief_id", sa.Uuid),
    sa.Column("code", sa.Text),
    sa.Column("wa_link", sa.Text),
    sa.Column("angle", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("approved_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)
