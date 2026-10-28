"""Tables the compliance module owns, mirroring migration 0001 (data-model.md §1)."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA, JSONB

from vitrina_core.db import metadata


def _moment(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column[Any]:
    default = sa.func.now() if now else None
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=default)


contacts = sa.Table(
    "contacts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("wa_id_hash", BYTEA, nullable=False),
    sa.Column("wa_id_enc", BYTEA),
    sa.Column("display_name", sa.Text),
    _moment("opted_out_at"),
    _moment("erased_at"),
    _moment("last_marketing_sent_at"),
    sa.Column("awaiting_consent_prompt_message_id", sa.Uuid),
    _moment("created_at", nullable=False, now=True),
    sa.UniqueConstraint("seller_id", "wa_id_hash", name="uq_contacts_seller_wa_id_hash"),
)

# Append-only: a trigger refuses UPDATE, DELETE and TRUNCATE (FR-060).
consent_records = sa.Table(
    "consent_records",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "contact_id", sa.Uuid, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("purpose", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("text_shown", sa.Text, nullable=False),
    sa.Column("response_text", sa.Text, nullable=False),
    sa.Column("channel", sa.Text, nullable=False),
    sa.Column("evidence_message_id", sa.Uuid),
    sa.Column("recorded_by", sa.Text, nullable=False),
    _moment("recorded_at", nullable=False, now=True),
    _moment("created_at", nullable=False, now=True),
)

# A record that an erasure happened, with nothing that identifies the person (FR-065).
erasure_receipts = sa.Table(
    "erasure_receipts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("wa_id_hash", BYTEA, nullable=False),
    _moment("requested_at", nullable=False, now=True),
    _moment("completed_at"),
    sa.Column("scope", sa.Text, nullable=False),
    _moment("created_at", nullable=False, now=True),
)

# Reactivation campaigns and every eligibility decision made for them (T064, FR-064).
reactivation_campaigns = sa.Table(
    "reactivation_campaigns",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("brief_id", sa.Uuid),  # FK to briefs in the migration; no module declares it yet
    sa.Column("ad_id", sa.Uuid, sa.ForeignKey("ads.id", ondelete="SET NULL")),
    sa.Column("template_name", sa.Text, nullable=False),
    sa.Column("template_language", sa.Text, nullable=False, server_default="es"),
    sa.Column("segment", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("status", sa.Text, nullable=False, server_default="preview"),
    _moment("scheduled_for"),
    _moment("created_at", nullable=False, now=True),
)

send_decisions = sa.Table(
    "send_decisions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "campaign_id",
        sa.Uuid,
        sa.ForeignKey("reactivation_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "contact_id", sa.Uuid, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("decision", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("reason_text_es", sa.Text),
    _moment("next_allowed_at"),
    sa.Column("message_id", sa.Uuid, sa.ForeignKey("messages.id", ondelete="SET NULL")),
    _moment("evaluated_at", nullable=False, now=True),
    _moment("created_at", nullable=False, now=True),
)
