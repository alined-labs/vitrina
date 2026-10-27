from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_compliance.consents import (
    ConsentRecordCreate,
    ConsentRefusedError,
    InMemoryCompliance,
    opt_out,
    record_consent,
)
from vitrina_compliance.contacts import ContactIdentity, mask_phone

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER_SELLER = UUID("00000000-0000-7000-8000-00000000000b")
NUMBER = "573000000001"
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"
NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


@pytest.fixture
def identity() -> ContactIdentity:
    return ContactIdentity(
        pepper=SecretStr("test-pepper-with-enough-entropy"),
        key=SecretStr(Fernet.generate_key().decode()),
    )


@pytest.fixture
def store(identity: ContactIdentity) -> InMemoryCompliance:
    return InMemoryCompliance(identity, clock=lambda: NOW)


# ── identity ──


def test_art_v_numbers_are_kept_as_a_peppered_hash_and_an_encrypted_copy(
    identity: ContactIdentity,
) -> None:
    sealed = identity.seal("+57 300 000 0001")

    assert identity.hash("573000000001") == identity.hash("+57 300-000-0001") == sealed.hash
    assert identity.hash("573000000002") != sealed.hash
    assert NUMBER.encode() not in sealed.encrypted
    assert identity.open(sealed.encrypted) == NUMBER
    other = ContactIdentity(pepper=SecretStr("another-pepper-entirely"), key=identity.key)
    assert other.hash(NUMBER) != sealed.hash


def test_the_number_shows_only_its_last_four_digits() -> None:
    assert mask_phone("573000004567") == "+57 *** *** 4567"


def test_without_the_pepper_or_the_key_contacts_cannot_be_resolved() -> None:
    with pytest.raises(LookupError, match="CONTACT_HASH_PEPPER and CONTACT_ENCRYPTION_KEY"):
        ContactIdentity.from_settings(pepper=None, key=None)


# ── contacts ──


async def test_resolving_a_number_twice_gives_one_contact_per_seller(
    store: InMemoryCompliance,
) -> None:
    first = await store.resolve_contact(SELLER, "+57 300 000 0001", display_name="Laura")
    again = await store.resolve_contact(SELLER, NUMBER)
    elsewhere = await store.resolve_contact(OTHER_SELLER, NUMBER)

    assert again.id == first.id
    assert elsewhere.id != first.id
    assert first.display_name == "Laura"
    assert first.phone_masked == "+57 *** *** 0001"
    assert first.marketing_consent == "none"
    assert first.opted_out_at is None
    assert await store.phone(SELLER, first.id) == NUMBER
    assert await store.contact(OTHER_SELLER, first.id) is None


# ── consent (R8) ──


async def test_as_6_1_an_affirmative_reply_to_the_prompt_is_recorded_verbatim(
    store: InMemoryCompliance,
) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER)
    evidence = UUID("00000000-0000-7000-8000-0000000000e1")

    record = await record_consent(
        store,
        SELLER,
        contact.id,
        prompt_text=PROMPT,
        response_text="Sí, porfa",
        evidence_message_id=evidence,
    )

    assert (record.purpose, record.status, record.channel) == ("marketing", "granted", "whatsapp")
    assert (record.text_shown, record.response_text) == (PROMPT, "Sí, porfa")
    assert (record.recorded_by, record.evidence_message_id, record.recorded_at) == (
        "agent",
        evidence,
        NOW,
    )
    updated = await store.contact(SELLER, contact.id)
    assert updated is not None
    assert updated.marketing_consent == "granted"


async def test_art_ii_1_no_prompt_or_no_clear_yes_records_nothing(
    store: InMemoryCompliance,
) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER)

    with pytest.raises(ConsentRefusedError, match="no consent prompt"):
        await record_consent(store, SELLER, contact.id, prompt_text=None, response_text="sí")
    with pytest.raises(ConsentRefusedError, match="not an affirmative"):
        await record_consent(
            store, SELLER, contact.id, prompt_text=PROMPT, response_text="mmm, no sé"
        )

    assert await store.consents(SELLER, contact.id) == []


async def test_a_seller_records_consent_given_in_person(store: InMemoryCompliance) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER)

    record = await store.append_consent(
        SELLER,
        contact.id,
        ConsentRecordCreate(
            purpose="marketing",
            status="granted",
            text_shown="Formulario en el local: ¿acepta recibir promociones por WhatsApp?",
            response_text="Firmó que sí",
            channel="in_person",
        ),
        recorded_by="seller",
    )

    assert record.recorded_by == "seller"
    assert (await store.consents(SELLER, contact.id))[0].id == record.id


# ── opt-out (R7) ──


async def test_as_6_5_opt_out_revokes_marketing_and_holds_its_first_time(
    store: InMemoryCompliance,
) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER)
    await record_consent(store, SELLER, contact.id, prompt_text=PROMPT, response_text="sí")

    opted = await opt_out(store, SELLER, contact.id, via="keyword", response_text="BAJA")
    again = await opt_out(store, SELLER, contact.id, via="api")

    assert opted.opted_out_at == NOW
    assert again.opted_out_at == NOW
    assert opted.marketing_consent == "revoked"
    assert await store.is_opted_out(SELLER, contact.id)
    history = await store.consents(SELLER, contact.id)
    # Newest first; the ledger only grows (FR-060): grant, then one revocation per request.
    assert [(r.status, r.recorded_by) for r in history] == [
        ("revoked", "seller"),
        ("revoked", "system"),
        ("granted", "agent"),
    ]
    assert store.events == [
        (
            "consent.recorded",
            {
                "contact_id": str(contact.id),
                "purpose": "marketing",
                "consent_id": str(history[2].id),
            },
        ),
        (
            "consent.revoked",
            {"contact_id": str(contact.id), "purpose": "marketing", "via": "keyword"},
        ),
        ("consent.revoked", {"contact_id": str(contact.id), "purpose": "marketing", "via": "api"}),
    ]
    assert history[-1].response_text == "sí"
    assert history[1].response_text == "BAJA"


async def test_opt_out_of_an_unknown_contact_is_a_lookup_error(store: InMemoryCompliance) -> None:
    with pytest.raises(LookupError):
        await opt_out(store, SELLER, UUID(int=1), via="api")


async def test_contacts_filter_by_their_current_marketing_consent(
    store: InMemoryCompliance,
) -> None:
    granted = await store.resolve_contact(SELLER, "573000000001")
    revoked = await store.resolve_contact(SELLER, "573000000002")
    none = await store.resolve_contact(SELLER, "573000000003")
    await record_consent(store, SELLER, granted.id, prompt_text=PROMPT, response_text="sí")
    await record_consent(store, SELLER, revoked.id, prompt_text=PROMPT, response_text="dale")
    await opt_out(store, SELLER, revoked.id, via="keyword", response_text="baja")

    async def ids(state: str | None) -> list[UUID]:
        items, _ = await store.list_contacts(SELLER, limit=10, marketing_consent=state)  # type: ignore[arg-type]
        return [item.id for item in items]

    assert await ids("granted") == [granted.id]
    assert await ids("revoked") == [revoked.id]
    assert await ids("none") == [none.id]
    assert set(await ids(None)) == {granted.id, revoked.id, none.id}
