"""FR-021, AS-3.1: the webhook ingest checks the signature, keeps the payload, queues each
message once and applies status callbacks (FR-028); the route acknowledges right after.
"""

import json
from uuid import UUID, uuid4

import pytest

from vitrina_conversation.api import InMemoryConversationStore
from vitrina_conversation.ingest import Ingest, InvalidSignatureError, TemplateAccount
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


async def test_as_3_1_one_unreadable_message_does_not_drop_the_delivery(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    good = json.loads(webhook_fixture("text_ad_code_a7"))
    good["entry"][0]["changes"][0]["value"]["messages"].insert(0, {"type": "text"})
    body = json.dumps(good).encode()

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert result.queued == ("wamid.VITRINA-FIXTURE-IN-0001",)
    assert result.payload_id is not None
    assert str(store.webhooks[result.payload_id]["error"]).startswith("messages.0: ")


async def test_as_3_1_a_message_from_a_username_without_a_number_is_kept_not_queued(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = webhook_fixture("text_from_username")

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert (result.queued, queue.jobs) == ((), [])
    assert result.payload_id is not None
    assert store.webhooks[result.payload_id]["error"] is None


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


ACCOUNT_ID = "100000000000002"


def template_event(
    event: str = "APPROVED",
    *,
    account: str = ACCOUNT_ID,
    name: str = "vitrina_reactivacion",
    language: str = "es",
) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": account,
                    "time": 1793114100,
                    "changes": [
                        {
                            "field": "message_template_status_update",
                            "value": {
                                "event": event,
                                "message_template_id": 1000000000000201,
                                "message_template_name": name,
                                "message_template_language": language,
                                "reason": "NONE",
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


class TemplateStatuses:
    def __init__(self) -> None:
        self.written: list[tuple[UUID, str, str]] = []

    async def __call__(self, seller_id: UUID, template_name: str, status: str) -> None:
        self.written.append((seller_id, template_name, status))


def with_templates(
    store: InMemoryConversationStore,
    queue: RecordingQueue,
    statuses: TemplateStatuses,
    *,
    number: str = BUSINESS_NUMBER,
) -> Ingest:
    return Ingest(
        store=store,
        queue=queue,
        sellers=by_number,
        app_secret=SECRET,
        template_account=TemplateAccount(ACCOUNT_ID, number),
        template_status=statuses,
    )


async def test_fr_064_an_approved_template_reaches_the_accounts_seller(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()
    body = webhook_fixture("template_status_update")

    result = await with_templates(store, queue, statuses).receive(body, sign(body, SECRET))

    assert statuses.written == [(SELLER, "vitrina_reactivacion", "approved")]
    assert result.templates == 1
    assert result.payload_id in store.webhooks
    assert queue.jobs == []


async def test_fr_064_a_redelivered_event_writes_the_same_status(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()
    ingest = with_templates(store, queue, statuses)
    body = template_event()

    await ingest.receive(body, sign(body, SECRET))
    await ingest.receive(body, sign(body, SECRET))

    assert statuses.written == [(SELLER, "vitrina_reactivacion", "approved")] * 2


async def test_fr_064_a_disabled_template_closes_the_gate_again(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()
    body = template_event("DISABLED")

    await with_templates(store, queue, statuses).receive(body, sign(body, SECRET))

    assert statuses.written == [(SELLER, "vitrina_reactivacion", "rejected")]


@pytest.mark.parametrize(
    "body",
    [
        template_event(account="100000000000099"),  # another business account on this app
        template_event(name="hello_world"),  # another template of this account
        template_event(language="en_US"),  # another translation: a template of its own
        template_event("FLAGGED"),  # no change of status
        template_event("SOMETHING_NEW"),  # never opens the gate
    ],
)
async def test_fr_064_an_event_that_is_not_about_this_template_changes_nothing(
    store: InMemoryConversationStore, queue: RecordingQueue, body: bytes
) -> None:
    statuses = TemplateStatuses()

    result = await with_templates(store, queue, statuses).receive(body, sign(body, SECRET))

    assert statuses.written == []
    assert result.templates == 0
    assert result.payload_id in store.webhooks  # kept and acknowledged all the same


async def test_fr_064_no_shop_with_the_configured_number_changes_nothing(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()
    body = template_event()
    ingest = with_templates(store, queue, statuses, number="573000000999")

    result = await ingest.receive(body, sign(body, SECRET))

    assert (statuses.written, result.templates) == ([], 0)


async def test_fr_064_without_a_configured_account_the_event_is_only_kept(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    body = template_event()

    result = await ingest(store, queue).receive(body, sign(body, SECRET))

    assert result.templates == 0
    assert result.payload_id in store.webhooks


async def test_fr_064_an_unsigned_template_event_changes_nothing(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()

    with pytest.raises(InvalidSignatureError):
        await with_templates(store, queue, statuses).receive(template_event(), "sha256=00")

    assert statuses.written == []


def message_and_template_event() -> bytes:
    """One delivery holding a customer's message and a template event, as Meta may batch them."""
    delivery = json.loads(webhook_fixture("text_ad_code_a7"))
    delivery["entry"].extend(json.loads(template_event())["entry"])
    return json.dumps(delivery).encode()


async def test_as_3_1_a_mixed_delivery_queues_the_message_and_applies_the_template(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()
    body = message_and_template_event()

    result = await with_templates(store, queue, statuses).receive(body, sign(body, SECRET))

    assert len(result.queued) == 1
    assert [job[2] for job in queue.jobs] == list(result.queued)
    assert statuses.written == [(SELLER, "vitrina_reactivacion", "approved")]
    assert result.templates == 1


async def test_as_3_1_an_unreadable_template_event_does_not_lose_the_message(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    statuses = TemplateStatuses()
    delivery = json.loads(message_and_template_event())
    del delivery["entry"][-1]["changes"][0]["value"]["message_template_name"]
    body = json.dumps(delivery).encode()

    result = await with_templates(store, queue, statuses).receive(body, sign(body, SECRET))

    assert len(result.queued) == 1
    assert (statuses.written, result.templates) == ([], 0)


async def test_as_3_1_a_template_write_that_fails_asks_meta_to_deliver_again(
    store: InMemoryConversationStore, queue: RecordingQueue
) -> None:
    class DatabaseDown(TemplateStatuses):
        async def __call__(self, seller_id: UUID, template_name: str, status: str) -> None:
            raise RuntimeError("the database went away")

    body = message_and_template_event()
    ingest_down = with_templates(store, queue, DatabaseDown())

    with pytest.raises(RuntimeError):
        await ingest_down.receive(body, sign(body, SECRET))

    assert len(queue.jobs) == 1  # already queued; the job's message id stops a second answer
