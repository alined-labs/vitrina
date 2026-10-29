from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import pytest
import sqlalchemy as sa
import yaml
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from vitrina_core.http import (
    IdempotencyKey,
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
    PageQuery,
    ProblemCode,
    ProblemError,
    RequestIdMiddleware,
    current_request_id,
    decode_cursor,
    encode_cursor,
    install_problem_handlers,
    keyset_page,
    next_cursor,
    problem_responses,
)
from vitrina_core.ids import NIL
from vitrina_core.providers import ProviderUnavailableError
from vitrina_core.settings import REPO_ROOT

SELLER_A = UUID("00000000-0000-7000-8000-00000000000a")
SELLER_B = UUID("00000000-0000-7000-8000-00000000000b")


class ProductIn(BaseModel):
    name: str
    price_cop: Annotated[int, Field(ge=0)]


def make_app(store: InMemoryIdempotencyStore | None = None) -> tuple[FastAPI, list[str]]:
    calls: list[str] = []
    app = FastAPI()
    install_problem_handlers(app)

    def scope(request: Request) -> UUID:
        return {"a": SELLER_A, "b": SELLER_B}.get(request.headers.get("x-seller", ""), NIL)

    app.add_middleware(
        IdempotencyMiddleware, store=store or InMemoryIdempotencyStore(), scope=scope
    )
    app.add_middleware(RequestIdMiddleware)

    @app.post("/products", status_code=201, responses=problem_responses(400))
    def create(product: ProductIn, _key: IdempotencyKey = None) -> dict[str, Any]:
        calls.append(product.name)
        return {"name": product.name, "n": len(calls)}

    @app.get("/things/{thing_id}", responses=problem_responses(404))
    def get_thing(thing_id: UUID) -> dict[str, str]:
        raise ProblemError(ProblemCode.NOT_FOUND, detail=f"thing {thing_id} not found")

    @app.get("/model")
    def model() -> dict[str, str]:
        raise ProviderUnavailableError("timeout after 20 s", provider="meta_model_api")

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("bug")

    @app.post("/fails")
    def fails(_key: IdempotencyKey = None) -> dict[str, str]:
        calls.append("fails")
        raise RuntimeError("transient")

    @app.get("/rid")
    def rid() -> dict[str, str | None]:
        return {"request_id": current_request_id()}

    return app, calls


def client(store: InMemoryIdempotencyStore | None = None) -> tuple[TestClient, list[str]]:
    app, calls = make_app(store)
    return TestClient(app, raise_server_exceptions=False), calls


# ── FR-082 problem+json ──


def test_fr_082_problem_error_renders_rfc_9457_with_code_and_request_id() -> None:
    http, _ = client()

    response = http.get(
        "/things/00000000-0000-7000-8000-000000000001", headers={"X-Request-Id": "req-123"}
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Not found",
        "status": 404,
        "code": "NOT_FOUND",
        "detail": "thing 00000000-0000-7000-8000-000000000001 not found",
        "request_id": "req-123",
    }


def test_fr_082_validation_errors_are_400_with_field_details() -> None:
    http, _ = client()

    response = http.post("/products", json={"name": "Bolso", "price_cop": -1})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] == "price_cop"
    assert body["errors"][0]["message"]


def test_fr_082_unknown_routes_and_bad_path_parameters_are_problems() -> None:
    http, _ = client()

    assert http.get("/nowhere").json()["code"] == "NOT_FOUND"
    bad = http.get("/things/not-a-uuid")
    assert (bad.status_code, bad.json()["code"], bad.json()["errors"][0]["field"]) == (
        400,
        "VALIDATION_ERROR",
        "thing_id",
    )


def test_fr_082_provider_outage_is_503_provider_unavailable() -> None:
    http, _ = client()

    response = http.get("/model")

    assert (response.status_code, response.json()["code"]) == (503, "PROVIDER_UNAVAILABLE")


def test_fr_082_unexpected_errors_are_500_problems_without_internals() -> None:
    http, _ = client()

    response = http.get("/boom")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "bug" not in response.text


def test_fr_082_every_problem_code_is_one_the_contract_declares() -> None:
    contract_path = REPO_ROOT / "specs" / "001-vitrina-api" / "contracts" / "openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    declared = set(contract["components"]["schemas"]["Problem"]["properties"]["code"]["enum"])
    http, _ = client()

    assert http.get("/boom").json()["code"] in declared
    assert {code.value for code in ProblemCode} == declared


def test_openapi_documents_problem_responses_instead_of_fastapi_422() -> None:
    app, _ = make_app()
    document = app.openapi()

    not_found = document["paths"]["/things/{thing_id}"]["get"]["responses"]["404"]
    assert not_found["content"] == {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
    }
    assert "Problem" in document["components"]["schemas"]
    assert "422" not in document["paths"]["/products"]["post"]["responses"]
    assert "HTTPValidationError" not in document["components"]["schemas"]


# ── FR-084 request id ──


def test_fr_084_request_id_is_echoed_or_generated() -> None:
    http, _ = client()

    echoed = http.get("/rid", headers={"X-Request-Id": "abc-123"})
    generated = http.get("/rid")
    hostile = http.get("/rid", headers={"X-Request-Id": "x" * 300})

    assert echoed.headers["x-request-id"] == "abc-123" == echoed.json()["request_id"]
    assert generated.headers["x-request-id"] == generated.json()["request_id"]
    assert len(generated.headers["x-request-id"]) == 32
    assert hostile.headers["x-request-id"] != "x" * 300


# ── FR-081 idempotency ──


def test_fr_081_same_key_and_body_replays_the_original_response() -> None:
    http, calls = client()
    headers = {"Idempotency-Key": "key-0001-abcd"}

    first = http.post("/products", json={"name": "Bolso", "price_cop": 85000}, headers=headers)
    second = http.post("/products", json={"name": "Bolso", "price_cop": 85000}, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json() == {"name": "Bolso", "n": 1}
    assert second.headers["idempotent-replayed"] == "true"
    assert calls == ["Bolso"]


def test_fr_081_same_key_with_another_body_is_409_idempotency_conflict() -> None:
    http, calls = client()
    headers = {"Idempotency-Key": "key-0002-abcd"}
    http.post("/products", json={"name": "Bolso", "price_cop": 85000}, headers=headers)

    response = http.post("/products", json={"name": "Gorra", "price_cop": 30000}, headers=headers)

    assert (response.status_code, response.json()["code"]) == (409, "IDEMPOTENCY_CONFLICT")
    assert calls == ["Bolso"]


def test_fr_081_keys_are_scoped_per_seller() -> None:
    http, calls = client()
    body = {"name": "Bolso", "price_cop": 85000}

    http.post("/products", json=body, headers={"Idempotency-Key": "shared-key-1", "X-Seller": "a"})
    http.post("/products", json=body, headers={"Idempotency-Key": "shared-key-1", "X-Seller": "b"})

    assert calls == ["Bolso", "Bolso"]


def test_fr_081_server_errors_are_not_stored_so_the_client_can_retry() -> None:
    http, calls = client()
    headers = {"Idempotency-Key": "retry-key-01"}

    assert http.post("/fails", headers=headers).status_code == 500
    assert http.post("/fails", headers=headers).status_code == 500
    assert calls == ["fails", "fails"]


def test_fr_081_keys_expire_after_24_hours() -> None:
    now = [datetime(2026, 10, 27, 12, 0, tzinfo=UTC)]
    store = InMemoryIdempotencyStore(clock=lambda: now[0])
    http, calls = client(store)
    headers = {"Idempotency-Key": "old-key-0001"}
    http.post("/products", json={"name": "Bolso", "price_cop": 85000}, headers=headers)

    now[0] += timedelta(hours=24, seconds=1)
    http.post("/products", json={"name": "Gorra", "price_cop": 30000}, headers=headers)

    assert calls == ["Bolso", "Gorra"]


def test_fr_081_key_length_follows_the_contract() -> None:
    http, _ = client()

    response = http.post(
        "/products", json={"name": "Bolso", "price_cop": 1}, headers={"Idempotency-Key": "short"}
    )

    assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_ERROR")


# ── FR-080 cursor pagination ──

rows = sa.Table(
    "rows",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)


def test_fr_080_cursor_round_trips_and_rejects_garbage() -> None:
    moment = datetime(2026, 10, 27, 12, 30, tzinfo=UTC)

    assert decode_cursor(encode_cursor(moment, SELLER_A)) == (moment, SELLER_A)
    with pytest.raises(ProblemError) as raised:
        decode_cursor("not-a-cursor")
    assert raised.value.code is ProblemCode.VALIDATION_ERROR


def test_fr_080_keyset_page_orders_newest_first_and_fetches_one_extra() -> None:
    moment = datetime(2026, 10, 27, 12, 30, tzinfo=UTC)
    statement = keyset_page(sa.select(rows), rows, limit=25, cursor=encode_cursor(moment, SELLER_A))
    sql = str(statement.compile(compile_kwargs={"literal_binds": False}))

    assert "(rows.created_at, rows.id) < (" in sql
    assert "ORDER BY rows.created_at DESC, rows.id DESC" in sql
    assert statement._limit_clause.value == 26  # type: ignore[attr-defined]


def test_fr_080_next_cursor_points_after_the_last_item_only_when_more_rows_exist() -> None:
    page = [{"id": SELLER_A, "created_at": datetime(2026, 10, 27, tzinfo=UTC)}] * 3

    items, cursor = next_cursor(page, limit=2)
    last_items, last_cursor = next_cursor(page[:2], limit=2)

    assert len(items) == 2
    assert cursor == encode_cursor(page[1]["created_at"], page[1]["id"])
    assert (len(last_items), last_cursor) == (2, None)


def test_fr_080_limit_is_bounded_by_the_contract() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/items")
    def items(page: Annotated[PageQuery, Depends()]) -> dict[str, Any]:
        return {"limit": page.limit, "cursor": page.cursor}

    http = TestClient(app)
    assert http.get("/items").json() == {"limit": 25, "cursor": None}
    assert http.get("/items", params={"limit": 101}).status_code == 400
    assert http.get("/items", params={"limit": 0}).status_code == 400
