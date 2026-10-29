"""Baseline schema: every table in data-model.md §1 (T005).

Tables are unqualified: the search path set by env.py puts them in the environment's schema
(a development schema, prod or a test schema). Enum values are copied here on purpose, so
this revision stays a fixed snapshot when the code moves on.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB
EMPTY_OBJECT = sa.text("'{}'::jsonb")
EMPTY_ARRAY = sa.text("'[]'::jsonb")

FACT_KINDS = ("faq", "shipping", "payment", "policy", "hours", "other")
FORMATS = ("story_9x16", "catalog_1x1", "feed_4x5")
ANGLES = ("price", "quality", "handmade", "urgency", "gift", "local_pride")
HOOKS = ("question", "bold_claim", "story", "social_proof_free", "scarcity", "benefit")
HANDOFF_REASONS = (
    "customer_request", "complaint_refund", "payment_issue", "medical_topic", "low_confidence_twice",
    "negative_sentiment", "amount_over_threshold", "price_unverified", "policy_exception", "other",
)  # fmt: skip
OBJECTION_TAGS = (
    "price",
    "shipping",
    "size",
    "availability",
    "trust",
    "timing",
    "payment",
    "off_topic",
    "other",
)
MESSAGE_TYPES = (
    "text",
    "image",
    "audio",
    "video",
    "document",
    "interactive",
    "template",
    "unsupported",
)
DELIVERY = ("queued", "sent", "delivered", "read", "failed", "mocked")
SEND_REASONS = (
    "OK", "OPTED_OUT", "NO_CONSENT_LEY_1581", "FREQUENCY_CAP", "OUTSIDE_HOURS_LEY_2300",
    "NO_MATCHING_AD", "TEMPLATE_NOT_APPROVED", "SEND_FAILED",
)  # fmt: skip
JOB_KINDS = (
    "voice_import", "product_import", "creative", "reel", "brief", "erasure", "eval_run",
    "replay", "campaign_send", "public_showcase",
)  # fmt: skip
PROVIDERS = ("meta_model_api", "modal", "baseten", "fireworks", "local_mock")
PURPOSES = (
    "agent",
    "copy",
    "scene",
    "segment",
    "verify",
    "transcribe",
    "brief",
    "judge",
    "classify",
    "embed",
)
SUITES = ("conversations_es", "fidelity", "consent_scheduler", "price_validator")

# Tables in creation order; downgrade drops them in reverse.
TABLES = (
    "sellers", "products", "facts", "assets", "briefs", "creative_jobs", "creatives", "copy_variants",
    "ads", "ad_code_counters", "contacts", "conversations", "webhook_payloads", "messages",
    "consent_records", "handoffs", "objections", "reservations", "bookings", "attribution_events",
    "reactivation_campaigns", "send_decisions", "erasure_receipts", "jobs", "idempotency_keys",
    "model_calls", "eval_runs", "public_showcases", "public_rate_limits", "events",
)  # fmt: skip


def one_of(table: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{column}")


def id_column() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid, primary_key=True)


def seller(nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=nullable
    )


def ref(
    name: str, target: str, *, nullable: bool = True, cascade: bool = False
) -> sa.Column[object]:
    ondelete = "CASCADE" if cascade else ("SET NULL" if nullable else "RESTRICT")
    return sa.Column(
        name, sa.Uuid, sa.ForeignKey(f"{target}.id", ondelete=ondelete), nullable=nullable
    )


def created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def moment(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    default = sa.text("now()") if default_now else None
    return sa.Column(name, sa.DateTime(timezone=True), server_default=default, nullable=nullable)


def text(name: str, *, nullable: bool = True, default: str | None = None) -> sa.Column[object]:
    return sa.Column(name, sa.Text, nullable=nullable, server_default=default)


def jsonb(
    name: str, default: sa.TextClause | None = EMPTY_OBJECT, *, nullable: bool = False
) -> sa.Column[object]:
    return sa.Column(name, JSONB, nullable=nullable, server_default=default)


def amounts() -> sa.Column[object]:
    return sa.Column(
        "amounts_cop",
        postgresql.ARRAY(sa.Integer),
        nullable=False,
        server_default=sa.text("'{}'::integer[]"),
    )


def search_tsv(expression: str) -> sa.Column[object]:
    return sa.Column(
        "search_tsv",
        postgresql.TSVECTOR,
        sa.Computed(f"to_tsvector('spanish', {expression})", persisted=True),
    )


def retrieval_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_seller_status", table, ["seller_id", "status"])
    op.create_index(
        f"ix_{table}_embedding",
        table,
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(f"ix_{table}_search_tsv", table, ["search_tsv"], postgresql_using="gin")


def upgrade() -> None:
    # Extensions are per database: keep them out of the machine schemas (Supabase convention).
    op.execute("CREATE SCHEMA IF NOT EXISTS extensions")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions")
    op.execute(
        """CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN NEW.updated_at := now(); RETURN NEW; END $$"""
    )

    # ── sellers and catalog ──
    op.create_table(
        "sellers",
        id_column(),
        text("business_name", nullable=False),
        text("owner_name", nullable=False),
        sa.Column("whatsapp_number", sa.Text, nullable=False, unique=True),
        text("city", nullable=False),
        text("timezone", nullable=False, default="America/Bogota"),
        text("category", nullable=False, default="products"),
        jsonb("business_hours"),
        sa.Column(
            "handoff_amount_threshold_cop", sa.Integer, nullable=False, server_default="300000"
        ),
        sa.Column("brand_voice_min", sa.Numeric(2, 1), nullable=False, server_default="3.5"),
        sa.Column(
            "low_confidence_threshold", sa.Numeric(3, 2), nullable=False, server_default="0.60"
        ),
        jsonb("brand_voice_profile"),
        sa.Column("ai_label_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("handoff_summary_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        text(
            "consent_prompt_text",
            nullable=False,
            default="¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ",
        ),
        text("marketing_template_name"),
        text("marketing_template_status", nullable=False, default="none"),
        sa.Column("auth_subject", sa.Text, unique=True),
        created_at(),
        one_of("sellers", "category", ("products", "services")),
        one_of("sellers", "marketing_template_status", ("none", "pending", "approved", "rejected")),
    )

    op.create_table(
        "products",
        id_column(),
        seller(),
        text("name", nullable=False),
        text("description"),
        sa.Column("price_cop", sa.Integer, nullable=False),
        sa.Column("stock", sa.Integer),
        jsonb("attributes"),
        text("status", nullable=False, default="active"),
        text("source", nullable=False, default="manual"),
        amounts(),
        sa.Column("embedding", Vector(1024)),
        sa.Column("image_embedding", Vector(768)),
        search_tsv("coalesce(name, '') || ' ' || coalesce(description, '')"),
        created_at(),
        moment("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint("price_cop >= 0", name="ck_products_price_cop"),
        sa.CheckConstraint("stock IS NULL OR stock >= 0", name="ck_products_stock"),
        one_of("products", "status", ("draft", "active", "archived")),
        one_of("products", "source", ("manual", "image_import")),
    )
    retrieval_indexes("products")
    op.create_index(
        "ix_products_image_embedding",
        "products",
        ["image_embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"image_embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_products_name_trgm",
        "products",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "facts",
        id_column(),
        seller(),
        text("kind", nullable=False),
        text("text", nullable=False),
        text("source"),
        text("status", nullable=False, default="active"),
        amounts(),
        sa.Column("embedding", Vector(1024)),
        search_tsv('"text"'),
        created_at(),
        moment("updated_at", nullable=False, default_now=True),
        one_of("facts", "kind", FACT_KINDS),
        one_of("facts", "status", ("draft", "active")),
    )
    retrieval_indexes("facts")
    for table in ("products", "facts"):
        op.execute(
            f"CREATE TRIGGER {table}_touch BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION touch_updated_at()"
        )

    # ── assets and creative ──
    op.create_table(
        "assets",
        id_column(),
        seller(),
        ref("product_id", "products", nullable=False, cascade=True),
        text("kind", nullable=False),
        text("storage_key", nullable=False),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("duration_s", sa.Float),
        text("sha256", nullable=False),
        sa.Column("image_embedding", Vector(768)),
        created_at(),
        sa.UniqueConstraint("seller_id", "sha256", name="uq_assets_seller_sha256"),
        one_of("assets", "kind", ("image", "video")),
    )

    op.create_table(
        "briefs",
        id_column(),
        seller(),
        sa.Column("period_from", sa.Date, nullable=False),
        sa.Column("period_to", sa.Date, nullable=False),
        text("status", nullable=False, default="ready"),
        text("headline"),
        jsonb("insights", EMPTY_ARRAY),
        jsonb("recommended_angles", EMPTY_ARRAY),
        jsonb("creative_directives", EMPTY_ARRAY),
        jsonb("reactivation_segments", EMPTY_ARRAY),
        text("dataset_hash"),
        jsonb("model_calls", EMPTY_ARRAY),
        created_at(),
        one_of("briefs", "status", ("ready", "applied", "failed")),
    )

    op.create_table(
        "creative_jobs",
        id_column(),
        seller(),
        ref("product_id", "products", nullable=False, cascade=True),
        jsonb("params"),
        text("status", nullable=False, default="queued"),
        text("stage", nullable=False, default="queued"),
        sa.Column("progress", sa.Numeric(4, 3), nullable=False, server_default="0"),
        jsonb("error", None, nullable=True),
        ref("brief_id", "briefs"),
        moment("finished_at"),
        created_at(),
        one_of("creative_jobs", "status", ("queued", "running", "succeeded", "failed")),
        one_of(
            "creative_jobs",
            "stage",
            (
                "queued",
                "segmenting",
                "composing",
                "verifying",
                "copywriting",
                "scoring",
                "ready",
                "failed",
            ),
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 1", name="ck_creative_jobs_progress"),
    )

    op.create_table(
        "creatives",
        id_column(),
        ref("job_id", "creative_jobs", nullable=False, cascade=True),
        seller(),
        ref("product_id", "products", nullable=False, cascade=True),
        text("format", nullable=False),
        text("storage_key"),
        text("mask_key"),
        ref("source_asset_id", "assets"),
        text("scene_prompt"),
        jsonb("verification", sa.text("""'{"status": "pending"}'::jsonb""")),
        jsonb("provenance"),
        created_at(),
        one_of("creatives", "format", FORMATS),
    )
    op.create_index(
        "ix_creatives_passed",
        "creatives",
        ["seller_id", "product_id"],
        postgresql_where=sa.text("(verification->>'status') = 'passed'"),
    )

    op.create_table(
        "copy_variants",
        id_column(),
        ref("job_id", "creative_jobs", nullable=False, cascade=True),
        seller(),
        text("hook", nullable=False),
        text("angle", nullable=False),
        text("text", nullable=False),
        sa.Column("brand_voice_score", sa.Numeric(2, 1)),
        text("brand_voice_judge", nullable=False, default="none"),
        text("status", nullable=False, default="candidate"),
        created_at(),
        one_of("copy_variants", "hook", HOOKS),
        one_of("copy_variants", "angle", ANGLES),
        one_of("copy_variants", "brand_voice_judge", ("glimmer", "spark_rubric", "none")),
        one_of(
            "copy_variants", "status", ("candidate", "approved", "rejected", "dropped_low_voice")
        ),
        sa.CheckConstraint('char_length("text") <= 300', name="ck_copy_variants_text"),
        sa.CheckConstraint(
            "brand_voice_score IS NULL OR brand_voice_score BETWEEN 1 AND 5",
            name="ck_copy_variants_score",
        ),
    )

    op.create_table(
        "ads",
        id_column(),
        seller(),
        ref("product_id", "products", nullable=False, cascade=True),
        ref("creative_id", "creatives", nullable=False, cascade=True),
        ref("copy_variant_id", "copy_variants", nullable=False, cascade=True),
        ref("brief_id", "briefs"),
        text("code"),
        text("wa_link"),
        text("angle"),
        text("status", nullable=False, default="draft"),
        moment("approved_at"),
        created_at(),
        sa.UniqueConstraint("seller_id", "code", name="uq_ads_seller_code"),
        one_of("ads", "status", ("draft", "approved", "archived")),
        sa.CheckConstraint(
            f"angle IS NULL OR angle IN ({', '.join(repr(a) for a in ANGLES)})", name="ck_ads_angle"
        ),
        sa.CheckConstraint("code IS NULL OR code ~ '^A[0-9A-Z]{1,3}$'", name="ck_ads_code"),
    )
    op.create_table(
        "ad_code_counters",
        sa.Column(
            "seller_id", sa.Uuid, sa.ForeignKey("sellers.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("last_value", sa.Integer, nullable=False, server_default="0"),
    )
    # FR-014: A + base-32 counter per seller (A1, A7, AK2 …) and the wa.me link, on approval.
    op.execute(
        """CREATE FUNCTION next_ad_code(p_seller uuid) RETURNS text LANGUAGE plpgsql
        SET search_path FROM CURRENT AS $$
        DECLARE
          n integer;
          alphabet constant text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
          digits text := '';
        BEGIN
          INSERT INTO ad_code_counters (seller_id, last_value) VALUES (p_seller, 1)
          ON CONFLICT (seller_id) DO UPDATE SET last_value = ad_code_counters.last_value + 1
          RETURNING last_value INTO n;
          IF n >= 32768 THEN
            RAISE EXCEPTION 'ad codes exhausted for seller %', p_seller;
          END IF;
          LOOP
            digits := substr(alphabet, (n % 32) + 1, 1) || digits;
            n := n / 32;
            EXIT WHEN n = 0;
          END LOOP;
          RETURN 'A' || digits;
        END $$"""
    )
    op.execute(
        """CREATE FUNCTION ads_assign_code() RETURNS trigger LANGUAGE plpgsql
        SET search_path FROM CURRENT AS $$
        DECLARE number_digits text;
        BEGIN
          IF NEW.status = 'approved' AND NEW.code IS NULL THEN
            NEW.code := next_ad_code(NEW.seller_id);
            SELECT regexp_replace(whatsapp_number, '[^0-9]', '', 'g') INTO number_digits
              FROM sellers WHERE id = NEW.seller_id;
            NEW.wa_link := 'https://wa.me/' || number_digits || '?text=Hola%2C%20vi%20el%20anuncio%20%23' || NEW.code;
            NEW.approved_at := coalesce(NEW.approved_at, now());
          END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER ads_assign_code BEFORE INSERT OR UPDATE ON ads FOR EACH ROW EXECUTE FUNCTION ads_assign_code()"
    )
    # Invariant 2 / AS-2.3: an approved ad always references a passed creative.
    op.execute(
        """CREATE FUNCTION ads_require_passed_creative() RETURNS trigger LANGUAGE plpgsql
        SET search_path FROM CURRENT AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM creatives WHERE id = NEW.creative_id AND verification->>'status' = 'passed'
          ) THEN
            RAISE EXCEPTION 'CREATIVE_BLOCKED: ad % references a creative that did not pass verification', NEW.id;
          END IF;
          RETURN NULL;
        END $$"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER ads_require_passed_creative AFTER INSERT OR UPDATE ON ads
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.status = 'approved')
        EXECUTE FUNCTION ads_require_passed_creative()"""
    )

    # ── contacts, conversations, messages ──
    op.create_table(
        "contacts",
        id_column(),
        seller(),
        sa.Column("wa_id_hash", postgresql.BYTEA, nullable=False),
        sa.Column("wa_id_enc", postgresql.BYTEA),
        text("display_name"),
        moment("opted_out_at"),
        moment("erased_at"),
        moment("last_marketing_sent_at"),
        sa.Column("awaiting_consent_prompt_message_id", sa.Uuid),
        created_at(),
        sa.UniqueConstraint("seller_id", "wa_id_hash", name="uq_contacts_seller_wa_id_hash"),
    )

    op.create_table(
        "conversations",
        id_column(),
        seller(),
        ref("contact_id", "contacts", nullable=False, cascade=True),
        ref("attributed_ad_id", "ads"),
        text("attribution_status", nullable=False, default="none"),
        text("mode", nullable=False, default="agent"),
        sa.Column("low_confidence_streak", sa.Integer, nullable=False, server_default="0"),
        text("summary"),
        moment("last_message_at"),
        sa.Column("is_replay", sa.Boolean, nullable=False, server_default=sa.false()),
        created_at(),
        one_of("conversations", "attribution_status", ("attributed", "unknown_code", "none")),
        one_of("conversations", "mode", ("agent", "human", "closed")),
    )

    op.create_table(
        "webhook_payloads",
        id_column(),
        moment("received_at", nullable=False, default_now=True),
        sa.Column("signature_valid", sa.Boolean, nullable=False),
        jsonb("body", None),
        moment("processed_at"),
        text("error"),
        created_at(),
    )
    op.create_index("ix_webhook_payloads_received_at", "webhook_payloads", ["received_at"])

    op.create_table(
        "messages",
        id_column(),
        seller(),
        ref("conversation_id", "conversations", nullable=False, cascade=True),
        text("direction", nullable=False),
        text("sender", nullable=False),
        sa.Column("wa_message_id", sa.Text, unique=True),
        text("type", nullable=False),
        text("text"),
        text("transcript"),
        text("media_key"),
        jsonb("normalized", None, nullable=True),
        jsonb("agent_output", None, nullable=True),
        jsonb("validation", None, nullable=True),
        text("delivery_status"),
        sa.Column("is_replay", sa.Boolean, nullable=False, server_default=sa.false()),
        ref("raw_payload_id", "webhook_payloads"),
        created_at(),
        one_of("messages", "direction", ("inbound", "outbound")),
        one_of("messages", "sender", ("customer", "agent", "seller", "system")),
        one_of("messages", "type", MESSAGE_TYPES),
        sa.CheckConstraint(
            f"delivery_status IS NULL OR delivery_status IN ({', '.join(repr(d) for d in DELIVERY)})",
            name="ck_messages_delivery_status",
        ),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )

    # ── consent ledger (append-only, invariant 4) ──
    op.create_table(
        "consent_records",
        id_column(),
        seller(),
        ref("contact_id", "contacts", nullable=False, cascade=True),
        text("purpose", nullable=False),
        text("status", nullable=False),
        text("text_shown", nullable=False),
        text("response_text", nullable=False),
        text("channel", nullable=False),
        ref("evidence_message_id", "messages"),
        text("recorded_by", nullable=False),
        moment("recorded_at", nullable=False, default_now=True),
        created_at(),
        one_of("consent_records", "purpose", ("marketing", "data_processing")),
        one_of("consent_records", "status", ("granted", "revoked")),
        one_of("consent_records", "channel", ("whatsapp", "in_person", "web", "other")),
        one_of("consent_records", "recorded_by", ("agent", "seller", "system")),
    )
    op.create_index(
        "ix_consent_records_contact", "consent_records", ["contact_id", "purpose", "recorded_at"]
    )
    op.execute(
        """CREATE FUNCTION consent_records_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'consent_records is append-only (FR-060): append a revocation instead';
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER consent_records_append_only BEFORE UPDATE OR DELETE ON consent_records
        FOR EACH ROW EXECUTE FUNCTION consent_records_append_only()"""
    )
    op.execute(
        """CREATE TRIGGER consent_records_no_truncate BEFORE TRUNCATE ON consent_records
        FOR EACH STATEMENT EXECUTE FUNCTION consent_records_append_only()"""
    )

    # ── agent outcomes ──
    op.create_table(
        "handoffs",
        id_column(),
        seller(),
        ref("conversation_id", "conversations", nullable=False, cascade=True),
        text("reason", nullable=False),
        text("summary"),
        text("urgency", nullable=False, default="normal"),
        moment("opened_at", nullable=False, default_now=True),
        moment("released_at"),
        sa.Column("seller_notified", sa.Boolean, nullable=False, server_default=sa.false()),
        created_at(),
        one_of("handoffs", "reason", HANDOFF_REASONS),
        one_of("handoffs", "urgency", ("low", "normal", "high")),
        sa.CheckConstraint(
            "summary IS NULL OR char_length(summary) <= 500", name="ck_handoffs_summary"
        ),
    )

    op.create_table(
        "objections",
        id_column(),
        seller(),
        ref("conversation_id", "conversations", nullable=False, cascade=True),
        ref("ad_id", "ads"),
        ref("product_id", "products"),
        text("tag", nullable=False),
        text("quote_redacted"),
        moment("occurred_at", nullable=False, default_now=True),
        created_at(),
        one_of("objections", "tag", OBJECTION_TAGS),
    )
    op.create_index("ix_objections_seller_occurred", "objections", ["seller_id", "occurred_at"])

    op.create_table(
        "reservations",
        id_column(),
        seller(),
        ref("conversation_id", "conversations"),
        ref("product_id", "products", nullable=False, cascade=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        jsonb("variant"),
        text("code", nullable=False),
        text("status", nullable=False, default="reserved"),
        moment("expires_at", nullable=False),
        created_at(),
        sa.UniqueConstraint("seller_id", "code", name="uq_reservations_seller_code"),
        sa.CheckConstraint("quantity > 0", name="ck_reservations_quantity"),
        one_of("reservations", "status", ("reserved", "released", "fulfilled", "expired")),
    )
    op.create_index(
        "ix_reservations_product_active", "reservations", ["product_id", "status", "expires_at"]
    )
    # data-model.md: products.reserved is a view over active reservations.
    op.execute(
        """CREATE VIEW product_stock AS
        SELECT p.id AS product_id, p.seller_id, p.stock,
               coalesce(sum(r.quantity) FILTER (WHERE r.status = 'reserved' AND r.expires_at > now()), 0)::integer
                 AS reserved
        FROM products p LEFT JOIN reservations r ON r.product_id = p.id
        GROUP BY p.id"""
    )

    op.create_table(
        "bookings",
        id_column(),
        seller(),
        ref("conversation_id", "conversations"),
        text("service", nullable=False),
        moment("starts_at", nullable=False),
        sa.Column("duration_min", sa.Integer, nullable=False, server_default="30"),
        text("customer_name"),
        text("status", nullable=False, default="booked"),
        created_at(),
        sa.CheckConstraint("duration_min BETWEEN 15 AND 240", name="ck_bookings_duration_min"),
    )

    op.create_table(
        "attribution_events",
        id_column(),
        seller(),
        ref("ad_id", "ads"),
        ref("conversation_id", "conversations"),
        text("kind", nullable=False),
        moment("occurred_at", nullable=False, default_now=True),
        created_at(),
        one_of(
            "attribution_events",
            "kind",
            ("chat_started", "reservation", "booking", "handoff", "objection"),
        ),
    )
    op.create_index(
        "ix_attribution_events_seller_ad",
        "attribution_events",
        ["seller_id", "ad_id", "occurred_at"],
    )

    # ── reactivation ──
    op.create_table(
        "reactivation_campaigns",
        id_column(),
        seller(),
        ref("brief_id", "briefs"),
        ref("ad_id", "ads"),
        text("template_name", nullable=False),
        text("template_language", nullable=False, default="es"),
        jsonb("segment"),
        text("status", nullable=False, default="preview"),
        moment("scheduled_for"),
        created_at(),
        one_of(
            "reactivation_campaigns",
            "status",
            ("preview", "scheduled", "sending", "sent", "cancelled"),
        ),
    )

    op.create_table(
        "send_decisions",
        id_column(),
        seller(),
        ref("campaign_id", "reactivation_campaigns", nullable=False, cascade=True),
        ref("contact_id", "contacts", nullable=False, cascade=True),
        text("decision", nullable=False),
        text("reason", nullable=False),
        text("reason_text_es"),
        moment("next_allowed_at"),
        ref("message_id", "messages"),
        moment("evaluated_at", nullable=False, default_now=True),
        created_at(),
        one_of("send_decisions", "decision", ("allowed", "blocked", "deferred", "sent", "failed")),
        one_of("send_decisions", "reason", SEND_REASONS),
    )
    op.create_index("ix_send_decisions_campaign", "send_decisions", ["campaign_id"])
    op.create_index(
        "ix_send_decisions_contact_evaluated", "send_decisions", ["contact_id", "evaluated_at"]
    )

    op.create_table(
        "erasure_receipts",
        id_column(),
        seller(),
        sa.Column("wa_id_hash", postgresql.BYTEA, nullable=False),
        moment("requested_at", nullable=False, default_now=True),
        moment("completed_at"),
        text("scope", nullable=False),
        created_at(),
    )

    # ── platform: jobs, idempotency, ledger, evals, public tool, events ──
    op.create_table(
        "jobs",
        id_column(),
        ref("owner_seller_id", "sellers", cascade=True),
        text("kind", nullable=False),
        text("status", nullable=False, default="queued"),
        text("stage"),
        sa.Column("progress", sa.Numeric(4, 3), nullable=False, server_default="0"),
        text("result_ref"),
        jsonb("error", None, nullable=True),
        text("idempotency_key"),
        moment("started_at"),
        moment("finished_at"),
        created_at(),
        one_of("jobs", "kind", JOB_KINDS),
        one_of("jobs", "status", ("queued", "running", "succeeded", "failed", "cancelled")),
        sa.CheckConstraint("progress BETWEEN 0 AND 1", name="ck_jobs_progress"),
    )
    op.create_index("ix_jobs_owner_created", "jobs", ["owner_seller_id", "created_at"])

    op.create_table(
        "idempotency_keys",
        text("key", nullable=False),
        # The nil UUID scopes keys sent to endpoints without a seller (POST /sellers).
        sa.Column("seller_id", sa.Uuid, nullable=False),
        text("request_hash", nullable=False),
        sa.Column("response_status", sa.Integer),
        jsonb("response_body", None, nullable=True),
        created_at(),
        sa.PrimaryKeyConstraint("seller_id", "key", name="pk_idempotency_keys"),
    )
    op.create_index("ix_idempotency_keys_created", "idempotency_keys", ["created_at"])

    op.create_table(
        "model_calls",
        id_column(),
        seller(nullable=True),
        text("request_id"),
        text("provider", nullable=False),
        text("model", nullable=False),
        text("purpose", nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("replayed", sa.Boolean, nullable=False, server_default=sa.false()),
        created_at(),
        one_of("model_calls", "provider", PROVIDERS),
        one_of("model_calls", "purpose", PURPOSES),
    )
    op.create_index("ix_model_calls_seller_created", "model_calls", ["seller_id", "created_at"])

    op.create_table(
        "eval_runs",
        id_column(),
        text("suite", nullable=False),
        sa.Column("replay", sa.Boolean, nullable=False, server_default=sa.true()),
        text("status", nullable=False, default="queued"),
        sa.Column("case_count", sa.Integer, nullable=False, server_default="0"),
        jsonb("metrics"),
        jsonb("cases", EMPTY_ARRAY),
        text("git_sha"),
        moment("started_at", nullable=False, default_now=True),
        moment("finished_at"),
        created_at(),
        one_of("eval_runs", "suite", SUITES),
        one_of("eval_runs", "status", ("queued", "running", "passed", "failed", "error")),
    )

    op.create_table(
        "public_showcases",
        id_column(),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        text("product_name", nullable=False),
        sa.Column("price_cop", sa.Integer),
        text("city"),
        text("ip_hash", nullable=False),
        text("status", nullable=False, default="processing"),
        jsonb("scenes", EMPTY_ARRAY),
        jsonb("copies", EMPTY_ARRAY),
        created_at(),
        one_of("public_showcases", "status", ("processing", "ready", "failed")),
        sa.CheckConstraint("slug ~ '^[a-z0-9-]{6,40}$'", name="ck_public_showcases_slug"),
    )
    op.create_table(
        "public_rate_limits",
        text("ip_hash", nullable=False),
        moment("window_start", nullable=False),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("ip_hash", "window_start", name="pk_public_rate_limits"),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("seller_id", sa.Uuid),
        text("type", nullable=False),
        jsonb("payload"),
        created_at(),
    )
    op.create_index("ix_events_type_id", "events", ["type", "id"])
    # One database, one schema per machine: the payload names the schema so listeners filter.
    op.execute(
        """CREATE FUNCTION events_notify() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_notify('vitrina_events', TG_TABLE_SCHEMA || ':' || NEW.id);
          RETURN NULL;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER events_notify AFTER INSERT ON events FOR EACH ROW EXECUTE FUNCTION events_notify()"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS product_stock")
    for table in reversed(TABLES):
        op.drop_table(table)
    for function in (
        "events_notify()",
        "consent_records_append_only()",
        "ads_require_passed_creative()",
        "ads_assign_code()",
        "next_ad_code(uuid)",
        "touch_updated_at()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
