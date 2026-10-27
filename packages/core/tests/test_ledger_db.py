from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_core.db.tables import model_calls, sellers
from vitrina_core.ledger import LedgerEntry, PostgresLedger, SpendWatch

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")


def entry(
    seller_id: UUID | None, cost: float, *, replayed: bool = False, model: str = "agent-model"
) -> LedgerEntry:
    return LedgerEntry(
        provider="meta_model_api",
        model=model,
        purpose="agent",
        tokens_in=100,
        tokens_out=20,
        latency_ms=700,
        cost_usd=cost,
        replayed=replayed,
        seller_id=seller_id,
        request_id="req-1",
    )


@pytest.mark.db
async def test_db_fr_084_the_cost_report_aggregates_one_sellers_live_calls(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessions() as session, session.begin():
        for seller_id, number in ((SELLER, "+573000000001"), (OTHER, "+573000000002")):
            await session.execute(
                sa.insert(sellers).values(
                    id=seller_id,
                    business_name="Tienda",
                    owner_name="Ana",
                    whatsapp_number=number,
                    city="Cali",
                    consent_prompt_text="¿Te aviso? Responde SÍ",
                )
            )
    watch = SpendWatch(limit_usd=100)
    ledger = PostgresLedger(db_sessions, watch)
    for item in (
        entry(SELLER, 0.002),
        entry(SELLER, 0.003),
        entry(SELLER, 0.010, model="embed-model"),
        entry(SELLER, 7.0, replayed=True),
        entry(OTHER, 1.0),
        entry(None, 0.5),
    ):
        await ledger.record(item)
    async with db_sessions() as session, session.begin():
        await session.execute(sa.insert(model_calls).values(
            id=UUID("00000000-0000-7000-8000-0000000000ff"), seller_id=SELLER, provider="meta_model_api",
            model="agent-model", purpose="agent", cost_usd=3.0, created_at=datetime.now(UTC) - timedelta(days=3),
        ))  # fmt: skip
    today = datetime.now(UTC).date()

    report = await ledger.report(SELLER, today, today)

    assert report.total_usd == pytest.approx(0.015)
    assert [
        (line.model, line.calls, line.tokens_in, line.cost_usd) for line in report.by_provider_model
    ] == [
        ("agent-model", 2, 200, pytest.approx(0.005)),
        ("embed-model", 1, 100, pytest.approx(0.010)),
    ]
    assert watch.live_total_usd == pytest.approx(1.515)
