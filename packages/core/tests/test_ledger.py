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
    SpendWatch,
    period_bounds,
)
from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
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
