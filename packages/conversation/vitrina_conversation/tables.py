"""Tables the conversation module owns, mirroring migration 0001 (data-model.md §1).

``attribution_events`` is filled by analytics from the ``ad.*`` events this module emits
(plan.md §3), so it is not declared here.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from vitrina_core.db import metadata


def _moment(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column[Any]:
    default = sa.func.now() if now else None
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=default)


def _seller() -> sa.Column[Any]:
    return sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    )


def _conversation() -> sa.Column[Any]:
    return sa.Column(
        "conversation_id",
        sa.Uuid,
        sa.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )


conversations = sa.Table(
    "conversations",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    _seller(),
    sa.Column(
        "contact_id", sa.Uuid, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("attributed_ad_id", sa.Uuid, sa.ForeignKey("ads.id", ondelete="SET NULL")),
    sa.Column("attribution_status", sa.Text, nullable=False, server_default="none"),
    sa.Column("mode", sa.Text, nullable=False, server_default="agent"),
    sa.Column("low_confidence_streak", sa.Integer, nullable=False, server_default="0"),
    sa.Column("summary", sa.Text),
    _moment("last_message_at"),
    sa.Column("is_replay", sa.Boolean, nullable=False, server_default=sa.false()),
    _moment("created_at", nullable=False, now=True),
)

webhook_payloads = sa.Table(
    "webhook_payloads",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    _moment("received_at", nullable=False, now=True),
    sa.Column("signature_valid", sa.Boolean, nullable=False),
    sa.Column("body", JSONB),
    _moment("processed_at"),
    sa.Column("error", sa.Text),
    _moment("created_at", nullable=False, now=True),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    _seller(),
    _conversation(),
    sa.Column("direction", sa.Text, nullable=False),
    sa.Column("sender", sa.Text, nullable=False),
    sa.Column("wa_message_id", sa.Text, unique=True),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("text", sa.Text),
    sa.Column("transcript", sa.Text),
    sa.Column("media_key", sa.Text),
    sa.Column("normalized", JSONB),
    sa.Column("agent_output", JSONB),
    sa.Column("validation", JSONB),
    sa.Column("delivery_status", sa.Text),
    sa.Column("is_replay", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("raw_payload_id", sa.Uuid, sa.ForeignKey("webhook_payloads.id", ondelete="SET NULL")),
    _moment("created_at", nullable=False, now=True),
)

handoffs = sa.Table(
    "handoffs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    _seller(),
    _conversation(),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("summary", sa.Text),
    sa.Column("urgency", sa.Text, nullable=False, server_default="normal"),
    _moment("opened_at", nullable=False, now=True),
    _moment("released_at"),
    sa.Column("seller_notified", sa.Boolean, nullable=False, server_default=sa.false()),
    _moment("created_at", nullable=False, now=True),
)

objections = sa.Table(
    "objections",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    _seller(),
    _conversation(),
    sa.Column("ad_id", sa.Uuid, sa.ForeignKey("ads.id", ondelete="SET NULL")),
    sa.Column("product_id", sa.Uuid, sa.ForeignKey("products.id", ondelete="SET NULL")),
    sa.Column("tag", sa.Text, nullable=False),
    sa.Column("quote_redacted", sa.Text),
    _moment("occurred_at", nullable=False, now=True),
    _moment("created_at", nullable=False, now=True),
)
