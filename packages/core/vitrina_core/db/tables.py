"""Tables owned by core, mirroring the 0001 baseline (data-model.md §1)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from vitrina_core.db import metadata

# The tenant: every other row is scoped to one of these.
sellers = sa.Table(
    "sellers",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("business_name", sa.Text, nullable=False),
    sa.Column("owner_name", sa.Text, nullable=False),
    sa.Column("whatsapp_number", sa.Text, nullable=False, unique=True),
    sa.Column("city", sa.Text, nullable=False),
    sa.Column("timezone", sa.Text, nullable=False, server_default="America/Bogota"),
    sa.Column("category", sa.Text, nullable=False, server_default="products"),
    sa.Column("business_hours", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("handoff_amount_threshold_cop", sa.Integer, nullable=False, server_default="300000"),
    sa.Column("brand_voice_min", sa.Numeric(2, 1), nullable=False, server_default="3.5"),
    sa.Column("low_confidence_threshold", sa.Numeric(3, 2), nullable=False, server_default="0.60"),
    sa.Column("brand_voice_profile", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("ai_label_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("handoff_summary_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("consent_prompt_text", sa.Text, nullable=False),
    sa.Column("marketing_template_name", sa.Text),
    sa.Column("marketing_template_status", sa.Text, nullable=False, server_default="none"),
    sa.Column("auth_subject", sa.Text, unique=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)
