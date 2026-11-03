"""T064 reactivation campaigns (AS-6.7, FR-061, FR-064; ADR-0008): a preview evaluates every
contact of the segment, a send re-evaluates each one, writes only to ``allowed`` contacts with
the approved template, and keeps every decision, sent or not.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_compliance.calendar import BOGOTA
from vitrina_compliance.campaigns import (
    CLOSING_MARGIN,
    FIRST_PAUSE,
    MAX_PAUSES,
    SEND_NOT_TRIED_ES,
    SEND_UNCONFIRMED_ES,
    UNKNOWN_IN_A_ROW,
    AdRef,
    CampaignConflictError,
    CampaignDeps,
    Candidate,
    InMemoryCampaigns,
    ReactivationCampaignCreate,
    Segment,
    SendRun,
    abandon_send,
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
from vitrina_compliance.eligibility import REASON_TEXT_ES, SendDecision
from vitrina_core.providers import OutboundMessage, OutboundTemplate, ProviderError, SendResult

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
PRODUCT = UUID("00000000-0000-7000-8000-0000000000b1")
OTHER_PRODUCT = UUID("00000000-0000-7000-8000-0000000000b2")
AD = AdRef(UUID("00000000-0000-7000-8000-0000000000d1"), "A7")
WEDNESDAY_10 = datetime(2026, 10, 28, 10, 0, tzinfo=BOGOTA)
SATURDAY_16 = datetime(2026, 10, 31, 16, 0, tzinfo=BOGOTA)
NEXT_WINDOW = datetime(2026, 11, 3, 7, 0, tzinfo=BOGOTA)  # Mon Nov 2 is a holiday
WEDNESDAY_19 = datetime(2026, 10, 28, 19, 0, tzinfo=BOGOTA)  # the window closes
THURSDAY_7 = datetime(2026, 10, 29, 7, 0, tzinfo=BOGOTA)
SENDS_STOP = WEDNESDAY_19 - CLOSING_MARGIN  # the last moment a send may start is before this
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"
TEMPLATE = "vitrina_reactivacion"


def undeliverable() -> ProviderError:
    """One customer WhatsApp cannot reach, as the real client reports it: it raises."""
    return ProviderError(
        "Cloud API refused the request (HTTP 400, code 131026)",
        provider="whatsapp",
        status_code=400,
        scope="recipient",
        nothing_sent=True,
    )


def bad_token() -> ProviderError:
    return ProviderError(
        "Cloud API refused the request (HTTP 401, code 190)",
        provider="whatsapp",
        status_code=401,
        scope="account",
        nothing_sent=True,
    )


def rate_limited(retry_after_s: float | None = None) -> ProviderError:
    return ProviderError(
        "Cloud API refused the request (HTTP 429, code 130429)",
        provider="whatsapp",
        retryable=True,
        status_code=429,
        scope="account",
        retry_after_s=retry_after_s,
        nothing_sent=True,
    )


def server_error() -> ProviderError:
    """A 5xx: Meta may have taken the message before it failed."""
    return ProviderError(
        "Cloud API refused the request (HTTP 503, code None)",
        provider="whatsapp",
        retryable=True,
        status_code=503,
        scope="account",
    )


def delivery_unknown() -> ProviderError:
    return ProviderError(
        "Cloud API request failed after it was sent (ReadTimeout); delivery unknown, not retried",
        provider="whatsapp",
    )


@dataclass
class Messages:
    """The messenger: every message tried, answered ``sent`` unless ``failing`` holds the number.

    Like the real client it never answers ``failed``: a refusal is raised. A number in ``dies``
    stands for a worker killed while the request is out, after Meta may have taken it.
    """

    sent: list[OutboundMessage] = field(default_factory=list)
    failing: dict[str, Exception] = field(default_factory=dict)
    dies: set[str] = field(default_factory=set)
    answers_failed: set[str] = field(default_factory=set)

    async def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        if message.to in self.dies:
            raise RuntimeError("the worker died")
        if message.to in self.failing:
            raise self.failing[message.to]
        if message.to in self.answers_failed:  # another Messenger may answer instead of raising
            return SendResult(message_id="", status="failed", error="refused")
        return SendResult(message_id=f"wamid.{len(self.sent)}", status="sent")

    def tried(self) -> list[str]:
        return [message.to for message in self.sent]


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
    await schedule_send(world.store, SELLER, campaign.id, approved_template=TEMPLATE)
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
    world.messages.failing["573000000011"] = undeliverable()
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


async def send_pass(world: World, campaign_id: UUID, *, pauses: int = 0) -> SendRun:
    return await run_send(
        world.deps(),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_10,
        pauses=pauses,
    )


async def three_contacts(world: World) -> tuple[UUID, UUID, UUID]:
    return (
        await world.contact("573000000011"),
        await world.contact("573000000012"),
        await world.contact("573000000013"),
    )


def by_contact(run: SendRun) -> dict[UUID, str]:
    return {d.contact_id: d.decision for d in run.campaign.decisions}


async def test_fr_064_one_refused_contact_does_not_stop_the_others(world: World) -> None:
    first, refused, last = await three_contacts(world)
    world.messages.failing["573000000012"] = undeliverable()
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == ["573000000011", "573000000012", "573000000013"]
    assert by_contact(run) == {first: "sent", refused: "failed", last: "sent"}
    assert (run.campaign.status, run.next_at, run.stopped) == ("sent", None, None)
    assert (run.campaign.counts.sent, run.campaign.counts.failed) == (2, 1)
    decision = next(d for d in run.campaign.decisions if d.contact_id == refused)
    assert decision.reason == "SEND_FAILED"
    assert decision.reason_text_es == REASON_TEXT_ES["SEND_FAILED"]  # never Meta's words
    assert (await last_sent(world, refused)) is None  # Meta said no: this send used up no slot
    assert [contact for contact, _, _ in world.outbox] == [first, last]


async def test_fr_064_a_number_the_client_cannot_use_only_fails_that_contact(
    world: World,
) -> None:
    refused, second, third = await three_contacts(world)
    world.messages.failing["573000000011"] = ValueError("recipient must be an E.164 phone number")
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert by_contact(run) == {refused: "failed", second: "sent", third: "sent"}
    (decision,) = [d for d in run.campaign.decisions if d.contact_id == refused]
    assert decision.reason_text_es == SEND_NOT_TRIED_ES  # WhatsApp was never asked
    assert (await last_sent(world, refused)) is None


async def test_fr_064_a_bad_token_stops_the_pass_and_finishes_the_campaign(world: World) -> None:
    await three_contacts(world)
    world.messages.failing["573000000011"] = bad_token()
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == ["573000000011"]  # the rest would only fail the same way
    assert (run.campaign.status, run.next_at, run.paused) == ("sent", None, False)
    assert run.stopped == "Cloud API refused the request (HTTP 401, code 190)"
    assert [d.reason_text_es for d in run.campaign.decisions] == [
        REASON_TEXT_ES["SEND_FAILED"],  # the one WhatsApp refused
        SEND_NOT_TRIED_ES,
        SEND_NOT_TRIED_ES,
    ]
    assert run.campaign.counts.model_dump() == {
        "allowed": 0, "blocked": 0, "deferred": 0, "sent": 0, "failed": 3
    }  # fmt: skip
    assert {d.reason for d in run.campaign.decisions} == {"SEND_FAILED"}


async def test_fr_064_a_rate_limit_pauses_the_pass_and_no_contact_is_lost(world: World) -> None:
    await three_contacts(world)
    world.messages.failing["573000000012"] = rate_limited()
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == ["573000000011", "573000000012"]
    assert (run.paused, run.next_at) == (True, WEDNESDAY_10 + FIRST_PAUSE)
    assert run.campaign.status == "sending"
    assert (run.campaign.counts.sent, run.campaign.counts.allowed) == (1, 2)
    assert run.campaign.counts.failed == 0

    del world.messages.failing["573000000012"]
    again = await send_pass(world, campaign_id, pauses=1)

    assert world.messages.tried() == [
        "573000000011", "573000000012", "573000000012", "573000000013"
    ]  # fmt: skip
    assert (again.paused, again.next_at, again.campaign.status) == (False, None, "sent")
    assert again.campaign.counts.sent == 3


async def test_fr_064_each_pause_is_longer_and_meta_can_ask_for_more(world: World) -> None:
    await world.contact("573000000011")
    world.messages.failing["573000000011"] = rate_limited()
    campaign_id = await started(world)

    second = await send_pass(world, campaign_id, pauses=1)
    world.messages.failing["573000000011"] = rate_limited(retry_after_s=3600)
    asked = await send_pass(world, campaign_id, pauses=1)

    assert second.next_at == WEDNESDAY_10 + FIRST_PAUSE * 2
    assert asked.next_at == WEDNESDAY_10 + timedelta(seconds=3600)


async def test_fr_064_a_limit_that_never_lifts_finishes_the_campaign(world: World) -> None:
    await three_contacts(world)
    world.messages.failing["573000000011"] = rate_limited()
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id, pauses=MAX_PAUSES)

    assert world.messages.tried() == ["573000000011"]
    assert (run.campaign.status, run.next_at, run.paused) == ("sent", None, False)
    assert run.stopped is not None
    assert run.campaign.counts.failed == 3


async def test_fr_064_failures_it_cannot_classify_stop_after_a_few_in_a_row(world: World) -> None:
    numbers = [f"57300000001{i}" for i in range(UNKNOWN_IN_A_ROW + 2)]
    for number in numbers:
        await world.contact(number)
        world.messages.failing[number] = delivery_unknown()
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == numbers[:UNKNOWN_IN_A_ROW]
    assert run.campaign.counts.failed == UNKNOWN_IN_A_ROW  # delivery unknown: never sent again
    assert run.campaign.counts.allowed == 2
    assert (run.paused, run.campaign.status) == (True, "sending")


async def test_fr_064_a_refusal_it_can_read_breaks_a_run_of_unknown_failures(
    world: World,
) -> None:
    numbers = [f"57300000001{i}" for i in range(UNKNOWN_IN_A_ROW * 2 + 1)]
    for position, number in enumerate(numbers):
        await world.contact(number)
        world.messages.failing[number] = (
            undeliverable() if position == UNKNOWN_IN_A_ROW - 1 else delivery_unknown()
        )
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == numbers[: UNKNOWN_IN_A_ROW * 2]
    assert run.paused is True


async def test_fr_064_a_pass_killed_during_a_send_never_sends_twice(world: World) -> None:
    sent, killed, last = await three_contacts(world)
    world.messages.dies.add("573000000012")
    campaign_id = await started(world)

    with pytest.raises(RuntimeError):
        await send_pass(world, campaign_id)
    world.messages.dies.clear()
    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == [
        "573000000011", "573000000012", "573000000013"
    ]  # fmt: skip
    assert run.campaign.status == "sent"
    assert (run.campaign.counts.sent, run.campaign.counts.failed) == (2, 1)
    # Meta may have the second: it is reported as failed and never sent again.
    assert by_contact(run) == {sent: "sent", killed: "failed", last: "sent"}
    assert (await last_sent(world, killed)) == WEDNESDAY_10  # a later campaign must not reach them
    (unclear,) = [d for d in run.campaign.decisions if d.contact_id == killed]
    assert unclear.reason_text_es == SEND_UNCONFIRMED_ES


async def test_fr_064_a_contact_another_pass_took_is_not_sent_again(world: World) -> None:
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)

    async def other_pass_wins(_recipient: str) -> None:
        taken = SendDecision(
            contact_id=contact_id, decision="sent", reason="OK", evaluated_at=WEDNESDAY_10
        )
        await world.store.add_decisions(campaign_id, [taken])

    run = await run_send(
        replace(world.deps(), pace=other_pass_wins),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_10,
    )

    assert world.messages.sent == []
    assert by_contact(run) == {contact_id: "sent"}


async def test_fr_064_a_campaign_whose_job_is_gone_finishes_with_true_totals(
    world: World,
) -> None:
    sent, _, _ = await three_contacts(world)
    world.messages.dies.add("573000000012")
    campaign_id = await started(world)
    with pytest.raises(RuntimeError):
        await send_pass(world, campaign_id)

    campaign = await abandon_send(
        world.store, world.compliance, SELLER, campaign_id, now=WEDNESDAY_10
    )

    assert campaign.status == "sent"
    assert campaign.counts.model_dump() == {
        "allowed": 0, "blocked": 0, "deferred": 0, "sent": 1, "failed": 2
    }  # fmt: skip
    assert len(world.messages.sent) == 2  # finishing a campaign sends nothing
    assert next(d for d in campaign.decisions if d.contact_id == sent).decision == "sent"
    again = await send_pass(world, campaign_id)
    assert len(world.messages.sent) == 2
    assert again.campaign.counts == campaign.counts


async def last_sent(world: World, contact_id: UUID) -> datetime | None:
    contact = await world.compliance.contact(SELLER, contact_id)
    assert contact is not None
    return contact.last_marketing_sent_at


async def second_campaign(world: World, at: datetime) -> SendRun:
    preview = await preview_campaign(world.deps(), SELLER, create(), now=at)
    await schedule_send(world.store, SELLER, preview.id, approved_template=TEMPLATE)
    return await run_send(world.deps(), SELLER, preview.id, business_name="Mochilas Ana", now=at)


# ── the 7-day cap holds whatever became of the request (Art. II.3) ──


@pytest.mark.parametrize(
    "unclear", [delivery_unknown, server_error], ids=("read timeout", "server error")
)
async def test_as_6_4_a_send_that_may_have_arrived_holds_the_cap_in_the_next_campaign(
    world: World, unclear: Callable[[], ProviderError]
) -> None:
    contact_id = await world.contact("573000000011")
    world.messages.failing["573000000011"] = unclear()
    await send_pass(world, await started(world))
    assert (await last_sent(world, contact_id)) == WEDNESDAY_10
    del world.messages.failing["573000000011"]

    run = await second_campaign(world, WEDNESDAY_10 + timedelta(days=1))

    assert world.messages.tried() == ["573000000011"]
    assert [(d.decision, d.reason) for d in run.campaign.decisions] == [
        ("blocked", "FREQUENCY_CAP")
    ]


async def test_as_6_4_a_pass_that_dies_after_meta_took_the_message_holds_the_cap(
    world: World,
) -> None:
    await world.contact("573000000011")

    async def chat_is_down(*_args: object, **_given: object) -> UUID:
        raise RuntimeError("the database went away")

    with pytest.raises(RuntimeError):
        await run_send(
            replace(world.deps(), outbox=chat_is_down),
            SELLER,
            await started(world),
            business_name="Mochilas Ana",
            now=WEDNESDAY_10,
        )

    run = await second_campaign(world, WEDNESDAY_10 + timedelta(days=1))

    assert world.messages.tried() == ["573000000011"]
    assert [d.reason for d in run.campaign.decisions] == ["FREQUENCY_CAP"]


async def test_as_6_4_a_definite_refusal_gives_the_slot_back(world: World) -> None:
    contact_id = await world.contact("573000000011")
    world.messages.failing["573000000011"] = undeliverable()
    await send_pass(world, await started(world))
    assert (await last_sent(world, contact_id)) is None
    del world.messages.failing["573000000011"]

    run = await second_campaign(world, WEDNESDAY_10 + timedelta(days=1))

    assert world.messages.tried() == ["573000000011", "573000000011"]
    assert [d.decision for d in run.campaign.decisions] == ["sent"]


async def test_as_6_4_a_definite_refusal_keeps_the_date_of_an_earlier_genuine_send(
    world: World,
) -> None:
    contact_id = await world.contact("573000000011")
    await send_pass(world, await started(world))
    assert (await last_sent(world, contact_id)) == WEDNESDAY_10
    world.messages.failing["573000000011"] = undeliverable()
    eight_days_later = WEDNESDAY_10 + timedelta(days=8)

    run = await second_campaign(world, eight_days_later)

    assert [d.decision for d in run.campaign.decisions] == ["failed"]
    assert (await last_sent(world, contact_id)) == WEDNESDAY_10  # not cleared, not the refused one


async def test_as_6_4_giving_a_slot_back_never_undoes_a_later_send(world: World) -> None:
    contact_id = await world.contact("573000000011")
    later = WEDNESDAY_10 + timedelta(days=1)
    world.compliance._row(SELLER, contact_id).last_marketing_sent_at = later

    await world.store.release_send(SELLER, contact_id, claimed_at=WEDNESDAY_10, previous=None)

    assert (await last_sent(world, contact_id)) == later


async def test_as_6_4_a_messenger_answering_failed_gives_the_slot_back(world: World) -> None:
    contact_id = await world.contact("573000000011")
    world.messages.answers_failed.add("573000000011")

    run = await send_pass(world, await started(world))

    (decision,) = run.campaign.decisions
    assert (decision.decision, decision.reason_text_es) == (
        "failed", REASON_TEXT_ES["SEND_FAILED"]
    )  # fmt: skip
    assert (await last_sent(world, contact_id)) is None


async def test_as_6_4_the_claim_itself_refuses_a_contact_another_campaign_just_reached(
    world: World,
) -> None:
    """Two campaigns at once: the second read the contact before the first one claimed them."""
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)
    real_claim = world.store.claim_send

    async def first_campaign_wins(*args: Any, **given: Any) -> Any:
        world.compliance._row(SELLER, contact_id).last_marketing_sent_at = WEDNESDAY_10
        return await real_claim(*args, **given)

    world.store.claim_send = first_campaign_wins  # type: ignore[method-assign]

    run = await send_pass(world, campaign_id)

    assert world.messages.sent == []
    (decision,) = run.campaign.decisions
    assert (decision.decision, decision.reason) == ("blocked", "FREQUENCY_CAP")
    assert decision.reason_text_es == REASON_TEXT_ES["FREQUENCY_CAP"]


# ── a server error may have landed: the customer is not asked again ──


async def test_fr_064_a_server_error_pauses_the_others_and_keeps_that_contacts_claim(
    world: World,
) -> None:
    unclear, second, third = await three_contacts(world)
    world.messages.failing["573000000011"] = server_error()
    campaign_id = await started(world)

    run = await send_pass(world, campaign_id)

    assert world.messages.tried() == ["573000000011"]
    assert by_contact(run) == {unclear: "failed", second: "allowed", third: "allowed"}
    assert (run.paused, run.campaign.status) == (True, "sending")
    (claim,) = [d for d in run.campaign.decisions if d.contact_id == unclear]
    assert claim.reason_text_es == SEND_UNCONFIRMED_ES

    del world.messages.failing["573000000011"]
    again = await send_pass(world, campaign_id, pauses=1)

    assert world.messages.tried() == ["573000000011", "573000000012", "573000000013"]
    assert by_contact(again) == {unclear: "failed", second: "sent", third: "sent"}


# ── anyone not tried is reported for what is true of them ──


async def test_fr_064_ending_a_campaign_reports_who_opted_out_as_opted_out(world: World) -> None:
    gone, silent, waiting = await three_contacts(world)
    campaign_id = await started(world)
    await opt_out(world.compliance, SELLER, gone, via="keyword")
    await world.compliance.append_consent(
        SELLER,
        silent,
        ConsentRecordCreate(
            purpose="marketing",
            status="revoked",
            text_shown=PROMPT,
            response_text="no",
            channel="whatsapp",
        ),
        recorded_by="agent",
    )

    campaign = await abandon_send(
        world.store, world.compliance, SELLER, campaign_id, now=WEDNESDAY_10
    )

    reasons = {d.contact_id: (d.decision, d.reason, d.reason_text_es) for d in campaign.decisions}
    assert reasons == {
        gone: ("blocked", "OPTED_OUT", REASON_TEXT_ES["OPTED_OUT"]),
        silent: ("blocked", "NO_CONSENT_LEY_1581", REASON_TEXT_ES["NO_CONSENT_LEY_1581"]),
        waiting: ("failed", "SEND_FAILED", SEND_NOT_TRIED_ES),
    }
    assert world.messages.sent == []


async def test_fr_064_ending_a_campaign_finishes_the_deferred_contacts_too(world: World) -> None:
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)
    waiting = await run_send(
        world.deps(), SELLER, campaign_id, business_name="Mochilas Ana", now=SATURDAY_16
    )
    assert [d.decision for d in waiting.campaign.decisions] == ["deferred"]

    # Saturday at 16:00 is out of hours: not being tried must not read as deferred any longer.
    campaign = await abandon_send(
        world.store, world.compliance, SELLER, campaign_id, now=SATURDAY_16
    )

    (decision,) = campaign.decisions
    assert (decision.contact_id, decision.decision) == (contact_id, "failed")
    assert (decision.reason_text_es, decision.next_allowed_at) == (SEND_NOT_TRIED_ES, None)
    assert campaign.status == "sent"


async def test_fr_064_a_contact_without_a_usable_number_was_not_tried(world: World) -> None:
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)

    async def no_number(_seller_id: UUID, _contact_id: UUID) -> None:
        return None

    world.compliance.phone = no_number  # type: ignore[method-assign]

    run = await send_pass(world, campaign_id)

    (decision,) = run.campaign.decisions
    assert (decision.contact_id, decision.decision) == (contact_id, "failed")
    assert decision.reason_text_es == SEND_NOT_TRIED_ES


# ── a contact is read again after any wait ──


async def test_fr_063_an_opt_out_during_a_pause_is_respected(world: World) -> None:
    gone, also_gone, stays = await three_contacts(world)
    world.messages.failing["573000000011"] = rate_limited()
    campaign_id = await started(world)
    assert (await send_pass(world, campaign_id)).paused
    await opt_out(world.compliance, SELLER, gone, via="keyword")
    await opt_out(world.compliance, SELLER, also_gone, via="keyword")
    del world.messages.failing["573000000011"]

    run = await send_pass(world, campaign_id, pauses=1)

    assert world.messages.tried() == ["573000000011", "573000000013"]  # the 429, then only them
    assert by_contact(run) == {gone: "blocked", also_gone: "blocked", stays: "sent"}
    assert {d.reason for d in run.campaign.decisions if d.decision == "blocked"} == {"OPTED_OUT"}


async def test_fr_063_an_opt_out_during_the_pacing_wait_is_respected(world: World) -> None:
    contact_id = await world.contact("573000000011")
    campaign_id = await started(world)

    async def baja_arrives(_recipient: str) -> None:
        await opt_out(world.compliance, SELLER, contact_id, via="keyword")

    run = await run_send(
        replace(world.deps(), pace=baja_arrives),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_10,
    )

    assert world.messages.sent == []
    (decision,) = run.campaign.decisions
    assert (decision.decision, decision.reason) == ("blocked", "OPTED_OUT")
    assert (await last_sent(world, contact_id)) is None


async def test_fr_061_an_ad_withdrawn_during_the_pacing_wait_blocks_the_send(world: World) -> None:
    await world.contact("573000000011")
    campaign_id = await started(world)

    async def ad_withdrawn(_recipient: str) -> None:
        world.ads.clear()

    run = await run_send(
        replace(world.deps(), pace=ad_withdrawn),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_10,
    )

    assert world.messages.sent == []
    assert [d.reason for d in run.campaign.decisions] == ["NO_MATCHING_AD"]


# ── pauses and the window ──


async def test_fr_064_a_pause_never_outlasts_an_earlier_deferred_contact(world: World) -> None:
    await world.contact("573000000011")
    await world.contact("573000000012")
    world.messages.failing["573000000012"] = rate_limited(retry_after_s=86400)
    campaign_id = await started(world)
    # First contact: read once, past closing, so deferred. Second: read at the morning's opening.
    clock = ticking(WEDNESDAY_19 + timedelta(seconds=5), THURSDAY_7, THURSDAY_7)

    run = await run_send(
        replace(world.deps(), clock=clock),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=WEDNESDAY_19,
    )

    assert run.paused is True
    assert run.next_at == THURSDAY_7  # not a day later, when the pause alone would end


async def test_as_6_3_a_pause_that_crosses_closing_defers_everyone(world: World) -> None:
    await three_contacts(world)
    world.messages.failing["573000000011"] = rate_limited()
    campaign_id = await started(world)
    before = SENDS_STOP - timedelta(seconds=30)
    first = await send_pass_at(world, campaign_id, before)
    assert (first.paused, first.next_at) == (True, before + FIRST_PAUSE)
    del world.messages.failing["573000000011"]

    run = await send_pass_at(world, campaign_id, before + FIRST_PAUSE, pauses=1)

    assert len(world.messages.sent) == 1
    assert {d.decision for d in run.campaign.decisions} == {"deferred"}
    assert (run.paused, run.next_at, run.campaign.status) == (False, THURSDAY_7, "sending")


async def send_pass_at(
    world: World, campaign_id: UUID, at: datetime, *, pauses: int = 0
) -> SendRun:
    return await run_send(
        replace(world.deps(), clock=lambda: at),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=at,
        pauses=pauses,
    )


@pytest.mark.parametrize(
    ("at", "sends"),
    [
        (SENDS_STOP - timedelta(seconds=1), True),
        (SENDS_STOP, False),  # it could still be on its way at 19:00
        (WEDNESDAY_19 - timedelta(seconds=1), False),
    ],
    ids=("just before the margin", "at the margin", "a second before closing"),
)
async def test_as_6_3_sends_stop_a_little_before_the_window_closes(
    world: World, at: datetime, sends: bool
) -> None:
    await world.contact("573000000011")

    run = await send_pass_at(world, await started(world), at)

    assert (len(world.messages.sent) == 1) is sends
    (decision,) = run.campaign.decisions
    if not sends:
        assert (decision.decision, decision.reason) == ("deferred", "OUTSIDE_HOURS_LEY_2300")
        assert (decision.next_allowed_at, decision.evaluated_at) == (THURSDAY_7, at)


async def test_as_6_3_the_margin_applies_to_saturdays_earlier_close(world: World) -> None:
    await world.contact("573000000011")
    saturday_15 = datetime(2026, 10, 31, 15, 0, tzinfo=BOGOTA)

    run = await send_pass_at(world, await started(world), saturday_15 - timedelta(seconds=1))

    assert world.messages.sent == []
    assert run.next_at == NEXT_WINDOW


async def test_as_6_3_the_legal_hours_themselves_do_not_change(world: World) -> None:
    await world.contact("573000000011")

    preview = await preview_campaign(
        world.deps(),
        SELLER,
        create(scheduled_for=(WEDNESDAY_19 - timedelta(seconds=1)).isoformat()),
        now=WEDNESDAY_10,
    )

    assert [d.decision for d in preview.decisions] == ["allowed"]


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
    # First contact: checked 30 s before sends stop and again after its pacing wait; second: late.
    clock = ticking(
        SENDS_STOP - timedelta(seconds=30),
        SENDS_STOP - timedelta(seconds=20),
        SENDS_STOP + timedelta(seconds=5),
    )

    run = await run_send(
        replace(world.deps(), clock=clock),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=SENDS_STOP - timedelta(minutes=1),
    )

    (message,) = world.messages.sent
    (late,) = [d for d in run.campaign.decisions if d.decision != "sent"]
    assert world.numbers[late.contact_id] != message.to
    assert (late.decision, late.reason) == ("deferred", "OUTSIDE_HOURS_LEY_2300")
    assert late.evaluated_at == SENDS_STOP + timedelta(seconds=5)
    assert late.next_allowed_at == THURSDAY_7
    assert (run.next_at, run.campaign.status) == (THURSDAY_7, "sending")


async def test_as_6_3_a_send_is_checked_again_after_the_pacing_wait(world: World) -> None:
    contact = await world.contact("573000000011")
    campaign_id = await started(world)
    clock = ticking(SENDS_STOP - timedelta(seconds=2), SENDS_STOP + timedelta(seconds=4))

    run = await run_send(
        replace(world.deps(), clock=clock),
        SELLER,
        campaign_id,
        business_name="Mochilas Ana",
        now=SENDS_STOP - timedelta(seconds=3),
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

    # The middle row is written before the request leaves, so a pass that dies never resends.
    assert [(d.contact_id, d.decision) for d in history] == [
        (contact_id, "allowed"),
        (contact_id, "failed"),
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
        await schedule_send(world.store, SELLER, campaign.id, approved_template=None)

    assert refused.value.code == "TEMPLATE_NOT_APPROVED"
    stored = await world.store.get(SELLER, campaign.id)
    assert stored is not None
    assert stored.status == "preview"


@pytest.mark.parametrize(
    "fields",
    [{"template_name": "otra_plantilla"}, {"template_language": "en_US"}],
    ids=("another template", "another translation"),
)
async def test_fr_064_approval_of_one_template_does_not_open_the_send_for_another(
    world: World, fields: dict[str, str]
) -> None:
    campaign = await preview_campaign(world.deps(), SELLER, create(**fields), now=WEDNESDAY_10)

    with pytest.raises(CampaignConflictError) as refused:
        await schedule_send(world.store, SELLER, campaign.id, approved_template=TEMPLATE)

    assert refused.value.code == "TEMPLATE_NOT_APPROVED"
    stored = await world.store.get(SELLER, campaign.id)
    assert stored is not None
    assert stored.status == "preview"


async def test_as_6_7_a_campaign_is_scheduled_once(world: World) -> None:
    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    scheduled = await schedule_send(world.store, SELLER, campaign.id, approved_template=TEMPLATE)
    with pytest.raises(CampaignConflictError) as again:
        await schedule_send(world.store, SELLER, campaign.id, approved_template=TEMPLATE)

    assert scheduled.status == "scheduled"
    assert again.value.code == "CAMPAIGN_ALREADY_SENT"


async def test_as_6_7_campaigns_belong_to_their_seller(world: World) -> None:
    campaign = await preview_campaign(world.deps(), SELLER, create(), now=WEDNESDAY_10)

    assert await world.store.get(uuid4(), campaign.id) is None
    with pytest.raises(LookupError):
        await schedule_send(world.store, uuid4(), campaign.id, approved_template=TEMPLATE)


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
