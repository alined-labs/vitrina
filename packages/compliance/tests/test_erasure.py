from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_compliance.consents import InMemoryCompliance, record_consent
from vitrina_compliance.contacts import ContactIdentity

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
NUMBER = "573000000001"
PROMPT = "¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"
NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


@pytest.fixture
def store() -> InMemoryCompliance:
    identity = ContactIdentity(
        pepper=SecretStr("erasure-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    return InMemoryCompliance(identity, clock=lambda: NOW)


async def test_as_6_6_erasure_removes_the_number_and_name_and_keeps_a_hash_receipt(
    store: InMemoryCompliance,
) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER, display_name="Laura")
    await record_consent(store, SELLER, contact.id, prompt_text=PROMPT, response_text="sí")
    requested = NOW - timedelta(hours=2)

    receipt = await store.erase(SELLER, contact.id, requested_at=requested, scope=("contact",))

    erased = await store.contact(SELLER, contact.id)
    assert erased is not None
    assert (erased.display_name, erased.erased_at) == (None, NOW)
    assert "phone_masked" not in erased.model_dump()
    assert await store.phone(SELLER, contact.id) is None
    # Erasure ends marketing for good (Art. II.3).
    assert erased.opted_out_at == NOW
    assert (receipt.requested_at, receipt.completed_at, receipt.scope) == (
        requested,
        NOW,
        "contact",
    )
    assert receipt.wa_id_hash == store.identity.hash(NUMBER)  # type: ignore[union-attr]
    assert NUMBER not in repr(receipt)
    # The ledger stays (ADR-0009): it proves what was consented and when.
    assert [record.status for record in await store.consents(SELLER, contact.id)] == ["granted"]


async def test_a_second_erasure_returns_the_first_receipt(store: InMemoryCompliance) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER)
    first = await store.erase(SELLER, contact.id, requested_at=NOW, scope=("contact",))

    again = await store.erase(SELLER, contact.id, requested_at=NOW, scope=("contact",))

    assert again == first


async def test_writing_again_after_erasure_keeps_the_opt_out(store: InMemoryCompliance) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER, display_name="Laura")
    await store.erase(SELLER, contact.id, requested_at=NOW, scope=("contact",))

    back = await store.resolve_contact(SELLER, NUMBER, display_name="Laura")

    # The number is needed to answer the new message; marketing stays off forever.
    assert back.id == contact.id
    assert await store.phone(SELLER, contact.id) == NUMBER
    assert back.opted_out_at == NOW
    assert await store.is_opted_out(SELLER, contact.id)


async def test_erasing_an_unknown_contact_is_a_lookup_error(store: InMemoryCompliance) -> None:
    with pytest.raises(LookupError):
        await store.erase(SELLER, UUID(int=1), requested_at=NOW, scope=("contact",))


async def test_coming_back_and_asking_again_erases_again(store: InMemoryCompliance) -> None:
    contact = await store.resolve_contact(SELLER, NUMBER)
    first = await store.erase(SELLER, contact.id, requested_at=NOW, scope=("contact",))
    await store.resolve_contact(SELLER, NUMBER)

    second = await store.erase(SELLER, contact.id, requested_at=NOW, scope=("contact",))

    assert second.id != first.id
    assert await store.phone(SELLER, contact.id) is None


def test_modules_register_their_part_of_the_erasure() -> None:
    from vitrina_compliance.erasure import load_erasers

    async def erase_messages(ctx: object, seller_id: UUID, contact_id: UUID) -> str:
        return "messages"

    class Point:
        def load(self) -> object:
            return erase_messages

    assert load_erasers([Point()]) == [erase_messages]  # type: ignore[list-item]
