"""T064's candidates on Postgres (AS-6.7; CI's db job): who a reactivation campaign writes to.

A candidate wrote at least ``no_purchase_since_days`` ago and booked nothing since; the last
objection says what the message answers and which product it is about. Replays never count.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.postgres import PostgresCatalog
from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_catalog.tables import products
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_compliance.campaigns import Segment
from vitrina_conversation.api import PostgresConversationStore
from vitrina_conversation.campaigns import PostgresCandidates, outbox
from vitrina_conversation.tables import bookings, messages, objections
from vitrina_core.ids import new_id

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


async def test_db_as_6_7_candidates_are_quiet_unbooked_contacts_with_their_interest(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    seller = await PostgresSellerStore(db_sessions).create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000141",
            city="Medellín",
        ),
        auth_subject=None,
    )
    mochila = new_id()
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.insert(products).values(
                id=mochila, seller_id=seller.id, name="Mochila tejida azul", price_cop=85000
            )
        )
    identity = ContactIdentity(
        pepper=SecretStr("campaigns-db-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(db_sessions, identity)
    store = PostgresConversationStore(db_sessions)

    async def contact(number: str, *, days_ago: int, replay: bool = False) -> tuple[UUID, UUID]:
        person = await compliance.resolve_contact(seller.id, number)
        chat = await store.open_conversation(seller.id, person.id, is_replay=replay)
        said = await store.add_message(
            seller.id,
            chat.id,
            direction="inbound",
            sender="customer",
            type="text",
            text="¿Cuánto vale el envío?",
            wa_message_id=f"wamid.VITRINA-CAMPAIGN-{number}",
        )
        assert said is not None
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.update(messages)
                .where(messages.c.id == said.id)
                .values(created_at=NOW - timedelta(days=days_ago))
            )
        return person.id, chat.id

    async def objected(chat: UUID, tag: str, *, product: UUID | None, days_ago: int) -> None:
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.insert(objections).values(
                    id=new_id(),
                    seller_id=seller.id,
                    conversation_id=chat,
                    product_id=product,
                    tag=tag,
                    occurred_at=NOW - timedelta(days=days_ago),
                )
            )

    quiet, quiet_chat = await contact("573000000011", days_ago=5)
    await objected(quiet_chat, "price", product=None, days_ago=6)
    await objected(quiet_chat, "shipping", product=mochila, days_ago=5)
    recent, _ = await contact("573000000012", days_ago=1)
    booked, booked_chat = await contact("573000000013", days_ago=4)
    async with db_sessions() as session, session.begin():
        await session.execute(
            sa.insert(bookings).values(
                id=new_id(),
                seller_id=seller.id,
                conversation_id=booked_chat,
                service="Arreglo",
                starts_at=NOW + timedelta(days=1),
                created_at=NOW - timedelta(days=2),
            )
        )
    plain, _ = await contact("573000000014", days_ago=10)
    await contact("573000000015", days_ago=8, replay=True)

    candidates = PostgresCandidates(db_sessions, PostgresCatalog(db_sessions))
    found = await candidates(seller.id, Segment(no_purchase_since_days=3), now=NOW)

    assert [(c.contact_id, c.objection_tag, c.product_id, c.product_name) for c in found] == [
        (quiet, "shipping", mochila, "Mochila tejida azul"),  # newest message first
        (plain, None, None, None),
    ]
    shipping = await candidates(
        seller.id, Segment(objection_tags=["shipping"], max_contacts=5), now=NOW
    )
    assert [c.contact_id for c in shipping] == [quiet]
    capped = await candidates(seller.id, Segment(max_contacts=1), now=NOW)
    assert [c.contact_id for c in capped] == [quiet]
    named = await candidates(seller.id, Segment(), now=NOW, contact_ids=[recent, booked])
    assert {c.contact_id for c in named} == {recent, booked}

    record = outbox(store)
    message_id = await record(
        seller.id, quiet, text="Hola, te escribe Mochilas Ana.", wa_id="wamid.T"
    )
    async with db_sessions() as session:
        sent = (
            (await session.execute(sa.select(messages).where(messages.c.id == message_id)))
            .mappings()
            .one()
        )
    assert (sent["conversation_id"], sent["type"], sent["sender"], sent["wa_message_id"]) == (
        quiet_chat, "template", "system", "wamid.T"
    )  # fmt: skip
