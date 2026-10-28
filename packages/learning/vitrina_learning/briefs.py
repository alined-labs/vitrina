"""The weekly brief (T070; AS-5.1, AS-5.3, FR-050): Muse Wonder reads what worked, writes JSON.

The dataset is aggregates only: each ad's funnel and objection tags, the totals per tag, the
seller's active products and how many ads the seller approved. No customer text reaches the
model (Art. V.2), and product names pass the same PII patterns as voice samples. Wonder
answers under a strict ``response_format``; the checks outside the model then confirm every
product and ad it names is the seller's. One answer that fails gets the reason and one more
try; a second failure ends the job with ``BRIEF_SCHEMA_INVALID``.

Strict structured output cannot carry the contract's free-form ``evidence`` object, so the
model gives ``{metric, value}`` pairs and the brief stores them as ``{metric: value}``.
"""

import hashlib
from collections.abc import Sequence
from datetime import date, datetime
from functools import partial
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import MISSING, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_catalog.api import CatalogStore, redact
from vitrina_catalog.postgres import PostgresCatalog
from vitrina_catalog.sellers import PostgresSellerStore, SellerStore
from vitrina_core.http import keyset_page, next_cursor
from vitrina_core.ids import new_id
from vitrina_core.jobs import JobContext, JobFailedError
from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ResponseFormat,
    with_backoff,
)
from vitrina_creative.api import AdStore
from vitrina_learning.attribution import (
    AdDirectory,
    CreativeAds,
    attribution_report,
    objection_report,
)
from vitrina_learning.schemas import CopyAngle
from vitrina_learning.tables import briefs

GENERATE_TASK = "learning.generate_brief"

# openapi.yaml ``CopyHook`` and ``ObjectionTag``.
CopyHook = Literal["question", "bold_claim", "story", "social_proof_free", "scarcity", "benefit"]
ObjectionTag = Literal[
    "price", "shipping", "size", "availability", "trust", "timing", "payment", "off_topic", "other"
]
ANGLES = ["price", "quality", "handmade", "urgency", "gift", "local_pride"]
HOOKS = ["question", "bold_claim", "story", "social_proof_free", "scarcity", "benefit"]
TAGS = [
    "price",
    "shipping",
    "size",
    "availability",
    "trust",
    "timing",
    "payment",
    "off_topic",
    "other",
]


# ── the dataset Wonder reads ──


class DatasetAd(BaseModel):
    ad_id: UUID
    code: str
    angle: str | None
    chats: int
    reservations: int
    bookings: int
    handoffs: int
    conversion_rate: float
    objections_by_tag: dict[str, int]


class DatasetProduct(BaseModel):
    id: UUID
    name: str
    price_cop: int


class AngleTotals(BaseModel):
    chats: int
    conversion_rate: float


class BriefDataset(BaseModel):
    business_name: str
    period_from: date
    period_to: date
    attributed_chats: int
    ads: list[DatasetAd]
    by_angle: dict[str, AngleTotals]
    objections: dict[str, int]
    products: list[DatasetProduct]
    ads_by_status: dict[str, int]

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


async def build_dataset(
    sessions: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    period_from: date,
    period_to: date,
    *,
    ads: AdDirectory,
    catalog: CatalogStore,
    sellers: SellerStore,
) -> BriefDataset:
    """Every list sorted and every map keyed in order, so one period gives one hash."""
    report = await attribution_report(sessions, seller_id, period_from, period_to, ads=ads)
    objections = await objection_report(sessions, seller_id, period_from, period_to, ads=ads)
    seller = await sellers.get(seller_id)
    cursor: str | None = None
    products: list[DatasetProduct] = []
    while True:
        found, cursor = await catalog.list_products(
            seller_id, limit=100, cursor=cursor, status="active"
        )
        products += [
            DatasetProduct(id=item.id, name=redact(item.name), price_cop=item.price_cop)
            for item in found
        ]
        if cursor is None:
            break
    statuses: dict[str, int] = {}
    for info in (await ads.ads(seller_id)).values():
        statuses[info.status] = statuses.get(info.status, 0) + 1
    return BriefDataset(
        business_name=redact(seller.business_name) if seller else "",
        period_from=period_from,
        period_to=period_to,
        attributed_chats=sum(row.chats for row in report.ads),
        ads=[
            DatasetAd(
                ad_id=row.ad_id,
                code=row.code,
                angle=None if row.angle is MISSING else str(row.angle),
                chats=row.chats,
                reservations=row.reservations,
                bookings=row.bookings,
                handoffs=row.handoffs,
                conversion_rate=row.conversion_rate,
                objections_by_tag=dict(
                    sorted(
                        {} if row.objections_by_tag is MISSING else row.objections_by_tag.items()
                    )
                ),
            )
            for row in report.ads
        ],
        by_angle={
            angle: AngleTotals(
                chats=0 if total.chats is MISSING else total.chats,
                conversion_rate=0.0 if total.conversion_rate is MISSING else total.conversion_rate,
            )
            for angle, total in sorted(report.by_angle.items())
        },
        objections=dict(sorted(objections.totals.items())),
        products=sorted(products, key=lambda product: (product.name, str(product.id))),
        ads_by_status=dict(sorted(statuses.items())),
    )


# ── what Wonder must answer: strict, so every property is required and nothing extra ──


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _list(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


BRIEF_OUTPUT_SCHEMA: dict[str, Any] = _object(
    {
        "headline": {"type": "string", "maxLength": 300},
        "insights": _list(
            _object(
                {
                    "text": {"type": "string"},
                    "evidence": _list(
                        _object({"metric": {"type": "string"}, "value": {"type": "number"}})
                    ),
                }
            )
        ),
        "recommended_angles": _list(
            _object({"angle": {"type": "string", "enum": ANGLES}, "rationale": {"type": "string"}})
        ),
        "creative_directives": _list(
            _object(
                {
                    "product_id": {"type": "string", "format": "uuid"},
                    "angle": {"type": "string", "enum": ANGLES},
                    "hook": {"type": ["string", "null"], "enum": [*HOOKS, None]},
                    "scene_brief": {"type": "string", "maxLength": 500},
                    "offer": {"type": ["string", "null"], "maxLength": 200},
                }
            )
        ),
        "reactivation_segments": _list(
            _object(
                {
                    "objection_tag": {"type": "string", "enum": TAGS},
                    "message_angle": {"type": "string"},
                    "suggested_ad_id": {"type": ["string", "null"], "format": "uuid"},
                }
            )
        ),
    }
)

SYSTEM = """Eres el estratega de marketing de {business}, un negocio de Colombia que vende por WhatsApp.
Recibes datos agregados de un periodo, sin datos personales: cada anuncio con su ángulo y su embudo
(chats, reservas, citas, traspasos a una persona, objeciones por tipo), las objeciones totales, los
productos activos y cuántos anuncios aprobó el dueño. Escribe el brief del próximo lote de anuncios:
- headline: una sola frase en español de Colombia, de hasta 300 caracteres, con el hallazgo y la
  acción, por ejemplo: "El ángulo 'hecho a mano' generó 3× más chats, pero el 40 % se frenó por el
  envío: el próximo lote abre con envío gratis en Bogotá."
- insights: cada uno con los números de los datos en que se basa (evidence: métrica y valor). Nunca
  inventes un número que no esté en los datos.
- recommended_angles: ángulos de la lista, cada uno con su razón.
- creative_directives: un producto activo (su id exacto), un ángulo, un gancho y la escena; una
  oferta solo como propuesta que el dueño debe aprobar.
- reactivation_segments: por tipo de objeción, el ángulo del mensaje y, si sirve, el id de uno de
  los anuncios de los datos.
En los textos nombra los ángulos en español: price precio, quality calidad, handmade hecho a
mano, urgency urgencia, gift regalo, local_pride orgullo local.
Responde solo con el JSON del esquema."""

RETRY = "Tu respuesta no sirve: {problem}. Responde de nuevo solo con el JSON corregido."


def brief_request(dataset: BriefDataset, *, model: str) -> ChatRequest:
    return ChatRequest(
        model=model,
        purpose="brief",
        messages=(
            ChatMessage(role="system", content=SYSTEM.format(business=dataset.business_name)),
            ChatMessage(role="user", content=dataset.model_dump_json()),
        ),
        response_format=ResponseFormat(name="BriefSchema", json_schema=BRIEF_OUTPUT_SCHEMA),
    )


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Evidence(_Strict):
    metric: str
    value: float


class _Insight(_Strict):
    text: str
    evidence: list[_Evidence]


class _Angle(_Strict):
    angle: CopyAngle
    rationale: str


class _Directive(_Strict):
    product_id: UUID
    angle: CopyAngle
    hook: CopyHook | None
    scene_brief: Annotated[str, Field(max_length=500)]
    offer: Annotated[str, Field(max_length=200)] | None


class _Segment(_Strict):
    objection_tag: ObjectionTag
    message_angle: str
    suggested_ad_id: UUID | None


class _Output(_Strict):
    headline: Annotated[str, Field(max_length=300)]
    insights: list[_Insight]
    recommended_angles: list[_Angle]
    creative_directives: list[_Directive]
    reactivation_segments: list[_Segment]


# ── the brief as openapi.yaml ``Brief`` stores it ──


class Insight(BaseModel):
    text: str
    evidence: dict[str, Any] = Field(description="Numbers the insight is based on")


class RecommendedAngle(BaseModel):
    angle: CopyAngle
    rationale: str


class CreativeDirective(BaseModel):
    product_id: UUID
    angle: CopyAngle
    hook: CopyHook | MISSING = MISSING
    scene_brief: Annotated[str, Field(max_length=500)]
    offer: (
        Annotated[
            str,
            Field(
                max_length=200, description="Must be approved by the seller; never auto-published"
            ),
        ]
        | MISSING
    ) = MISSING


class ReactivationSegment(BaseModel):
    objection_tag: ObjectionTag
    message_angle: str
    suggested_ad_id: UUID | None = None


class BriefContent(BaseModel):
    headline: Annotated[str, Field(max_length=300)] | MISSING = MISSING
    insights: list[Insight]
    recommended_angles: list[RecommendedAngle]
    creative_directives: list[CreativeDirective]
    reactivation_segments: list[ReactivationSegment]


class ModelCallMeta(BaseModel):
    """openapi.yaml ``ModelCallMeta`` (FR-084)."""

    provider: Literal["meta_model_api", "modal", "baseten", "fireworks", "local_mock"] | MISSING = (
        MISSING
    )
    model: str | MISSING = MISSING
    tokens_in: int | MISSING = MISSING
    tokens_out: int | MISSING = MISSING
    latency_ms: int | MISSING = MISSING
    cost_usd: float | MISSING = MISSING
    replayed: bool | MISSING = MISSING


class Brief(BriefContent):
    """Muse Wonder output, validated against BriefSchema (FR-050)"""

    id: UUID
    seller_id: UUID
    period_from: date
    period_to: date
    status: Literal["ready", "applied", "failed"]
    model_calls: list[ModelCallMeta] | MISSING = MISSING
    created_at: datetime


class _UnusableError(Exception):
    """Why an answer cannot be the brief; sent back to Wonder once."""


def _content(text: str | None, dataset: BriefDataset) -> BriefContent:
    if not text:
        raise _UnusableError("no JSON came back")
    try:
        output = _Output.model_validate_json(text)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'root'}: {item['msg']}"
            for item in error.errors()[:5]
        )
        raise _UnusableError(problems or "the JSON does not follow the schema") from error
    products = {product.id for product in dataset.products}
    ads = {ad.ad_id for ad in dataset.ads}
    for index, directive in enumerate(output.creative_directives):
        if directive.product_id not in products:
            raise _UnusableError(
                f"creative_directives[{index}].product_id {directive.product_id} is not one of"
                " the active products in the data"
            )
    for index, segment in enumerate(output.reactivation_segments):
        if segment.suggested_ad_id is not None and segment.suggested_ad_id not in ads:
            raise _UnusableError(
                f"reactivation_segments[{index}].suggested_ad_id is not one of the ads in the data"
            )
    if not output.insights:
        raise _UnusableError("insights is empty: give at least one, with the numbers behind it")
    numbers = _numbers(dataset.model_dump(mode="json"))
    for index, insight in enumerate(output.insights):
        if not insight.evidence:
            raise _UnusableError(f"insights[{index}] has no evidence")
        for pair in insight.evidence:
            if not _grounded(pair.value, numbers):
                raise _UnusableError(
                    f"insights[{index}].evidence {pair.metric} = {pair.value!r} is not a number"
                    " from the data, nor a ratio, percentage or difference of two of them"
                )
    return BriefContent(
        headline=output.headline,
        insights=[
            Insight(text=item.text, evidence={pair.metric: pair.value for pair in item.evidence})
            for item in output.insights
        ],
        recommended_angles=[
            RecommendedAngle(angle=item.angle, rationale=item.rationale)
            for item in output.recommended_angles
        ],
        creative_directives=[
            CreativeDirective.model_validate(
                {
                    "product_id": item.product_id,
                    "angle": item.angle,
                    "scene_brief": item.scene_brief,
                    **({"hook": item.hook} if item.hook else {}),
                    **({"offer": item.offer} if item.offer else {}),
                }
            )
            for item in output.creative_directives
        ],
        reactivation_segments=[
            ReactivationSegment(
                objection_tag=item.objection_tag,
                message_angle=item.message_angle,
                suggested_ad_id=item.suggested_ad_id,
            )
            for item in output.reactivation_segments
        ],
    )


def _numbers(value: Any) -> set[float]:
    """Every number in the dataset: the only ones an insight may stand on."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, int | float):
        return {float(value)}
    if isinstance(value, dict):
        return set().union(*(_numbers(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_numbers(item) for item in value))
    return set()


def _grounded(value: float, numbers: set[float]) -> bool:
    """In the data, or a ratio, percentage or difference of two numbers in it, to rounding."""
    candidates = set(numbers)
    for first in numbers:
        for second in numbers:
            candidates.add(abs(first - second))
            if second:
                candidates |= {first / second, 100 * first / second}
    return any(abs(value - known) <= max(0.01, 0.005 * abs(known)) for known in candidates)


async def write_brief(
    chat: ChatModel, dataset: BriefDataset, *, model: str
) -> tuple[BriefContent, list[CallMeta]]:
    request = brief_request(dataset, model=model)
    calls: list[CallMeta] = []
    problem = ""
    for _ in range(2):
        response = await with_backoff(partial(chat.complete, request))
        calls.append(response.meta)
        text = response.message.content if isinstance(response.message.content, str) else None
        try:
            return _content(text, dataset), calls
        except _UnusableError as error:
            problem = str(error)
            request = request.model_copy(
                update={
                    "messages": (
                        *request.messages,
                        ChatMessage(role="assistant", content=text or ""),
                        ChatMessage(role="user", content=RETRY.format(problem=problem)),
                    )
                }
            )
    raise JobFailedError("BRIEF_SCHEMA_INVALID", f"the brief did not validate twice: {problem}")


# ── storage ──


def _call_view(meta: CallMeta) -> dict[str, Any]:
    view = ModelCallMeta(
        provider=meta.provider,
        model=meta.model,
        tokens_in=meta.tokens_in,
        tokens_out=meta.tokens_out,
        latency_ms=meta.latency_ms,
        replayed=meta.replayed,
        **({} if meta.cost_usd is None else {"cost_usd": meta.cost_usd}),
    )
    return view.model_dump(mode="json")


def _brief_view(row: Any) -> Brief:
    return Brief.model_validate(
        {
            "id": row["id"],
            "seller_id": row["seller_id"],
            "period_from": row["period_from"],
            "period_to": row["period_to"],
            "status": row["status"],
            **({"headline": row["headline"]} if row["headline"] is not None else {}),
            "insights": row["insights"],
            "recommended_angles": row["recommended_angles"],
            "creative_directives": row["creative_directives"],
            "reactivation_segments": row["reactivation_segments"],
            "model_calls": row["model_calls"],
            "created_at": row["created_at"],
        }
    )


class PostgresBriefs:
    """Briefs on Postgres, scoped to the seller in every read and write (Art. V.3)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create(
        self,
        seller_id: UUID,
        dataset: BriefDataset,
        content: BriefContent,
        calls: Sequence[CallMeta],
    ) -> Brief:
        values = content.model_dump(mode="json")
        async with self.sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        sa.insert(briefs)
                        .values(
                            id=new_id(),
                            seller_id=seller_id,
                            period_from=dataset.period_from,
                            period_to=dataset.period_to,
                            status="ready",
                            headline=values.get("headline"),
                            insights=values["insights"],
                            recommended_angles=values["recommended_angles"],
                            creative_directives=values["creative_directives"],
                            reactivation_segments=values["reactivation_segments"],
                            dataset_hash=dataset.digest(),
                            model_calls=[_call_view(meta) for meta in calls],
                        )
                        .returning(*briefs.c)
                    )
                )
                .mappings()
                .one()
            )
        return _brief_view(row)

    async def get(self, seller_id: UUID, brief_id: UUID) -> Brief | None:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(briefs).where(
                            briefs.c.seller_id == seller_id, briefs.c.id == brief_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _brief_view(row) if row is not None else None

    async def page(
        self, seller_id: UUID, *, limit: int, cursor: str | None = None
    ) -> tuple[list[Brief], str | None]:
        statement = sa.select(briefs).where(briefs.c.seller_id == seller_id)
        async with self.sessions() as session:
            rows = (
                (await session.execute(keyset_page(statement, briefs, limit=limit, cursor=cursor)))
                .mappings()
                .all()
            )
        items, next_page = next_cursor(rows, limit=limit)
        return [_brief_view(row) for row in items], next_page


# ── the job ──


async def generate_brief(ctx: JobContext) -> str:
    """AS-5.1: the job ``POST /briefs/generate`` queues; its result is the brief."""
    seller_id = ctx.owner_seller_id
    if ctx.sessions is None or ctx.providers is None or seller_id is None:
        raise JobFailedError("INTERNAL_ERROR", "briefs need the database, providers and a seller")
    try:
        period_from = date.fromisoformat(str(ctx.payload["period_from"]))
        period_to = date.fromisoformat(str(ctx.payload["period_to"]))
    except (KeyError, ValueError) as error:
        raise JobFailedError("VALIDATION_ERROR", "the job names no period") from error
    try:
        model = ctx.settings.model_id("strategist")
    except LookupError as error:
        raise JobFailedError("PROVIDER_UNAVAILABLE", str(error)) from error
    await ctx.progress("dataset", 0.2)
    dataset = await build_dataset(
        ctx.sessions,
        seller_id,
        period_from,
        period_to,
        ads=CreativeAds(AdStore(ctx.sessions)),
        catalog=PostgresCatalog(ctx.sessions),
        sellers=PostgresSellerStore(ctx.sessions),
    )
    await ctx.progress("writing", 0.5)
    content, calls = await write_brief(ctx.providers.chat, dataset, model=model)
    brief = await PostgresBriefs(ctx.sessions).create(seller_id, dataset, content, calls)
    return f"/v1/briefs/{brief.id}"
