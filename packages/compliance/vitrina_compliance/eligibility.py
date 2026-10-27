"""Whether a contact may get a marketing message now (FR-061, ADR-0008; AS-6.2–6.4).

``evaluate`` is pure: the first failing check, in the fixed order OPTED_OUT →
NO_CONSENT_LEY_1581 → FREQUENCY_CAP → OUTSIDE_HOURS_LEY_2300 → NO_MATCHING_AD, decides.
Hours defer to the next legal window; every other check blocks. A contact without consent
is therefore never deferred into a later send (Art. II.1).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import MISSING, BaseModel

from vitrina_compliance.calendar import is_legal, next_legal

FREQUENCY_WINDOW = timedelta(days=7)  # Art. II.3: one marketing message per 7 days

Decision = Literal["allowed", "blocked", "deferred", "sent", "failed"]
Reason = Literal[
    "OK",
    "OPTED_OUT",
    "NO_CONSENT_LEY_1581",
    "FREQUENCY_CAP",
    "OUTSIDE_HOURS_LEY_2300",
    "NO_MATCHING_AD",
    "TEMPLATE_NOT_APPROVED",
    "SEND_FAILED",
]
REASON_TEXT_ES: dict[Reason, str] = {
    "OK": "Permitido",
    "OPTED_OUT": "Se dio de baja: no recibe mensajes de marketing",
    "NO_CONSENT_LEY_1581": "Sin autorización — Ley 1581",
    "FREQUENCY_CAP": "Ya recibió un mensaje de marketing en los últimos 7 días",
    "OUTSIDE_HOURS_LEY_2300": "Fuera del horario permitido — Ley 2300",
    "NO_MATCHING_AD": "No hay un anuncio aprobado para lo que le interesa",
    "TEMPLATE_NOT_APPROVED": "La plantilla de WhatsApp aún no está aprobada",
    "SEND_FAILED": "WhatsApp no entregó el mensaje",
}


@dataclass(frozen=True)
class ContactState:
    """What eligibility reads about a contact; the ledgers that hold it build this."""

    contact_id: UUID
    opted_out: bool
    marketing_consent: bool  # the latest marketing consent record is a grant (FR-060)
    last_marketing_sent_at: datetime | None
    matching_ad_id: UUID | None  # the newest approved ad for the contact's interest


class SendDecision(BaseModel):
    """openapi.yaml ``SendDecision``."""

    id: UUID | MISSING = MISSING
    campaign_id: UUID | MISSING = MISSING
    contact_id: UUID
    decision: Decision
    reason: Reason
    reason_text_es: str | MISSING = MISSING
    next_allowed_at: datetime | None = None
    message_id: UUID | None = None
    evaluated_at: datetime


def evaluate(contact: ContactState, at: datetime) -> SendDecision:
    legal = is_legal(at)  # also refuses a naive time before any other check
    reason: Reason
    if contact.opted_out:
        reason = "OPTED_OUT"
    elif not contact.marketing_consent:
        reason = "NO_CONSENT_LEY_1581"
    elif (
        contact.last_marketing_sent_at is not None
        and at - contact.last_marketing_sent_at < FREQUENCY_WINDOW
    ):
        reason = "FREQUENCY_CAP"
    elif not legal:
        return _decision(contact, at, "deferred", "OUTSIDE_HOURS_LEY_2300", next_legal(at))
    elif contact.matching_ad_id is None:
        reason = "NO_MATCHING_AD"
    else:
        return _decision(contact, at, "allowed", "OK")
    return _decision(contact, at, "blocked", reason)


def _decision(
    contact: ContactState,
    at: datetime,
    decision: Decision,
    reason: Reason,
    next_allowed_at: datetime | None = None,
) -> SendDecision:
    return SendDecision(
        contact_id=contact.contact_id,
        decision=decision,
        reason=reason,
        reason_text_es=REASON_TEXT_ES[reason],
        next_allowed_at=next_allowed_at,
        evaluated_at=at,
    )
