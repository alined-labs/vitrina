"""T027 inbound pipeline: one customer message from WhatsApp to the paced reply (plan.md §4.1).

In-memory store and compliance, the scripted Spark, a recording messenger, a fake clock.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_catalog.api import SellerSettings
from vitrina_compliance.api import ContactIdentity, InMemoryCompliance
from vitrina_conversation.api import (
    AgentDeps,
    Inbound,
    InboundOutcome,
    InMemoryConversationStore,
    Pacer,
    PipelineDeps,
    process_inbound,
)
from vitrina_conversation.booking import InMemoryBookings
from vitrina_conversation.image import PhotoReading
from vitrina_conversation.pipeline import (
    ERASURE_REPLY,
    OPT_OUT_REPLY,
    PHOTO_NOT_SEEN_REPLY,
    TEXT_ONLY_REPLY,
    VOICE_NOT_HEARD_REPLY,
)
from vitrina_conversation.scope import decline_reply
from vitrina_conversation.testing import (
    AdGallery,
    ScriptedChat,
    Shop,
    final_answer,
    scope_answer,
    tool_call,
    webhook_fixture,
)
from vitrina_conversation.wiring import process_stored_message
from vitrina_core.providers import (
    ChatMessage,
    OutboundImage,
    OutboundMessage,
    OutboundText,
    ProviderError,
    SendResult,
)

CUSTOMER = "573000000001"
AD_A7 = uuid4()
FALLBACK = "No tengo ese dato, te comunico con Ana"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class RecordingMessenger:
    clock: FakeClock
    sent: list[tuple[float, OutboundMessage]] = field(default_factory=list)
    failure: ProviderError | None = None
    failing_kinds: tuple[str, ...] = ()
    """Message kinds the failure applies to; all of them when empty."""

    async def send(self, message: OutboundMessage) -> SendResult:
        if self.failure is not None and message.kind in (self.failing_kinds or (message.kind,)):
            raise self.failure
        self.sent.append((self.clock.now, message))
        return SendResult(message_id=f"wamid.VITRINA-TEST-OUT-{len(self.sent):04d}", status="sent")

    @property
    def texts(self) -> list[str]:
        return [message.text for _, message in self.sent if isinstance(message, OutboundText)]

    @property
    def images(self) -> list[OutboundImage]:
        return [message for _, message in self.sent if isinstance(message, OutboundImage)]


@dataclass
class FakeVoice:
    """Voice notes by media id, as Transcribe would hear them."""

    transcripts: dict[str, str] = field(default_factory=dict)
    failure: Exception | None = None
    heard: list[str] = field(default_factory=list)

    async def transcribe(self, media_id: str) -> str:
        self.heard.append(media_id)
        if self.failure is not None:
            raise self.failure
        return self.transcripts[media_id]


@dataclass
class FakePhotos:
    """Customer photos by media id, as Spark would read them."""

    readings: dict[str, PhotoReading] = field(default_factory=dict)
    failure: Exception | None = None
    seen: list[str] = field(default_factory=list)

    async def read(self, seller_id: UUID, media_id: str) -> PhotoReading:
        self.seen.append(media_id)
        if self.failure is not None:
            raise self.failure
        return self.readings[media_id]


@dataclass
class World:
    shop: Shop
    chat: ScriptedChat
    store: InMemoryConversationStore
    compliance: InMemoryCompliance
    messenger: RecordingMessenger
    clock: FakeClock
    erasures: list[tuple[UUID, UUID]]
    gallery: AdGallery
    deps: PipelineDeps

    @property
    def seller_id(self) -> UUID:
        return self.shop.seller.seller_id

    async def contact_id(self, wa_id: str = CUSTOMER) -> UUID:
        return (await self.compliance.resolve_contact(self.seller_id, wa_id)).id

    async def say(self, text: str | None, **options: Any) -> InboundOutcome:
        inbound = Inbound(
            seller_id=options.pop("seller_id", self.seller_id),
            wa_id=options.pop("wa_id", CUSTOMER),
            message_id=options.pop("message_id", f"wamid.VITRINA-TEST-IN-{uuid4().hex[:8]}"),
            kind=options.pop("kind", "text"),
            text=text,
            profile_name="Laura Prueba",
            **options,
        )
        return await process_inbound(inbound, self.deps)

    def script(self, *answers: ChatMessage | str | ProviderError) -> None:
        self.chat.script.extend(answers)

    def with_voice(self, **transcripts: str) -> FakeVoice:
        voice = FakeVoice(transcripts)
        self.deps = replace(self.deps, voice=voice)
        return voice

    def with_photos(self, **readings: PhotoReading) -> FakePhotos:
        photos = FakePhotos(readings)
        self.deps = replace(self.deps, photos=photos)
        return photos

    async def send_photo(self, media_id: str, caption: str | None = None) -> InboundOutcome:
        return await self.say(caption, kind="image", wa_type="image", media_id=media_id)

    async def speak(self, media_id: str) -> InboundOutcome:
        return await self.say(None, kind="audio", wa_type="audio", media_id=media_id)

    def inbound_rows(self) -> list[Any]:
        return [row for row in self.store.messages if row.stored.direction == "inbound"]

    def conversation(self, outcome: InboundOutcome) -> Any:
        assert outcome.conversation_id is not None
        return self.store.conversations[outcome.conversation_id]


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def world(shop: Shop) -> World:
    clock = FakeClock()
    chat = ScriptedChat()
    store = InMemoryConversationStore()
    compliance = InMemoryCompliance(
        ContactIdentity(
            pepper=SecretStr("pipeline-test-pepper"),
            key=SecretStr(Fernet.generate_key().decode()),
        )
    )
    messenger = RecordingMessenger(clock)
    erasures: list[tuple[UUID, UUID]] = []
    gallery = AdGallery()

    async def settings(seller_id: UUID) -> SellerSettings | None:
        return shop.seller if seller_id == shop.seller.seller_id else None

    async def ads(seller_id: UUID, code: str) -> UUID | None:
        return AD_A7 if (seller_id, code) == (shop.seller.seller_id, "A7") else None

    async def erase(seller_id: UUID, contact_id: UUID) -> None:
        erasures.append((seller_id, contact_id))

    deps = PipelineDeps(
        store=store,
        compliance=compliance,
        seller_settings=settings,
        agent=AgentDeps(
            chat=chat, catalog=shop.catalog, model="spark-test", sleep=no_sleep, ad_assets=gallery
        ),
        messenger=messenger,
        pacer=Pacer(clock=clock, sleep=clock.sleep),
        ad_lookup=ads,
        request_erasure=erase,
    )
    return World(shop, chat, store, compliance, messenger, clock, erasures, gallery, deps)


GREETING = "¡Hola! ¿En qué te ayudo?"


async def test_as_3_2_an_ad_code_attributes_the_chat_and_starts_it(world: World) -> None:
    world.script(final_answer(GREETING, confidence=0.95))

    outcome = await world.say("Hola, vi el anuncio #A7")

    assert (outcome.status, outcome.reply) == ("replied", GREETING)
    conversation = world.conversation(outcome)
    assert (conversation.attribution.status, conversation.attribution.ad_id) == (
        "attributed",
        AD_A7,
    )
    ((seller_id, kind, payload),) = world.store.events
    assert (seller_id, kind) == (world.seller_id, "ad.chat_started")
    assert payload["ad_id"] == str(AD_A7)
    assert payload["contact_id"] == str(await world.contact_id())
    assert payload["conversation_id"] == str(conversation.id)


async def test_as_3_3_a_question_gets_the_cited_answer_end_to_end(world: World) -> None:
    fact = world.shop.ids["envio-medellin"]
    reply = "El envío dentro de Medellín vale $8.000."
    world.script(tool_call("search_catalog", query="envío Medellín"), final_answer(reply, fact))

    outcome = await world.say("¿cuánto vale el envío a Medellín?")

    assert world.messenger.texts == [reply]
    (_, message) = world.messenger.sent[0]
    assert message.to == "+573000000001"
    stored = world.store.messages[-1]
    assert (stored.stored.direction, stored.stored.delivery_status) == ("outbound", "sent")
    assert stored.stored.wa_message_id == "wamid.VITRINA-TEST-OUT-0001"
    assert stored.stored.agent_output is not None
    assert stored.stored.agent_output["cited_fact_ids"] == [str(fact)]
    assert stored.validation is not None
    assert stored.validation["price_validator"]["passed"]
    assert outcome.turn is not None


async def test_as_3_1_a_redelivered_message_is_processed_once(world: World) -> None:
    world.script(final_answer(GREETING, confidence=0.95))

    first = await world.say("Hola", message_id="wamid.VITRINA-TEST-IN-SAME")
    second = await world.say("Hola", message_id="wamid.VITRINA-TEST-IN-SAME")

    assert (first.status, second.status) == ("replied", "duplicate")
    assert len(world.chat.requests) == 1
    assert world.messenger.texts == [GREETING]


async def test_r7_baja_opts_out_before_any_model_sees_it(world: World) -> None:
    outcome = await world.say("BAJA")

    assert (outcome.status, world.messenger.texts) == ("opted_out", [OPT_OUT_REPLY])
    assert world.chat.requests == []
    assert await world.compliance.is_opted_out(world.seller_id, await world.contact_id())


async def test_as_6_6_borrar_mis_datos_requests_erasure(world: World) -> None:
    outcome = await world.say("BORRAR MIS DATOS")

    assert (outcome.status, world.messenger.texts) == ("erasure_requested", [ERASURE_REPLY])
    assert world.erasures == [(world.seller_id, await world.contact_id())]
    assert world.chat.requests == []


async def test_as_3_8_a_handoff_switches_to_human_mode_with_its_event(world: World) -> None:
    world.script(
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")
    )

    outcome = await world.say("vi el #A7, quiero hablar con una persona")

    conversation = world.conversation(outcome)
    assert conversation.mode == "human"
    ((conversation_id, handoff),) = world.store.handoffs
    assert (conversation_id, handoff.reason) == (conversation.id, "customer_request")
    handoff_events = [p for _, kind, p in world.store.events if kind == "ad.handoff"]
    assert handoff_events == [
        {
            "ad_id": str(AD_A7),
            "conversation_id": str(conversation.id),
            "handoff_id": str(handoff.id),
            "reason": "customer_request",
        }
    ]


async def test_r6_in_human_mode_messages_are_stored_without_a_reply(world: World) -> None:
    world.script(
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")
    )
    await world.say("Quiero hablar con Ana")

    outcome = await world.say("¿Sigues ahí?")

    assert outcome.status == "human_mode"
    assert len(world.chat.requests) == 1
    assert world.messenger.texts == ["Te comunico con Ana."]
    assert world.store.messages[-1].stored.text == "¿Sigues ahí?"


async def test_as_6_1_a_yes_to_the_consent_question_is_recorded(world: World) -> None:
    prompt = world.shop.seller.consent_prompt_text
    world.script(
        final_answer(
            f"La mochila azul vale $85.000. {prompt}",
            world.shop.ids["mochila-azul"],
            proposed_consent_prompt=True,
        ),
        final_answer("¡Listo, veci! ¿Te ayudo con algo más?", confidence=0.95),
    )
    await world.say("¿Cuánto vale la mochila azul?")

    await world.say("Sí, dale")

    (record,) = await world.compliance.consents(world.seller_id, await world.contact_id())
    assert (record.purpose, record.status, record.text_shown, record.response_text) == (
        "marketing",
        "granted",
        prompt,
        "Sí, dale",
    )


async def test_as_6_1_without_a_question_a_yes_grants_nothing(world: World) -> None:
    world.script(final_answer(GREETING, confidence=0.95))

    await world.say("Sí, dale")

    assert await world.compliance.consents(world.seller_id, await world.contact_id()) == []


async def test_as_4_2_objections_are_stored_redacted_with_their_events(world: World) -> None:
    world.script(
        final_answer(
            "Entiendo. El envío dentro de Medellín vale $8.000.",
            world.shop.ids["envio-medellin"],
            objection_tag="shipping",
        )
    )

    await world.say("vi el #A7 pero $8.000 de envío es caro, llámame al +57 300 000 0001")

    ((_, objection, ad_id),) = world.store.objections
    assert (objection.tag, ad_id) == ("shipping", AD_A7)
    assert "[número]" in objection.quote
    assert "300 000 0001" not in objection.quote
    kinds = [kind for _, kind, _ in world.store.events]
    assert kinds == ["ad.chat_started", "objection.recorded", "ad.objection"]


async def test_as_3_7_a_reservation_on_an_attributed_chat_reports_ad_reservation(
    world: World,
) -> None:
    product = world.shop.ids["mochila-azul"]
    world.script(
        tool_call("reserve_item", product_id=product, quantity=1),
        final_answer("Listo, quedó reservada por 24 horas.", product),
    )

    await world.say("vi el #A7, resérvame la mochila azul")

    reservations = [p for _, kind, p in world.store.events if kind == "ad.reservation"]
    assert len(reservations) == 1
    assert (reservations[0]["product_id"], reservations[0]["quantity"]) == (str(product), 1)
    stock = await world.shop.catalog.check_stock(world.seller_id, product)
    assert stock.available == 2


async def test_art_viii_when_the_model_is_down_the_owner_takes_over(world: World) -> None:
    world.script(ProviderError("refused", provider="meta_model_api"))

    outcome = await world.say("¿Cuánto vale la mochila azul?")

    assert world.messenger.texts == [FALLBACK]
    assert world.conversation(outcome).mode == "human"
    ((_, handoff),) = world.store.handoffs
    assert handoff.reason == "other"


async def test_fr_028_a_send_that_fails_is_recorded_as_failed(world: World) -> None:
    world.script(final_answer(GREETING, confidence=0.95))
    world.messenger.failure = ProviderError("number not on WhatsApp", provider="whatsapp")

    outcome = await world.say("Hola")

    assert outcome.status == "send_failed"
    assert world.store.messages[-1].stored.delivery_status == "failed"


@pytest.mark.parametrize(
    ("kind", "text", "wa_type"),
    [("audio", None, "audio"), ("unsupported", None, "sticker"), ("image", None, "image")],
)
async def test_fr_022_what_the_agent_cannot_read_gets_a_text_only_note(
    world: World, kind: str, text: str | None, wa_type: str
) -> None:
    outcome = await world.say(text, kind=kind, wa_type=wa_type)

    assert (outcome.status, world.messenger.texts) == ("replied", [TEXT_ONLY_REPLY])
    assert world.chat.requests == []


async def test_fr_022_a_reaction_gets_no_reply(world: World) -> None:
    outcome = await world.say(None, kind="unsupported", wa_type="reaction")

    assert outcome.status == "stored"
    assert world.messenger.sent == []


async def test_fr_022_an_image_caption_is_answered(world: World) -> None:
    world.script(final_answer("¿De qué talla la buscas?", confidence=0.95))

    await world.say("¿La tienen en rojo?", kind="image", wa_type="image")

    assert world.chat.requests[0].messages[-1].content == "¿La tienen en rojo?"


async def test_fr_024_the_model_sees_the_earlier_turns(world: World) -> None:
    world.script(final_answer(GREETING, confidence=0.95), final_answer(GREETING, confidence=0.95))
    await world.say("Hola")

    await world.say("¿Tienen mochilas?")

    contents = [message.content for message in world.chat.requests[1].messages[1:]]
    assert contents == ["Hola", GREETING, "¿Tienen mochilas?"]


async def test_r4_low_confidence_twice_across_messages_hands_off(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    world.script(
        final_answer("Creo que sí.", product, confidence=0.3),
        final_answer("Puede ser.", product, confidence=0.3),
    )

    first = await world.say("¿La tienen en verde?")
    second = await world.say("¿Y en morado?")

    assert world.messenger.texts == [FALLBACK, FALLBACK]
    assert world.conversation(first).mode == "human"
    ((_, handoff),) = world.store.handoffs
    assert handoff.reason == "low_confidence_twice"
    assert second.turn is not None


async def test_as_3_11_two_replies_to_one_customer_are_6_seconds_apart(world: World) -> None:
    world.script(final_answer(GREETING, confidence=0.95), final_answer(GREETING, confidence=0.95))

    await world.say("Hola")
    await world.say("¿Hay alguien?")

    first, second = (sent_at for sent_at, _ in world.messenger.sent)
    assert second - first >= 6.0


async def test_an_unknown_seller_stores_nothing(world: World) -> None:
    outcome = await world.say("Hola", seller_id=uuid4())

    assert outcome.status == "unknown_seller"
    assert world.store.messages == []


async def test_as_6_1_consent_is_recorded_once_even_if_the_model_also_records_it(
    world: World,
) -> None:
    prompt = world.shop.seller.consent_prompt_text
    world.script(
        final_answer(f"¡Hola! {prompt}", confidence=0.95, proposed_consent_prompt=True),
        tool_call("record_consent", purpose="marketing", response_text="Sí"),
        final_answer("¡Listo! Te avisamos.", confidence=0.95),
    )
    await world.say("Hola")

    await world.say("Sí")

    records = await world.compliance.consents(world.seller_id, await world.contact_id())
    assert len(records) == 1


async def test_t043_the_ad_image_follows_the_reply_6_seconds_later(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    asset = world.gallery.approve(product)
    reply = "Claro, te envío la foto de la mochila azul."
    caption = "Así se ve la mochila azul"
    world.script(
        tool_call("send_ad_asset", product_id=product, caption=caption),
        final_answer(reply, product),
    )

    outcome = await world.say("¿Tienen foto de la mochila azul?")

    assert (outcome.status, world.messenger.texts) == ("replied", [reply])
    (image,) = world.messenger.images
    assert (image.to, image.image, image.caption) == ("+573000000001", asset.image, caption)
    replied_at, sent_at = (at for at, _ in world.messenger.sent)
    assert sent_at - replied_at >= 6.0
    assert outcome.turn is not None
    (queued,) = outcome.turn.actions.ad_assets
    stored = world.store.messages[-1].stored
    assert (stored.id, stored.type, stored.sender, stored.text, stored.media_key) == (
        queued.message_id,
        "image",
        "agent",
        caption,
        f"ad:{asset.ad_id}",
    )
    assert (stored.delivery_status, stored.wa_message_id) == (
        "sent",
        "wamid.VITRINA-TEST-OUT-0002",
    )


@pytest.mark.parametrize("why", ["invented-price", "low-confidence"])
async def test_t043_no_ad_image_goes_with_a_fallback(world: World, why: str) -> None:
    product = world.shop.ids["mochila-azul"]
    world.gallery.approve(product)
    if why == "invented-price":  # R2 twice: the fallback and a handoff
        invented = final_answer("Te la dejo en $70.000, mira la foto.", product)
        world.script(tool_call("send_ad_asset", product_id=product), invented, invented)
    else:  # R4 once: the fallback, no handoff yet
        unsure = final_answer("Te envío la foto de la mochila azul.", product, confidence=0.3)
        world.script(tool_call("send_ad_asset", product_id=product), unsure)

    outcome = await world.say("¿Me la dejas más barata?")

    assert (world.messenger.texts, world.messenger.images) == ([FALLBACK], [])
    assert [row for row in world.store.messages if row.stored.type == "image"] == []
    assert world.conversation(outcome).mode == ("human" if why == "invented-price" else "agent")


async def test_t043_no_ad_image_goes_with_a_handoff(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    world.gallery.approve(product)
    world.script(
        tool_call("send_ad_asset", product_id=product),
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request"),
    )

    await world.say("Mándame la foto y comunícame con Ana")

    assert (world.messenger.texts, world.messenger.images) == (["Te comunico con Ana."], [])


async def test_fr_028_an_ad_image_that_fails_is_recorded_as_failed(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    world.gallery.approve(product)
    world.messenger.failure = ProviderError("media upload refused", provider="whatsapp")
    world.messenger.failing_kinds = ("image",)
    world.script(
        tool_call("send_ad_asset", product_id=product),
        final_answer("Te envío la foto de la mochila azul.", product),
    )

    outcome = await world.say("¿Tienen foto de la mochila azul?")

    assert outcome.status == "replied"
    (image_row,) = [row for row in world.store.messages if row.stored.type == "image"]
    assert image_row.stored.delivery_status == "failed"


async def test_fr_028_no_ad_image_follows_a_reply_that_failed(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    world.gallery.approve(product)
    world.messenger.failure = ProviderError("number not on WhatsApp", provider="whatsapp")
    world.script(
        tool_call("send_ad_asset", product_id=product),
        final_answer("Te envío la foto de la mochila azul.", product),
    )

    outcome = await world.say("¿Tienen foto de la mochila azul?")

    assert outcome.status == "send_failed"
    assert [row for row in world.store.messages if row.stored.type == "image"] == []


async def test_r8_an_ad_image_after_the_consent_question_keeps_it_pending(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    world.gallery.approve(product)
    prompt = world.shop.seller.consent_prompt_text
    world.script(
        tool_call("send_ad_asset", product_id=product),
        final_answer(
            f"Te envío la foto de la mochila azul. {prompt}", product, proposed_consent_prompt=True
        ),
        final_answer("¡Listo, veci! ¿Te ayudo con algo más?", confidence=0.95),
    )
    await world.say("¿Tienen foto de la mochila azul?")

    await world.say("Sí, dale")

    (record,) = await world.compliance.consents(world.seller_id, await world.contact_id())
    assert (record.status, record.text_shown) == ("granted", prompt)


ENVIO = "¿Cuánto vale el envío a Medellín?"


async def test_as_3_6_a_voice_note_is_answered_from_its_transcript(world: World) -> None:
    voice = world.with_voice(voice_1=ENVIO)
    fact = world.shop.ids["envio-medellin"]
    reply = "El envío dentro de Medellín vale $8.000."
    world.script(tool_call("search_catalog", query="envío Medellín"), final_answer(reply, fact))

    outcome = await world.speak("voice_1")

    assert (outcome.status, world.messenger.texts) == ("replied", [reply])
    assert voice.heard == ["voice_1"]
    (row,) = world.inbound_rows()
    assert (row.stored.type, row.stored.text, row.stored.transcript) == ("audio", None, ENVIO)
    assert row.normalized is not None
    assert (row.normalized["kind"], row.normalized["text"]) == ("audio", ENVIO)
    assert world.chat.requests[0].messages[-1].content == ENVIO


async def test_r7_a_spoken_baja_opts_out_before_any_model_sees_it(world: World) -> None:
    world.with_voice(voice_2="Baja por favor")

    outcome = await world.speak("voice_2")

    assert (outcome.status, world.messenger.texts) == ("opted_out", [OPT_OUT_REPLY])
    assert world.chat.requests == []


@pytest.mark.parametrize(
    "failure",
    [
        ProviderError("no live transcription adapter", provider="meta_model_api"),
        TimeoutError(),
        None,
    ],
    ids=["provider", "timeout", "silence"],
)
async def test_as_3_6_a_voice_note_that_cannot_be_heard_gets_a_note(
    world: World, failure: Exception | None
) -> None:
    voice = world.with_voice(voice_3="")
    voice.failure = failure

    outcome = await world.speak("voice_3")

    assert (outcome.status, world.messenger.texts) == ("replied", [VOICE_NOT_HEARD_REPLY])
    assert world.chat.requests == []
    (row,) = world.inbound_rows()
    assert row.stored.transcript is None


async def test_fr_022_without_a_media_id_a_voice_note_is_not_sent_to_transcribe(
    world: World,
) -> None:
    voice = world.with_voice()

    await world.say(None, kind="audio", wa_type="audio")

    assert (voice.heard, world.messenger.texts) == ([], [TEXT_ONLY_REPLY])


async def test_fr_022_only_voice_notes_go_to_transcribe(world: World) -> None:
    voice = world.with_voice()
    world.script(final_answer(GREETING, confidence=0.95))

    await world.say("¿Tienen esta?", kind="image", wa_type="image", media_id="image_1")

    assert voice.heard == []


async def test_r6_in_human_mode_a_voice_note_is_transcribed_for_the_owner(world: World) -> None:
    world.with_voice(voice_4="¿Ana, ya me puedes atender?")
    world.script(
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")
    )
    await world.say("Quiero hablar con Ana")

    outcome = await world.speak("voice_4")

    assert outcome.status == "human_mode"
    assert world.inbound_rows()[-1].stored.transcript == "¿Ana, ya me puedes atender?"
    assert len(world.chat.requests) == 1


async def test_fr_024_the_model_sees_an_earlier_voice_note_as_text(world: World) -> None:
    world.with_voice(voice_5="Hola, ¿tienen mochilas?")
    world.script(final_answer(GREETING, confidence=0.95), final_answer(GREETING, confidence=0.95))
    await world.speak("voice_5")

    await world.say("¿Y en azul?")

    contents = [message.content for message in world.chat.requests[1].messages[1:]]
    assert contents == ["Hola, ¿tienen mochilas?", GREETING, "¿Y en azul?"]


async def test_as_3_6_a_kept_voice_note_is_heard_by_its_media_id(world: World) -> None:
    voice = world.with_voice(**{"1000000000000102": "Hola"})
    world.script(final_answer(GREETING, confidence=0.95))
    body = json.loads(webhook_fixture("audio_voice_note"))
    payload_id = await world.store.save_webhook(body, signature_valid=True)

    outcome = await process_stored_message(
        world.deps, world.seller_id, payload_id, "wamid.VITRINA-FIXTURE-IN-0005"
    )

    assert outcome is not None
    assert (outcome.status, voice.heard) == ("replied", ["1000000000000102"])


async def test_as_3_10_an_off_topic_question_is_declined_and_counted(world: World) -> None:
    world.chat.scope.append(scope_answer(False, "deportes"))

    outcome = await world.say("vi el #A7, ¿quién ganó el partido?")

    assert (outcome.status, world.messenger.texts) == (
        "replied",
        [decline_reply(world.shop.seller)],
    )
    assert world.chat.requests == []
    ((_, objection, ad_id),) = world.store.objections
    assert (objection.tag, ad_id) == ("off_topic", AD_A7)
    kinds = [kind for _, kind, _ in world.store.events]
    assert kinds == ["ad.chat_started", "objection.recorded", "ad.objection"]
    reply = world.store.messages[-1]
    assert reply.validation is not None
    assert reply.validation["scope"] == {"passed": False, "reason": "classifier: deportes"}
    assert reply.stored.agent_output is not None
    assert reply.stored.agent_output["objection_tag"] == "off_topic"


async def test_as_3_7_a_booking_on_an_attributed_chat_reports_ad_booking(world: World) -> None:
    bookings = InMemoryBookings()
    tuesday_8am = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
    world.deps = replace(
        world.deps, agent=replace(world.deps.agent, bookings=bookings, clock=lambda: tuesday_8am)
    )
    world.script(
        tool_call("book_slot", service="Arreglo", starts_at="2026-10-31T10:00:00-05:00"),
        final_answer("Listo, te esperamos el sábado a las 10:00.", confidence=0.95),
    )

    outcome = await world.say("vi el #A7, ¿me agendas el sábado a las 10?")

    assert world.messenger.texts == [
        "Listo, quedó tu cita de Arreglo para el sábado 31 de octubre de 2026, 10:00. "
        "¿Te ayudo con algo más?"
    ]
    (booking,) = bookings.bookings
    assert booking.conversation_id == outcome.conversation_id
    booked = [payload for _, kind, payload in world.store.events if kind == "ad.booking"]
    assert booked == [
        {
            "ad_id": str(AD_A7),
            "conversation_id": str(outcome.conversation_id),
            "booking_id": str(booking.id),
        }
    ]


BLUE_BAG = "Mochila azul de tela con correas negras"


async def test_as_3_5_a_photo_is_answered_with_the_closest_product(world: World) -> None:
    product = world.shop.ids["mochila-azul"]
    world.with_photos(photo_1=PhotoReading(BLUE_BAG, (product,)))
    world.gallery.approve(product)
    reply = "¡La tenemos! Es la mochila azul; te envío la foto del anuncio."
    world.script(tool_call("send_ad_asset", product_id=product), final_answer(reply, product))

    outcome = await world.send_photo("photo_1")

    assert (outcome.status, world.messenger.texts) == ("replied", [reply])
    assert len(world.messenger.images) == 1
    assert world.chat.requests[0].messages[-1].content == f"[Foto del cliente: {BLUE_BAG}]"
    (row,) = world.inbound_rows()
    assert row.normalized is not None
    assert row.normalized["image"] == {
        "description": BLUE_BAG,
        "matched_product_ids": [str(product)],
    }


async def test_as_3_5_the_caption_travels_with_the_photo(world: World) -> None:
    world.with_photos(photo_2=PhotoReading(BLUE_BAG, ()))
    world.script(final_answer("¿De qué talla la buscas?", confidence=0.95))

    await world.send_photo("photo_2", caption="¿La tienen en rojo?")

    assert world.chat.requests[0].messages[-1].content == (
        f"¿La tienen en rojo?\n[Foto del cliente: {BLUE_BAG}]"
    )


async def test_as_3_5_a_photo_without_a_product_gets_a_note(world: World) -> None:
    world.with_photos(photo_3=PhotoReading(None))

    outcome = await world.send_photo("photo_3")

    assert (outcome.status, world.messenger.texts) == ("replied", [PHOTO_NOT_SEEN_REPLY])
    assert world.chat.requests == []


@pytest.mark.parametrize(
    "failure",
    [ProviderError("media expired", provider="whatsapp"), TimeoutError()],
    ids=["provider", "timeout"],
)
async def test_art_viii_a_photo_that_cannot_be_read_falls_back_to_its_caption(
    world: World, failure: Exception
) -> None:
    photos = world.with_photos()
    photos.failure = failure
    world.script(final_answer("¿De qué talla la buscas?", confidence=0.95))

    await world.send_photo("photo_4", caption="¿La tienen en rojo?")
    no_caption = await world.send_photo("photo_5")

    assert world.chat.requests[0].messages[-1].content == "¿La tienen en rojo?"
    assert no_caption.reply == PHOTO_NOT_SEEN_REPLY


async def test_fr_022_only_photos_with_a_media_id_go_to_spark(world: World) -> None:
    photos = world.with_photos()
    world.deps = replace(world.deps, voice=FakeVoice({"voice_7": "Hola"}))
    world.script(final_answer(GREETING, confidence=0.95), final_answer(GREETING, confidence=0.95))

    await world.speak("voice_7")
    await world.say("¿La tienen en rojo?", kind="image", wa_type="image")

    assert photos.seen == []
    assert world.chat.requests[1].messages[-1].content == "¿La tienen en rojo?"


async def test_r6_in_human_mode_photos_are_not_read(world: World) -> None:
    photos = world.with_photos()
    world.script(
        final_answer("Te comunico con Ana.", confidence=0.95, handoff_reason="customer_request")
    )
    await world.say("Quiero hablar con Ana")

    outcome = await world.send_photo("photo_6")

    assert (outcome.status, photos.seen) == ("human_mode", [])


async def test_as_3_5_a_kept_photo_is_read_by_its_media_id(world: World) -> None:
    photos = world.with_photos(**{"1000000000000101": PhotoReading(BLUE_BAG, ())})
    world.script(final_answer("¿De qué talla la buscas?", confidence=0.95))
    body = json.loads(webhook_fixture("image_with_caption"))
    payload_id = await world.store.save_webhook(body, signature_valid=True)

    await process_stored_message(
        world.deps, world.seller_id, payload_id, "wamid.VITRINA-FIXTURE-IN-0004"
    )

    assert photos.seen == ["1000000000000101"]
