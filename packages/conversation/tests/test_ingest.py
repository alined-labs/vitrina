"""FR-021, AS-3.1: the webhook ingest checks the signature, keeps the payload, queues each
message once and applies status callbacks (FR-028); the route acknowledges right after.
"""

from uuid import UUID, uuid4

import pytest

from vitrina_conversation.api import InMemoryConversationStore
from vitrina_conversation.ingest import Ingest, InvalidSignatureError
from vitrina_conversation.testing import webhook_fixture
from vitrina_whatsapp.signature import sign

SECRET = "fixture-app-secret"
SELLER = uuid4()
BUSINESS_NUMBER = "573000000000"


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[UUID, UUID, str]] = []

    async def enqueue(self, seller_id: UUID, payload_id: UUID, message_id: str) -> None:
        self.jobs.append((seller_id, payload_id, message_id))


async def by_number(phone_number_id: str, business_number: str) -> UUID | None:
    return SELLER if business_number == BUSINESS_NUMBER else None


def ingest(store: InMemoryConversationStore, queue: RecordingQueue) -> Ingest:
    return Ingest(store=store, queue=queue, sellers=by_number, app_secret=SECRET)


@pytest.fixture
def store() -> InMemoryConversationStore:
    return InMemoryConversationStore()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


async def test_as_3_1_a_signed_payload_is_kept_and_each_message_queued(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = webhook_fixture("batch_two_customers")

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert result.queued == ("wamid.VITRINA-FIXTURE-IN-0011", "wamid.VITRINA-FIXTURE-IN-0012")
    assert queue.jobs == [
        (SELLER, result.payload_id, "wamid.VITRINA-FIXTURE-IN-0011"),
        (SELLER, result.payload_id, "wamid.VITRINA-FIXTURE-IN-0012"),
    ]
    assert result.payload_id is not None
    assert store.webhooks[result.payload_id]["signature_valid"]


@pytest.mark.parametrize("signature", [None, "", "sha256=" + "0" * 64])
async def test_as_3_1_an_invalid_signature_is_refused_and_nothing_is_kept(
    store: InMemoryConversationStore, queue: RecordingQueue, signature: str | None
) -> None:
    with pytest.raises(InvalidSignatureError):
        await ingest(store, queue).receive(webhook_fixture("text_ad_code_a7"), signature)

    assert (store.webhooks, queue.jobs) == ({}, [])


async def test_as_3_1_an_unset_app_secret_refuses_everything(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = webhook_fixture("text_ad_code_a7")

    with pytest.raises(InvalidSignatureError):
        await Ingest(store=store, queue=queue, sellers=by_number, app_secret="").receive(
            body, sign(body, "")
        )


async def test_fr_028_status_callbacks_update_the_sent_message(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    conversation = await store.open_conversation(SELLER, uuid4())
    sent = await store.add_message(
        SELLER,
        conversation.id,
        direction="outbound",
        sender="agent",
        type="text",
        text="¡Hola!",
        delivery_status="sent",
        wa_message_id="wamid.VITRINA-FIXTURE-OUT-0001",
    )
    assert sent is not None
    body = webhook_fixture("status_read")

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert result.statuses == 1
    assert store.messages[-1].stored.delivery_status == "read"
    assert queue.jobs == []


async def test_as_3_1_a_malformed_payload_is_kept_with_its_error_and_acknowledged(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = b'{"object": "page", "entry": []}'

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert result.queued == ()
    assert result.payload_id is not None
    assert store.webhooks[result.payload_id]["body"] == {"object": "page", "entry": []}


async def test_as_3_1_messages_to_an_unknown_business_number_are_not_queued(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = webhook_fixture("text_ad_code_a7").replace(b'"573000000000"', b'"573000000099"')

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert (result.queued, queue.jobs) == ((), [])


async def test_as_3_1_other_webhook_fields_are_acknowledged(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = webhook_fixture("template_status_update")

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert (result.queued, result.statuses) == ((), 0)
