"""Tables the compliance module owns, mirroring migration 0001 (data-model.md §1)."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA

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
