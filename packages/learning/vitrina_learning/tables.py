"""Tables the learning module owns, mirroring migration 0001 (data-model.md)."""

import sqlalchemy as sa

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
