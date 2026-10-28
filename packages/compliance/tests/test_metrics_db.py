"""T073 live counter of the compliance module, per seller (FR-070, AS-7.1): the campaign sends
that eligibility blocked today, one per contact and campaign (AS-6.2, AS-6.4).
"""

from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.sellers import PostgresSellerStore, SellerCreate
from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_compliance.metrics import counters
from vitrina_compliance.tables import reactivation_campaigns, send_decisions
from vitrina_core.ids import new_id

SINCE = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)  # 00:00 in Bogotá
TODAY = SINCE + timedelta(hours=10)
YESTERDAY = SINCE - timedelta(hours=2)


def test_the_counter_is_registered_for_the_metrics_summary() -> None:
    names = {point.name: point.value for point in entry_points(group="vitrina.metrics")}
    assert names.get("compliance") == "vitrina_compliance.metrics:counters"


@pytest.mark.db
async def test_db_as_7_1_blocked_sends_today_count_each_contact_once_per_campaign(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    sellers = PostgresSellerStore(db_sessions)

    async def seller(number: str) -> UUID:
        created = await sellers.create(
            SellerCreate(
                business_name="Mochilas Ana", owner_name="Ana", whatsapp_number=number, city="Cali"
            ),
            auth_subject=None,
        )
        return created.id

    shop, other = await seller("+573000000161"), await seller("+573000000162")
    identity = ContactIdentity(
        pepper=SecretStr("metrics-db-pepper"), key=SecretStr(Fernet.generate_key().decode())
    )
    compliance = PostgresCompliance(db_sessions, identity)

    async def contact(seller_id: UUID, number: str) -> UUID:
        return (await compliance.resolve_contact(seller_id, number)).id

    async def campaign(seller_id: UUID) -> UUID:
        campaign_id = new_id()
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.insert(reactivation_campaigns).values(
                    id=campaign_id, seller_id=seller_id, template_name="vitrina_reactivacion"
                )
            )
        return campaign_id

    async def decided(
        seller_id: UUID, campaign_id: UUID, contact_id: UUID, decision: str, *, at: datetime
    ) -> None:
        async with db_sessions() as session, session.begin():
            await session.execute(
                sa.insert(send_decisions).values(
                    id=new_id(),
                    seller_id=seller_id,
                    campaign_id=campaign_id,
                    contact_id=contact_id,
                    decision=decision,
                    reason="NO_CONSENT_LEY_1581"
                    if decision == "blocked"
                    else "OUTSIDE_HOURS_LEY_2300",
                    evaluated_at=at,
                )
            )

    first, second = await campaign(shop), await campaign(shop)
    laura, pedro, sofia = (
        await contact(shop, "573000000021"),
        await contact(shop, "573000000022"),
        await contact(shop, "573000000023"),
    )
    await decided(shop, first, laura, "blocked", at=TODAY)
    await decided(shop, first, laura, "blocked", at=TODAY + timedelta(hours=1))  # evaluated again
    await decided(shop, first, pedro, "blocked", at=YESTERDAY)
    await decided(shop, second, pedro, "blocked", at=TODAY)
    await decided(shop, first, sofia, "deferred", at=TODAY)
    theirs = await campaign(other)
    await decided(other, theirs, await contact(other, "573000000024"), "blocked", at=TODAY)

    assert await counters(db_sessions, shop, SINCE) == {"blocked_sends_today": 2}
