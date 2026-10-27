"""T080 replay (AS-8.1, FR-071): a recorded conversation through the normal pipeline.

Fixtures are recorded webhook bodies from one synthetic customer. A session plays them into a
fresh replay conversation with ``is_replay`` on every message, through a mock messenger, with
the recorded gaps divided by the speed. The fake clock below makes the gaps instant.
"""

import re
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_catalog.api import SellerSettings
from vitrina_compliance.api import ContactIdentity, InMemoryCompliance
from vitrina_conversation.api import InMemoryConversationStore, Pacer, PipelineDeps
from vitrina_conversation.replay import (
    LiveConversationError,
    play,
    replay_fixture,
    replay_fixtures,
    start_replay,
)
from vitrina_conversation.testing import ScriptedChat, Shop, final_answer, tool_call
from vitrina_conversation.wiring import memory_pipeline
from vitrina_core.providers import OutboundMessage, OutboundText, SendResult
from vitrina_core.settings import Settings

GREETING = "¡Hola! ¿En qué te ayudo?"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@dataclass
class Outbox:
    sent: list[OutboundMessage] = field(default_factory=list)

    async def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        return SendResult(message_id=f"mock-{len(self.sent)}", status="mocked")

    @property
    def texts(self) -> list[str]:
        return [message.text for message in self.sent if isinstance(message, OutboundText)]


@dataclass
class Stage:
    shop: Shop
    chat: ScriptedChat
    store: InMemoryConversationStore
    compliance: InMemoryCompliance
    outbox: Outbox
    clock: FakeClock
    deps: PipelineDeps

    @property
    def seller_id(self) -> UUID:
        return self.shop.seller.seller_id


@pytest.fixture
def stage(shop: Shop) -> Stage:
    chat, store, outbox, clock = ScriptedChat(), InMemoryConversationStore(), Outbox(), FakeClock()
    compliance = InMemoryCompliance(
        ContactIdentity(
            pepper=SecretStr("replay-test-pepper"), key=SecretStr(Fernet.generate_key().decode())
        )
    )

    async def settings(seller_id: UUID) -> SellerSettings | None:
        return shop.seller if seller_id == shop.seller.seller_id else None

    deps = memory_pipeline(
        settings=Settings(_env_file=None, model_agent="spark-test"),  # type: ignore[call-arg]
        store=store,
        compliance=compliance,
        seller_settings=settings,
        chat=chat,
        catalog=shop.catalog,
        messenger=outbox,
        pacer=Pacer(clock=clock, sleep=clock.sleep),
    )
    return Stage(shop, chat, store, compliance, outbox, clock, deps)


def test_as_8_1_the_fixtures_are_listed_with_their_message_counts() -> None:
    fixtures = replay_fixtures()

    assert [fixture.fixture_id for fixture in fixtures] == [
        "anuncio-a7-envio",
        "fuera-de-tema-y-baja",
        "reserva-mochila",
    ]
    assert all(fixture.message_count == 3 for fixture in fixtures)
    assert all(fixture.name and fixture.description for fixture in fixtures)


def test_art_v_1_replay_fixtures_carry_synthetic_numbers_only() -> None:
    for fixture in replay_fixtures():
        customers = {message.from_wa_id for message in fixture.messages}
        assert len(customers) == 1
        assert all(re.fullmatch(r"57300000\d{4}", number) for number in customers)
        assert {message.business_number for message in fixture.messages} == {"573000000000"}


@pytest.mark.parametrize("fixture_id", ["no-such-fixture", "../webhooks/text_ad_code_a7", ""])
def test_as_8_1_an_unknown_or_unsafe_fixture_id_is_not_found(fixture_id: str) -> None:
    assert replay_fixture(fixture_id) is None


async def test_fr_071_a_session_plays_the_fixture_as_replay(stage: Stage) -> None:
    fixture = replay_fixture("anuncio-a7-envio")
    assert fixture is not None
    fact = stage.shop.ids["envio-medellin"]
    stage.chat.script.extend(
        [
            final_answer(GREETING, confidence=0.95),
            tool_call("search_catalog", query="envío Medellín"),
            final_answer("El envío dentro de Medellín vale $8.000.", fact),
            final_answer("¡Con gusto, veci! Aquí estamos.", confidence=0.95),
        ]
    )
    session = uuid4()

    conversation_id = await start_replay(stage.store, stage.compliance, stage.seller_id, fixture)
    outcomes = await play(
        fixture, stage.deps, stage.seller_id, session, speed=1, sleep=stage.clock.sleep
    )

    assert [outcome.status for outcome in outcomes] == ["replied"] * 3
    assert {outcome.conversation_id for outcome in outcomes} == {conversation_id}
    assert stage.store.conversations[conversation_id].is_replay
    inbound = [row.stored for row in stage.store.messages if row.stored.direction == "inbound"]
    assert all(message.is_replay for message in inbound)
    assert [message.wa_message_id for message in inbound] == [
        f"{message.message_id}.{session}" for message in fixture.messages
    ]
    assert stage.outbox.texts[1] == "El envío dentro de Medellín vale $8.000."


@pytest.mark.parametrize(("speed", "gaps"), [(1, [20.0, 25.0]), (10, [2.0, 2.5])])
async def test_as_8_1_the_recorded_gaps_are_divided_by_the_speed(
    stage: Stage, speed: float, gaps: list[float]
) -> None:
    fixture = replay_fixture("anuncio-a7-envio")
    assert fixture is not None
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    stage.chat.script.extend([final_answer(GREETING, confidence=0.95)] * 3)
    await start_replay(stage.store, stage.compliance, stage.seller_id, fixture)

    await play(fixture, stage.deps, stage.seller_id, uuid4(), speed=speed, sleep=sleep)

    assert slept == gaps


async def test_fr_071_a_fixture_plays_again_in_a_fresh_conversation(stage: Stage) -> None:
    fixture = replay_fixture("reserva-mochila")
    assert fixture is not None
    stage.chat.script.extend([final_answer(GREETING, confidence=0.95)] * 6)

    first = await start_replay(stage.store, stage.compliance, stage.seller_id, fixture)
    await play(fixture, stage.deps, stage.seller_id, uuid4(), speed=10, sleep=stage.clock.sleep)
    second = await start_replay(stage.store, stage.compliance, stage.seller_id, fixture)
    outcomes = await play(
        fixture, stage.deps, stage.seller_id, uuid4(), speed=10, sleep=stage.clock.sleep
    )

    assert first != second
    assert stage.store.conversations[first].mode == "closed"
    assert [outcome.status for outcome in outcomes] == ["replied"] * 3
    assert {outcome.conversation_id for outcome in outcomes} == {second}


async def test_fr_071_a_replay_never_takes_over_a_live_conversation(stage: Stage) -> None:
    fixture = replay_fixture("reserva-mochila")
    assert fixture is not None
    contact = await stage.compliance.resolve_contact(
        stage.seller_id, fixture.messages[0].from_wa_id
    )
    live = await stage.store.open_conversation(stage.seller_id, contact.id)

    with pytest.raises(LiveConversationError):
        await start_replay(stage.store, stage.compliance, stage.seller_id, fixture)

    assert stage.store.conversations[live.id].mode == "agent"
