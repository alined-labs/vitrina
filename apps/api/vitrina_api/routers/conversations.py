"""Conversations router.

Contract paths: /conversations, /conversations/{conversation_id} and its /messages, /takeover, /release.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from vitrina_conversation.api import (
    Conversation,
    ConversationRecord,
    ConversationStore,
    HumanMessageCreate,
    Message,
    conversation_store,
    conversation_view,
    message_view,
    messenger,
    pacer,
)
from vitrina_core.auth import SellerId
from vitrina_core.http import (
    IdempotencyKey,
    Page,
    PageQuery,
    ProblemCode,
    ProblemError,
    problem_responses,
)
from vitrina_core.providers import OutboundText, ProviderError
from vitrina_creative.api import AdStore

router = APIRouter(tags=["Conversations"])


def store_of(request: Request) -> ConversationStore:
    return conversation_store(request.app.state)


Store = Annotated[ConversationStore, Depends(store_of)]
Paging = Annotated[PageQuery, Depends()]


def _not_found(conversation_id: UUID) -> ProblemError:
    return ProblemError(
        ProblemCode.NOT_FOUND, detail=f"no conversation {conversation_id} for this seller"
    )


async def _record(
    store: ConversationStore, seller_id: UUID, conversation_id: UUID
) -> ConversationRecord:
    record = await store.get_conversation(seller_id, conversation_id)
    if record is None:
        raise _not_found(conversation_id)
    return record


async def _views(
    request: Request, seller_id: UUID, records: Sequence[ConversationRecord]
) -> list[Conversation]:
    """Each conversation with its ad's code, read through creative.api (Art. VI.3)."""
    codes = {
        r.state.attribution.ad_id: r.state.attribution.code
        for r in records
        if r.state.attribution.ad_id is not None and r.state.attribution.code is not None
    }
    missing = {
        r.state.attribution.ad_id
        for r in records
        if r.state.attribution.ad_id is not None and r.state.attribution.ad_id not in codes
    }
    if missing and request.app.state.sessions is not None:
        ads = AdStore(request.app.state.sessions)
        for ad_id in missing:
            ad = await ads.get(seller_id, ad_id)
            if ad is not None and ad.code is not None:
                codes[ad_id] = ad.code
    return [
        conversation_view(
            record,
            ad_code=codes.get(record.state.attribution.ad_id)
            if record.state.attribution.ad_id is not None
            else None,
        )
        for record in records
    ]


@router.get(
    "/conversations",
    operation_id="listConversations",
    summary="List conversations",
    response_description="Page of conversations",
)
async def list_conversations(
    request: Request,
    seller_id: SellerId,
    store: Store,
    page: Paging,
    mode: Literal["agent", "human", "closed"] | None = None,
    attributed_ad_id: UUID | None = None,
    needs_attention: Annotated[
        bool | None,
        Query(description="Only conversations in `human` mode with no seller reply yet"),
    ] = None,
) -> Page[Conversation]:
    records, cursor = await store.list_conversations(
        seller_id,
        limit=page.limit,
        cursor=page.cursor,
        mode=mode,
        attributed_ad_id=attributed_ad_id,
        needs_attention=bool(needs_attention),
    )
    return Page[Conversation](items=await _views(request, seller_id, records), next_cursor=cursor)


@router.get(
    "/conversations/{conversation_id}",
    operation_id="getConversation",
    summary="Get a conversation with attribution and handoff state",
    response_description="Conversation",
    responses=problem_responses(404),
)
async def get_conversation(
    request: Request, seller_id: SellerId, store: Store, conversation_id: UUID
) -> Conversation:
    (view,) = await _views(request, seller_id, [await _record(store, seller_id, conversation_id)])
    return view


@router.get(
    "/conversations/{conversation_id}/messages",
    operation_id="listMessages",
    summary="List messages (oldest first)",
    response_description="Page of messages",
)
async def list_messages(
    seller_id: SellerId, store: Store, conversation_id: UUID, page: Paging
) -> Page[Message]:
    await _record(store, seller_id, conversation_id)
    found, cursor = await store.list_messages(
        seller_id, conversation_id, limit=page.limit, cursor=page.cursor
    )
    return Page[Message](items=[message_view(message) for message in found], next_cursor=cursor)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=201,
    operation_id="sendHumanMessage",
    summary="Seller sends a message from the dashboard (conversation must be in `human` mode)",
    response_description="Outbound message queued",
    responses=problem_responses(
        409, description_409="Conversation is in `agent` mode (`CONVERSATION_NOT_HUMAN`)"
    ),
)
async def send_human_message(
    request: Request,
    seller_id: SellerId,
    store: Store,
    conversation_id: UUID,
    body: HumanMessageCreate,
    _key: IdempotencyKey = None,
) -> Message:
    record = await _record(store, seller_id, conversation_id)
    if record.state.mode != "human":
        raise ProblemError(
            ProblemCode.CONVERSATION_NOT_HUMAN,
            detail="take the conversation over before writing to the customer",
        )
    try:
        phone = await request.app.state.compliance.phone(seller_id, record.state.contact_id)
    except LookupError as error:  # no CONTACT_HASH_PEPPER / CONTACT_ENCRYPTION_KEY
        raise ProblemError(ProblemCode.PROVIDER_UNAVAILABLE, detail=str(error)) from error
    if phone is None:
        raise ProblemError(ProblemCode.CONFLICT, detail="this customer's number was erased")
    recipient = "+" + phone.removeprefix("+")  # E.164, the key the pipeline paces by
    stored = await store.add_message(
        seller_id,
        conversation_id,
        direction="outbound",
        sender="seller",
        type="text",
        text=body.text,
        delivery_status="queued",
        is_replay=record.state.is_replay,
    )
    assert stored is not None  # an outbound row carries no WhatsApp id yet
    state = request.app.state
    async with pacer(state).slot(recipient):  # R12: the seller's messages are paced too
        try:
            sent = await messenger(state).send(OutboundText(to=recipient, text=body.text))
        except ProviderError:
            await store.set_delivery(seller_id, stored.id, status="failed")
            stored = replace(stored, delivery_status="failed")
        else:
            await store.set_delivery(
                seller_id, stored.id, status=sent.status, wa_message_id=sent.message_id
            )
            stored = replace(stored, delivery_status=sent.status, wa_message_id=sent.message_id)
    await store.update_conversation(seller_id, conversation_id, touched_at=datetime.now(UTC))
    return message_view(stored)


@router.post(
    "/conversations/{conversation_id}/takeover",
    operation_id="takeoverConversation",
    summary="Seller takes over; agent stops replying",
    response_description="Conversation in `human` mode",
)
async def takeover_conversation(
    request: Request, seller_id: SellerId, store: Store, conversation_id: UUID
) -> Conversation:
    await _record(store, seller_id, conversation_id)
    await store.set_mode(seller_id, conversation_id, "human", touched_at=datetime.now(UTC))
    return await get_conversation(request, seller_id, store, conversation_id)


@router.post(
    "/conversations/{conversation_id}/release",
    operation_id="releaseConversation",
    summary="Return the conversation to the agent",
    response_description="Conversation in `agent` mode",
)
async def release_conversation(
    request: Request, seller_id: SellerId, store: Store, conversation_id: UUID
) -> Conversation:
    await _record(store, seller_id, conversation_id)
    await store.set_mode(seller_id, conversation_id, "agent", touched_at=datetime.now(UTC))
    return await get_conversation(request, seller_id, store, conversation_id)
