"""Creative job resources as the API shows them (openapi.yaml ``CreativeJobCreate``,
``CreativeJob``, ``Creative``, ``Verification``, ``CopyVariant``), shaped for the drift check:
optional and nullable means ``T | None = None``, optional and not nullable means ``MISSING``.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import MISSING, BaseModel, Field

from vitrina_core.jobs import JobErrorDetail
from vitrina_creative.compose import CreativeFormat
from vitrina_creative.copy import CopyAngle, CopyHook

Uri = Annotated[str, Field(json_schema_extra={"format": "uri"})]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobStage = Literal[
    "queued", "segmenting", "composing", "verifying", "copywriting", "scoring", "ready", "failed"
]


class CreativeJobCreate(BaseModel):
    product_id: UUID
    asset_ids: (
        Annotated[list[UUID], Field(description="Defaults to all product images")] | MISSING
    ) = MISSING
    formats: list[CreativeFormat] = ["story_9x16", "catalog_1x1"]
    scenes_per_format: Annotated[int, Field(ge=1, le=5)] = 3
    copy_variants: Annotated[int, Field(ge=1, le=20)] = 10
    angles: list[CopyAngle] | MISSING = MISSING
    scene_brief: Annotated[str, Field(max_length=500)] | MISSING = MISSING
    overlay_text: bool = False
    brief_id: UUID | None = None


class VisualReviewView(BaseModel):
    altered: bool
    # openapi.yaml line 1718 leaves this description unquoted in a YAML flow mapping, which adds
    # a null-valued key that the drift check ignores (#38); amendment A-005 quotes it.
    reason: str | MISSING = MISSING


class ThresholdsView(BaseModel):
    mask_iou_min: float = 0.85
    color_delta_max: float = 0.25


class VerificationView(BaseModel):
    status: Literal["passed", "blocked", "pending"]
    mask_iou: Annotated[float, Field(ge=0, le=1)]
    color_delta: Annotated[float, Field(ge=0)]
    visual_review: VisualReviewView
    thresholds: ThresholdsView
    reason: str | MISSING = MISSING
    checked_at: datetime


class ProvenanceView(BaseModel):
    generator: str | MISSING = MISSING
    segmenter: str | MISSING = MISSING
    ai_generated: bool = True
    label_applied: bool | MISSING = MISSING


class Creative(BaseModel):
    id: UUID
    job_id: UUID
    product_id: UUID
    format: CreativeFormat
    image_url: Uri
    mask_url: Uri | MISSING = MISSING
    source_asset_id: UUID | MISSING = MISSING
    scene_prompt: str | MISSING = MISSING
    verification: VerificationView
    provenance: ProvenanceView | MISSING = MISSING
    created_at: datetime


class CopyVariant(BaseModel):
    id: UUID
    job_id: UUID
    hook: CopyHook
    angle: CopyAngle
    text: Annotated[str, Field(max_length=300)]
    brand_voice_score: Annotated[float, Field(ge=1, le=5)] | None = None
    brand_voice_judge: Literal["glimmer", "spark_rubric", "none"] | MISSING = MISSING
    status: Literal["candidate", "approved", "rejected", "dropped_low_voice"]


class ModelCallMeta(BaseModel):
    """openapi.yaml ``ModelCallMeta`` (FR-084); every property optional."""

    provider: Literal["meta_model_api", "modal", "baseten", "fireworks", "local_mock"] | MISSING = (
        MISSING
    )
    model: str | MISSING = MISSING
    tokens_in: int | MISSING = MISSING
    tokens_out: int | MISSING = MISSING
    latency_ms: int | MISSING = MISSING
    cost_usd: float | MISSING = MISSING
    replayed: bool | MISSING = MISSING


class CreativeJob(BaseModel):
    id: UUID
    seller_id: UUID
    product_id: UUID
    params: CreativeJobCreate | MISSING = MISSING
    status: JobStatus
    stage: JobStage
    progress: Annotated[float, Field(ge=0, le=1)] | MISSING = MISSING
    creatives: list[Creative] | MISSING = MISSING
    copy_variants: list[CopyVariant] | MISSING = MISSING
    # Calls are in the ledger (GET /metrics/costs); per-job attribution is not recorded yet.
    model_calls: list[ModelCallMeta] | MISSING = MISSING
    error: JobErrorDetail | None = None
    created_at: datetime
    finished_at: datetime | None = None
