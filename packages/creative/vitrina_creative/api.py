"""Public interface of the creative module (constitution Art. VI.3).

Planned: start_job, approve_ad, get_approved_asset(product_id, variant) (plan.md §3).
Available: the segment stage (T031), the copy grid (T040), the brand-voice judge (T041) and the
fidelity verifier (T033), which the ``fidelity`` eval suite (T072) also uses.
"""

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
    "BRAND_VOICE_MIN",
    "BrandVoice",
    "CopyDraft",
    "CopyError",
    "CopyInput",
    "CopyResult",
    "DroppedCell",
    "RubricJudge",
    "SegmentationError",
    "SegmentedPhoto",
    "SourceProduct",
    "Thresholds",
    "Verification",
    "VerifierModels",
    "VisualReview",
    "VoicedVariant",
    "judge_copy",
    "plan_grid",
    "segment_photo",
    "verify_scene",
    "write_copy",
]
