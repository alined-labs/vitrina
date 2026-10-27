"""Settings from the environment and ``.env`` (T005). ``.env.example`` lists every name.

Defaults are the safe ones: replay mode, the local WhatsApp mock, no keys. Model IDs stay
unset until they are confirmed against the Meta Model API (spec.md §8); ``model_id()``
fails loudly instead of guessing one.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

ModelRole = Literal["agent", "strategist", "image", "segment", "transcribe", "embed"]


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

    # Cost-ledger alert (plan.md §6).
    spend_limit_usd: float = 100.0

    # Thresholds and limits.
    mask_iou_min: float = 0.85  # FR-012
    color_delta_max: float = 0.25  # FR-012
    outbound_spacing_s: float = 6.0  # R12, Art. VIII.2
    provider_attempts: int = 3  # R12
    provider_timeout_s: float = 20.0  # Art. VIII.1
    agent_max_tool_rounds: int = 4  # plan.md §4.1
    agent_turn_budget_s: float = 20.0  # plan.md §4.1
    retrieval_top_k: int = 5  # plan.md §4.1
    idempotency_ttl_hours: int = 24  # data-model.md idempotency_keys

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
