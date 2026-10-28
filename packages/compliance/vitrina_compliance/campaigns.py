"""Reactivation campaigns (T064; AS-6.7, FR-061, FR-064; ADR-0008; Art. II).

A campaign starts as a preview: every contact the segment finds gets a ``SendDecision`` from
``evaluate``, at the scheduled time (the next legal window by default). Nothing is sent until
``schedule_send``, which needs the seller's approved marketing template. ``run_send`` (the
``campaign_send`` job) evaluates each contact again at send time; only ``allowed`` contacts get
the template, personalised to their last objection and the newest approved ad for what they
asked about. A contact blocked in the preview stays blocked for this campaign; a deferred one
waits for its ``next_allowed_at``. Every evaluation is kept (FR-064), and the campaign shows
the latest decision per contact.

Who the candidates are, which ad matches and where the sent message is recorded belong to
other modules; they arrive as the callables in ``CampaignDeps`` (Art. VI.3).
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.calendar import is_legal, next_legal
from vitrina_compliance.consents import ComplianceStore, InMemoryCompliance
from vitrina_compliance.contacts import ObjectionTag
from vitrina_compliance.eligibility import (
    REASON_TEXT_ES,
    ContactState,
    Decision,
    SendDecision,
    evaluate,
)
from vitrina_compliance.tables import contacts, reactivation_campaigns, send_decisions
from vitrina_core.http import keyset_page, next_cursor
from vitrina_core.ids import new_id
from vitrina_core.providers import Messenger, OutboundTemplate

CAMPAIGN_SEND = "compliance.campaign_send"
CampaignStatus = Literal["preview", "scheduled", "sending", "sent", "cancelled"]
ConflictCode = Literal["TEMPLATE_NOT_APPROVED", "CAMPAIGN_ALREADY_SENT"]

# The template's name in WhatsApp Manager, language es (Q04).
TEMPLATE_NAME = "vitrina_reactivacion"
# The approved marketing template (Q04: submitted by Daniel in WhatsApp Manager). Four body
# parameters, no price, no claim about stock: the objection line only invites a reply.
TEMPLATE_BODY = (
    "Hola, te escribe {{1}}. Hace poco nos preguntaste por {{2}}. {{3}} "
    "Si quieres retomarlo, responde este mensaje o escribe el código #{{4}}.\n\n"
    "Si no quieres recibir más mensajes, responde BAJA."
)
OBJECTION_LINES: dict[ObjectionTag | None, str] = {
    "price": "Si el precio te hizo dudar, escríbenos y lo miramos contigo.",
    "shipping": "Si el envío te hizo dudar, cuéntanos tu ciudad y te contamos cómo llega.",
    "size": "Si dudabas del tamaño, te ayudamos a escoger.",
    "availability": "Si no encontraste lo que buscabas, pregúntanos otra vez.",
    "trust": "Si tienes dudas sobre cómo comprar, te contamos paso a paso.",
    "timing": "Cuando tengas un momento, aquí seguimos.",
    "payment": "Si el pago te frenó, te contamos qué medios recibimos.",
    "off_topic": "Aquí seguimos para ayudarte.",
    "other": "Aquí seguimos para ayudarte.",
    None: "Aquí seguimos para ayudarte.",
}
ANY_PRODUCT = "lo que tenemos"


class Segment(BaseModel):
    """openapi.yaml ``ReactivationCampaignCreate.segment``; every filter is optional."""

    objection_tags: list[ObjectionTag] | MISSING = MISSING
    no_purchase_since_days: Annotated[int, Field(ge=1)] = 3
    max_contacts: Annotated[int, Field(ge=1, le=500)] = 100


class ReactivationCampaignCreate(BaseModel):
    brief_id: UUID | None = None
    template_name: str
    template_language: str = "es"
    segment: Segment | MISSING = MISSING
    ad_id: UUID | None = None
    scheduled_for: datetime | None = None


class CampaignCounts(BaseModel):
    allowed: int
    blocked: int
    deferred: int
    sent: int
    failed: int


class ReactivationCampaign(BaseModel):
    """openapi.yaml ``ReactivationCampaign``: ``decisions`` is the latest one per contact."""

    id: UUID
    seller_id: UUID
    brief_id: UUID | None = None
    ad_id: UUID | None = None
    template_name: str
    status: CampaignStatus
    scheduled_for: datetime | None = None
    decisions: list[SendDecision]
    counts: CampaignCounts
    created_at: datetime


@dataclass(frozen=True)
class Candidate:
    """A contact the segment found, with what the message is about (conversation's data)."""

    contact_id: UUID
    objection_tag: ObjectionTag | None = None
    product_id: UUID | None = None
    product_name: str | None = None


@dataclass(frozen=True)
class AdRef:
    ad_id: UUID
    code: str


@dataclass(frozen=True)
class CampaignRow:
    id: UUID
    seller_id: UUID
    template_name: str
    template_language: str
    segment: Segment
    status: CampaignStatus
    scheduled_for: datetime | None
    created_at: datetime
    brief_id: UUID | None = None
    ad_id: UUID | None = None


class CandidateSource(Protocol):
    async def __call__(
        self,
        seller_id: UUID,
        segment: Segment,
        *,
        now: datetime,
        contact_ids: Sequence[UUID] | None = None,
    ) -> Sequence[Candidate]:
        """The segment's contacts; with ``contact_ids``, those contacts whatever the segment."""
        ...


class AdFinder(Protocol):
    async def __call__(
        self, seller_id: UUID, product_id: UUID, *, ad_id: UUID | None = None
    ) -> AdRef | None:
        """The newest approved ad of the product, or ``ad_id`` if it is one of them."""
        ...


class Outbox(Protocol):
    async def __call__(self, seller_id: UUID, contact_id: UUID, *, text: str, wa_id: str) -> UUID:
        """Records the sent template in the contact's chat; returns the message id."""
        ...


class CampaignStore(Protocol):
    async def create(self, row: CampaignRow, decisions: Sequence[SendDecision]) -> None: ...

    async def row(self, seller_id: UUID, campaign_id: UUID) -> CampaignRow | None: ...

    async def get(self, seller_id: UUID, campaign_id: UUID) -> ReactivationCampaign | None: ...

    async def page(
        self, seller_id: UUID, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ReactivationCampaign], str | None]: ...

    async def set_status(
        self, seller_id: UUID, campaign_id: UUID, status: CampaignStatus, *, expect: CampaignStatus
    ) -> bool:
        """Moves the campaign only from ``expect``; False when it was elsewhere."""
        ...

    async def add_decisions(self, campaign_id: UUID, decisions: Sequence[SendDecision]) -> None: ...

    async def marketing_sent(self, seller_id: UUID, contact_id: UUID, at: datetime) -> None: ...


@dataclass(frozen=True)
class CampaignDeps:
    store: CampaignStore
    compliance: ComplianceStore
    candidates: CandidateSource
    ads: AdFinder
    messenger: Messenger | None = None
    outbox: Outbox | None = None
    pace: Callable[[str], Awaitable[None]] | None = None
    # A send pass reads it for every contact, and again after the pacing wait, so a pass that
    # runs past 19:00 defers the rest (Ley 2300). None checks the whole pass at its ``now``.
    clock: Callable[[], datetime] | None = None


@dataclass(frozen=True)
class SendRun:
    campaign: ReactivationCampaign
    next_at: datetime | None  # the earliest deferred contact: run again then


class CampaignConflictError(Exception):
    def __init__(self, code: ConflictCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def render_template(
    business_name: str, product_name: str | None, tag: ObjectionTag | None, ad_code: str
) -> tuple[tuple[dict[str, Any], ...], str]:
    """The template's components and the text the customer reads, for the chat record."""
    values = (business_name, product_name or ANY_PRODUCT, OBJECTION_LINES[tag], ad_code)
    text = TEMPLATE_BODY
    for number, value in enumerate(values, start=1):
        text = text.replace("{{" + str(number) + "}}", value)
    body = {"type": "body", "parameters": [{"type": "text", "text": value} for value in values]}
    return (body,), text


async def preview_campaign(
    deps: CampaignDeps, seller_id: UUID, data: ReactivationCampaignCreate, *, now: datetime
) -> ReactivationCampaign:
    at = data.scheduled_for or (now if is_legal(now) else next_legal(now))
    segment = data.segment if isinstance(data.segment, Segment) else Segment()
    row = CampaignRow(
        id=new_id(),
        seller_id=seller_id,
        template_name=data.template_name,
        template_language=data.template_language,
        segment=segment,
        status="preview",
        scheduled_for=at,
        created_at=now,
        brief_id=data.brief_id,
        ad_id=data.ad_id,
    )
    found = await deps.candidates(seller_id, segment, now=now)
    decisions = []
    for candidate in found:  # the source applies max_contacts
        state, _ = await _state(deps, seller_id, candidate, row.ad_id)
        decisions.append(_for(evaluate(state, at), row.id))
    await deps.store.create(row, decisions)
    campaign = await deps.store.get(seller_id, row.id)
    assert campaign is not None
    return campaign


async def check_eligibility(
    deps: CampaignDeps, seller_id: UUID, contact_id: UUID, *, at: datetime
) -> SendDecision | None:
    """``POST /reactivation/eligibility``: one contact now; None for an unknown contact."""
    if await deps.compliance.contact(seller_id, contact_id) is None:
        return None
    found = await deps.candidates(seller_id, Segment(), now=at, contact_ids=[contact_id])
    candidate = found[0] if found else Candidate(contact_id)
    state, _ = await _state(deps, seller_id, candidate, None)
    return evaluate(state, at)


async def schedule_send(
    store: CampaignStore, seller_id: UUID, campaign_id: UUID, *, template_approved: bool
) -> ReactivationCampaign:
    """``POST …/send``: the preview becomes ``scheduled``; the caller queues the job."""
    row = await store.row(seller_id, campaign_id)
    if row is None:
        raise LookupError(f"no campaign {campaign_id} for this seller")
    if not template_approved:
        raise CampaignConflictError(
            "TEMPLATE_NOT_APPROVED",
            "the seller's marketing template is not approved in WhatsApp Manager yet",
        )
    if not await store.set_status(seller_id, campaign_id, "scheduled", expect="preview"):
        raise CampaignConflictError("CAMPAIGN_ALREADY_SENT", f"the campaign is {row.status}")
    campaign = await store.get(seller_id, campaign_id)
    assert campaign is not None
    return campaign


async def run_send(
    deps: CampaignDeps,
    seller_id: UUID,
    campaign_id: UUID,
    *,
    business_name: str,
    now: datetime,
) -> SendRun:
    """One pass of the ``campaign_send`` job; ``next_at`` says when to run again."""
    row = await deps.store.row(seller_id, campaign_id)
    campaign = await deps.store.get(seller_id, campaign_id)
    if row is None or campaign is None:
        raise LookupError(f"no campaign {campaign_id} for this seller")
    if row.status not in ("scheduled", "sending"):
        return SendRun(campaign, None)
    if row.status == "scheduled":
        await deps.store.set_status(seller_id, campaign_id, "sending", expect="scheduled")
    waiting = [d.contact_id for d in campaign.decisions if d.decision in ("allowed", "deferred")]
    found = await deps.candidates(seller_id, row.segment, now=now, contact_ids=waiting)
    by_contact = {candidate.contact_id: candidate for candidate in found}
    next_at: datetime | None = None
    for contact_id in waiting:
        candidate = by_contact.get(contact_id, Candidate(contact_id))
        state, ad = await _state(deps, seller_id, candidate, row.ad_id)
        at = deps.clock() if deps.clock is not None else now
        decision = _for(evaluate(state, at), campaign_id)
        if decision.decision == "allowed" and ad is not None:
            decision = await _send(deps, row, candidate, ad, state, decision, business_name, at)
        if decision.decision == "deferred" and decision.next_allowed_at is not None:
            next_at = min(next_at or decision.next_allowed_at, decision.next_allowed_at)
        await deps.store.add_decisions(campaign_id, [decision])
    if next_at is None:
        await deps.store.set_status(seller_id, campaign_id, "sent", expect="sending")
    done = await deps.store.get(seller_id, campaign_id)
    assert done is not None
    return SendRun(done, next_at)


async def _send(
    deps: CampaignDeps,
    row: CampaignRow,
    candidate: Candidate,
    ad: AdRef,
    state: ContactState,
    decision: SendDecision,
    business_name: str,
    now: datetime,
) -> SendDecision:
    number = await deps.compliance.phone(row.seller_id, candidate.contact_id)
    if deps.messenger is None or number is None:
        return _failed(decision)
    if deps.pace is not None:
        await deps.pace(number)
    if deps.clock is not None:  # the wait may have run past the window
        now = deps.clock()
        again = _for(evaluate(state, now), row.id)
        if again.decision != "allowed":
            return again
    components, text = render_template(
        business_name, candidate.product_name, candidate.objection_tag, ad.code
    )
    result = await deps.messenger.send(
        OutboundTemplate(
            to=number,
            template_name=row.template_name,
            language=row.template_language,
            components=components,
        )
    )
    if result.status == "failed":
        return _failed(decision)
    await deps.store.marketing_sent(row.seller_id, candidate.contact_id, now)
    message_id = None
    if deps.outbox is not None:
        message_id = await deps.outbox(
            row.seller_id, candidate.contact_id, text=text, wa_id=result.message_id
        )
    return decision.model_copy(update={"decision": "sent", "message_id": message_id})


def _failed(decision: SendDecision) -> SendDecision:
    return decision.model_copy(
        update={
            "decision": "failed",
            "reason": "SEND_FAILED",
            "reason_text_es": REASON_TEXT_ES["SEND_FAILED"],
        }
    )


async def _state(
    deps: CampaignDeps, seller_id: UUID, candidate: Candidate, ad_id: UUID | None
) -> tuple[ContactState, AdRef | None]:
    contact = await deps.compliance.contact(seller_id, candidate.contact_id)
    ad = None
    if candidate.product_id is not None:
        ad = await deps.ads(seller_id, candidate.product_id, ad_id=ad_id)
    state = ContactState(
        contact_id=candidate.contact_id,
        # An unknown contact is never written to; erasure also opts the contact out.
        opted_out=contact is None or contact.opted_out_at is not None,
        marketing_consent=contact is not None and contact.marketing_consent == "granted",
        last_marketing_sent_at=contact.last_marketing_sent_at if contact else None,
        matching_ad_id=ad.ad_id if ad else None,
    )
    return state, ad


def _for(decision: SendDecision, campaign_id: UUID) -> SendDecision:
    return decision.model_copy(update={"id": new_id(), "campaign_id": campaign_id})


def _counts(decisions: Sequence[SendDecision]) -> CampaignCounts:
    tally: dict[Decision, int] = {"allowed": 0, "blocked": 0, "deferred": 0, "sent": 0, "failed": 0}
    for decision in decisions:
        tally[decision.decision] += 1
    return CampaignCounts(**tally)


def _latest(history: Sequence[SendDecision]) -> list[SendDecision]:
    latest: dict[UUID, SendDecision] = {}
    for decision in history:  # oldest first: a later evaluation replaces an earlier one
        latest[decision.contact_id] = decision
    return list(latest.values())


def _view(row: CampaignRow, history: Sequence[SendDecision]) -> ReactivationCampaign:
    decisions = _latest(history)
    return ReactivationCampaign(
        id=row.id,
        seller_id=row.seller_id,
        brief_id=row.brief_id,
        ad_id=row.ad_id,
        template_name=row.template_name,
        status=row.status,
        scheduled_for=row.scheduled_for,
        decisions=decisions,
        counts=_counts(decisions),
        created_at=row.created_at,
    )


@dataclass
class InMemoryCampaigns:
    """For tests and machines without a database; marks contacts in ``compliance``."""

    compliance: InMemoryCompliance
    rows: dict[UUID, CampaignRow] = field(default_factory=dict)
    decisions: dict[UUID, list[SendDecision]] = field(default_factory=dict)

    def history(self, campaign_id: UUID) -> list[SendDecision]:
        return list(self.decisions.get(campaign_id, []))

    async def create(self, row: CampaignRow, decisions: Sequence[SendDecision]) -> None:
        self.rows[row.id] = row
        self.decisions[row.id] = list(decisions)

    async def row(self, seller_id: UUID, campaign_id: UUID) -> CampaignRow | None:
        row = self.rows.get(campaign_id)
        return row if row is not None and row.seller_id == seller_id else None

    async def get(self, seller_id: UUID, campaign_id: UUID) -> ReactivationCampaign | None:
        row = await self.row(seller_id, campaign_id)
        return _view(row, self.decisions[row.id]) if row is not None else None

    async def page(
        self, seller_id: UUID, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ReactivationCampaign], str | None]:
        rows = sorted(
            (row for row in self.rows.values() if row.seller_id == seller_id),
            key=lambda row: (row.created_at, row.id),
            reverse=True,
        )
        start = int(cursor) if cursor else 0
        page = rows[start : start + limit]
        more = str(start + limit) if start + limit < len(rows) else None
        return [_view(row, self.decisions[row.id]) for row in page], more

    async def set_status(
        self, seller_id: UUID, campaign_id: UUID, status: CampaignStatus, *, expect: CampaignStatus
    ) -> bool:
        row = await self.row(seller_id, campaign_id)
        if row is None or row.status != expect:
            return False
        self.rows[campaign_id] = replace(row, status=status)
        return True

    async def add_decisions(self, campaign_id: UUID, decisions: Sequence[SendDecision]) -> None:
        self.decisions[campaign_id].extend(decisions)

    async def marketing_sent(self, seller_id: UUID, contact_id: UUID, at: datetime) -> None:
        self.compliance._row(seller_id, contact_id).last_marketing_sent_at = at


class PostgresCampaigns:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create(self, row: CampaignRow, decisions: Sequence[SendDecision]) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.insert(reactivation_campaigns).values(
                    id=row.id,
                    seller_id=row.seller_id,
                    brief_id=row.brief_id,
                    ad_id=row.ad_id,
                    template_name=row.template_name,
                    template_language=row.template_language,
                    segment=row.segment.model_dump(mode="json"),
                    status=row.status,
                    scheduled_for=row.scheduled_for,
                    created_at=row.created_at,
                )
            )
            await self._insert(session, row.seller_id, decisions)

    async def row(self, seller_id: UUID, campaign_id: UUID) -> CampaignRow | None:
        async with self.sessions() as session:
            found = (
                (
                    await session.execute(
                        sa.select(reactivation_campaigns).where(
                            reactivation_campaigns.c.seller_id == seller_id,
                            reactivation_campaigns.c.id == campaign_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _row(found) if found is not None else None

    async def get(self, seller_id: UUID, campaign_id: UUID) -> ReactivationCampaign | None:
        row = await self.row(seller_id, campaign_id)
        if row is None:
            return None
        history = await self._history([campaign_id])
        return _view(row, history.get(campaign_id, []))

    async def page(
        self, seller_id: UUID, *, limit: int, cursor: str | None = None
    ) -> tuple[list[ReactivationCampaign], str | None]:
        statement = sa.select(reactivation_campaigns).where(
            reactivation_campaigns.c.seller_id == seller_id
        )
        async with self.sessions() as session:
            found = (
                (
                    await session.execute(
                        keyset_page(statement, reactivation_campaigns, limit=limit, cursor=cursor)
                    )
                )
                .mappings()
                .all()
            )
        page, token = next_cursor(found, limit=limit)
        rows = [_row(item) for item in page]
        history = await self._history([row.id for row in rows])
        return [_view(row, history.get(row.id, [])) for row in rows], token

    async def set_status(
        self, seller_id: UUID, campaign_id: UUID, status: CampaignStatus, *, expect: CampaignStatus
    ) -> bool:
        async with self.sessions() as session, session.begin():
            moved = await session.execute(
                sa.update(reactivation_campaigns)
                .where(
                    reactivation_campaigns.c.seller_id == seller_id,
                    reactivation_campaigns.c.id == campaign_id,
                    reactivation_campaigns.c.status == expect,
                )
                .values(status=status)
                .returning(reactivation_campaigns.c.id)
            )
            return moved.first() is not None

    async def add_decisions(self, campaign_id: UUID, decisions: Sequence[SendDecision]) -> None:
        async with self.sessions() as session, session.begin():
            seller_id = (
                await session.execute(
                    sa.select(reactivation_campaigns.c.seller_id).where(
                        reactivation_campaigns.c.id == campaign_id
                    )
                )
            ).scalar_one()
            await self._insert(session, seller_id, decisions)

    async def marketing_sent(self, seller_id: UUID, contact_id: UUID, at: datetime) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                sa.update(contacts)
                .where(contacts.c.seller_id == seller_id, contacts.c.id == contact_id)
                .values(last_marketing_sent_at=at)
            )

    async def _insert(
        self, session: AsyncSession, seller_id: UUID, decisions: Sequence[SendDecision]
    ) -> None:
        if not decisions:
            return
        await session.execute(
            sa.insert(send_decisions),
            [
                {
                    "id": decision.id if isinstance(decision.id, UUID) else new_id(),
                    "seller_id": seller_id,
                    "campaign_id": decision.campaign_id,
                    "contact_id": decision.contact_id,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "reason_text_es": decision.reason_text_es or None,
                    "next_allowed_at": decision.next_allowed_at,
                    "message_id": decision.message_id,
                    "evaluated_at": decision.evaluated_at,
                }
                for decision in decisions
            ],
        )

    async def _history(self, campaign_ids: Sequence[UUID]) -> dict[UUID, list[SendDecision]]:
        if not campaign_ids:
            return {}
        async with self.sessions() as session:
            found = (
                (
                    await session.execute(
                        sa.select(send_decisions)
                        .where(send_decisions.c.campaign_id.in_(campaign_ids))
                        .order_by(send_decisions.c.created_at, send_decisions.c.evaluated_at)
                    )
                )
                .mappings()
                .all()
            )
        history: dict[UUID, list[SendDecision]] = {}
        for item in found:
            history.setdefault(item["campaign_id"], []).append(
                SendDecision(
                    id=item["id"],
                    campaign_id=item["campaign_id"],
                    contact_id=item["contact_id"],
                    decision=item["decision"],
                    reason=item["reason"],
                    reason_text_es=item["reason_text_es"] or MISSING,
                    next_allowed_at=item["next_allowed_at"],
                    message_id=item["message_id"],
                    evaluated_at=item["evaluated_at"],
                )
            )
        return history


def _row(item: Mapping[Any, Any]) -> CampaignRow:
    return CampaignRow(
        id=item["id"],
        seller_id=item["seller_id"],
        template_name=item["template_name"],
        template_language=item["template_language"],
        segment=Segment.model_validate(item["segment"] or {}),
        status=item["status"],
        scheduled_for=item["scheduled_for"],
        created_at=item["created_at"],
        brief_id=item["brief_id"],
        ad_id=item["ad_id"],
    )
