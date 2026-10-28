"""The conversation module's part of reactivation campaigns (T064; AS-6.7, FR-064).

``compliance.campaigns`` decides who may get a marketing message; this module knows who the
candidates are and records what was sent. A candidate is a contact of the seller who wrote
at least ``no_purchase_since_days`` ago and booked nothing since; the last objection says
what the message answers, and the product it named (or the product of the ad the chat came
from) says which ad matches. ``campaign_send_job`` runs one pass of ``run_send`` and queues
the next pass for the earliest deferred contact.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import Catalog, ProductNotFoundError
from vitrina_catalog.sellers import PostgresSellerStore
from vitrina_compliance.api import (
    CAMPAIGN_SEND,
    AdFinder,
    AdRef,
    CampaignDeps,
    Candidate,
    ComplianceStore,
    ContactIdentity,
    Outbox,
    PostgresCampaigns,
    PostgresCompliance,
    Segment,
    run_send,
)
from vitrina_conversation import tables as db
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.store import ConversationStore
from vitrina_core.jobs import JobContext, JobFailedError, PostgresJobQueue
from vitrina_core.providers import Messenger
from vitrina_creative.api import AdStore

ProductOfAd = Callable[[UUID, UUID], Awaitable[UUID | None]]


class PostgresCandidates:
    """``compliance.campaigns.CandidateSource`` on the conversation tables."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        catalog: Catalog,
        product_of_ad: ProductOfAd | None = None,
    ) -> None:
        self.sessions = sessions
        self.catalog = catalog
        self.product_of_ad = product_of_ad

    async def __call__(
        self,
        seller_id: UUID,
        segment: Segment,
        *,
        now: datetime,
        contact_ids: Sequence[UUID] | None = None,
    ) -> list[Candidate]:
        cutoff = now - timedelta(days=segment.no_purchase_since_days)
        chats = db.conversations.c
        live = sa.and_(chats.seller_id == seller_id, chats.is_replay.is_(False))
        last_word = (
            sa.select(chats.contact_id, sa.func.max(db.messages.c.created_at).label("at"))
            .join(db.messages, db.messages.c.conversation_id == chats.id)
            .where(live, db.messages.c.sender == "customer")
            .group_by(chats.contact_id)
            .subquery()
        )
        objection = (
            sa.select(
                chats.contact_id,
                db.objections.c.tag,
                db.objections.c.product_id,
                sa.func.coalesce(db.objections.c.ad_id, chats.attributed_ad_id).label("ad_id"),
            )
            .join(db.objections, db.objections.c.conversation_id == chats.id)
            .where(live)
            .ext(distinct_on(chats.contact_id))
            .order_by(chats.contact_id, db.objections.c.occurred_at.desc())
            .subquery()
        )
        attributed = (
            sa.select(chats.contact_id, chats.attributed_ad_id)
            .where(live, chats.attributed_ad_id.is_not(None))
            .ext(distinct_on(chats.contact_id))
            .order_by(chats.contact_id, chats.created_at.desc())
            .subquery()
        )
        statement = (
            sa.select(
                last_word.c.contact_id,
                objection.c.tag,
                objection.c.product_id,
                sa.func.coalesce(objection.c.ad_id, attributed.c.attributed_ad_id).label("ad_id"),
            )
            .outerjoin(objection, objection.c.contact_id == last_word.c.contact_id)
            .outerjoin(attributed, attributed.c.contact_id == last_word.c.contact_id)
            .order_by(last_word.c.at.desc(), last_word.c.contact_id)
        )
        if contact_ids is not None:
            statement = statement.where(last_word.c.contact_id.in_(list(contact_ids)))
        else:
            booked = (
                sa.select(chats.contact_id)
                .join(db.bookings, db.bookings.c.conversation_id == chats.id)
                .where(live, db.bookings.c.created_at >= cutoff)
            )
            statement = statement.where(
                last_word.c.at <= cutoff, last_word.c.contact_id.not_in(booked)
            ).limit(segment.max_contacts)
            if isinstance(segment.objection_tags, list):
                statement = statement.where(objection.c.tag.in_(segment.objection_tags))
        async with self.sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [await self._candidate(seller_id, row) for row in rows]

    async def _candidate(self, seller_id: UUID, row: Any) -> Candidate:
        product_id = row["product_id"]
        if product_id is None and row["ad_id"] is not None and self.product_of_ad is not None:
            product_id = await self.product_of_ad(seller_id, row["ad_id"])
        name = None
        if product_id is not None:
            try:
                name = (await self.catalog.get_product(seller_id, product_id)).name
            except ProductNotFoundError:  # archived or a draft since: no ad can match it
                product_id = None
        return Candidate(row["contact_id"], row["tag"], product_id, name)


def ad_finder(ads: AdStore) -> AdFinder:
    """``compliance.campaigns.AdFinder`` on creative's approved ads (I-10)."""

    async def find(seller_id: UUID, product_id: UUID, *, ad_id: UUID | None = None) -> AdRef | None:
        for row in await ads.approved_for(seller_id, product_id):  # newest first
            if row["code"] and (ad_id is None or row["id"] == ad_id):
                return AdRef(row["id"], row["code"])
        return None

    return find


def outbox(store: ConversationStore) -> Outbox:
    """``compliance.campaigns.Outbox``: the template shows in the contact's chat."""

    async def record(seller_id: UUID, contact_id: UUID, *, text: str, wa_id: str) -> UUID:
        conversation = await store.open_conversation(seller_id, contact_id)
        stored = await store.add_message(
            seller_id,
            conversation.id,
            direction="outbound",
            sender="system",
            type="template",
            text=text,
            wa_message_id=wa_id or None,
            delivery_status="sent",
        )
        assert stored is not None
        return stored.id

    return record


def paced(pacer: Pacer) -> Callable[[str], Awaitable[None]]:
    """Waits out the recipient's 6 s spacing (R12) before a send."""

    async def pace(recipient: str) -> None:
        async with pacer.slot(recipient):
            return

    return pace


async def campaign_send_job(
    ctx: JobContext,
    *,
    messenger: Messenger,
    pacer: Pacer,
    store: ConversationStore,
    catalog: Catalog,
) -> str:
    """T064: one pass over the campaign; the deferred contacts get another job."""
    seller_id = ctx.owner_seller_id
    if ctx.sessions is None or seller_id is None:
        raise JobFailedError("INTERNAL_ERROR", "campaigns need the database and the seller")
    settings = ctx.settings
    if not (settings.contact_hash_pepper and settings.contact_encryption_key):
        raise JobFailedError("INTERNAL_ERROR", "sending needs CONTACT_HASH_PEPPER and the key")
    try:
        campaign_id = UUID(str(ctx.payload.get("campaign_id")))
    except ValueError as error:
        raise JobFailedError("VALIDATION_ERROR", "the job names no campaign") from error
    seller = await PostgresSellerStore(ctx.sessions).settings(seller_id)
    if seller is None:
        raise JobFailedError("NOT_FOUND", "the seller is gone")
    identity = ContactIdentity(
        pepper=settings.contact_hash_pepper, key=settings.contact_encryption_key
    )
    deps = replace(
        campaign_deps(ctx.sessions, catalog, PostgresCompliance(ctx.sessions, identity)),
        messenger=messenger,
        outbox=outbox(store),
        pace=paced(pacer),
        clock=lambda: datetime.now(UTC),  # each contact is checked at its own send time
    )
    try:
        run = await run_send(
            deps, seller_id, campaign_id, business_name=seller.business_name, now=datetime.now(UTC)
        )
    except LookupError as error:
        raise JobFailedError("NOT_FOUND", str(error)) from error
    if run.next_at is not None:
        async with ctx.sessions() as session, session.begin():
            await PostgresJobQueue(ctx.sessions).enqueue(
                session,
                kind="campaign_send",
                task=CAMPAIGN_SEND,
                payload={"campaign_id": str(campaign_id)},
                owner_seller_id=seller_id,
                run_after=run.next_at,
            )
    return f"/v1/reactivation/campaigns/{campaign_id}"


def campaign_deps(
    sessions: async_sessionmaker[AsyncSession],
    catalog: Catalog,
    compliance: ComplianceStore,
) -> CampaignDeps:
    """For the API's previews and eligibility checks: no messenger, nothing is sent."""
    ads = AdStore(sessions)

    async def product_of_ad(seller_id: UUID, ad_id: UUID) -> UUID | None:
        ad = await ads.get(seller_id, ad_id)
        return ad.product_id if ad is not None else None

    return CampaignDeps(
        store=PostgresCampaigns(sessions),
        compliance=compliance,
        candidates=PostgresCandidates(sessions, catalog, product_of_ad),
        ads=ad_finder(ads),
    )
