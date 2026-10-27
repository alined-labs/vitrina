"""Public interface of the creative module (constitution Art. VI.3).

Planned: start_job, approve_ad, get_approved_asset(product_id, variant) (plan.md §3).
Available: assets (T030), the segment stage (T031), the copy grid (T040), the brand-voice judge (T041), the
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
from vitrina_creative.judge import BRAND_VOICE_MIN, RubricJudge, VoicedVariant, judge_copy
from vitrina_creative.media import MediaRejectedError
from vitrina_creative.provenance import (
    AI_LABEL,
    Provenance,
    ai_label,
    embed_provenance,
    provenance_json,
    provenance_record,
    read_provenance,
)
from vitrina_creative.segment import SegmentationError, SegmentedPhoto, segment_photo
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
    "Asset",
    "AssetRecord",
    "AssetStore",
    "BrandVoice",
    "CopyDraft",
    "CopyError",
    "CopyInput",
    "CopyResult",
    "DroppedCell",
    "InMemoryAssetStore",
    "MediaRejectedError",
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
    "embed_provenance",
    "judge_copy",
    "list_asset_ids",
    "plan_grid",
    "provenance_json",
    "provenance_record",
    "read_provenance",
    "segment_photo",
    "upload_asset",
    "verify_scene",
    "write_copy",
]
