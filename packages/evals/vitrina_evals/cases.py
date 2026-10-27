"""Eval fixtures: the conversation cases and the synthetic businesses they talk to (T028).

``evals/fixtures/conversations_es.jsonl`` holds one case per line: the business, the
customer's messages and what the agent's last reply must do (``Expected``). Each business in
``evals/fixtures/sellers/`` becomes a ``Scene``: its seller settings and an in-memory catalog
whose ids derive from the fixture keys, so prompts, citations and cassettes are the same on
every run (AS-7.3). Contacts and numbers are synthetic (Art. V.1).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from vitrina_catalog.api import FactKind, InMemoryCatalog, ProductStatus, SellerSettings
from vitrina_catalog.sellers import InMemorySellerStore, SellerCreate
from vitrina_catalog.text import product_amounts
from vitrina_core.money import amounts_cop
from vitrina_core.settings import REPO_ROOT

FIXTURES = REPO_ROOT / "evals" / "fixtures"
CONVERSATIONS = FIXTURES / "conversations_es.jsonl"
SELLERS = FIXTURES / "sellers"
# Fixture ids: uuid5 of "<business>/<key>" in this namespace.
NAMESPACE = UUID("6d2f4a52-8a3e-5b1c-9f7d-0e4c2b1a9d30")

HandoffReason = Literal[
    "customer_request",
    "complaint_refund",
    "payment_issue",
    "medical_topic",
    "low_confidence_twice",
    "negative_sentiment",
    "amount_over_threshold",
    "price_unverified",
    "policy_exception",
    "other",
]


class Fixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Expected(Fixture):
    """What the last reply must do. ``handoff``: False (none), True (any reason), a reason,
    or None (not labelled: left out of the handoff metrics)."""

    cites: tuple[str, ...] = ()
    forbidden_cites: tuple[str, ...] = ()
    forbidden_amounts: tuple[int, ...] = ()
    handoff: bool | HandoffReason | None = False
    off_topic: bool = False


class ConversationCase(Fixture):
    case_id: str
    category: Literal["grounded", "price_trap", "handoff", "off_topic"]
    business: str
    messages: tuple[str, ...] = Field(min_length=1)
    expected: Expected


class ProductFixture(Fixture):
    key: str
    name: str
    description: str | None = None
    price_cop: int
    stock: int | None = None
    status: ProductStatus = "active"
    attributes: dict[str, str] = Field(default_factory=dict)


class FactFixture(Fixture):
    key: str
    kind: FactKind
    text: str
    status: Literal["draft", "active"] = "active"


class BusinessFixture(Fixture):
    key: str
    seller: dict[str, Any]
    products: tuple[ProductFixture, ...] = ()
    facts: tuple[FactFixture, ...] = ()


def load_cases(path: Path = CONVERSATIONS, *, sample: int | None = None) -> list[ConversationCase]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [ConversationCase.model_validate_json(line) for line in lines]
    return cases[:sample] if sample is not None else cases


def load_business(key: str, root: Path = SELLERS) -> BusinessFixture:
    return BusinessFixture.model_validate_json((root / f"{key}.json").read_text(encoding="utf-8"))


def fixture_id(business: str, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{business}/{key}")


@dataclass(frozen=True)
class Scene:
    """One business, seeded: what the agent can see, and what each active item may quote."""

    seller: SellerSettings
    catalog: InMemoryCatalog
    ids: dict[str, UUID]
    allowed: dict[UUID, frozenset[int]]


async def build_scene(business: BusinessFixture) -> Scene:
    sellers = InMemorySellerStore()
    seller_id = fixture_id(business.key, "seller")
    await sellers.create(
        SellerCreate.model_validate(business.seller), auth_subject=None, seller_id=seller_id
    )
    settings = await sellers.settings(seller_id)
    assert settings is not None
    catalog = InMemoryCatalog()
    ids: dict[str, UUID] = {}
    allowed: dict[UUID, frozenset[int]] = {}
    for product in business.products:
        amounts = product_amounts(product.price_cop, product.description, product.attributes)
        ids[product.key] = catalog.add_product(
            seller_id,
            product.name,
            price_cop=product.price_cop,
            stock=product.stock,
            status=product.status,
            description=product.description,
            attributes=product.attributes,
            amounts_cop=amounts,
            product_id=fixture_id(business.key, product.key),
        )
        if product.status == "active":
            allowed[ids[product.key]] = frozenset(amounts)
    for fact in business.facts:
        amounts = tuple(amounts_cop(fact.text))
        ids[fact.key] = catalog.add_fact(
            seller_id,
            fact.kind,
            fact.text,
            status=fact.status,
            amounts_cop=amounts,
            fact_id=fixture_id(business.key, fact.key),
        )
        if fact.status == "active":
            allowed[ids[fact.key]] = frozenset(amounts)
    return Scene(seller=settings, catalog=catalog, ids=ids, allowed=allowed)
