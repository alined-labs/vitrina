"""The agent's tools, run by the engine for one seller and one conversation (agent-tools.json).

The model never supplies seller or conversation ids: they come from the turn. Arguments are
checked against the contract's parameters, and anything wrong goes back to the model as a
tool error instead of failing the turn. Read-only tools answer from the catalog. In a dry run
the tools with side effects answer ``skipped_dry_run`` (R9). Decisions (handoff, objection,
consent, opt-out) are kept in ``TurnActions`` either way, for the pipeline to apply;
``reserve_item`` reserves at once outside a dry run, because its code goes to the customer.
``send_ad_asset`` queues the approved ad's image for the pipeline to send after the reply
(T043); a caption is customer text too, so it keeps only the product's own amounts (R2).
``book_slot`` books at once outside a dry run, inside the seller's hours (T052).
"""

import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vitrina_catalog.api import (
    Catalog,
    ProductNotFoundError,
    SearchKind,
    SellerSettings,
)
from vitrina_compliance.api import is_affirmative
from vitrina_conversation.booking import BookingResult, BookingStore, SlotRequest, book_slot
from vitrina_conversation.contract import HandoffReason, ObjectionTag
from vitrina_conversation.validation import check_prices
from vitrina_core.ids import new_id
from vitrina_core.providers import Blob, ToolCall
from vitrina_creative.api import ApprovedAsset

ToolStatus = Literal["ok", "error", "skipped_dry_run"]
Urgency = Literal["low", "normal", "high"]
ApprovedAds = Callable[[UUID, UUID], Awaitable[Sequence[UUID]]]
"""``(seller_id, product_id) -> approved ad ids``, from creative.api (I-10)."""


class AdAssets(Protocol):
    """``creative.api.get_approved_asset`` bound to its stores (I-10): the ad to send, or None."""

    async def __call__(
        self,
        seller_id: UUID,
        product_id: UUID,
        *,
        ad_id: UUID | None = None,
        variant_hint: Mapping[str, str] | None = None,
    ) -> ApprovedAsset | None: ...


SKIPPED: dict[str, Any] = {"status": "skipped_dry_run"}


# ── arguments, mirroring each tool's ``parameters`` (a test keeps them equal) ──


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Variant = dict[str, str]


class SearchCatalogArgs(_Arguments):
    query: Annotated[str, Field(max_length=300)]
    kinds: tuple[SearchKind, ...] = ()
    image_ref: str | None = None
    top_k: Annotated[int, Field(ge=1, le=10)] = 5


class GetProductArgs(_Arguments):
    product_id: UUID


class CheckStockArgs(_Arguments):
    product_id: UUID
    variant: Variant | None = None


class ReserveItemArgs(_Arguments):
    product_id: UUID
    quantity: Annotated[int, Field(ge=1, le=20)]
    variant: Variant | None = None
    note: Annotated[str, Field(max_length=200)] | None = None


class BookSlotArgs(_Arguments):
    service: Annotated[str, Field(max_length=120)]
    starts_at: datetime
    duration_min: Annotated[int, Field(ge=15, le=240)] = 30
    customer_name: Annotated[str, Field(max_length=80)] | None = None


class SendAdAssetArgs(_Arguments):
    product_id: UUID
    ad_id: UUID | None = None
    variant_hint: Variant | None = None
    caption: Annotated[str, Field(max_length=200)] | None = None


class HandoffArgs(_Arguments):
    reason: HandoffReason
    summary: Annotated[str, Field(max_length=500)]
    urgency: Urgency = "normal"


class RecordObjectionArgs(_Arguments):
    tag: ObjectionTag
    quote: Annotated[str, Field(max_length=200)]
    product_id: UUID | None = None


class RecordConsentArgs(_Arguments):
    purpose: Literal["marketing", "data_processing"]
    response_text: Annotated[str, Field(max_length=200)]


class OptOutArgs(_Arguments):
    also_erase_data: bool = False


ARGUMENTS: dict[str, type[_Arguments]] = {
    "search_catalog": SearchCatalogArgs,
    "get_product": GetProductArgs,
    "check_stock": CheckStockArgs,
    "reserve_item": ReserveItemArgs,
    "book_slot": BookSlotArgs,
    "send_ad_asset": SendAdAssetArgs,
    "handoff_to_human": HandoffArgs,
    "record_objection": RecordObjectionArgs,
    "record_consent": RecordConsentArgs,
    "opt_out": OptOutArgs,
}


# ── what a turn decided, for the pipeline to apply ──


@dataclass(frozen=True)
class HandoffAction:
    id: UUID
    reason: HandoffReason
    summary: str
    urgency: Urgency = "normal"


@dataclass(frozen=True)
class ObjectionAction:
    id: UUID
    tag: ObjectionTag
    quote: str
    product_id: UUID | None = None


@dataclass(frozen=True)
class ConsentAction:
    """R8 passed in the engine; ``response_text`` is the customer's literal message."""

    purpose: Literal["marketing", "data_processing"]
    prompt_text: str
    response_text: str


@dataclass(frozen=True)
class OptOutAction:
    erase: bool


@dataclass(frozen=True)
class ReservedItem:
    """A reservation the tool made, with what ``ad.reservation`` reports (data-model.md §4)."""

    reservation_id: UUID | None
    code: str | None
    expires_at: datetime | None
    product_id: UUID
    quantity: int


@dataclass(frozen=True)
class BookedSlot:
    """A booking the tool made, with what ``ad.booking`` reports (data-model.md §4)."""

    booking_id: UUID
    service: str
    starts_at: datetime
    """In the seller's local time."""


@dataclass(frozen=True)
class QueuedAsset:
    """An approved ad's image for the customer; ``message_id`` is the outbound message's id."""

    message_id: UUID
    ad_id: UUID
    product_id: UUID
    image: Blob
    caption: str | None = None


@dataclass
class TurnActions:
    handoff: HandoffAction | None = None
    objections: list[ObjectionAction] = field(default_factory=list)
    consent: ConsentAction | None = None
    opt_out: OptOutAction | None = None
    reservations: list[ReservedItem] = field(default_factory=list)
    ad_assets: list[QueuedAsset] = field(default_factory=list)
    bookings: list[BookedSlot] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCallRecord:
    """openapi.yaml ``ToolCallRecord``: one tool call as the model made it and what it got."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    status: ToolStatus
    latency_ms: int


class ToolError(Exception):
    """A call the tool cannot serve; the message goes back to the model."""


class ToolBox:
    def __init__(
        self,
        *,
        catalog: Catalog,
        seller: SellerSettings,
        conversation_id: UUID,
        dry_run: bool,
        message: str,
        pending_consent_prompt: str | None = None,
        approved_ads: ApprovedAds | None = None,
        ad_assets: AdAssets | None = None,
        bookings: BookingStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.catalog = catalog
        self.seller = seller
        self.seller_id = seller.seller_id
        self.conversation_id = conversation_id
        self.dry_run = dry_run
        self.message = message
        self.pending_consent_prompt = pending_consent_prompt
        self.approved_ads = approved_ads
        self.ad_assets = ad_assets
        self.bookings = bookings
        self.clock = clock
        self.actions = TurnActions()
        self.records: list[ToolCallRecord] = []

    async def run(self, call: ToolCall) -> ToolCallRecord:
        """Run one tool call and record it; failures become error results for the model."""
        name = call.name
        started = time.perf_counter()
        parsed: dict[str, Any] = {}
        try:
            loaded = json.loads(call.arguments or "{}")
            if not isinstance(loaded, dict):
                raise ToolError("the arguments must be a JSON object")
            parsed = loaded
            model = ARGUMENTS.get(name)
            if model is None:
                raise ToolError(f"there is no tool named {name}")
            status, result = await self._handle(name, model.model_validate(parsed))
        except json.JSONDecodeError:
            status, result = "error", {"error": "the arguments are not valid JSON"}
        except ValidationError as error:
            status, result = "error", {"error": _describe(error)}
        except ToolError as error:
            status, result = "error", {"error": str(error)}
        record = ToolCallRecord(
            name, parsed, result, status, round((time.perf_counter() - started) * 1000)
        )
        self.records.append(record)
        return record

    async def _handle(self, name: str, args: _Arguments) -> tuple[ToolStatus, dict[str, Any]]:
        match args:
            case SearchCatalogArgs():
                hits = await self.catalog.search(
                    self.seller_id, args.query, kinds=args.kinds, top_k=args.top_k
                )
                return "ok", {"results": [hit.model_dump(mode="json") for hit in hits]}
            case GetProductArgs():
                product = await self._product(self.catalog.get_product, args.product_id)
                ads = (
                    await self.approved_ads(self.seller_id, args.product_id)
                    if self.approved_ads
                    else ()
                )
                return "ok", {
                    **product.model_dump(mode="json"),
                    "approved_ad_ids": [str(ad) for ad in ads],
                }
            case CheckStockArgs():
                stock = await self._product(
                    lambda seller, product: self.catalog.check_stock(seller, product, args.variant),
                    args.product_id,
                )
                return "ok", stock.model_dump(mode="json")
            case ReserveItemArgs():
                if self.dry_run:
                    return "skipped_dry_run", SKIPPED
                reservation = await self._product(
                    lambda seller, product: self.catalog.reserve(
                        seller,
                        self.conversation_id,
                        product,
                        args.quantity,
                        variant=args.variant,
                        note=args.note,
                    ),
                    args.product_id,
                )
                if reservation.status == "reserved":
                    self.actions.reservations.append(
                        ReservedItem(
                            reservation.reservation_id,
                            reservation.code,
                            reservation.expires_at,
                            args.product_id,
                            args.quantity,
                        )
                    )
                return "ok", reservation.model_dump(mode="json")
            case HandoffArgs():
                if self.actions.handoff is None:
                    self.actions.handoff = HandoffAction(
                        new_id(), args.reason, args.summary, args.urgency
                    )
                if self.dry_run:
                    return "skipped_dry_run", SKIPPED
                return "ok", {"handoff_id": str(self.actions.handoff.id), "seller_notified": False}
            case RecordObjectionArgs():
                # A product id the model made up would break the row's foreign key: keep only
                # an active product of this shop.
                product_id = args.product_id
                if product_id is not None:
                    try:
                        await self.catalog.get_product(self.seller_id, product_id)
                    except ProductNotFoundError:
                        product_id = None
                objection = ObjectionAction(new_id(), args.tag, args.quote, product_id)
                self.actions.objections.append(objection)
                return "ok", {"objection_id": str(objection.id)}
            case RecordConsentArgs():
                return self._consent(args)
            case OptOutArgs():
                self.actions.opt_out = OptOutAction(erase=args.also_erase_data)
                if self.dry_run:
                    return "skipped_dry_run", SKIPPED
                return "ok", {
                    "status": "erasure_scheduled" if args.also_erase_data else "opted_out"
                }
            case SendAdAssetArgs():
                if self.dry_run:
                    return "skipped_dry_run", SKIPPED
                return "ok", await self._send_ad_asset(args)
            case BookSlotArgs():
                if self.dry_run:
                    return "skipped_dry_run", SKIPPED
                if self.bookings is None:
                    raise ToolError(
                        f"{name} is not available here; offer to pass the chat to the owner"
                    )
                return "ok", await self._book(args)
        raise ToolError(f"there is no tool named {name}")

    async def _book(self, args: BookSlotArgs) -> dict[str, Any]:
        assert self.bookings is not None
        slot = SlotRequest(args.service, args.starts_at, args.duration_min, args.customer_name)
        result = await book_slot(
            self.bookings, self.seller, self.conversation_id, slot, now=self.clock()
        )
        if result.booking_id is not None and result.starts_at is not None:
            self.actions.bookings.append(
                BookedSlot(result.booking_id, args.service, result.starts_at)
            )
        return _booking_json(result)

    async def _send_ad_asset(self, args: SendAdAssetArgs) -> dict[str, Any]:
        await self._product(self.catalog.get_product, args.product_id)
        asset = (
            await self.ad_assets(
                self.seller_id,
                args.product_id,
                ad_id=args.ad_id,
                variant_hint=args.variant_hint,
            )
            if self.ad_assets is not None
            else None
        )
        if asset is None:
            return {"status": "no_approved_ad", "ad_id": None, "message_id": None}
        queued = next((item for item in self.actions.ad_assets if item.ad_id == asset.ad_id), None)
        if queued is None:  # the same image twice in one turn is noise
            queued = QueuedAsset(
                new_id(),
                asset.ad_id,
                args.product_id,
                asset.image,
                await self._caption(args.caption, args.product_id),
            )
            self.actions.ad_assets.append(queued)
        return {"status": "queued", "ad_id": str(asset.ad_id), "message_id": str(queued.message_id)}

    async def _caption(self, caption: str | None, product_id: UUID) -> str | None:
        """R2 for the caption: an amount the product's own facts do not carry drops it."""
        if not caption or not caption.strip():
            return None
        citations = await self.catalog.citations(self.seller_id, [product_id])
        return caption if check_prices(caption, citations).passed else None

    def _consent(self, args: RecordConsentArgs) -> tuple[ToolStatus, dict[str, Any]]:
        """R8: a pending prompt and an affirmative customer reply, checked here, not trusted."""
        if self.pending_consent_prompt is None:
            return "ok", {"consent_id": None, "status": "rejected_no_prompt_shown"}
        if not is_affirmative(self.message):
            return "ok", {"consent_id": None, "status": "rejected_not_affirmative"}
        self.actions.consent = ConsentAction(
            args.purpose, self.pending_consent_prompt, self.message
        )
        if self.dry_run:
            return "skipped_dry_run", SKIPPED
        return "ok", {"consent_id": None, "status": "granted"}

    async def _product[T](self, call: Callable[[UUID, UUID], Awaitable[T]], product_id: UUID) -> T:
        try:
            return await call(self.seller_id, product_id)
        except ProductNotFoundError as error:
            raise ToolError("there is no active product with that id in this shop") from error


def _booking_json(result: BookingResult) -> dict[str, Any]:
    """The contract's ``book_slot`` result; alternatives in the seller's local time."""
    if result.booking_id is not None:
        return {"status": result.status, "booking_id": str(result.booking_id)}
    return {"status": result.status, "alternatives": [t.isoformat() for t in result.alternatives]}


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or 'arguments'}: {problem['msg']}"
        for problem in error.errors(include_url=False, include_context=False, include_input=False)
    )
