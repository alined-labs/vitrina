"""Types shared by every provider interface."""

import hashlib
from functools import cached_property
from typing import Literal

from pydantic import BaseModel, ConfigDict

# openapi.yaml ModelCallMeta.provider and data-model.md model_calls.purpose.
ProviderName = Literal["meta_model_api", "modal", "baseten", "fireworks", "local_mock"]
ModelPurpose = Literal[
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
]


class Frozen(BaseModel):
    """Immutable request/response model; bytes travel as base64 in JSON (cassettes)."""

    model_config = ConfigDict(frozen=True, ser_json_bytes="base64", val_json_bytes="base64")


class Blob(Frozen):
    """Binary payload: an image, an audio clip or a mask."""

    data: bytes
    mime_type: str

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class CallMeta(Frozen):
    """What the model-call ledger records for every call (FR-084, openapi.yaml ModelCallMeta).

    ``cost_usd`` is None when the provider does not report a price (research question on
    per-model pricing); the ledger then stores 0.
    """

    provider: ProviderName
    model: str
    purpose: ModelPurpose
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float | None = None
    replayed: bool = False
