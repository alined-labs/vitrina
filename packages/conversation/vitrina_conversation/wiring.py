"""How the inbound pipeline is put together in the API and in the worker (T027).

With a database (MX2's dev API, the deployed API and its worker): Postgres stores, the job queue
keyed by WhatsApp message id, the creative module's ad codes and approved ads (with the
worker's blob store, the images ``send_ad_asset`` sends), the compliance module's erasure job
and, with the Cloud API and ``MODEL_TRANSCRIBE``, voice notes heard through Transcribe; the
API queues and the worker answers. Without one (CI's check job, a laptop
demo): the API's in-memory stores, no ad codes, and the answer computed in a background task
right after the ack. The messenger is the local mock unless ``WHATSAPP_MOCK=false`` (ADR-0011:
the live number points at MX2 only).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from uuid import UUID

import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import Catalog, SellerSettings
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_catalog.sellers import InMemorySellerStore, PostgresSellerStore
from vitrina_compliance.api import ComplianceStore, ContactIdentity, PostgresCompliance
from vitrina_compliance.erasure import request_erasure
from vitrina_conversation.engine import AgentDeps
from vitrina_conversation.ingest import SellerResolver
from vitrina_conversation.pacing import Pacer
from vitrina_conversation.pipeline import Inbound, InboundOutcome, PipelineDeps, process_inbound
from vitrina_conversation.store import ConversationStore, PostgresConversationStore
from vitrina_conversation.tools import AdAssets, ApprovedAds
from vitrina_conversation.voice import VoiceNotes, ffmpeg_on_path
from vitrina_core.db.tables import sellers
from vitrina_core.jobs import PostgresJobQueue
from vitrina_core.providers import ChatModel, Messenger
from vitrina_core.providers.compose import Providers
from vitrina_core.settings import Settings
from vitrina_core.storage import BlobStore
from vitrina_creative.api import AdStore, get_approved_asset
from vitrina_local_mock import LocalMockMessenger, MockOutbox
from vitrina_whatsapp.client import WhatsAppClient
from vitrina_whatsapp.config import WhatsAppConfig
from vitrina_whatsapp.webhook import WebhookPayloadError, parse_webhook

logger = logging.getLogger(__name__)

PROCESS_INBOUND = "conversation.process_inbound"


def messenger_for(settings: Settings) -> Messenger:
    """The local mock by default; the Cloud API when ``WHATSAPP_MOCK=false`` (ADR-0011)."""
    if settings.whatsapp_mock:
        return LocalMockMessenger(MockOutbox(settings.mock_outbox_path))
    return WhatsAppClient.from_config(
        WhatsAppConfig(
            phone_number_id=settings.whatsapp_phone_number_id or "",
            app_secret=_secret(settings.whatsapp_app_secret),
            verify_token=_secret(settings.whatsapp_verify_token),
            access_token=_secret(settings.whatsapp_access_token),
        )
    )


def agent_deps(
    settings: Settings,
    chat: ChatModel,
    catalog: Catalog,
    *,
    approved_ads: ApprovedAds | None = None,
    ad_assets: AdAssets | None = None,
) -> AgentDeps:
    """``LookupError`` while ``MODEL_AGENT`` is unset (spec.md §8: IDs are configuration)."""
    return AgentDeps(
        chat=chat,
        catalog=catalog,
        model=settings.model_id("agent"),
        max_tool_rounds=settings.agent_max_tool_rounds,
        budget_s=settings.agent_turn_budget_s,
        top_k=settings.retrieval_top_k,
        approved_ads=approved_ads,
        ad_assets=ad_assets,
    )


def voice_notes(
    settings: Settings, providers: Providers, messenger: Messenger
) -> VoiceNotes | None:
    """T051: the Cloud API downloads the audio (the local mock has none); Transcribe hears it."""
    if not isinstance(messenger, WhatsAppClient):
        return None
    try:
        model = settings.model_id("transcribe")
    except LookupError:
        return None
    return VoiceNotes(
        download=messenger.download_media,
        transcriber=providers.transcriber,
        model=model,
        transcoder=ffmpeg_on_path(timeout_s=settings.provider_timeout_s),
    )


def postgres_pipeline(
    *,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    providers: Providers,
    messenger: Messenger,
    pacer: Pacer,
    storage: BlobStore | None = None,
) -> PipelineDeps:
    identity = (
        ContactIdentity(pepper=settings.contact_hash_pepper, key=settings.contact_encryption_key)
        if settings.contact_hash_pepper and settings.contact_encryption_key
        else None
    )
    catalog = PostgresCatalog(
        sessions, embedder=providers.embedder, embed_model=settings.model_embed
    )
    ads = AdStore(sessions)
    seller_store = PostgresSellerStore(sessions)
    jobs = PostgresJobQueue(sessions)

    async def ad_lookup(seller_id: UUID, code: str) -> UUID | None:
        ad = await ads.by_code(seller_id, code)
        return ad.id if ad is not None else None

    async def approved_ads(seller_id: UUID, product_id: UUID) -> list[UUID]:
        return [row["id"] for row in await ads.approved_for(seller_id, product_id)]

    ad_assets = (
        partial(get_approved_asset, store=ads, storage=storage, sellers=seller_store)
        if storage is not None
        else None
    )

    async def erase(seller_id: UUID, contact_id: UUID) -> None:
        async with sessions() as session, session.begin():
            await request_erasure(jobs, session, seller_id, contact_id)

    return PipelineDeps(
        store=PostgresConversationStore(sessions),
        compliance=PostgresCompliance(sessions, identity),
        seller_settings=seller_store.settings,
        agent=agent_deps(
            settings, providers.chat, catalog, approved_ads=approved_ads, ad_assets=ad_assets
        ),
        messenger=messenger,
        pacer=pacer,
        ad_lookup=ad_lookup,
        request_erasure=erase,
        voice=voice_notes(settings, providers, messenger),
    )


def memory_pipeline(
    *,
    settings: Settings,
    store: ConversationStore,
    compliance: ComplianceStore,
    seller_settings: Callable[[UUID], Awaitable[SellerSettings | None]],
    chat: ChatModel,
    catalog: Catalog,
    messenger: Messenger,
    pacer: Pacer,
) -> PipelineDeps:
    async def no_ads(_seller_id: UUID, _code: str) -> UUID | None:
        return None

    return PipelineDeps(
        store=store,
        compliance=compliance,
        seller_settings=seller_settings,
        agent=agent_deps(settings, chat, catalog),
        messenger=messenger,
        pacer=pacer,
        ad_lookup=no_ads,
    )


async def process_stored_message(
    deps: PipelineDeps, seller_id: UUID, payload_id: UUID, message_id: str
) -> InboundOutcome | None:
    """Answer one message of a kept webhook body; None when the body or message is gone."""
    body = await deps.store.webhook(payload_id)
    if body is None:
        return None
    try:
        batch = parse_webhook(body)
    except WebhookPayloadError:
        return None
    message = next((m for m in batch.messages if m.message_id == message_id), None)
    if message is None:
        return None
    return await process_inbound(
        Inbound(
            seller_id=seller_id,
            wa_id=message.from_wa_id,
            message_id=message.message_id,
            kind=message.kind,
            text=message.text,
            profile_name=message.profile_name,
            wa_type=message.wa_type,
            raw_payload_id=payload_id,
            media_id=message.media.media_id if message.media else None,
        ),
        deps,
    )


class PostgresInboundQueue:
    """``process_inbound`` jobs keyed by the WhatsApp message id: a redelivery adds nothing."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], jobs: PostgresJobQueue) -> None:
        self.sessions = sessions
        self.jobs = jobs

    async def enqueue(self, seller_id: UUID, payload_id: UUID, message_id: str) -> None:
        async with self.sessions() as session, session.begin():
            await self.jobs.enqueue(
                session,
                kind="process_inbound",
                task=PROCESS_INBOUND,
                payload={"payload_id": str(payload_id), "message_id": message_id},
                owner_seller_id=seller_id,
                idempotency_key=message_id,
            )


@dataclass(frozen=True)
class BackgroundInboundQueue:
    """Without a database: answer right after the ack (FastAPI ``BackgroundTasks.add_task``).

    The in-memory store's unique message ids still drop redeliveries.
    """

    schedule: Callable[..., None]
    pipeline: Callable[[], PipelineDeps]

    async def enqueue(self, seller_id: UUID, payload_id: UUID, message_id: str) -> None:
        self.schedule(self._answer, seller_id, payload_id, message_id)

    async def _answer(self, seller_id: UUID, payload_id: UUID, message_id: str) -> None:
        try:
            deps = self.pipeline()
        except LookupError as error:
            logger.error("inbound message not answered: %s", error)
            return
        await process_stored_message(deps, seller_id, payload_id, message_id)


def sellers_on_postgres(sessions: async_sessionmaker[AsyncSession]) -> SellerResolver:
    """The shop whose ``whatsapp_number`` is the business number (core's ``sellers`` table)."""

    async def resolve(_phone_number_id: str, business_number: str) -> UUID | None:
        async with sessions() as session:
            found = await session.scalar(
                sa.select(sellers.c.id).where(sellers.c.whatsapp_number == _e164(business_number))
            )
        return UUID(str(found)) if found is not None else None

    return resolve


def sellers_in_memory(store: InMemorySellerStore) -> SellerResolver:
    async def resolve(_phone_number_id: str, business_number: str) -> UUID | None:
        wanted = _e164(business_number)
        for row in store.rows.values():
            if row.get("whatsapp_number") == wanted:
                return UUID(str(row["id"]))
        return None

    return resolve


def _e164(number: str) -> str:
    return "+" + number.strip().removeprefix("+")


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""
