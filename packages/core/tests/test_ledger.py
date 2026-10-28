import logging
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from vitrina_core.http.context import request_id_var, seller_id_var
from vitrina_core.ledger import (
    CostLine,
    InMemoryLedger,
    LedgeredChatModel,
    LedgeredEmbedder,
    LedgeredImageModel,
    LedgeredSegmenter,
    LedgeredTranscriber,
    SpendWatch,
    period_bounds,
)
from vitrina_core.pricing import ModelPrice
from vitrina_core.providers import (
    Blob,
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ImageRequest,
    ImageResponse,
    ModelPurpose,
    SegmentRequest,
    SegmentResponse,
    TranscriptionRequest,
    TranscriptionResponse,
)

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
REQUEST = ChatRequest(
    model="agent-model", purpose="agent", messages=(ChatMessage(role="user", content="hola"),)
)


class Chat:
    def __init__(self, cost: float | None = 0.0021, replayed: bool = False) -> None:
        self.cost = cost
        self.replayed = replayed

    async def complete(self, request: ChatRequest) -> ChatResponse:
        meta = CallMeta(
            provider="meta_model_api",
            model=request.model,
            purpose=request.purpose,
            tokens_in=120,
            tokens_out=30,
            latency_ms=850,
            cost_usd=self.cost,
            replayed=self.replayed,
        )
        return ChatResponse(message=ChatMessage(role="assistant", content="¡Hola!"), meta=meta)


async def test_fr_084_every_model_call_is_recorded_with_seller_and_request_id() -> None:
    ledger = InMemoryLedger()
    model = LedgeredChatModel(Chat(), ledger)
    seller_token, request_token = seller_id_var.set(SELLER), request_id_var.set("req-42")
    try:
        await model.complete(REQUEST)
    finally:
        seller_id_var.reset(seller_token)
        request_id_var.reset(request_token)

    [entry] = ledger.entries
    assert (entry.seller_id, entry.request_id) == (SELLER, "req-42")
    assert (entry.provider, entry.model, entry.purpose) == (
        "meta_model_api",
        "agent-model",
        "agent",
    )
    assert (entry.tokens_in, entry.tokens_out, entry.latency_ms, entry.cost_usd) == (
        120,
        30,
        850,
        0.0021,
    )


async def test_an_unknown_price_is_recorded_as_zero_never_guessed() -> None:
    ledger = InMemoryLedger()

    await LedgeredChatModel(Chat(cost=None), ledger).complete(REQUEST)

    assert ledger.entries[0].cost_usd == 0.0


SPARK = ModelPrice(input_per_mtok=1.25, cached_input_per_mtok=0.15, output_per_mtok=4.25)


async def test_fr_084_a_live_call_without_a_cost_is_priced_from_its_list_price() -> None:
    ledger = InMemoryLedger()
    model = LedgeredChatModel(Chat(cost=None), ledger, {"agent-model": SPARK})

    response = await model.complete(REQUEST)

    # 120 tokens in at $1.25 and 30 out at $4.25 per million.
    assert ledger.entries[0].cost_usd == pytest.approx(0.0002775)
    assert response.meta.cost_usd == pytest.approx(0.0002775)


async def test_fr_084_input_served_from_the_prompt_cache_costs_the_cached_rate() -> None:
    ledger = InMemoryLedger()

    class Cached(Chat):
        async def complete(self, request: ChatRequest) -> ChatResponse:
            response = await super().complete(request)
            meta = response.meta.model_copy(
                update={"tokens_in": 4488, "tokens_cached": 4465, "tokens_out": 58}
            )
            return response.model_copy(update={"meta": meta})

    await LedgeredChatModel(Cached(cost=None), ledger, {"agent-model": SPARK}).complete(REQUEST)
    no_cache_rate = SPARK.model_copy(update={"cached_input_per_mtok": None})
    await LedgeredChatModel(Cached(cost=None), ledger, {"agent-model": no_cache_rate}).complete(
        REQUEST
    )

    # 23 uncached at $1.25, 4,465 cached at $0.15 and 58 out at $4.25 per million; without a
    # cached rate, all 4,488 bill as ordinary input.
    assert [entry.cost_usd for entry in ledger.entries] == pytest.approx([0.000945, 0.0058565])


async def test_fr_084_a_reported_cost_or_a_replay_is_never_repriced() -> None:
    ledger = InMemoryLedger()
    prices = {"agent-model": SPARK}

    await LedgeredChatModel(Chat(cost=0.0021), ledger, prices).complete(REQUEST)
    await LedgeredChatModel(Chat(cost=None, replayed=True), ledger, prices).complete(REQUEST)

    assert [entry.cost_usd for entry in ledger.entries] == [0.0021, 0.0]


def meta_for(model: str, purpose: ModelPurpose) -> CallMeta:
    return CallMeta(provider="meta_model_api", model=model, purpose=purpose)


async def test_fr_084_images_masks_and_audio_are_priced_by_their_unit() -> None:
    ledger = InMemoryLedger()
    prices = {
        "image-model": ModelPrice(per_image=0.01),
        "segment-model": ModelPrice(per_image=0.0025),
        "transcribe-model": ModelPrice(per_audio_hour=0.18),
    }
    photo = Blob(data=b"photo", mime_type="image/png")

    class Images:
        async def generate(self, request: ImageRequest) -> ImageResponse:
            return ImageResponse(
                images=(photo,) * request.count, meta=meta_for(request.model, "scene")
            )

    class Masks:
        async def segment(self, request: SegmentRequest) -> SegmentResponse:
            return SegmentResponse(segments=(), meta=meta_for(request.model, "segment"))

    class Voice:
        async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
            return TranscriptionResponse(
                text="hola", duration_s=61.9, meta=meta_for(request.model, "transcribe")
            )

    await LedgeredImageModel(Images(), ledger, prices).generate(
        ImageRequest(model="image-model", prompt="bolso tejido", count=3)
    )
    await LedgeredSegmenter(Masks(), ledger, prices).segment(
        SegmentRequest(model="segment-model", image=photo)
    )
    await LedgeredTranscriber(Voice(), ledger, prices).transcribe(
        TranscriptionRequest(
            model="transcribe-model", audio=Blob(data=b"ogg", mime_type="audio/ogg")
        )
    )

    # Three images at $0.01, one segmented image at $0.0025, 61 whole seconds at $0.18 an hour.
    assert [entry.cost_usd for entry in ledger.entries] == pytest.approx([0.03, 0.0025, 0.00305])


async def test_embeddings_are_recorded_too() -> None:
    ledger = InMemoryLedger()

    class Embedder:
        async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
            meta = CallMeta(
                provider="meta_model_api", model=request.model, purpose="embed", tokens_in=8
            )
            return EmbeddingResponse(vectors=((0.1,),), meta=meta)

    await LedgeredEmbedder(Embedder(), ledger).embed(
        EmbeddingRequest(model="embed-model", texts=("bolso",))
    )

    assert [(entry.purpose, entry.tokens_in) for entry in ledger.entries] == [("embed", 8)]


async def test_spend_alert_fires_once_when_live_spend_crosses_the_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    watch = SpendWatch(limit_usd=0.004)
    ledger = InMemoryLedger(watch=watch)
    model = LedgeredChatModel(Chat(cost=0.003), ledger)

    with caplog.at_level(logging.WARNING, logger="vitrina.ledger"):
        for _ in range(3):
            await model.complete(REQUEST)
        await LedgeredChatModel(Chat(cost=5.0, replayed=True), ledger).complete(REQUEST)

    alerts = [record for record in caplog.records if "spend limit" in record.message]
    assert len(alerts) == 1
    assert watch.live_total_usd == pytest.approx(0.009)


async def test_fr_084_cost_report_counts_live_calls_by_provider_and_model() -> None:
    ledger = InMemoryLedger()
    token = seller_id_var.set(SELLER)
    try:
        await LedgeredChatModel(Chat(cost=0.002), ledger).complete(REQUEST)
        await LedgeredChatModel(Chat(cost=0.003), ledger).complete(REQUEST)
        await LedgeredChatModel(Chat(cost=9.0, replayed=True), ledger).complete(REQUEST)
    finally:
        seller_id_var.reset(token)
    today = datetime.now(UTC).date()

    report = await ledger.report(SELLER, today, today)

    assert (report.period_from, report.period_to, report.total_usd) == (today, today, 0.005)
    assert report.by_provider_model == [
        CostLine(
            provider="meta_model_api",
            model="agent-model",
            calls=2,
            tokens_in=240,
            tokens_out=60,
            cost_usd=0.005,
        )
    ]


def test_periods_cover_whole_utc_days() -> None:
    start, end = period_bounds(date(2026, 10, 27), date(2026, 10, 28))

    assert start == datetime(2026, 10, 27, tzinfo=UTC)
    assert end == datetime(2026, 10, 29, tzinfo=UTC)
