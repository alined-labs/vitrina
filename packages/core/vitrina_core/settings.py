"""Settings from the environment and ``.env`` (T005). ``.env.example`` lists every name.

Defaults are the safe ones: replay mode, the local WhatsApp mock, no keys. Model IDs stay
unset until they are confirmed against the Meta Model API (spec.md §8); ``model_id()``
fails loudly instead of guessing one.
"""

import hmac
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vitrina_core.pricing import META_LIST_PRICES, ModelPrice

REPO_ROOT = Path(__file__).resolve().parents[3]

ModelRole = Literal["agent", "strategist", "image", "segment", "transcribe", "embed"]
# Muse Spark and Wonder reason before they answer; "none" is refused (400) by both (T011).
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Replay: providers answer from recorded cassettes; no keys, no network (AS-7.3, NFR-07).
    replay: bool = True
    record_cassettes: bool = False
    cassette_dir: Path = REPO_ROOT / "evals" / "cassettes"

    # Hosted Supabase Postgres, one schema per machine (plan.md §3).
    database_url: SecretStr | None = None
    db_schema: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")

    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None
    service_token: SecretStr | None = None

    # Blob storage (I-15): local files without Supabase (tests, replay, B8), else one private
    # Supabase Storage bucket. Local signed URLs are served by the API at PUBLIC_API_URL.
    storage_backend: Literal["local", "supabase"] = "local"
    storage_dir: Path = REPO_ROOT / "tmp" / "storage"
    storage_bucket: str = "vitrina"
    signed_url_ttl_s: int = Field(default=900, gt=0)
    public_api_url: str = "http://localhost:8000"
    # The dashboard's public address, for share links and sign-up links (T083).
    public_web_url: str = "http://localhost:3000"
    # Browser origins allowed to call the API directly, comma-separated (the deployed dashboard).
    # Unset: no cross-origin headers; a same-origin proxy needs none.
    cors_origins: str | None = None
    # Shared with the dashboard's API proxy, which sends it next to the visitor's address
    # (X-Vitrina-Visitor, T083). Unset or blank: no request can vouch for a visitor.
    visitor_proxy_secret: SecretStr | None = None

    # Meta Model API, OpenAI-compatible (plan.md §1, ADR-0003).
    meta_api_base_url: str = "https://api.meta.ai/v1"
    meta_api_key: SecretStr | None = None
    model_agent: str | None = None
    model_strategist: str | None = None
    model_image: str | None = None
    model_segment: str | None = None
    model_transcribe: str | None = None
    model_embed: str | None = None

    # WhatsApp Cloud API; the mock answers whenever whatsapp_mock is on (ADR-0011).
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_mock: bool = True
    mock_outbox_path: Path = REPO_ROOT / "tmp" / "mock-whatsapp.jsonl"

    modal_glimmer_url: str | None = None
    baseten_api_key: SecretStr | None = None
    baseten_model: str = "meta-models/muse-spark-open-1026"

    # contacts.wa_id_hash (HMAC pepper) and wa_id_enc (encryption key), data-model.md §5.
    contact_hash_pepper: SecretStr | None = None
    contact_encryption_key: SecretStr | None = None

    # Cost-ledger alert (plan.md §6), counted at the list prices per model ID
    # (``vitrina_core.pricing``); MODEL_PRICES (JSON) replaces the whole table.
    spend_limit_usd: float = 100.0
    model_prices: dict[str, ModelPrice] = Field(default_factory=lambda: dict(META_LIST_PRICES))

    # Thresholds and limits.
    mask_iou_min: float = 0.85  # FR-012
    color_delta_max: float = 0.25  # FR-012
    outbound_spacing_s: float = 6.0  # R12, Art. VIII.2
    provider_attempts: int = 3  # R12
    provider_timeout_s: float = 20.0  # Art. VIII.1
    # Chat calls in background jobs: a live copy grid took 31 s (2026-10-28). Turns keep
    # provider_timeout_s inside their own budget.
    provider_job_timeout_s: float = 90.0
    # GET /health/providers: each check's limit, and how long a report is reused.
    health_check_timeout_s: float = 5.0
    health_cache_s: float = 30.0
    agent_max_tool_rounds: int = 4  # plan.md §4.1
    agent_turn_budget_s: float = 20.0  # plan.md §4.1
    # Spark's hidden reasoning is most of a turn's time: "low" kept the cited answers at about
    # 8.8 s p50 live, and the three-key scope check needs the least (2026-10-28).
    agent_reasoning_effort: ReasoningEffort = "low"
    scope_reasoning_effort: ReasoningEffort = "minimal"
    retrieval_top_k: int = 5  # plan.md §4.1
    idempotency_ttl_hours: int = 24  # data-model.md idempotency_keys

    @field_validator(
        "visitor_proxy_secret",
        "whatsapp_app_secret",
        "whatsapp_verify_token",
        "db_schema",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `VISITOR_PROXY_SECRET=` in .env must not become a secret that an empty header matches,
        # nor a blank app secret a key that anyone can sign webhooks with; and the blank
        # `DB_SCHEMA=` that .env.example ships means no schema, not an invalid one.
        return None if isinstance(value, str) and not value.strip() else value

    def visitor_proxy_matches(self, presented: str | None) -> bool:
        """Whether a request carries the dashboard proxy's secret; never while it is unset."""
        if self.visitor_proxy_secret is None or not presented:
            return False
        expected = self.visitor_proxy_secret.get_secret_value()
        return hmac.compare_digest(presented.encode(), expected.encode())

    def model_id(self, role: ModelRole) -> str:
        value: str | None = getattr(self, f"model_{role}")
        if not value:
            raise LookupError(
                f"MODEL_{role.upper()} is not set; model IDs are configuration (spec.md §8)"
            )
        return value

    def async_database_url(self) -> str:
        if self.database_url is None:
            raise LookupError(
                "DATABASE_URL is not set; use the hosted Supabase project (plan.md §3)"
            )
        url = self.database_url.get_secret_value()
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url.removeprefix(prefix)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
