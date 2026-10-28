"""Tables the learning module owns, mirroring migration 0001 (data-model.md)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from vitrina_core.db import metadata

# Filled from the conversation module's ``ad.*`` events by ``AttributionConsumer`` (T044).
attribution_events = sa.Table(
    "attribution_events",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("ad_id", sa.Uuid, sa.ForeignKey("ads.id", ondelete="SET NULL")),
    sa.Column("conversation_id", sa.Uuid, sa.ForeignKey("conversations.id", ondelete="SET NULL")),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column(
        "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)


def _jsonb_list(name: str) -> sa.Column[object]:
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))


# Muse Wonder's weekly brief, validated against BriefSchema (T070, FR-050).
briefs = sa.Table(
    "briefs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("period_from", sa.Date, nullable=False),
    sa.Column("period_to", sa.Date, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="ready"),
    sa.Column("headline", sa.Text),
    _jsonb_list("insights"),
    _jsonb_list("recommended_angles"),
    _jsonb_list("creative_directives"),
    _jsonb_list("reactivation_segments"),
    sa.Column("dataset_hash", sa.Text),
    _jsonb_list("model_calls"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
)
