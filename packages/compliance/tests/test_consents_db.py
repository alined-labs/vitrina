from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.consents import (
    ConsentRecordCreate,
    ConsentRefusedError,
    PostgresCompliance,
    opt_out,
    record_consent,
)
from vitrina_compliance.contacts import ContactIdentity
from vitrina_compliance.tables import consent_records, contacts
from vitrina_core.db.tables import events, sellers
from vitrina_core.ids import new_id

PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"


async def seller(sessions: async_sessionmaker[AsyncSession], number: str) -> UUID:
    seller_id = new_id()
    async with sessions() as session, session.begin():
        await session.execute(
            sa.insert(sellers).values(
                id=seller_id,
                business_name="Mochilas Ana",
                owner_name="Ana",
                whatsapp_number=number,
                city="Medellín",
            )
        )
    return seller_id


@pytest.fixture
def identity() -> ContactIdentity:
    return ContactIdentity(
        pepper=SecretStr("db-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )


@pytest.mark.db
async def test_db_fr_060_the_ledger_is_append_only_and_drives_the_contact_state(
    db_sessions: async_sessionmaker[AsyncSession], identity: ContactIdentity
) -> None:
    store = PostgresCompliance(db_sessions, identity)
    shop = await seller(db_sessions, "+573000000201")
    other_shop = await seller(db_sessions, "+573000000202")

    contact = await store.resolve_contact(shop, "+57 300 000 0001", display_name="Laura")
    again = await store.resolve_contact(shop, "573000000001", display_name="Otra")
    elsewhere = await store.resolve_contact(other_shop, "573000000001")
    assert again.id == contact.id
    assert again.display_name == "Laura"
    assert elsewhere.id != contact.id
    assert contact.phone_masked == "+57 *** *** 0001"
    assert await store.phone(shop, contact.id) == "573000000001"
    async with db_sessions() as session:
        stored = await session.scalar(
            sa.select(contacts.c.wa_id_enc).where(contacts.c.id == contact.id)
        )
    assert b"573000000001" not in bytes(stored)  # Art. V: never in the clear

    granted = await record_consent(
        store, shop, contact.id, prompt_text=PROMPT, response_text="dale"
    )
    assert (await store.contact(shop, contact.id)).marketing_consent == "granted"  # type: ignore[union-attr]
    opted = await opt_out(store, shop, contact.id, via="keyword", response_text="BAJA")
    assert opted.marketing_consent == "revoked"
    assert opted.opted_out_at is not None
    assert await store.is_opted_out(shop, contact.id)
    assert [r.status for r in await store.consents(shop, contact.id)] == ["revoked", "granted"]
    assert await store.contact(other_shop, contact.id) is None

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.update(consent_records)
                .where(consent_records.c.id == granted.id)
                .values(status="revoked")
            )

    async with db_sessions() as session:
        types = (
            (
                await session.execute(
                    sa.select(events.c.type).where(events.c.seller_id == shop).order_by(events.c.id)
                )
            )
            .scalars()
            .all()
        )
    assert types == ["consent.recorded", "consent.revoked"]

    granted_page, _ = await store.list_contacts(shop, limit=10, marketing_consent="granted")
    revoked_page, _ = await store.list_contacts(shop, limit=10, marketing_consent="revoked")
    assert granted_page == []
    assert [item.id for item in revoked_page] == [contact.id]


@pytest.mark.db
async def test_db_opting_out_an_unknown_contact_is_a_lookup_error(
    db_sessions: async_sessionmaker[AsyncSession], identity: ContactIdentity
) -> None:
    store = PostgresCompliance(db_sessions, identity)
    shop = await seller(db_sessions, "+573000000203")

    with pytest.raises(LookupError):
        await opt_out(store, shop, new_id(), via="api")


@pytest.mark.db
async def test_db_after_an_opt_out_a_new_yes_records_nothing(
    db_sessions: async_sessionmaker[AsyncSession], identity: ContactIdentity
) -> None:
    store = PostgresCompliance(db_sessions, identity)
    shop = await seller(db_sessions, "+573000000204")
    contact = await store.resolve_contact(shop, "573000000002")
    await record_consent(store, shop, contact.id, prompt_text=PROMPT, response_text="dale")
    await opt_out(store, shop, contact.id, via="keyword", response_text="BAJA")
    paper_form = ConsentRecordCreate(
        purpose="marketing",
        status="granted",
        text_shown="Formulario en el local",
        response_text="Firmó que sí",
        channel="in_person",
    )

    with pytest.raises(ConsentRefusedError, match="opted out"):
        await record_consent(store, shop, contact.id, prompt_text=PROMPT, response_text="sí")
    with pytest.raises(ConsentRefusedError, match="opted out"):
        await store.append_consent(shop, contact.id, paper_form, recorded_by="seller")
    processing = await store.append_consent(
        shop,
        contact.id,
        paper_form.model_copy(update={"purpose": "data_processing"}),
        recorded_by="seller",
    )

    after = await store.contact(shop, contact.id)
    assert after is not None
    assert after.marketing_consent == "revoked"
    assert [(r.purpose, r.status) for r in await store.consents(shop, contact.id)] == [
        ("data_processing", "granted"),
        ("marketing", "revoked"),
        ("marketing", "granted"),
    ]
    assert processing.purpose == "data_processing"
