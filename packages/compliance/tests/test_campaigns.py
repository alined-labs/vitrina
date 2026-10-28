"""T064 reactivation campaigns (AS-6.7, FR-061, FR-064; ADR-0008): a preview evaluates every
contact of the segment, a send re-evaluates each one, writes only to ``allowed`` contacts with
the approved template, and keeps every decision, sent or not.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_compliance.calendar import BOGOTA
from vitrina_compliance.campaigns import (
    AdRef,
    CampaignConflictError,
    CampaignDeps,
    Candidate,
    InMemoryCampaigns,
    ReactivationCampaignCreate,
    Segment,
    check_eligibility,
    preview_campaign,
    render_template,
    run_send,
    schedule_send,
)
from vitrina_compliance.consents import (
    ConsentRecordCreate,
    InMemoryCompliance,
    opt_out,
)
from vitrina_compliance.contacts import ContactIdentity
from vitrina_core.providers import OutboundMessage, OutboundTemplate, SendResult

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
PRODUCT = UUID("00000000-0000-7000-8000-0000000000b1")
OTHER_PRODUCT = UUID("00000000-0000-7000-8000-0000000000b2")
AD = AdRef(UUID("00000000-0000-7000-8000-0000000000d1"), "A7")
WEDNESDAY_10 = datetime(2026, 10, 28, 10, 0, tzinfo=BOGOTA)
SATURDAY_16 = datetime(2026, 10, 31, 16, 0, tzinfo=BOGOTA)
NEXT_WINDOW = datetime(2026, 11, 3, 7, 0, tzinfo=BOGOTA)  # Mon Nov 2 is a holiday
WEDNESDAY_19 = datetime(2026, 10, 28, 19, 0, tzinfo=BOGOTA)  # the window closes
THURSDAY_7 = datetime(2026, 10, 29, 7, 0, tzinfo=BOGOTA)
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"


@dataclass
class Messages:
    """The messenger: every message, answered ``sent`` unless the number is in ``failing``."""

    sent: list[OutboundMessage] = field(default_factory=list)
    failing: set[str] = field(default_factory=set)

    async def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        if message.to in self.failing:
            return SendResult(message_id="", status="failed", error="131026 undeliverable")
        return SendResult(message_id=f"wamid.{len(self.sent)}", status="sent")


@dataclass
class World:
    compliance: InMemoryCompliance
    store: InMemoryCampaigns
    messages: Messages = field(default_factory=Messages)
    candidates: list[Candidate] = field(default_factory=list)
    ads: dict[UUID, AdRef] = field(default_factory=lambda: {PRODUCT: AD})
    asked: list[tuple[Segment, tuple[UUID, ...] | None]] = field(default_factory=list)
    ad_lookups: list[tuple[UUID, UUID | None]] = field(default_factory=list)
    outbox: list[tuple[UUID, str, str]] = field(default_factory=list)
    paced: list[str] = field(default_factory=list)
    numbers: dict[UUID, str] = field(default_factory=dict)

    async def find_candidates(
        self,
        seller_id: UUID,
        segment: Segment,
        *,
        now: datetime,
        contact_ids: Sequence[UUID] | None = None,
    ) -> list[Candidate]:
        self.asked.append((segment, None if contact_ids is None else tuple(contact_ids)))
        chosen = [c for c in self.candidates if contact_ids is None or c.contact_id in contact_ids]
        return chosen[: segment.max_contacts] if contact_ids is None else chosen

    async def find_ad(
        self, seller_id: UUID, product_id: UUID, *, ad_id: UUID | None = None
    ) -> AdRef | None:
        self.ad_lookups.append((product_id, ad_id))
        ad = self.ads.get(product_id)
        return ad if ad is not None and (ad_id is None or ad.ad_id == ad_id) else None

    async def record(self, seller_id: UUID, contact_id: UUID, *, text: str, wa_id: str) -> UUID:
        self.outbox.append((contact_id, text, wa_id))
        return uuid4()

    async def pace(self, recipient: str) -> None:
        self.paced.append(recipient)

    def deps(self) -> CampaignDeps:
        return CampaignDeps(
            store=self.store,
            compliance=self.compliance,
            candidates=self.find_candidates,
            ads=self.find_ad,
            messenger=self.messages,
            outbox=self.record,
            pace=self.pace,
        )

    async def contact(
        self,
        number: str,
        *,
        consent: bool = True,
        tag: str | None = "shipping",
        product: UUID | None = PRODUCT,
        name: str | None = "Laura",
    ) -> UUID:
        contact = await self.compliance.resolve_contact(SELLER, number, display_name=name)
        if consent:
            await self.compliance.append_consent(
                SELLER,
                contact.id,
                ConsentRecordCreate(
                    purpose="marketing",
                    status="granted",
                    text_shown=PROMPT,
                    response_text="sí",
                    channel="whatsapp",
                ),
                recorded_by="agent",
            )
        self.numbers[contact.id] = number
        self.candidates.append(
            Candidate(
                contact_id=contact.id,
                objection_tag=tag,  # type: ignore[arg-type]
                product_id=product,
                product_name="Mochila tejida azul" if product == PRODUCT else None,
            )
        )
        return contact.id


@pytest.fixture
def world() -> World:
    identity = ContactIdentity(
        pepper=SecretStr("test-pepper-with-enough-entropy"),
        key=SecretStr(Fernet.generate_key().decode()),
    )
    compliance = InMemoryCompliance(identity, clock=lambda: WEDNESDAY_10 - timedelta(days=9))
    return World(compliance=compliance, store=InMemoryCampaigns(compliance))


def create(**fields: object) -> ReactivationCampaignCreate:
    return ReactivationCampaignCreate.model_validate(
        {"template_name": "vitrina_reactivacion", **fields}
    )


async def started(world: World, **fields: object) -> UUID:
    campaign = await preview_campaign(world.deps(), SELLER, create(**fields), now=WEDNESDAY_10)
    await schedule_send(world.store, SELLER, campaign.id, template_approved=True)
    return campaign.id


# ── preview ──


async def test_as_6_2_the_preview_blocks_a_contact_without_consent(world: World) -> None:
    granted = await world.contact("573000000011")
    silent = await world.contact("573000000012", consent=False)

    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    decisions = {d.contact_id: (d.decision, d.reason) for d in campaign.decisions}
    assert decisions == {
        granted: ("allowed", "OK"),
        silent: ("blocked", "NO_CONSENT_LEY_1581"),
    }
    assert campaign.status == "preview"
    assert campaign.counts.model_dump() == {
        "allowed": 1, "blocked": 1, "deferred": 0, "sent": 0, "failed": 0
    }  # fmt: skip
    assert world.messages.sent == []


async def test_fr_061_opted_out_blocks_before_anything_else(world: World) -> None:
    contact_id = await world.contact("573000000011", product=None)
    await opt_out(world.compliance, SELLER, contact_id, via="api")

    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    (decision,) = campaign.decisions
    assert (decision.decision, decision.reason) == ("blocked", "OPTED_OUT")


async def test_as_6_4_a_marketing_message_this_week_blocks_by_frequency(world: World) -> None:
    await world.contact("573000000011")
    first = await started(world)
    await run_send(world.deps(), SELLER, first, business_name="Mochilas Ana", now=WEDNESDAY_10)

    later = await preview_campaign(
        world.deps(), SELLER, create(), now=WEDNESDAY_10 + timedelta(days=6)
    )

    (decision,) = later.decisions
    assert (decision.decision, decision.reason) == ("blocked", "FREQUENCY_CAP")


async def test_fr_061_no_approved_ad_for_the_contacts_interest_blocks(world: World) -> None:
    other = await world.contact("573000000011", product=OTHER_PRODUCT)
    unknown = await world.contact("573000000012", product=None)

    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    assert {(d.contact_id, d.reason) for d in campaign.decisions} == {
        (other, "NO_MATCHING_AD"),
        (unknown, "NO_MATCHING_AD"),
    }


async def test_as_6_7_an_attached_ad_must_be_for_the_contacts_product(world: World) -> None:
    await world.contact("573000000011")

    campaign = await preview_campaign(
        world.deps(), SELLER, create(ad_id=str(AD.ad_id)), now=WEDNESDAY_10
    )
    elsewhere = await preview_campaign(
        world.deps(), SELLER, create(ad_id=str(uuid4())), now=WEDNESDAY_10
    )

    assert [d.reason for d in campaign.decisions] == ["OK"]
    assert [d.reason for d in elsewhere.decisions] == ["NO_MATCHING_AD"]
    assert world.ad_lookups[0] == (PRODUCT, AD.ad_id)


async def test_as_6_3_a_preview_outside_legal_hours_defers_to_the_next_window(
    world: World,
) -> None:
    await world.contact("573000000011")

    campaign = await preview_campaign(
        world.deps(), SELLER, create(scheduled_for=SATURDAY_16.isoformat()), now=WEDNESDAY_10
    )

    (decision,) = campaign.decisions
    assert (decision.decision, decision.reason) == ("deferred", "OUTSIDE_HOURS_LEY_2300")
    assert decision.next_allowed_at == NEXT_WINDOW


async def test_as_6_3_without_a_date_the_campaign_goes_in_the_next_legal_window(
    world: World,
) -> None:
    await world.contact("573000000011")

    campaign = await preview_campaign(world.deps(), SELLER, create(), now=SATURDAY_16)

    assert campaign.scheduled_for == NEXT_WINDOW
    assert [d.decision for d in campaign.decisions] == ["allowed"]


async def test_as_6_7_the_segment_reaches_the_candidate_search(world: World) -> None:
    await world.contact("573000000011")
    await world.contact("573000000012")
    segment = {"objection_tags": ["shipping"], "no_purchase_since_days": 5, "max_contacts": 1}

    campaign = await preview_campaign(
        world.deps(), SELLER, create(segment=segment), now=WEDNESDAY_10
    )

    ((asked, ids),) = world.asked
    assert (asked.objection_tags, asked.no_purchase_since_days, asked.max_contacts, ids) == (
        ["shipping"], 5, 1, None
    )  # fmt: skip
    assert len(campaign.decisions) == 1


# ── send ──


async def test_as_6_7_only_allowed_contacts_get_the_template(world: World) -> None:
    allowed = await world.contact("573000000011")
    await world.contact("573000000012", consent=False)
    campaign_id = await started(world)

    run = await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    (message,) = world.messages.sent
    assert isinstance(message, OutboundTemplate)
    assert (message.to, message.template_name, message.language) == (
        "573000000011", "vitrina_reactivacion", "es"
    )  # fmt: skip
    components, text = render_template("Mochilas Ana", "Mochila tejida azul", "shipping", "A7")
    assert message.components == components
    assert world.outbox == [(allowed, text, "wamid.1")]
    assert world.paced == ["573000000011"]
    assert run.next_at is None
    assert run.campaign.status == "sent"
    sent = next(d for d in run.campaign.decisions if d.contact_id == allowed)
    assert (sent.decision, sent.reason, sent.evaluated_at) == ("sent", "OK", WEDNESDAY_10)
    assert sent.message_id is not None
    contact = await world.compliance.contact(SELLER, allowed)
    assert contact is not None
    assert contact.last_marketing_sent_at == WEDNESDAY_10
    assert run.campaign.counts.sent == 1
    assert run.campaign.counts.blocked == 1


def test_as_6_7_the_template_names_the_product_the_objection_and_the_ad() -> None:
    components, text = render_template("Mochilas Ana", "Mochila tejida azul", "shipping", "A7")

    (body,) = components
    assert body["type"] == "body"
    assert [p["text"] for p in body["parameters"]] == [
        "Mochilas Ana",
        "Mochila tejida azul",
        "Si el envío te hizo dudar, cuéntanos tu ciudad y te contamos cómo llega.",
        "A7",
    ]
    assert text.startswith("Hola, te escribe Mochilas Ana. Hace poco nos preguntaste por ")
    assert "#A7" in text
    assert text.endswith("Si no quieres recibir más mensajes, responde BAJA.")
    assert "$" not in text  # Art. III: the template states no price of its own


async def test_as_6_7_the_send_reevaluates_a_contact_who_opted_out_since(world: World) -> None:
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)
    await opt_out(world.compliance, SELLER, contact_id, via="keyword")

    run = await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    assert world.messages.sent == []
    (decision,) = run.campaign.decisions
    assert (decision.decision, decision.reason) == ("blocked", "OPTED_OUT")
    assert world.asked[-1][1] == (contact_id,)


async def test_fr_064_a_send_whatsapp_refuses_is_logged_as_failed(world: World) -> None:
    contact_id = await world.contact("573000000011")
    world.messages.failing.add("573000000011")
    campaign_id = await started(world)

    run = await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    (decision,) = run.campaign.decisions
    assert (decision.decision, decision.reason, decision.message_id) == (
        "failed", "SEND_FAILED", None
    )  # fmt: skip
    assert world.outbox == []
    contact = await world.compliance.contact(SELLER, contact_id)
    assert contact is not None
    assert contact.last_marketing_sent_at is None


async def test_as_6_3_a_send_outside_hours_waits_for_the_next_window(world: World) -> None:
    await world.contact("573000000011")
    campaign_id = await started(world)

    run = await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=SATURDAY_16
    )

    assert world.messages.sent == []
    assert run.next_at == NEXT_WINDOW
    assert run.campaign.status == "sending"
    assert [d.decision for d in run.campaign.decisions] == ["deferred"]

    again = await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=NEXT_WINDOW
    )

    assert len(world.messages.sent) == 1
    assert (again.next_at, again.campaign.status) == (None, "sent")


def ticking(*readings: datetime) -> Callable[[], datetime]:
    """A clock that gives each reading once, in order: the moments a send pass looks at it."""
    times = iter(readings)
    return lambda: next(times)


async def test_as_6_3_a_pass_that_runs_into_19_00_defers_the_rest(world: World) -> None:
    await world.contact("573000000011")
    await world.contact("573000000012")
    campaign_id = await started(world)
    # First contact: checked at 18:59:30 and again after its pacing wait; second: at 19:00:05.
    clock = ticking(
        WEDNESDAY_19 - timedelta(seconds=30),
        WEDNESDAY_19 - timedelta(seconds=20),
        WEDNESDAY_19 + timedelta(seconds=5),
    )

    run = await run_send(
        replace(world.deps(), clock=clock),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_19 - timedelta(minutes=1),
    )

    (message,) = world.messages.sent
    (late,) = [d for d in run.campaign.decisions if d.decision != "sent"]
    assert world.numbers[late.contact_id] != message.to
    assert (late.decision, late.reason) == ("deferred", "OUTSIDE_HOURS_LEY_2300")
    assert late.evaluated_at == WEDNESDAY_19 + timedelta(seconds=5)
    assert late.next_allowed_at == THURSDAY_7
    assert (run.next_at, run.campaign.status) == (THURSDAY_7, "sending")


async def test_as_6_3_a_send_is_checked_again_after_the_pacing_wait(world: World) -> None:
    contact = await world.contact("573000000011")
    campaign_id = await started(world)
    clock = ticking(WEDNESDAY_19 - timedelta(seconds=2), WEDNESDAY_19 + timedelta(seconds=4))

    run = await run_send(
        replace(world.deps(), clock=clock),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_19 - timedelta(seconds=3),
    )

    assert world.paced == ["573000000011"]
    assert world.messages.sent == []
    (decision,) = run.campaign.decisions
    assert (decision.contact_id, decision.decision) == (contact, "deferred")
    assert decision.next_allowed_at == THURSDAY_7
    contact_now = await world.compliance.contact(SELLER, contact)
    assert contact_now is not None
    assert contact_now.last_marketing_sent_at is None
    assert run.next_at == THURSDAY_7


async def test_fr_064_every_evaluation_is_kept(world: World) -> None:
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)
    await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    history = world.store.history(campaign_id)

    assert [(d.contact_id, d.decision) for d in history] == [
        (contact_id, "allowed"),
        (contact_id, "sent"),
    ]


async def test_as_6_7_a_blocked_contact_stays_blocked_for_the_campaign(world: World) -> None:
    contact_id = await world.contact("573000000011", consent=False)
    campaign_id = await started(world)
    await world.compliance.append_consent(
        SELLER,
        contact_id,
        ConsentRecordCreate(
            purpose="marketing",
            status="granted",
            text_shown=PROMPT,
            response_text="sí",
            channel="whatsapp",
        ),
        recorded_by="agent",
    )

    await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    assert world.messages.sent == []


async def test_as_6_7_a_sent_campaign_is_not_run_twice(world: World) -> None:
    await world.contact("573000000011")
    campaign_id = await started(world)
    await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=WEDNESDAY_10
    )

    assert len(world.messages.sent) == 1


# ── schedule ──


async def test_fr_064_an_unapproved_template_cannot_be_sent(world: World) -> None:
    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    with pytest.raises(CampaignConflictError) as refused:
        await schedule_send(world.store, SELLER, campaign.id, template_approved=False)

    assert refused.value.code == "TEMPLATE_NOT_APPROVED"
    stored = await world.store.get(SELLER, campaign.id)
    assert stored is not None
    assert stored.status == "preview"


async def test_as_6_7_a_campaign_is_scheduled_once(world: World) -> None:
    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    scheduled = await schedule_send(world.store, SELLER, campaign.id, template_approved=True)
    with pytest.raises(CampaignConflictError) as again:
        await schedule_send(world.store, SELLER, campaign.id, template_approved=True)

    assert scheduled.status == "scheduled"
    assert again.value.code == "CAMPAIGN_ALREADY_SENT"


async def test_as_6_7_campaigns_belong_to_their_seller(world: World) -> None:
    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    assert await world.store.get(uuid4(), campaign.id) is None
    with pytest.raises(LookupError):
        await schedule_send(world.store, uuid4(), campaign.id, template_approved=True)


# ── eligibility now ──


async def test_fr_061_one_contact_is_evaluated_on_demand(world: World) -> None:
    contact_id = await world.contact("573000000011")

    now = await check_eligibility(world.deps(), SELLER, contact_id, at=WEDNESDAY_10)
    weekend = await check_eligibility(world.deps(), SELLER, contact_id, at=SATURDAY_16)
    nobody = await check_eligibility(world.deps(), SELLER, uuid4(), at=WEDNESDAY_10)

    assert now is not None
    assert (now.decision, now.reason, now.evaluated_at) == ("allowed", "OK", WEDNESDAY_10)
    assert weekend is not None
    assert (weekend.decision, weekend.next_allowed_at) == ("deferred", NEXT_WINDOW)
    assert nobody is None


async def test_fr_061_an_erased_contact_is_never_eligible(world: World) -> None:
    contact_id = await world.contact("573000000011")
    await world.compliance.erase(
        SELLER, contact_id, requested_at=datetime(2026, 10, 27, tzinfo=UTC), scope=("contact",)
    )

    decision = await check_eligibility(world.deps(), SELLER, contact_id, at=WEDNESDAY_10)

    assert decision is not None
    assert (decision.decision, decision.reason) == ("blocked", "OPTED_OUT")
