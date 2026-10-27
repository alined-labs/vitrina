"""Ad attribution from the customer's own words (FR-023, AS-3.2; code format FR-014).

An approved ad's wa.me link pre-fills ``Hola, vi el anuncio #A7``. The first ``#A..`` code
in an inbound message attributes the conversation, and the first attribution stays (first
touch). A code no ad of this seller carries marks the conversation ``unknown_code``, and a
later message can still attribute it. Codes match regardless of case. Ads belong to the
creative module, so the engine gets an ``AdLookup`` and never reads their table.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

AttributionStatus = Literal["attributed", "unknown_code", "none"]
AdLookup = Callable[[UUID, str], Awaitable[UUID | None]]
"""``(seller_id, code) -> ad_id`` for the seller's ad with that code, from creative.api."""

AD_CODE = re.compile(r"#(A[0-9A-Z]{1,3})(?![0-9A-Z])", re.IGNORECASE)


@dataclass(frozen=True)
class Attribution:
    status: AttributionStatus
    ad_id: UUID | None = None
    code: str | None = None


NO_ATTRIBUTION = Attribution("none")


def parse_ad_code(text: str) -> str | None:
    """The first ``#A..`` code in ``text``, upper-cased (``#a7`` → ``A7``)."""
    match = AD_CODE.search(text)
    return match.group(1).upper() if match else None


async def attribute(
    current: Attribution, text: str, seller_id: UUID, lookup: AdLookup
) -> tuple[Attribution, bool]:
    """Fold one inbound message into the conversation's attribution.

    The flag is True when this message attributed the conversation, the moment to emit
    ``ad.chat_started``.
    """
    if current.status == "attributed":
        return current, False
    code = parse_ad_code(text)
    if code is None:
        return current, False
    ad_id = await lookup(seller_id, code)
    if ad_id is None:
        return Attribution("unknown_code", None, code), False
    return Attribution("attributed", ad_id, code), True


def chat_started_payload(
    ad_id: UUID, conversation_id: UUID, contact_id: UUID, occurred_at: datetime
) -> dict[str, str]:
    """data-model.md §4 ``ad.chat_started``."""
    return {
        "ad_id": str(ad_id),
        "conversation_id": str(conversation_id),
        "contact_id": str(contact_id),
        "occurred_at": occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
