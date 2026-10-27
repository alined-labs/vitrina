"""Public interface of the creative module (constitution Art. VI.3).

Planned: start_job, approve_ad, get_approved_asset(product_id, variant) (plan.md §3).
Available: creative jobs (T034), assets (T030), the segment stage (T031), the compose stage (T032), the copy grid (T040), the brand-voice judge (T041), the
fidelity verifier (T033, also used by the ``fidelity`` eval suite, T072) and provenance with
the visible AI label (Art. IV.3).
"""

from vitrina_creative.assets import (
    Asset,
    AssetRecord,
    AssetStore,
    InMemoryAssetStore,
    PostgresAssetStore,
    asset_view,
    assets_from_state,
    list_asset_ids,
    upload_asset,
)
from vitrina_creative.compose import FORMAT_SIZES, ComposedScene, CreativeFormat, compose_scenes
from vitrina_creative.copy import (
    BrandVoice,
    CopyDraft,
    CopyError,
    CopyInput,
    CopyResult,
    DroppedCell,
    plan_grid,
    write_copy,
)
from vitrina_creative.jobs import CreativeJobStore, creative_view, job_view
from vitrina_creative.judge import BRAND_VOICE_MIN, RubricJudge, VoicedVariant, judge_copy
from vitrina_creative.media import MediaRejectedError
from vitrina_creative.pipeline import PipelineInput, PipelineResult, run_pipeline
from vitrina_creative.provenance import (
    AI_LABEL,
    Provenance,
    ai_label,
    embed_provenance,
    provenance_json,
    provenance_record,
    read_provenance,
)
from vitrina_creative.schemas import Creative, CreativeJob, CreativeJobCreate
from vitrina_creative.segment import SegmentationError, SegmentedPhoto, segment_photo
from vitrina_creative.start import job_payload, start_job
from vitrina_creative.tasks import CREATIVE_TASK
from vitrina_creative.verifier import (
    SourceProduct,
    Thresholds,
    Verification,
    VerifierModels,
    VisualReview,
    verify_scene,
)

__all__ = [
    "AI_LABEL",
    "BRAND_VOICE_MIN",
    "CREATIVE_TASK",
    "FORMAT_SIZES",
    "Asset",
    "AssetRecord",
    "AssetStore",
    "BrandVoice",
    "ComposedScene",
    "CopyDraft",
    "CopyError",
    "CopyInput",
    "CopyResult",
    "Creative",
    "CreativeFormat",
    "CreativeJob",
    "CreativeJobCreate",
    "CreativeJobStore",
    "DroppedCell",
    "InMemoryAssetStore",
    "MediaRejectedError",
    "PipelineInput",
    "PipelineResult",
    "PostgresAssetStore",
    "Provenance",
    "RubricJudge",
    "SegmentationError",
    "SegmentedPhoto",
    "SourceProduct",
    "Thresholds",
    "Verification",
    "VerifierModels",
    "VisualReview",
    "VoicedVariant",
    "ai_label",
    "asset_view",
    "assets_from_state",
    "compose_scenes",
    "creative_view",
    "embed_provenance",
    "job_payload",
    "job_view",
    "judge_copy",
    "list_asset_ids",
    "plan_grid",
    "provenance_json",
    "provenance_record",
    "read_provenance",
    "run_pipeline",
    "segment_photo",
    "start_job",
    "upload_asset",
    "verify_scene",
    "write_copy",
]
