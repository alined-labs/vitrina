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
    AdRef,
    CampaignDeps,
    Candidate,
    PostgresCampaigns,
    ReactivationCampaignCreate,
    Segment,
    preview_campaign,
    run_send,
    schedule_send,
)
from vitrina_compliance.consents import PostgresCompliance, record_consent
from vitrina_compliance.contacts import ContactIdentity
from vitrina_compliance.tables import send_decisions
from vitrina_core.db.tables import sellers
from vitrina_core.ids import new_id
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
    await schedule_send(store, shop, preview.id, template_approved=True)
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
        (granted.id, "sent"),
        (silent.id, "blocked"),
    ]

    later = await preview_campaign(deps, shop, create, now=WEDNESDAY_10 + timedelta(days=1))
    page, cursor = await store.page(shop, limit=1)
    assert ([c.id for c in page], cursor is not None) == ([later.id], True)
    rest, end = await store.page(shop, limit=1, cursor=cursor)
    assert ([c.id for c in rest], end) == ([preview.id], None)
    assert await store.get(new_id(), preview.id) is None
