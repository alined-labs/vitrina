"""RFC 9457 problem details with the contract's stable codes (FR-082, openapi.yaml ``Problem``)."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import MISSING, BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from vitrina_core.http.context import current_request_id
from vitrina_core.providers import ProviderError

PROBLEM_JSON = "application/problem+json"


class ProblemCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CREATIVE_BLOCKED = "CREATIVE_BLOCKED"
    CONVERSATION_NOT_HUMAN = "CONVERSATION_NOT_HUMAN"
    TEMPLATE_NOT_APPROVED = "TEMPLATE_NOT_APPROVED"
    CAMPAIGN_ALREADY_SENT = "CAMPAIGN_ALREADY_SENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    BRIEF_SCHEMA_INVALID = "BRIEF_SCHEMA_INVALID"
    PRICE_UNVERIFIED = "PRICE_UNVERIFIED"
    DRAFT_NOT_CITABLE = "DRAFT_NOT_CITABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"  # an unhandled error (A-004)


# Status and title per code: from the contract's responses where it names them.
# BRIEF_SCHEMA_INVALID, PRICE_UNVERIFIED and DRAFT_NOT_CITABLE appear only in job errors and
# validation results there; INTERNAL_ERROR answers any unhandled error.
CATALOGUE: dict[ProblemCode, tuple[int, str]] = {
    ProblemCode.VALIDATION_ERROR: (400, "Validation error"),
    ProblemCode.UNAUTHORIZED: (401, "Unauthorized"),
    ProblemCode.INVALID_SIGNATURE: (401, "Invalid signature"),
    ProblemCode.FORBIDDEN: (403, "Forbidden"),
    ProblemCode.NOT_FOUND: (404, "Not found"),
    ProblemCode.CONFLICT: (409, "Conflict"),
    ProblemCode.IDEMPOTENCY_CONFLICT: (409, "Idempotency key reused with a different request"),
    ProblemCode.PAYLOAD_TOO_LARGE: (413, "Payload too large"),
    ProblemCode.RATE_LIMITED: (429, "Rate limited"),
    ProblemCode.PROVIDER_UNAVAILABLE: (503, "Provider unavailable"),
    ProblemCode.CREATIVE_BLOCKED: (409, "Creative blocked by the fidelity verifier"),
    ProblemCode.CONVERSATION_NOT_HUMAN: (409, "Conversation is not in human mode"),
    ProblemCode.TEMPLATE_NOT_APPROVED: (409, "Template not approved"),
    ProblemCode.CAMPAIGN_ALREADY_SENT: (409, "Campaign already sent"),
    ProblemCode.INSUFFICIENT_DATA: (422, "Not enough data"),
    ProblemCode.BRIEF_SCHEMA_INVALID: (502, "Brief did not match its schema"),
    ProblemCode.PRICE_UNVERIFIED: (422, "Price not supported by the cited facts"),
    ProblemCode.DRAFT_NOT_CITABLE: (422, "Draft items cannot be cited"),
    ProblemCode.INTERNAL_ERROR: (500, "Internal error"),
}

_BY_STATUS = {
    401: ProblemCode.UNAUTHORIZED,
    403: ProblemCode.FORBIDDEN,
    409: ProblemCode.CONFLICT,
    413: ProblemCode.PAYLOAD_TOO_LARGE,
    429: ProblemCode.RATE_LIMITED,
}


class FieldError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    type: Annotated[str, Field(json_schema_extra={"format": "uri", "default": "about:blank"})]
    title: str
    status: int
    detail: str | MISSING = MISSING
    instance: str | MISSING = MISSING
    code: ProblemCode
    errors: list[FieldError] | MISSING = MISSING
    request_id: str | MISSING = MISSING


class ProblemError(Exception):
    """Raise anywhere below a router to answer with a problem response."""

    def __init__(
        self,
        code: ProblemCode,
        *,
        detail: str | None = None,
        title: str | None = None,
        status: int | None = None,
        errors: Sequence[FieldError] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        default_status, default_title = CATALOGUE[code]
        super().__init__(detail or default_title)
        self.code = code
        self.status = status or default_status
        self.title = title or default_title
        self.detail = detail
        self.errors = list(errors)
        self.headers = dict(headers or {})


def problem_body(
    code: str,
    status: int,
    title: str,
    *,
    detail: str | None = None,
    errors: Sequence[FieldError] = (),
) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "about:blank", "title": title, "status": status, "code": code}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = [error.model_dump() for error in errors]
    request_id = current_request_id()
    if request_id:
        body["request_id"] = request_id
    return body


def problem_response(error: ProblemError) -> JSONResponse:
    body = problem_body(
        error.code, error.status, error.title, detail=error.detail, errors=error.errors
    )
    return JSONResponse(
        body, status_code=error.status, media_type=PROBLEM_JSON, headers=error.headers
    )


def internal_error_response() -> JSONResponse:
    status, title = CATALOGUE[ProblemCode.INTERNAL_ERROR]
    body = problem_body(
        ProblemCode.INTERNAL_ERROR, status, title, detail="Unexpected error; quote the request_id"
    )
    return JSONResponse(body, status_code=500, media_type=PROBLEM_JSON)


async def _on_problem(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ProblemError)
    return problem_response(error)


async def _on_validation(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    fields = [
        FieldError(
            field=".".join(str(part) for part in item["loc"][1:]) or str(item["loc"][0]),
            message=item["msg"],
        )
        for item in error.errors()
    ]
    return problem_response(ProblemError(ProblemCode.VALIDATION_ERROR, errors=fields))


async def _on_http(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    # Routing errors: 404 for unknown paths, 405 for unknown methods (reported as NOT_FOUND).
    code = _BY_STATUS.get(error.status_code, ProblemCode.NOT_FOUND)
    default_status, default_title = CATALOGUE[code]
    title = default_title if error.status_code == default_status else str(error.detail)
    return problem_response(ProblemError(code, status=error.status_code, title=title))


async def _on_provider(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ProviderError)
    return problem_response(
        ProblemError(ProblemCode.PROVIDER_UNAVAILABLE, detail=f"{error.provider}: {error}")
    )


# ── OpenAPI ──

DESCRIPTIONS = {
    400: "Validation error (`VALIDATION_ERROR`)",
    401: "Missing or invalid credentials (`UNAUTHORIZED`, `INVALID_SIGNATURE`)",
    403: "Forbidden (`FORBIDDEN`)",
    404: "Not found in this seller's scope (`NOT_FOUND`)",
    409: "State conflict (`CONFLICT`, `IDEMPOTENCY_CONFLICT`)",
    413: "File too large (`PAYLOAD_TOO_LARGE`)",
    422: "Not enough data (`INSUFFICIENT_DATA`)",
    429: "Rate limited (`RATE_LIMITED`); see `Retry-After`",
    503: "Upstream model/messaging provider unavailable (`PROVIDER_UNAVAILABLE`)",
}


def problem_responses(*statuses: int, **descriptions: str) -> dict[int | str, dict[str, Any]]:
    """``responses=`` for a route: each status documented as ``application/problem+json``.

    Pass ``description_409="…"`` style keywords to reuse a contract description verbatim.
    """
    responses: dict[int | str, dict[str, Any]] = {}
    for status in statuses:
        entry: dict[str, Any] = {
            "description": descriptions.get(
                f"description_{status}", DESCRIPTIONS.get(status, "Problem")
            ),
            "content": {PROBLEM_JSON: {"schema": {"$ref": "#/components/schemas/Problem"}}},
        }
        if status == 429:
            entry["headers"] = {"Retry-After": {"schema": {"type": "integer"}}}
        responses[status] = entry
    return responses


def _problem_components() -> dict[str, Any]:
    schema = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
    components: dict[str, Any] = schema.pop("$defs", {})
    components["Problem"] = schema
    return components


def _rewrite_openapi(document: dict[str, Any]) -> None:
    """Answer validation errors as 400 problems, so drop FastAPI's 422 entries; add Problem."""
    for item in document.get("paths", {}).values():
        for operation in item.values():
            responses = operation.get("responses", {}) if isinstance(operation, dict) else {}
            entry = responses.get("422", {})
            refs = [
                media.get("schema", {}).get("$ref", "")
                for media in entry.get("content", {}).values()
            ]
            if any(ref.endswith("/HTTPValidationError") for ref in refs):
                del responses["422"]
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)
    schemas.update(_problem_components())


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _on_problem)
    app.add_exception_handler(RequestValidationError, _on_validation)
    app.add_exception_handler(StarletteHTTPException, _on_http)
    app.add_exception_handler(ProviderError, _on_provider)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            _rewrite_openapi(FastAPI.openapi(app))
        assert app.openapi_schema is not None
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
