"""T064 on Postgres: campaigns, every decision kept, the contact's last marketing send."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.calendar import BOGOTA
from vitrina_compliance.campaigns import (
    CAMPAIGN_SEND,
    SEND_NOT_TRIED_ES,
    AdRef,
    CampaignDeps,
    Candidate,
    PostgresCampaigns,
    ReactivationCampaignCreate,
    Segment,
    finish_stranded_campaigns,
    preview_campaign,
    run_send,
    schedule_send,
)
from vitrina_compliance.consents import PostgresCompliance, record_consent
from vitrina_compliance.contacts import ContactIdentity
from vitrina_compliance.eligibility import REASON_TEXT_ES, SendDecision
from vitrina_compliance.tables import contacts, send_decisions
from vitrina_core.db.tables import jobs, sellers
from vitrina_core.ids import new_id
from vitrina_core.jobs import PostgresJobQueue
from vitrina_core.providers import OutboundMessage, SendResult

PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"
PRODUCT = UUID("00000000-0000-7000-8000-0000000000b1")
AD = AdRef(UUID("00000000-0000-7000-8000-0000000000d1"), "A7")
WEDNESDAY_10 = datetime(2026, 10, 28, 10, 0, tzinfo=BOGOTA)


class Sent:
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> SendResult:
        self.messages.append(message)
        return SendResult(message_id="wamid.db", status="sent")


@pytest.mark.db
async def test_db_as_6_7_a_campaign_keeps_every_decision_and_marks_the_send(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    identity = ContactIdentity(
        pepper=SecretStr("db-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(db_sessions, identity)
    shop = new_id()
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.insert(sellers).values(
                id=shop,
                business_name="Mochilas Ana",
                owner_name="Ana",
                whatsapp_number="+573000000231",
                city="Medellín",
            )
        )
    granted = await compliance.resolve_contact(shop, "573000000011", display_name="Laura")
    await record_consent(compliance, shop, granted.id, prompt_text=PROMPT, response_text="sí")
    silent = await compliance.resolve_contact(shop, "573000000012")

    async def candidates(
        seller_id: UUID,
        segment: Segment,
        *,
        now: datetime,
        contact_ids: Sequence[UUID] | None = None,
    ) -> list[Candidate]:
        found = [
            Candidate(granted.id, "shipping", PRODUCT, "Mochila tejida azul"),
            Candidate(silent.id, "price", PRODUCT, "Mochila tejida azul"),
        ]
        return [c for c in found if contact_ids is None or c.contact_id in contact_ids]

    async def ads(seller_id: UUID, product_id: UUID, *, ad_id: UUID | None = None) -> AdRef:
        return AD

    store = PostgresCampaigns(db_sessions)
    messenger = Sent()
    deps = CampaignDeps(store, compliance, candidates, ads, messenger=messenger)
    create = ReactivationCampaignCreate(template_name="vitrina_reactivacion")

    preview = await preview_campaign(deps, shop, create, now=WEDNESDAY_10)
    assert (preview.counts.allowed, preview.counts.blocked) == (1, 1)
    assert preview.scheduled_for == WEDNESDAY_10
    await schedule_send(store, shop, preview.id, approved_template="vitrina_reactivacion")
    run = await run_send(deps, shop, preview.id, business_name="Mochilas Ana", now=WEDNESDAY_10)

    assert run.campaign.status == "sent"
    assert (run.campaign.counts.sent, run.campaign.counts.blocked) == (1, 1)
    assert [m.to for m in messenger.messages] == ["573000000011"]
    contact = await compliance.contact(shop, granted.id)
    assert contact is not None
    assert contact.last_marketing_sent_at == WEDNESDAY_10
    async with db_sessions() as session:
        kept = (
            await session.execute(
                sa.select(send_decisions.c.contact_id, send_decisions.c.decision).where(
                    send_decisions.c.campaign_id == preview.id
                )
            )
        ).all()
    assert sorted(kept, key=lambda row: (row.contact_id == silent.id, row.decision)) == [
        (granted.id, "allowed"),
        (granted.id, "failed"),  # written before the request left, replaced by the next row
        (granted.id, "sent"),
        (silent.id, "blocked"),
    ]

    later = await preview_campaign(deps, shop, create, now=WEDNESDAY_10 + timedelta(days=1))
    page, cursor = await store.page(shop, limit=1)
    assert ([c.id for c in page], cursor is not None) == ([later.id], True)
    rest, end = await store.page(shop, limit=1, cursor=cursor)
    assert ([c.id for c in rest], end) == ([preview.id], None)
    assert await store.get(new_id(), preview.id) is None


async def _contact_sent_at(
    db_sessions: async_sessionmaker[AsyncSession], contact_id: UUID
) -> datetime | None:
    async with db_sessions() as session:
        return await session.scalar(
            sa.select(contacts.c.last_marketing_sent_at).where(contacts.c.id == contact_id)
        )


async def _waiting_campaign(
    db_sessions: async_sessionmaker[AsyncSession], number: str
) -> tuple[UUID, UUID, UUID, PostgresCampaigns]:
    """A shop with one consenting contact and a campaign that holds them as ``allowed``."""
    identity = ContactIdentity(
        pepper=SecretStr("db-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(db_sessions, identity)
    shop = new_id()
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.insert(sellers).values(
                id=shop,
                business_name="Mochilas Ana",
                owner_name="Ana",
                whatsapp_number=number,
                city="Medellín",
            )
        )
    person = await compliance.resolve_contact(shop, "573000000011")
    await record_consent(compliance, shop, person.id, prompt_text=PROMPT, response_text="sí")

    async def candidates(
        seller_id: UUID,
        segment: Segment,
        *,
        now: datetime,
        contact_ids: Sequence[UUID] | None = None,
    ) -> list[Candidate]:
        return [Candidate(person.id, "shipping", PRODUCT, "Mochila tejida azul")]

    async def ads(seller_id: UUID, product_id: UUID, *, ad_id: UUID | None = None) -> AdRef:
        return AD

    store = PostgresCampaigns(db_sessions)
    deps = CampaignDeps(store, compliance, candidates, ads)
    preview = await preview_campaign(
        deps,
        shop,
        ReactivationCampaignCreate(template_name="vitrina_reactivacion"),
        now=WEDNESDAY_10,
    )
    return shop, preview.id, person.id, store


@pytest.mark.db
async def test_db_fr_064_a_claim_takes_the_contact_and_the_slot_in_one_transaction(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    shop, campaign_id, contact_id, store = await _waiting_campaign(db_sessions, "+573000000232")
    claim = SendDecision(
        id=new_id(),
        campaign_id=campaign_id,
        contact_id=contact_id,
        decision="failed",
        reason="SEND_FAILED",
        reason_text_es=REASON_TEXT_ES["SEND_FAILED"],
        evaluated_at=WEDNESDAY_10,
    )
    earlier = WEDNESDAY_10 - timedelta(days=1)
    long_ago = WEDNESDAY_10 - timedelta(days=8)

    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.update(contacts)
            .where(contacts.c.id == contact_id)
            .values(last_marketing_sent_at=earlier)
        )
    capped = await store.claim_send(campaign_id, claim, at=WEDNESDAY_10)
    assert capped == "capped"
    assert await _contact_sent_at(db_sessions, contact_id) == earlier
    still = await store.get(shop, campaign_id)
    assert still is not None
    assert [d.decision for d in still.decisions] == ["allowed"]  # a refused claim writes nothing

    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.update(contacts)
            .where(contacts.c.id == contact_id)
            .values(last_marketing_sent_at=long_ago)
        )
    first = await store.claim_send(campaign_id, claim, at=WEDNESDAY_10)
    second = await store.claim_send(
        campaign_id, claim.model_copy(update={"id": new_id()}), at=WEDNESDAY_10
    )

    assert (first, second) == ("claimed", "taken")
    assert await _contact_sent_at(db_sessions, contact_id) == WEDNESDAY_10
    campaign = await store.get(shop, campaign_id)
    assert campaign is not None
    assert [d.decision for d in campaign.decisions] == ["failed"]

    # A later send is never undone; the claim's own stamp goes back to what it replaced.
    await store.release_send(
        shop, contact_id, claimed_at=WEDNESDAY_10 - timedelta(hours=1), previous=long_ago
    )
    assert await _contact_sent_at(db_sessions, contact_id) == WEDNESDAY_10
    await store.release_send(shop, contact_id, claimed_at=WEDNESDAY_10, previous=long_ago)
    assert await _contact_sent_at(db_sessions, contact_id) == long_ago
    await store.release_send(new_id(), contact_id, claimed_at=long_ago, previous=None)
    assert await _contact_sent_at(db_sessions, contact_id) == long_ago  # another seller's


@pytest.mark.db
async def test_db_fr_064_a_campaign_whose_job_ended_is_finished_by_the_sweep(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    shop, stranded, _, store = await _waiting_campaign(db_sessions, "+573000000233")
    other, waiting, _, _ = await _waiting_campaign(db_sessions, "+573000000234")
    no_job, unqueued, _, _ = await _waiting_campaign(db_sessions, "+573000000235")
    busy, running, _, _ = await _waiting_campaign(db_sessions, "+573000000236")
    await schedule_send(store, shop, stranded, approved_template="vitrina_reactivacion")
    await store.set_status(shop, stranded, "sending", expect="scheduled")
    await schedule_send(store, other, waiting, approved_template="vitrina_reactivacion")
    await store.set_status(other, waiting, "sending", expect="scheduled")
    await schedule_send(store, no_job, unqueued, approved_template="vitrina_reactivacion")
    await schedule_send(store, busy, running, approved_template="vitrina_reactivacion")
    await store.set_status(busy, running, "sending", expect="scheduled")
    async with db_sessions() as session, session.begin():
        queue = PostgresJobQueue(db_sessions)
        for owner, campaign in ((shop, stranded), (other, waiting), (busy, running)):
            await queue.enqueue(
                session,
                kind="campaign_send",
                task=CAMPAIGN_SEND,
                payload={"campaign_id": str(campaign)},
                owner_seller_id=owner,
            )
        await session.execute(
            sa.update(jobs)
            .where(jobs.c.owner_seller_id == shop)
            .values(status="failed", attempts=jobs.c.max_attempts)
        )
        await session.execute(
            sa.update(jobs).where(jobs.c.owner_seller_id == busy).values(status="running")
        )

    changed = await finish_stranded_campaigns(db_sessions, WEDNESDAY_10)

    assert changed == 1
    finished = await store.get(shop, stranded)
    assert finished is not None
    assert (finished.status, finished.counts.failed, finished.counts.allowed) == ("sent", 1, 0)
    assert [d.reason_text_es for d in finished.decisions] == [SEND_NOT_TRIED_ES]
    live = await store.get(other, waiting)
    assert live is not None
    assert (live.status, live.counts.allowed) == ("sending", 1)  # its job is still queued
    mid_pass = await store.get(busy, running)
    assert mid_pass is not None
    assert (mid_pass.status, mid_pass.counts.allowed) == ("sending", 1)  # its job is running
    just_scheduled = await store.get(no_job, unqueued)
    assert just_scheduled is not None
    assert just_scheduled.status == "scheduled"  # its job may be a moment from being queued
