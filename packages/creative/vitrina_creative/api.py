"""Public interface of the creative module (constitution Art. VI.3).

Planned: start_job, approve_ad, get_approved_asset(product_id, variant) (plan.md §3).
Available: the fidelity verifier (T033), also used by the ``fidelity`` eval suite (T072).
"""

from vitrina_creative.verifier import (
    SourceProduct,
    Thresholds,
    Verification,
    VerifierModels,
    VisualReview,
    verify_scene,
)

__all__ = [
    "SourceProduct",
    "Thresholds",
    "Verification",
    "VerifierModels",
    "VisualReview",
    "verify_scene",
]
