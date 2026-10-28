"""T073 live counters of the conversation module, per seller (FR-070, AS-7.1): today's chats,
the share of them that came from an ad, and the handoffs the owner still holds. Replays never
count.
"""

from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_conversation.api import PostgresConversationStore
from vitrina_conversation.metrics import counters
from vitrina_conversation.tables import conversations, handoffs, messages
from vitrina_core.ids import new_id

SINCE = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)  # 00:00 in Bogotá
TODAY = SINCE + timedelta(hours=10)
YESTERDAY = SINCE - timedelta(hours=2)


def test_the_counters_are_registered_for_the_metrics_summary() -> None:
    names = {point.name: point.value for point in entry_points(group="vitrina.metrics")}
    assert names.get("conversation") == "vitrina_conversation.metrics:counters"


@pytest.mark.db
async def test_db_as_7_1_todays_chats_their_ads_and_the_open_handoffs(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    sellers = PostgresSellerStore(db_sessions)

    async def seller(number: str) -> UUID:
        created = await sellers.create(
            SellerCreate(
                business_name="Mochilas Ana", owner_name="Ana", whatsapp_number=number, city="Cali"
            ),
            auth_subject=None,
        )
        return created.id

    shop, other, quiet = (
        await seller("+573000000151"),
        await seller("+573000000152"),
        await seller("+573000000153"),
    )
    identity = ContactIdentity(
        pepper=SecretStr("metrics-db-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(db_sessions, identity)
    store = PostgresConversationStore(db_sessions)

    async def chat(
        seller_id: UUID, number: str, *, at: datetime, ad: bool = False, replay: bool = False
    ) -> UUID:
        person = await compliance.resolve_contact(seller_id, number)
        conversation = await store.open_conversation(seller_id, person.id, is_replay=replay)
        said = await store.add_message(
            seller_id,
            conversation.id,
            direction="inbound",
            sender="customer",
            type="text",
            text="Hola",
            wa_message_id=f"wamid.VITRINA-METRICS-{number}",
        )
        assert said is not None
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.update(messages).where(messages.c.id == said.id).values(created_at=at)
            )
            if ad:
                await session.execute(
                    sa.update(conversations)
                    .where(conversations.c.id == conversation.id)
                    .values(attribution_status="attributed")
                )
        return conversation.id

    async def handoff(seller_id: UUID, conversation_id: UUID, *, released: bool) -> None:
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.insert(handoffs).values(
                    id=new_id(),
                    seller_id=seller_id,
                    conversation_id=conversation_id,
                    reason="customer_request",
                    released_at=TODAY if released else None,
                )
            )

    from_ad = await chat(shop, "573000000011", at=TODAY, ad=True)
    plain = await chat(shop, "573000000012", at=TODAY)
    older = await chat(shop, "573000000013", at=YESTERDAY, ad=True)
    replayed = await chat(shop, "573000000014", at=TODAY, ad=True, replay=True)
    elsewhere = await chat(other, "573000000015", at=TODAY, ad=True)
    await handoff(shop, from_ad, released=False)
    await handoff(shop, plain, released=True)
    await handoff(shop, older, released=False)  # still open: open handoffs have no day
    await handoff(shop, replayed, released=False)
    await handoff(other, elsewhere, released=False)

    counted = await counters(db_sessions, shop, SINCE)
    nothing = await counters(db_sessions, quiet, SINCE)

    assert counted == {"conversations_today": 2, "attributed_rate": 0.5, "handoffs_open": 2}
    assert nothing == {"conversations_today": 0, "attributed_rate": 0.0, "handoffs_open": 0}
