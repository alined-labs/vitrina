"""Public interface of the compliance module (constitution Art. II, Art. VI.3).

Eligibility (T063): ``evaluate(contact, at) -> SendDecision`` decides whether a contact may
get a marketing message at ``at``, in the fixed order of FR-061 (ADR-0008); the calendar
helpers answer when the next legal window opens (Ley 2300). Consent ledger, opt-out and
erasure (T060–T062) and campaigns (T064) join this interface as they land.
"""

from vitrina_compliance.calendar import BOGOTA, is_holiday, is_legal, next_legal, window
from vitrina_compliance.eligibility import (
    FREQUENCY_WINDOW,
    REASON_TEXT_ES,
    ContactState,
    Decision,
    Reason,
    SendDecision,
    evaluate,
)

__all__ = [
    "BOGOTA",
    "FREQUENCY_WINDOW",
    "REASON_TEXT_ES",
    "ContactState",
    "Decision",
    "Reason",
    "SendDecision",
    "evaluate",
    "is_holiday",
    "is_legal",
    "next_legal",
    "window",
]
