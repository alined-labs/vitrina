"""Test support for the agent loop: a scripted chat model and builders for its answers.

``ScriptedChat`` stands in for Muse Spark: it answers from a script (a final JSON answer, a
tool call, or a provider failure) and records every request, so tests can assert what the
engine sent. MX0's eval tests can use it too. ``AdGallery`` stands in for the creative
module's approved ads (I-10).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from vitrina_catalog.api import InMemoryCatalog, SellerSettings
from vitrina_catalog.sellers import BrandVoiceProfile
from vitrina_core.providers import (
    Blob,
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderError,
    ToolCall,
)
from vitrina_core.settings import REPO_ROOT
from vitrina_creative.api import ApprovedAsset

SHOP_SELLER_ID = UUID("0b6f5c0e-3a52-4d55-9d0a-6c1a3f1e2b10")


@dataclass
class Shop:
    """One seeded business: its settings, its catalog and the ids of what it sells."""

    seller: SellerSettings
    catalog: InMemoryCatalog
    ids: dict[str, UUID]


def seeded_shop() -> Shop:
    """A small synthetic shop: two active bags (one sold out), a draft, a shipping fact."""
    seller = SellerSettings(
        seller_id=SHOP_SELLER_ID,
        business_name="Mochilas Ana",
        owner_name="Ana",
        whatsapp_number="+573000000101",
        timezone="America/Bogota",
        category="products",
        business_hours={"mon": [["09:00", "18:00"]], "sat": [["09:00", "13:00"]]},
        handoff_amount_threshold_cop=300000,
        low_confidence_threshold=0.6,
        brand_voice_min=3.5,
        brand_voice_profile=BrandVoiceProfile(formality=2, warmth=4.5, regionalisms=["veci"]),
        ai_label_enabled=True,
        handoff_summary_enabled=True,
        consent_prompt_text="¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ",
    )
    catalog = InMemoryCatalog()
    ids = {
        "mochila-azul": catalog.add_product(
            SHOP_SELLER_ID, "Mochila azul", price_cop=85000, stock=3, amounts_cop=(85000,)
        ),
        "mochila-roja": catalog.add_product(
            SHOP_SELLER_ID, "Mochila roja", price_cop=85000, stock=0, amounts_cop=(85000,)
        ),
        "borrador": catalog.add_product(
            SHOP_SELLER_ID, "Mochila edición especial", price_cop=120000, status="draft"
        ),
        "envio-medellin": catalog.add_fact(
            SHOP_SELLER_ID,
            "shipping",
            "Envío dentro de Medellín: $8.000, llega en 1 día hábil.",
            amounts_cop=(8000,),
        ),
    }
    return Shop(seller, catalog, ids)


def final_answer(
    reply: str,
    *cited: UUID,
    confidence: float = 0.9,
    handoff_reason: str | None = None,
    objection_tag: str | None = None,
    proposed_consent_prompt: bool = False,
) -> str:
    """An ``AgentTurnOutput`` as the JSON text an OpenAI-compatible API returns."""
    return json.dumps(
        {
            "reply": reply,
            "cited_fact_ids": [str(fact_id) for fact_id in cited],
            "confidence": confidence,
            "handoff_reason": handoff_reason,
            "objection_tag": objection_tag,
            "proposed_consent_prompt": proposed_consent_prompt,
        },
        ensure_ascii=False,
    )


def tool_call(name: str, *, call_id: str | None = None, **arguments: object) -> ChatMessage:
    """An assistant message that calls one tool with ``arguments``."""
    return ChatMessage(
        role="assistant",
        tool_calls=(
            ToolCall(
                id=call_id or f"call-{uuid4().hex[:8]}",
                name=name,
                arguments=json.dumps(arguments, default=str),
            ),
        ),
    )


@dataclass
class ScriptedChat:
    """A ``ChatModel`` that answers from ``script`` in order and records every request."""

    script: list[ChatMessage | str | ProviderError] = field(default_factory=list)
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        answer = self.script.pop(0)
        if isinstance(answer, ProviderError):
            raise answer
        message = (
            answer
            if isinstance(answer, ChatMessage)
            else ChatMessage(role="assistant", content=answer)
        )
        return ChatResponse(
            message=message,
            finish_reason="tool_calls" if message.tool_calls else "stop",
            meta=CallMeta(provider="local_mock", model=request.model, purpose=request.purpose),
        )


@dataclass
class AdGallery:
    """``send_ad_asset``'s backend: one approved ad per product, and every lookup it got."""

    assets: dict[UUID, ApprovedAsset] = field(default_factory=dict)
    calls: list[tuple[UUID, UUID, UUID | None, Mapping[str, str] | None]] = field(
        default_factory=list
    )

    def approve(self, product_id: UUID, code: str = "A7") -> ApprovedAsset:
        asset = ApprovedAsset(
            ad_id=uuid4(),
            code=code,
            image=Blob(data=b"\x89PNG\r\n\x1a\n" + code.encode(), mime_type="image/png"),
            caption=f"Mira el anuncio #{code}",
            wa_link=f"https://wa.me/573000000101?text=Hola%2C%20vi%20el%20anuncio%20%23{code}",
        )
        self.assets[product_id] = asset
        return asset

    async def __call__(
        self,
        seller_id: UUID,
        product_id: UUID,
        *,
        ad_id: UUID | None = None,
        variant_hint: Mapping[str, str] | None = None,
    ) -> ApprovedAsset | None:
        self.calls.append((seller_id, product_id, ad_id, variant_hint))
        asset = self.assets.get(product_id)
        if asset is None or ad_id not in (None, asset.ad_id):
            return None
        return asset


WEBHOOK_FIXTURES = REPO_ROOT / "evals" / "fixtures" / "webhooks"


def webhook_fixture(name: str) -> bytes:
    """A recorded WhatsApp webhook body, byte for byte (evals/fixtures/webhooks)."""
    return (WEBHOOK_FIXTURES / f"{name}.json").read_bytes()
