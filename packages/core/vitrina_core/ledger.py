"""The model-call ledger (T009, FR-084; data-model.md ``model_calls``; openapi.yaml ``CostReport``).

Wrap each provider with its ``Ledgered*`` class and every call is recorded with provider,
model, purpose, tokens, latency, cost and the seller and request it served. Costs come
from the provider's response or are 0: prices are never guessed. ``GET /metrics/costs``
reports live spend; replayed calls cost nothing. ``SpendWatch`` warns once when live
spend crosses ``SPEND_LIMIT_USD`` (plan.md §6).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import model_calls
from vitrina_core.http.context import current_request_id, current_seller_id
from vitrina_core.ids import new_id
from vitrina_core.providers import (
    CallMeta,
    ChatModel,
    ChatRequest,
    ChatResponse,
    Embedder,
    EmbeddingRequest,
    EmbeddingResponse,
    ImageModel,
    ImageRequest,
    ImageResponse,
    Segmenter,
    SegmentRequest,
    SegmentResponse,
    Transcriber,
    TranscriptionRequest,
    TranscriptionResponse,
)

logger = logging.getLogger("vitrina.ledger")


@dataclass(frozen=True)
class LedgerEntry:
    provider: str
    model: str
    purpose: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    replayed: bool
    seller_id: UUID | None
    request_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_meta(cls, meta: CallMeta) -> LedgerEntry:
        return cls(
            provider=meta.provider,
            model=meta.model,
            purpose=meta.purpose,
            tokens_in=meta.tokens_in,
            tokens_out=meta.tokens_out,
            latency_ms=meta.latency_ms,
            cost_usd=meta.cost_usd or 0.0,
            replayed=meta.replayed,
            seller_id=current_seller_id(),
            request_id=current_request_id(),
        )


# ── the report (openapi.yaml CostReport; its line items declare no required fields) ──


class CostLine(BaseModel):
    provider: str | MISSING = MISSING
    model: str | MISSING = MISSING
    calls: int | MISSING = MISSING
    tokens_in: int | MISSING = MISSING
    tokens_out: int | MISSING = MISSING
    cost_usd: float | MISSING = MISSING


class WhatsAppCosts(BaseModel):
    service: int | MISSING = MISSING
    marketing: int | MISSING = MISSING
    cost_usd: float | MISSING = MISSING


class CostReport(BaseModel):
    period_from: date
    period_to: date
    total_usd: float
    by_provider_model: list[CostLine]
    # Message counts live in the conversation module; reported once it exposes them.
    whatsapp_messages: WhatsAppCosts | MISSING = MISSING


def period_bounds(period_from: date, period_to: date) -> tuple[datetime, datetime]:
    """Whole UTC days, ``to`` included (FR-085: timestamps are UTC)."""
    start = datetime.combine(period_from, time.min, tzinfo=UTC)
    return start, datetime.combine(period_to, time.min, tzinfo=UTC) + timedelta(days=1)


# ── recording ──


@dataclass
class SpendWatch:
    limit_usd: float
    live_total_usd: float = 0.0
    alerted: bool = False

    def observe(self, entry: LedgerEntry) -> None:
        if entry.replayed:
            return
        self.live_total_usd += entry.cost_usd
        if not self.alerted and self.live_total_usd >= self.limit_usd:
            self.alerted = True
            logger.warning(
                "spend limit reached: %.2f USD of live model calls (SPEND_LIMIT_USD=%.2f); switch to REPLAY",
                self.live_total_usd,
                self.limit_usd,
            )


class ModelCallLedger(Protocol):
    async def record(self, entry: LedgerEntry) -> None: ...


class CostReader(Protocol):
    async def report(self, seller_id: UUID, period_from: date, period_to: date) -> CostReport: ...


def _report(period_from: date, period_to: date, lines: list[CostLine]) -> CostReport:
    total = round(sum(line.cost_usd for line in lines if isinstance(line.cost_usd, float)), 6)
    return CostReport(
        period_from=period_from, period_to=period_to, total_usd=total, by_provider_model=lines
    )


@dataclass
class InMemoryLedger:
    """For tests and machines without a database; lost when the process ends."""

    watch: SpendWatch | None = None
    entries: list[LedgerEntry] = field(default_factory=list)

    async def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)
        if self.watch is not None:
            self.watch.observe(entry)

    async def report(self, seller_id: UUID, period_from: date, period_to: date) -> CostReport:
        start, end = period_bounds(period_from, period_to)
        totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0, 0, 0.0])
        for entry in self.entries:
            if (
                entry.seller_id == seller_id
                and not entry.replayed
                and start <= entry.created_at < end
            ):
                line = totals[(entry.provider, entry.model)]
                line[0] += 1
                line[1] += entry.tokens_in
                line[2] += entry.tokens_out
                line[3] += entry.cost_usd
        lines = [
            CostLine(
                provider=provider,
                model=model,
                calls=int(calls),
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                cost_usd=round(cost, 6),
            )
            for (provider, model), (calls, tokens_in, tokens_out, cost) in sorted(totals.items())
        ]
        return _report(period_from, period_to, lines)


class PostgresLedger:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], watch: SpendWatch | None = None
    ) -> None:
        self.sessions = sessions
        self.watch = watch
        self._seeded = False

    async def record(self, entry: LedgerEntry) -> None:
        # Its own transaction: a call that cost money stays recorded if the caller rolls back.
        async with self.sessions() as session, session.begin():
            if self.watch is not None and not self._seeded:
                spent = await session.scalar(
                    sa.select(sa.func.coalesce(sa.func.sum(model_calls.c.cost_usd), 0)).where(
                        ~model_calls.c.replayed
                    )
                )
                self.watch.live_total_usd = float(spent or 0)
                self._seeded = True
            await session.execute(
                sa.insert(model_calls).values(
                    id=new_id(),
                    seller_id=entry.seller_id,
                    request_id=entry.request_id,
                    provider=entry.provider,
                    model=entry.model,
                    purpose=entry.purpose,
                    tokens_in=entry.tokens_in,
                    tokens_out=entry.tokens_out,
                    latency_ms=entry.latency_ms,
                    cost_usd=entry.cost_usd,
                    replayed=entry.replayed,
                )
            )
        if self.watch is not None:
            self.watch.observe(entry)

    async def report(self, seller_id: UUID, period_from: date, period_to: date) -> CostReport:
        start, end = period_bounds(period_from, period_to)
        statement = (
            sa.select(
                model_calls.c.provider,
                model_calls.c.model,
                sa.func.count().label("calls"),
                sa.func.sum(model_calls.c.tokens_in).label("tokens_in"),
                sa.func.sum(model_calls.c.tokens_out).label("tokens_out"),
                sa.func.sum(model_calls.c.cost_usd).label("cost_usd"),
            )
            .where(
                model_calls.c.seller_id == seller_id,
                ~model_calls.c.replayed,
                model_calls.c.created_at >= start,
                model_calls.c.created_at < end,
            )
            .group_by(model_calls.c.provider, model_calls.c.model)
            .order_by(model_calls.c.provider, model_calls.c.model)
        )
        async with self.sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        lines = [
            CostLine(
                provider=row["provider"],
                model=row["model"],
                calls=int(row["calls"]),
                tokens_in=int(row["tokens_in"]),
                tokens_out=int(row["tokens_out"]),
                cost_usd=round(float(row["cost_usd"]), 6),
            )
            for row in rows
        ]
        return _report(period_from, period_to, lines)


async def _record(ledger: ModelCallLedger, meta: CallMeta) -> None:
    try:
        await ledger.record(LedgerEntry.from_meta(meta))
    except Exception:
        # Losing a ledger row must not fail the customer's turn; the log keeps the trace.
        logger.exception("could not record a %s call to %s", meta.purpose, meta.model)


class LedgeredChatModel:
    def __init__(self, inner: ChatModel, ledger: ModelCallLedger) -> None:
        self.inner, self.ledger = inner, ledger

    async def complete(self, request: ChatRequest) -> ChatResponse:
        response = await self.inner.complete(request)
        await _record(self.ledger, response.meta)
        return response


class LedgeredEmbedder:
    def __init__(self, inner: Embedder, ledger: ModelCallLedger) -> None:
        self.inner, self.ledger = inner, ledger

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        response = await self.inner.embed(request)
        await _record(self.ledger, response.meta)
        return response


class LedgeredImageModel:
    def __init__(self, inner: ImageModel, ledger: ModelCallLedger) -> None:
        self.inner, self.ledger = inner, ledger

    async def generate(self, request: ImageRequest) -> ImageResponse:
        response = await self.inner.generate(request)
        await _record(self.ledger, response.meta)
        return response


class LedgeredSegmenter:
    def __init__(self, inner: Segmenter, ledger: ModelCallLedger) -> None:
        self.inner, self.ledger = inner, ledger

    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        response = await self.inner.segment(request)
        await _record(self.ledger, response.meta)
        return response


class LedgeredTranscriber:
    def __init__(self, inner: Transcriber, ledger: ModelCallLedger) -> None:
        self.inner, self.ledger = inner, ledger

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        response = await self.inner.transcribe(request)
        await _record(self.ledger, response.meta)
        return response
