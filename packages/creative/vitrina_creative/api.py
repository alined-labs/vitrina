"""Public interface of the creative module (constitution Art. VI.3).

Planned: start_job, approve_ad, get_approved_asset(product_id, variant) (plan.md §3).
Available: the segment stage (T031) and the fidelity verifier (T033), which the ``fidelity``
eval suite (T072) also uses.
"""

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
    "SegmentationError",
    "SegmentedPhoto",
    "SourceProduct",
    "Thresholds",
    "Verification",
    "VerifierModels",
    "VisualReview",
    "segment_photo",
    "verify_scene",
]
