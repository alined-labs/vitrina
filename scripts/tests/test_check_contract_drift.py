import importlib.util
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends, FastAPI, File, Query, Response, UploadFile
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

from vitrina_api.main import create_app

SCRIPT = Path(__file__).resolve().parents[1] / "check-contract-drift.py"
_spec = importlib.util.spec_from_file_location("check_contract_drift", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
drift = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = drift
_spec.loader.exec_module(drift)

HEALTH_SCHEMA = {
    "type": "object",
    "required": ["status", "version"],
    "properties": {"status": {"type": "string", "enum": ["ok"]}, "version": {"type": "string"}},
}

CONTRACT: dict[str, Any] = {
    "openapi": "3.1.0",
    "servers": [{"url": "https://api.example/v1"}, {"url": "http://localhost:8000/v1"}],
    "security": [{"sellerAuth": []}],
    "paths": {
        "/health": {
            "get": {
                "tags": ["Health"],
                "operationId": "getHealth",
                "summary": "Liveness",
                "security": [],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": HEALTH_SCHEMA}},
                    }
                },
            }
        },
        "/things": {
            "get": {
                "tags": ["Things"],
                "operationId": "listThings",
                "parameters": [
                    {"$ref": "#/components/parameters/Limit"},
                    {
                        "name": "status",
                        "in": "query",
                        "schema": {"$ref": "#/components/schemas/ThingStatus"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Page of things",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "allOf": [
                                        {"$ref": "#/components/schemas/Page"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "items": {
                                                    "type": "array",
                                                    "items": {"$ref": "#/components/schemas/Thing"},
                                                }
                                            },
                                        },
                                    ]
                                }
                            }
                        },
                    }
                },
            },
        },
        "/things/{thing_id}": {
            "parameters": [
                {
                    "name": "thing_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "delete": {
                "tags": ["Things"],
                "operationId": "deleteThing",
                "responses": {"204": {"description": "Deleted"}},
            },
        },
        "/uploads": {
            "post": {
                "tags": ["Things"],
                "operationId": "uploadThing",
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "required": ["file"],
                                "properties": {"file": {"type": "string", "format": "binary"}},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Stored"}},
            }
        },
    },
    "components": {
        "securitySchemes": {
            "sellerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Seller JWT",
            }
        },
        "parameters": {
            "Limit": {
                "name": "limit",
                "in": "query",
                "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            }
        },
        "schemas": {
            "Page": {
                "type": "object",
                "required": ["items", "next_cursor"],
                "properties": {
                    "items": {"type": "array", "items": {}},
                    "next_cursor": {"type": ["string", "null"]},
                },
            },
            "ThingStatus": {"type": "string", "enum": ["draft", "active"]},
            "Thing": {
                "type": "object",
                "required": ["id", "status"],
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "status": {"$ref": "#/components/schemas/ThingStatus"},
                    "stock": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "null = not tracked",
                    },
                    "parent_id": {
                        "oneOf": [{"type": "string", "format": "uuid"}, {"type": "null"}]
                    },
                },
            },
        },
    },
}


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class ThingStatus(StrEnum):
    draft = "draft"
    active = "active"


class Thing(BaseModel):
    id: UUID
    status: ThingStatus
    stock: Annotated[int, Field(ge=0)] | None = None
    parent_id: UUID | None = None


class ThingPage(BaseModel):
    items: list[Thing]
    next_cursor: str | None


seller_auth = HTTPBearer(scheme_name="sellerAuth", bearerFormat="JWT")


def matching_app() -> FastAPI:
    router = APIRouter()

    @router.get("/health", tags=["Health"], operation_id="getHealth")
    def get_health() -> Health:
        raise NotImplementedError

    @router.get(
        "/things", tags=["Things"], operation_id="listThings", dependencies=[Depends(seller_auth)]
    )
    def list_things(
        limit: Annotated[int, Query(ge=1, le=100)] = 25, status: ThingStatus | None = None
    ) -> ThingPage:
        raise NotImplementedError

    @router.delete(
        "/things/{thing_id}",
        tags=["Things"],
        operation_id="deleteThing",
        status_code=204,
        dependencies=[Depends(seller_auth)],
    )
    def delete_thing(thing_id: UUID) -> Response:
        raise NotImplementedError

    @router.post(
        "/uploads",
        tags=["Things"],
        operation_id="uploadThing",
        response_class=Response,
        dependencies=[Depends(seller_auth)],
    )
    def upload_thing(file: Annotated[UploadFile, File()]) -> Response:
        raise NotImplementedError

    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return app


def problems_for(app: FastAPI, contract: dict[str, Any] = CONTRACT) -> list[str]:
    return drift.check(contract, app.openapi()).problems


def test_matching_app_has_no_drift() -> None:
    report = drift.check(CONTRACT, matching_app().openapi())

    assert report.problems == []
    assert report.pending == []
    assert len(report.implemented) == 4


def test_deliberate_type_drift_is_reported() -> None:
    class WrongHealth(BaseModel):
        status: Literal["ok"]
        version: int

    app = FastAPI()

    @app.get("/v1/health", tags=["Health"], operation_id="getHealth")
    def get_health() -> WrongHealth:
        raise NotImplementedError

    problems = problems_for(app)

    assert problems == [
        "GET /health (getHealth): responses.200.content.application/json.schema.properties"
        '.version.type: contract "string" != app "integer"'
    ]


def test_operation_outside_the_contract_is_drift() -> None:
    app = matching_app()

    @app.get("/v1/surprise", operation_id="surprise")
    def surprise() -> Health:
        raise NotImplementedError

    assert problems_for(app) == ["GET /surprise (surprise): not in the contract"]


def test_route_outside_the_base_path_is_drift() -> None:
    app = FastAPI()

    @app.get("/health", operation_id="getHealth")
    def get_health() -> Health:
        raise NotImplementedError

    assert problems_for(app) == ["GET /health (getHealth): outside the contract base path /v1"]


def test_operation_id_and_security_drift_are_reported() -> None:
    app = FastAPI()

    @app.delete(
        "/v1/things/{thing_id}", tags=["Things"], operation_id="removeThing", status_code=204
    )
    def delete_thing(thing_id: UUID) -> Response:
        raise NotImplementedError

    assert problems_for(app) == [
        'DELETE /things/{thing_id} (removeThing): operationId: contract "deleteThing" != app "removeThing"',
        'DELETE /things/{thing_id} (removeThing): security: contract [["sellerAuth"]] != app []',
    ]


def test_undeclared_response_is_drift() -> None:
    app = FastAPI()

    @app.post(
        "/v1/uploads",
        tags=["Things"],
        operation_id="uploadThing",
        dependencies=[Depends(seller_auth)],
    )
    def upload_thing(file: Annotated[UploadFile, File()]) -> None:
        raise NotImplementedError

    assert problems_for(app) == [
        'POST /uploads (uploadThing): responses.200.content.application/json: not in the contract (app: {"schema": {}})'
    ]


def test_pending_operations_fail_only_when_complete_is_required(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = FastAPI()

    @app.get("/v1/health", tags=["Health"], operation_id="getHealth")
    def get_health() -> Health:
        raise NotImplementedError

    assert drift.run(CONTRACT, app.openapi(), require_complete=False, list_pending=True) == 0
    assert drift.run(CONTRACT, app.openapi(), require_complete=True, list_pending=False) == 1
    output = capsys.readouterr().out
    assert "1 of 4 contract operations implemented, 3 pending" in output
    assert "pending: DELETE /things/{thing_id} (deleteThing)" in output


def test_cli_passes_on_the_real_contract_and_app(capsys: pytest.CaptureFixture[str]) -> None:
    assert drift.main([]) == 0
    assert "contract drift: OK" in capsys.readouterr().out


@pytest.mark.parametrize(
    "spelling",
    [
        {"type": ["string", "null"], "format": "uuid", "description": "contract style"},
        {"oneOf": [{"type": "string", "format": "uuid"}, {"type": "null"}]},
        {
            "anyOf": [{"type": "string", "format": "uuid", "title": "Id"}, {"type": "null"}],
            "default": None,
        },
    ],
)
def test_nullable_spellings_normalize_alike(spelling: dict[str, Any]) -> None:
    assert drift.normalize_schema(spelling) == {
        "anyOf": [{"format": "uuid", "type": "string"}, {"type": "null"}]
    }


def test_fastapi_validation_422_is_ignored_but_a_contract_422_is_not() -> None:
    operation = {
        "operationId": "generateBrief",
        "security": [],
        "parameters": [
            {
                "name": "min_conversations",
                "in": "query",
                "schema": {"type": "integer", "default": 10},
            }
        ],
        "responses": {"202": {"description": "Accepted"}},
    }
    contract = {"servers": [{"url": "/v1"}], "paths": {"/briefs/generate": {"post": operation}}}
    app = FastAPI()

    @app.post(
        "/v1/briefs/generate",
        operation_id="generateBrief",
        status_code=202,
        response_class=Response,
    )
    def generate(min_conversations: int = 10) -> Response:
        raise NotImplementedError

    assert drift.check(contract, app.openapi()).problems == []

    operation["responses"]["422"] = {"description": "Not enough data"}

    assert drift.check(contract, app.openapi()).problems == [
        'POST /briefs/generate (generateBrief): responses.422: missing in the app (contract: {"content": {}})'
    ]


def test_real_app_matches_the_contract() -> None:
    contract = drift.load_contract(drift.DEFAULT_CONTRACT)

    report = drift.check(contract, create_app().openapi())

    assert report.problems == []
    assert "GET /health" in report.implemented
