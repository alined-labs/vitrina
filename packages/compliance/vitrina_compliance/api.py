"""Public interface of the compliance module (constitution Art. II, Art. VI.3).

For the conversation engine (I-12): run ``is_opt_out_request`` and ``is_erasure_request`` on
every inbound text before anything else (R7, FR-063); ``store.resolve_contact`` gives the
contact for a WhatsApp number; ``record_consent`` (R8 checked here) and ``opt_out`` write
the ledger; ``store.is_opted_out`` comes before any marketing; ``request_erasure`` answers
``BORRAR MIS DATOS``, and each module erases its own part through the ``vitrina.erasure``
entry point. For campaigns: ``evaluate(contact, at) -> SendDecision`` in the fixed order of
FR-061 (ADR-0008) with the Ley 2300 calendar; campaigns themselves (T064) join later.
"""

from vitrina_compliance.calendar import BOGOTA, is_holiday, is_legal, next_legal, window
from vitrina_compliance.consents import (
    ComplianceStore,
    ConsentRecord,
    ConsentRecordCreate,
    ConsentRefusedError,
    ErasureReceipt,
    InMemoryCompliance,
    PostgresCompliance,
    opt_out,
    record_consent,
)
from vitrina_compliance.contacts import Contact, ContactIdentity, MarketingConsent, mask_phone
from vitrina_compliance.eligibility import (
    FREQUENCY_WINDOW,
    REASON_TEXT_ES,
    ContactState,
    Decision,
    Reason,
    SendDecision,
    evaluate,
)
from vitrina_compliance.erasure import ERASERS_ENTRY_POINT, ERASURE_TASK, request_erasure
from vitrina_compliance.keywords import is_affirmative, is_erasure_request, is_opt_out_request

__all__ = [
    "BOGOTA",
    "ERASERS_ENTRY_POINT",
    "ERASURE_TASK",
    "FREQUENCY_WINDOW",
    "REASON_TEXT_ES",
    "ComplianceStore",
    "ConsentRecord",
    "ConsentRecordCreate",
    "ConsentRefusedError",
    "Contact",
    "ContactIdentity",
    "ContactState",
    "Decision",
    "ErasureReceipt",
    "InMemoryCompliance",
    "MarketingConsent",
    "PostgresCompliance",
    "Reason",
    "SendDecision",
    "evaluate",
    "is_affirmative",
    "is_erasure_request",
    "is_holiday",
    "is_legal",
    "is_opt_out_request",
    "mask_phone",
    "next_legal",
    "opt_out",
    "record_consent",
    "request_erasure",
    "window",
]
