"""Contract drift check (T003, constitution Art. VI.1).

Compares the OpenAPI document FastAPI generates for the app with the frozen contract
``specs/001-vitrina-api/contracts/openapi.yaml``. Both sides are normalized first: ``$ref``s
are inlined, annotations (title, description, summary, examples) are dropped, the nullable
spellings ``type: [X, "null"]``, ``oneOf``/``anyOf`` with a null branch and Pydantic's
``anyOf`` are unified, ``allOf`` is merged, and sets (enum, required, tags) are sorted.

Every operation the app implements must equal its contract operation: operationId, tags,
effective security, parameters, request body and responses. An operation outside the
contract is drift. Contract operations the app does not implement yet are pending; they
fail only with ``--require-complete`` (the freeze, T084).

    uv run python scripts/check-contract-drift.py [--require-complete] [--list-pending]

Exit codes: 0 no drift, 1 drift (or pending operations with --require-complete),
2 the contract or the app could not be loaded.

Matching the contract with Pydantic models and FastAPI routes:
- required property                 ``name: T``
- optional and nullable             ``name: T | None = None``
- optional, not nullable            ``name: T | MISSING = MISSING`` (``from pydantic import MISSING``)
- property with a contract default  ``name: T = <the contract default>``
- response without a body           ``response_class=Response`` (``-> None`` is documented as JSON)
- operation id, tags, security      ``operation_id=``, the router's tags, the core auth dependencies
"""

import argparse
import importlib
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_CONTRACT = (
    Path(__file__).resolve().parents[1] / "specs/001-vitrina-api/contracts/openapi.yaml"
)
DEFAULT_APP = "vitrina_api.main:create_app"

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
# Keywords that document a schema without constraining it.
ANNOTATIONS = frozenset(
    {"title", "description", "summary", "example", "examples", "$comment", "externalDocs"}
)
NULL = {"type": "null"}


class DriftError(Exception):
    """The contract or the generated document cannot be compared."""


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    implemented: list[str] = field(default_factory=list)


def load_contract(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise DriftError(f"{path} is not an OpenAPI document")
    return document


# ── $ref resolution ───────────────────────────────────────────────────────────────────


def resolve(document: dict[str, Any], node: Any, seen: tuple[str, ...] = ()) -> Any:
    """Inline every local ``$ref`` below ``node``; sibling keywords override the target."""
    if isinstance(node, list):
        return [resolve(document, item, seen) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise DriftError(f"only local $refs are supported, got {ref!r}")
        if ref in seen:
            raise DriftError(f"recursive $ref {ref} is not supported")
        target: Any = document
        for part in ref[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or key not in target:
                raise DriftError(f"unresolvable $ref {ref}")
            target = target[key]
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        resolved = resolve(document, target, (*seen, ref))
        if siblings:
            return {**resolved, **resolve(document, siblings, seen)}
        return resolved
    return {key: resolve(document, value, seen) for key, value in node.items()}


# ── schema normalization ──────────────────────────────────────────────────────────────


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _is_null(schema: Any) -> bool:
    return bool(schema == NULL)


def _branch_key(schema: Any) -> tuple[bool, str]:
    return (_is_null(schema), canonical(schema))


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Merge two ``allOf`` parts: properties merge per name, required lists unite."""
    merged = dict(base)
    for key, value in extra.items():
        if key not in merged:
            merged[key] = value
        elif key == "required":
            merged[key] = sorted(set(merged[key]) | set(value))
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_schema(schema: Any) -> Any:
    """Canonical form of a resolved JSON Schema (2020-12, as used by OpenAPI 3.1)."""
    if schema is True:
        return {}
    if schema is False:
        return {"not": {}}
    if not isinstance(schema, dict):
        return schema
    # FastAPI writes the document with exclude_none, so a null-valued keyword (``default:
    # null``, or a YAML slip such as a comma inside a flow mapping, A-005) never appears in
    # the app; only ``const: null`` means something.
    result = {
        key: value
        for key, value in schema.items()
        if key not in ANNOTATIONS and (value is not None or key == "const")
    }

    if "allOf" in result:
        merged: dict[str, Any] = {}
        for part in [*result.pop("allOf"), result]:
            normalized = normalize_schema(part)
            merged = _merge(merged, normalized if isinstance(normalized, dict) else {})
        return normalize_schema(merged)

    if "const" in result:
        result["enum"] = [result.pop("const")]
    # OpenAPI 3.0 wrote raw bytes as format: binary; 3.1 (and FastAPI) uses contentMediaType.
    if result.get("format") == "binary":
        del result["format"]
        result.setdefault("contentMediaType", "application/octet-stream")

    if isinstance(result.get("type"), list):
        types = result.pop("type")
        outer = {key: result.pop(key) for key in ("default",) if key in result}
        branches = [NULL if kind == "null" else {"type": kind, **result} for kind in types]
        result = {"anyOf": branches, **outer}
    if "oneOf" in result and any(_is_null(branch) for branch in result["oneOf"]):
        # oneOf with a null branch is a nullable union, the same as anyOf.
        result["anyOf"] = result.pop("oneOf")

    for key in ("properties", "patternProperties", "$defs"):
        if isinstance(result.get(key), dict):
            result[key] = {name: normalize_schema(sub) for name, sub in result[key].items()}
    for key in (
        "items",
        "additionalProperties",
        "not",
        "contains",
        "propertyNames",
        "unevaluatedProperties",
    ):
        if key in result and isinstance(result[key], dict | bool):
            result[key] = normalize_schema(result[key])
    for key in ("anyOf", "oneOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [normalize_schema(sub) for sub in result[key]]

    if "anyOf" in result:
        flat: list[Any] = []
        for branch in result["anyOf"]:
            if isinstance(branch, dict) and set(branch) == {"anyOf"}:
                flat.extend(branch["anyOf"])
            else:
                flat.append(branch)
        unique = {canonical(branch): branch for branch in flat}
        branches = sorted(unique.values(), key=_branch_key)
        rest = {key: value for key, value in result.items() if key != "anyOf"}
        if (
            len(branches) == 1
            and isinstance(branches[0], dict)
            and not set(rest) & set(branches[0])
        ):
            return normalize_schema({**branches[0], **rest})
        result["anyOf"] = branches

    if isinstance(result.get("enum"), list):
        result["enum"] = sorted(result["enum"], key=canonical)
    if isinstance(result.get("required"), list):
        result["required"] = sorted(set(result["required"]))
    return result


def _without_null(schema: Any) -> Any:
    """Query, path and header values are never JSON null: optional means not required."""
    if isinstance(schema, dict) and isinstance(schema.get("anyOf"), list):
        branches = [branch for branch in schema["anyOf"] if not _is_null(branch)]
        return normalize_schema({**schema, "anyOf": branches})
    return schema


FORM_MEDIA_TYPES = {"multipart/form-data", "application/x-www-form-urlencoded"}


def _form_fields_without_null(schema: Any) -> Any:
    """Form fields are text or files, never JSON null, like query values."""
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        fields = {name: _without_null(field) for name, field in schema["properties"].items()}
        return {**schema, "properties": fields}
    return schema


# ── operations ────────────────────────────────────────────────────────────────────────


def _normalize_operation(
    operation: dict[str, Any], shared_parameters: list[Any], security: list[Any]
) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for parameter in [*shared_parameters, *operation.get("parameters", [])]:
        location = parameter["in"]
        name = parameter["name"].lower() if location == "header" else parameter["name"]
        parameters[f"{location}:{name}"] = {
            "required": bool(parameter.get("required", False)),
            "schema": _without_null(normalize_schema(parameter.get("schema", {}))),
        }
    normalized: dict[str, Any] = {
        "operationId": operation.get("operationId"),
        "tags": sorted(set(operation.get("tags", []))),
        "security": sorted(sorted(requirement) for requirement in security),
        "parameters": parameters,
        "responses": {
            str(code): _normalize_response(response)
            for code, response in operation.get("responses", {}).items()
        },
    }
    body = operation.get("requestBody")
    if body is not None:
        normalized["requestBody"] = {
            "required": bool(body.get("required", False)),
            "content": {
                media_type: {"schema": _body_schema(media_type, item.get("schema", {}))}
                for media_type, item in body.get("content", {}).items()
            },
        }
    return normalized


def _body_schema(media_type: str, schema: Any) -> Any:
    normalized = normalize_schema(schema)
    return _form_fields_without_null(normalized) if media_type in FORM_MEDIA_TYPES else normalized


def _normalize_response(response: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "content": {
            media_type: {"schema": normalize_schema(item.get("schema", {}))}
            for media_type, item in response.get("content", {}).items()
        }
    }
    if response.get("headers"):
        normalized["headers"] = {
            name.lower(): normalize_schema(header.get("schema", {}))
            for name, header in response["headers"].items()
        }
    return normalized


def _is_fastapi_validation_error(response: Any) -> bool:
    """FastAPI documents a 422 HTTPValidationError on every route with inputs; the contract
    answers validation errors with 400 problem+json, so these entries are not part of it."""
    content = response.get("content", {}) if isinstance(response, dict) else {}
    return any(
        str(item.get("schema", {}).get("$ref", "")).endswith("/HTTPValidationError")
        for item in content.values()
    )


def base_path(contract: dict[str, Any]) -> str:
    paths = {urlparse(server["url"]).path.rstrip("/") for server in contract.get("servers", [])}
    if len(paths) > 1:
        raise DriftError(f"contract servers disagree on the base path: {sorted(paths)}")
    return paths.pop() if paths else ""


def contract_operations(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    operations: dict[str, dict[str, Any]] = {}
    default_security = contract.get("security", [])
    for path, item in contract.get("paths", {}).items():
        item = resolve(contract, item)
        for method in HTTP_METHODS:
            if method in item:
                operation = item[method]
                security = operation.get("security", default_security)
                key = f"{method.upper()} {path}"
                operations[key] = _normalize_operation(
                    operation, item.get("parameters", []), security
                )
    return operations


def app_operations(
    generated: dict[str, Any], prefix: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    operations: dict[str, dict[str, Any]] = {}
    outside: list[str] = []
    for path, item in generated.get("paths", {}).items():
        for method in HTTP_METHODS:
            if method not in item:
                continue
            operation = dict(item[method])
            if not (path == prefix or path.startswith(f"{prefix}/")):
                outside.append(f"{method.upper()} {path} ({operation.get('operationId')})")
                continue
            operation["responses"] = {
                code: response
                for code, response in operation.get("responses", {}).items()
                if not (str(code) == "422" and _is_fastapi_validation_error(response))
            }
            resolved = resolve(generated, operation)
            shared = resolve(generated, item.get("parameters", []))
            # FastAPI writes `security` only for operations with security dependencies.
            key = f"{method.upper()} {path[len(prefix) :] or '/'}"
            operations[key] = _normalize_operation(resolved, shared, resolved.get("security", []))
    return operations, outside


# ── comparison ────────────────────────────────────────────────────────────────────────


def _short(value: Any, limit: int = 160) -> str:
    text = canonical(value)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def diff(contract: Any, app: Any, where: str = "") -> Iterator[str]:
    if isinstance(contract, dict) and isinstance(app, dict):
        for key in sorted(set(contract) | set(app)):
            here = f"{where}.{key}" if where else str(key)
            if key not in app:
                yield f"{here}: missing in the app (contract: {_short(contract[key])})"
            elif key not in contract:
                yield f"{here}: not in the contract (app: {_short(app[key])})"
            else:
                yield from diff(contract[key], app[key], here)
    elif (
        isinstance(contract, list)
        and isinstance(app, list)
        and len(contract) == len(app)
        and all(isinstance(item, dict) for item in [*contract, *app])
    ):
        for index, (expected, actual) in enumerate(zip(contract, app, strict=True)):
            yield from diff(expected, actual, f"{where}[{index}]")
    elif contract != app:
        yield f"{where}: contract {_short(contract)} != app {_short(app)}"


def _schemes_used(operations: dict[str, dict[str, Any]], keys: list[str]) -> set[str]:
    return {
        name for key in keys for requirement in operations[key]["security"] for name in requirement
    }


def _security_schemes(document: dict[str, Any]) -> dict[str, Any]:
    schemes = document.get("components", {}).get("securitySchemes", {})
    return {
        name: {key: value for key, value in scheme.items() if key not in ANNOTATIONS}
        for name, scheme in schemes.items()
    }


def check(contract: dict[str, Any], generated: dict[str, Any]) -> Report:
    prefix = base_path(contract)
    expected = contract_operations(contract)
    actual, outside = app_operations(generated, prefix)
    report = Report(
        problems=[f"{operation}: outside the contract base path {prefix}" for operation in outside],
        pending=sorted(
            f"{key} ({expected[key]['operationId']})" for key in set(expected) - set(actual)
        ),
        implemented=sorted(set(expected) & set(actual)),
    )
    for key in sorted(actual):
        label = f"{key} ({actual[key]['operationId']})"
        if key not in expected:
            report.problems.append(f"{label}: not in the contract")
            continue
        report.problems.extend(f"{label}: {line}" for line in diff(expected[key], actual[key]))

    contract_schemes = _security_schemes(contract)
    app_schemes = _security_schemes(generated)
    for name in sorted(_schemes_used(actual, report.implemented)):
        if name not in contract_schemes:
            report.problems.append(f"securitySchemes.{name}: not in the contract")
        elif app_schemes.get(name) != contract_schemes[name]:
            found = _short(app_schemes.get(name))
            report.problems.append(
                f"securitySchemes.{name}: contract {_short(contract_schemes[name])} != app {found}"
            )
    return report


# ── command line ──────────────────────────────────────────────────────────────────────


def run(
    contract: dict[str, Any],
    generated: dict[str, Any],
    *,
    require_complete: bool,
    list_pending: bool,
) -> int:
    report = check(contract, generated)
    total = len(report.implemented) + len(report.pending)
    summary = f"{len(report.implemented)} of {total} contract operations implemented, {len(report.pending)} pending"
    if report.problems:
        print(f"contract drift: {len(report.problems)} problem(s) — {summary}")
        for problem in report.problems:
            print(f"  {problem}")
    else:
        print(f"contract drift: OK — {summary}")
    if list_pending or (require_complete and report.pending):
        for operation in report.pending:
            print(f"  pending: {operation}")
    if report.problems or (require_complete and report.pending):
        return 1
    return 0


def load_app_document(reference: str) -> dict[str, Any]:
    module_name, _, factory_name = reference.partition(":")
    factory = getattr(importlib.import_module(module_name), factory_name or "create_app")
    document = factory().openapi()
    if not isinstance(document, dict):
        raise DriftError(f"{reference} did not produce an OpenAPI document")
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--contract", type=Path, default=DEFAULT_CONTRACT, help="OpenAPI contract (YAML)"
    )
    parser.add_argument(
        "--app", default=DEFAULT_APP, help="module:factory returning the FastAPI app"
    )
    parser.add_argument(
        "--require-complete", action="store_true", help="fail while contract operations are pending"
    )
    parser.add_argument(
        "--list-pending", action="store_true", help="print the pending contract operations"
    )
    args = parser.parse_args(argv)
    try:
        contract = load_contract(args.contract)
        generated = load_app_document(args.app)
        return run(
            contract,
            generated,
            require_complete=args.require_complete,
            list_pending=args.list_pending,
        )
    except (DriftError, OSError, ImportError, AttributeError, yaml.YAMLError) as error:
        print(f"contract drift: cannot compare: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
