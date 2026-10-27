"""agent-tools.json tools, run by the engine for one seller and one conversation (FR-024).

Seller and conversation come from the turn, never from the model. Errors go back to the
model as tool results. Side effects are skipped in a dry run (R9); decisions (handoff,
objection, consent, opt-out) are recorded either way for the pipeline to apply.
"""

import json
from typing import Any
from uuid import uuid4

import pytest

from vitrina_conversation.contract import tool_specs
from vitrina_conversation.testing import Shop
from vitrina_conversation.tools import ARGUMENTS, ToolBox
from vitrina_core.providers import ToolCall

CONVERSATION = uuid4()


def toolbox(shop: Shop, **options: Any) -> ToolBox:
    options.setdefault("dry_run", True)
    options.setdefault("message", "Hola")
    return ToolBox(
        catalog=shop.catalog, seller=shop.seller, conversation_id=CONVERSATION, **options
    )


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=json.dumps(arguments, default=str))


def test_fr_024_tool_arguments_mirror_the_contract() -> None:
    for spec in tool_specs():
        schema = ARGUMENTS[spec.name].model_json_schema()
        assert set(schema.get("properties", {})) == set(spec.parameters["properties"]), spec.name
        assert set(schema.get("required", [])) == set(spec.parameters["required"]), spec.name
        assert schema["additionalProperties"] is False, spec.name


async def test_fr_024_search_catalog_answers_from_active_items(shop: Shop) -> None:
    record = await toolbox(shop).run(call("search_catalog", query="mochila azul"))

    found = [hit["id"] for hit in record.result["results"]]
    assert record.status == "ok"
    assert str(shop.ids["mochila-azul"]) in found
    assert str(shop.ids["borrador"]) not in found


async def test_fr_024_get_product_adds_approved_ad_ids(shop: Shop) -> None:
    ad_id = uuid4()

    async def approved(_seller: object, _product: object) -> list[Any]:
        return [ad_id]

    box = toolbox(shop, approved_ads=approved)
    record = await box.run(call("get_product", product_id=shop.ids["mochila-azul"]))

    assert record.status == "ok"
    assert (record.result["name"], record.result["price_cop"]) == ("Mochila azul", 85000)
    assert record.result["approved_ad_ids"] == [str(ad_id)]


async def test_fr_024_check_stock_reports_a_sold_out_product(shop: Shop) -> None:
    record = await toolbox(shop).run(call("check_stock", product_id=shop.ids["mochila-roja"]))

    assert (record.result["available"], record.result["tracked"]) == (0, True)


@pytest.mark.parametrize(
    "name",
    ["get_product", "check_stock"],
)
async def test_fr_024_a_product_of_another_seller_is_not_found(shop: Shop, name: str) -> None:
    stranger = shop.catalog.add_product(uuid4(), "Ajena", price_cop=1000)

    record = await toolbox(shop).run(call(name, product_id=stranger))

    assert record.status == "error"
    assert "error" in record.result


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_product", "not json"),
        ("get_product", '{"product_id": "not-a-uuid"}'),
        ("search_catalog", '{"query": "x", "seller_id": "someone-else"}'),
        ("reserve_item", json.dumps({"product_id": str(uuid4()), "quantity": 99})),
        ("no_such_tool", "{}"),
    ],
    ids=["not-json", "bad-uuid", "extra-argument", "over-limit", "unknown-tool"],
)
async def test_fr_024_bad_calls_come_back_as_tool_errors(
    shop: Shop, name: str, arguments: str
) -> None:
    record = await toolbox(shop).run(ToolCall(id="call-1", name=name, arguments=arguments))

    assert record.status == "error"
    assert record.result["error"]


async def test_r9_a_dry_run_reserves_nothing(shop: Shop) -> None:
    box = toolbox(shop)

    record = await box.run(call("reserve_item", product_id=shop.ids["mochila-azul"], quantity=1))

    assert (record.status, record.result) == ("skipped_dry_run", {"status": "skipped_dry_run"})
    assert box.actions.reservations == []
    stock = await shop.catalog.check_stock(shop.seller.seller_id, shop.ids["mochila-azul"])
    assert stock.available == 3


async def test_as_3_7_reserve_item_holds_stock_outside_a_dry_run(shop: Shop) -> None:
    box = toolbox(shop, dry_run=False)

    record = await box.run(call("reserve_item", product_id=shop.ids["mochila-azul"], quantity=1))

    assert record.result["status"] == "reserved"
    assert record.result["code"]
    (reservation,) = box.actions.reservations
    assert reservation.code == record.result["code"]
    stock = await shop.catalog.check_stock(shop.seller.seller_id, shop.ids["mochila-azul"])
    assert stock.available == 2


async def test_r9_a_handoff_is_decided_even_in_a_dry_run(shop: Shop) -> None:
    box = toolbox(shop)

    record = await box.run(
        call("handoff_to_human", reason="customer_request", summary="Quiere hablar con Ana.")
    )

    assert record.status == "skipped_dry_run"
    assert box.actions.handoff is not None
    assert (box.actions.handoff.reason, box.actions.handoff.summary) == (
        "customer_request",
        "Quiere hablar con Ana.",
    )


async def test_as_4_2_record_objection_keeps_tag_quote_and_product(shop: Shop) -> None:
    box = toolbox(shop)

    record = await box.run(
        call(
            "record_objection",
            tag="shipping",
            quote="muy caro el envío",
            product_id=shop.ids["mochila-azul"],
        )
    )

    (objection,) = box.actions.objections
    assert record.result == {"objection_id": str(objection.id)}
    assert (objection.tag, objection.quote, objection.product_id) == (
        "shipping",
        "muy caro el envío",
        shop.ids["mochila-azul"],
    )


async def test_r8_consent_needs_a_pending_prompt(shop: Shop) -> None:
    box = toolbox(shop, dry_run=False, message="Sí")

    record = await box.run(call("record_consent", purpose="marketing", response_text="Sí"))

    assert record.result == {"consent_id": None, "status": "rejected_no_prompt_shown"}
    assert box.actions.consent is None


async def test_r8_consent_needs_an_affirmative_reply(shop: Shop) -> None:
    box = toolbox(shop, dry_run=False, message="mmm no sé", pending_consent_prompt="¿Te aviso?")

    record = await box.run(call("record_consent", purpose="marketing", response_text="Sí"))

    assert record.result["status"] == "rejected_not_affirmative"
    assert box.actions.consent is None


async def test_r8_consent_records_the_customers_literal_reply(shop: Shop) -> None:
    box = toolbox(shop, dry_run=False, message="Sí, dale", pending_consent_prompt="¿Te aviso?")

    record = await box.run(call("record_consent", purpose="marketing", response_text="SI ACEPTO"))

    assert record.result["status"] == "granted"
    assert box.actions.consent is not None
    assert (box.actions.consent.prompt_text, box.actions.consent.response_text) == (
        "¿Te aviso?",
        "Sí, dale",
    )


@pytest.mark.parametrize(("erase", "status"), [(False, "opted_out"), (True, "erasure_scheduled")])
async def test_as_6_5_opt_out_records_the_request(shop: Shop, erase: bool, status: str) -> None:
    box = toolbox(shop, dry_run=False)

    record = await box.run(call("opt_out", also_erase_data=erase))

    assert record.result == {"status": status}
    assert box.actions.opt_out is not None
    assert box.actions.opt_out.erase is erase


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("book_slot", {"service": "Corte", "starts_at": "2026-10-28T10:00:00-05:00"}),
        ("send_ad_asset", {"product_id": str(uuid4())}),
    ],
)
async def test_tools_without_a_backend_yet_answer_with_an_error(
    shop: Shop, name: str, arguments: dict[str, object]
) -> None:
    record = await toolbox(shop, dry_run=False).run(call(name, **arguments))

    assert record.status == "error"


async def test_r5_a_second_handoff_call_keeps_the_first_handoff(shop: Shop) -> None:
    box = toolbox(shop, dry_run=False)

    first = await box.run(call("handoff_to_human", reason="customer_request", summary="Primera."))
    second = await box.run(call("handoff_to_human", reason="other", summary="Segunda."))

    assert box.actions.handoff is not None
    assert (box.actions.handoff.reason, box.actions.handoff.summary) == (
        "customer_request",
        "Primera.",
    )
    assert first.result["handoff_id"] == second.result["handoff_id"]
