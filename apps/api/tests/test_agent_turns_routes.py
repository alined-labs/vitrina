"""FR-030: POST /v1/agent/turns runs one agent turn without WhatsApp (tests, evals, "try it")."""

import dataclasses
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.api import InMemoryCatalog
from vitrina_catalog.sellers import SellerCreate
from vitrina_conversation.testing import ScriptedChat, final_answer, tool_call
from vitrina_core.providers import ChatMessage, ProviderError
from vitrina_core.settings import Settings

SECRET = "agent-turns-routes-test-secret-32-bytes"
SELLER = UUID("00000000-0000-7000-8000-0000000000a1")
FALLBACK = "No tengo ese dato, te comunico con Ana"


def bearer(seller_id: UUID = SELLER) -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller_id)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@dataclasses.dataclass
class Api:
    client: TestClient
    catalog: InMemoryCatalog
    ids: dict[str, UUID]
    app: Any

    def script(self, *answers: ChatMessage | str | ProviderError) -> ScriptedChat:
        chat = ScriptedChat(list(answers))
        self.app.state.providers = dataclasses.replace(self.app.state.providers, chat=chat)
        return chat

    def turn(self, text: str | None = "Hola", **body: Any) -> Any:
        message: dict[str, Any] = {"kind": body.pop("kind", "text")}
        if text is not None:
            message["text"] = text
        return self.client.post(
            "/v1/agent/turns", json={"message": message, **body}, headers=bearer()
        )


async def make_api(**settings: Any) -> Api:
    app = create_app(
        Settings(  # type: ignore[call-arg]
            _env_file=None, supabase_jwt_secret=SECRET, **{"model_agent": "spark-test", **settings}
        )
    )
    await app.state.sellers.create(
        SellerCreate(
            business_name="Mochilas Ana",
            owner_name="Ana",
            whatsapp_number="+573000000101",
            city="Medellín",
        ),
        auth_subject=None,
        seller_id=SELLER,
    )
    catalog: InMemoryCatalog = app.state.catalog
    ids = {
        "mochila-azul": catalog.add_product(
            SELLER, "Mochila azul", price_cop=85000, stock=3, amounts_cop=(85000,)
        ),
        "envio-medellin": catalog.add_fact(
            SELLER, "shipping", "Envío dentro de Medellín: $8.000.", amounts_cop=(8000,)
        ),
    }
    return Api(TestClient(app), catalog, ids, app)


@pytest.fixture
async def api() -> Api:
    return await make_api()


async def test_fr_030_a_turn_returns_output_tools_validation_and_the_reply(api: Api) -> None:
    fact = api.ids["envio-medellin"]
    reply = "El envío dentro de Medellín vale $8.000."
    api.script(tool_call("search_catalog", query="envío Medellín"), final_answer(reply, fact))

    response = api.turn("¿cuánto vale el envío a Medellín?")

    assert response.status_code == 200
    body = response.json()
    UUID(body["conversation_id"])
    assert body["final_reply"] == reply
    assert body["output"]["cited_fact_ids"] == [str(fact)]
    assert body["output"]["confidence"] == 0.9
    (record,) = body["tool_calls"]
    assert (record["name"], record["status"]) == ("search_catalog", "ok")
    assert record["arguments"] == {"query": "envío Medellín"}
    price = body["validation"]["price_validator"]
    assert (price["passed"], price["amounts_in_reply"], price["retried"]) == (True, [8000], False)
    assert body["validation"]["groundedness"]["passed"]
    assert body["validation"]["scope"] == {"passed": True, "reason": "classifier: en tema"}
    assert {item["kind"] for item in body["retrieved"]} <= {"fact", "product"}
    assert [call["provider"] for call in body["model_calls"]] == ["local_mock"] * 3  # + scope


async def test_fr_030_a_dry_run_is_the_default_and_reserves_nothing(api: Api) -> None:
    product = api.ids["mochila-azul"]
    api.script(
        tool_call("reserve_item", product_id=product, quantity=1),
        final_answer("Te la reservo.", product),
    )

    body = api.turn("Resérvame la mochila azul").json()

    assert body["tool_calls"][0]["status"] == "skipped_dry_run"
    stock = await api.catalog.check_stock(SELLER, product)
    assert stock.available == 3


async def test_as_3_7_outside_a_dry_run_the_tool_reserves(api: Api) -> None:
    product = api.ids["mochila-azul"]
    api.script(
        tool_call("reserve_item", product_id=product, quantity=1),
        final_answer("Listo, quedó reservada.", product),
    )

    body = api.turn("Resérvame la mochila azul", dry_run=False).json()

    assert body["tool_calls"][0]["result"]["status"] == "reserved"
    stock = await api.catalog.check_stock(SELLER, product)
    assert stock.available == 2


async def test_as_3_4_a_turn_that_keeps_inventing_prices_shows_why_it_fell_back(api: Api) -> None:
    fact = api.ids["envio-medellin"]
    api.script(final_answer("Vale $12.000.", fact), final_answer("Vale $13.000.", fact))

    body = api.turn("¿cuánto vale el envío?").json()

    assert body["final_reply"] == FALLBACK
    price = body["validation"]["price_validator"]
    assert (price["passed"], price["violations"], price["retried"]) == (False, [13000], True)


async def test_fr_030_an_unreadable_turn_has_a_null_output(api: Api) -> None:
    api.script("no sé", "tampoco")

    body = api.turn("¿cuánto vale?").json()

    assert (body["output"], body["final_reply"]) == (None, FALLBACK)
    assert not body["validation"]["groundedness"]["passed"]


async def test_fr_030_the_conversation_id_is_kept(api: Api) -> None:
    conversation = uuid4()
    api.script(final_answer("¡Hola!", confidence=0.95))

    body = api.turn("Hola", conversation_id=str(conversation)).json()

    assert body["conversation_id"] == str(conversation)


async def test_fr_030_needs_a_seller_token(api: Api) -> None:
    response = api.client.post(
        "/v1/agent/turns", json={"message": {"kind": "text", "text": "Hola"}}
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("kind", "text"), [("image", "foto"), ("audio", None), ("text", None), ("text", "  ")]
)
async def test_fr_030_only_text_turns_with_text_are_accepted(
    api: Api, kind: str, text: str | None
) -> None:
    response = api.turn(text, kind=kind)

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    if kind != "text":  # photos and voice notes are read when they arrive on WhatsApp
        assert "WhatsApp" in response.json()["detail"]


async def test_fr_030_without_a_model_id_the_provider_is_unavailable() -> None:
    api = await make_api(model_agent=None)

    response = api.turn("Hola")

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


async def test_fr_030_a_provider_failure_is_503(api: Api) -> None:
    api.script(ProviderError("the model refused the request", provider="meta_model_api"))

    response = api.turn("Hola")

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
