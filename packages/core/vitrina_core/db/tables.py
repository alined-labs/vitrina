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

# The Job resource and the queue behind it (FR-083; migrations 0001 and 0002).
jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("owner_seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE")),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("stage", sa.Text),
    sa.Column("progress", sa.Numeric(4, 3), nullable=False, server_default="0"),
    sa.Column("result_ref", sa.Text),
    sa.Column("error", JSONB),
    sa.Column("idempotency_key", sa.Text),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.Column("task", sa.Text, nullable=False, server_default=""),
    sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
    sa.Column(
        "run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.Column("locked_by", sa.Text),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
)

# The model-call ledger (FR-084): no prompt text, ever (data-model.md §5).
model_calls = sa.Table(
    "model_calls",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE")),
    sa.Column("request_id", sa.Text),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("purpose", sa.Text, nullable=False),
    sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
    sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
    sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
    sa.Column("replayed", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)

# Cross-module events (plan.md §3; data-model.md §4); inserting one notifies listeners.
events = sa.Table(
    "events",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("seller_id", sa.Uuid),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)
