import importlib.util
import secrets
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

SCRIPT = Path(__file__).resolve().parents[1] / "dev-catalog.py"
_spec = importlib.util.spec_from_file_location("dev_catalog", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
dev_catalog = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dev_catalog
_spec.loader.exec_module(dev_catalog)

PNG = b"\x89PNG\r\n\x1a\n synthetic menu photo"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, str]]]:
    secret = secrets.token_hex(32)
    app = dev_catalog.local_app(secret, tmp_path, step_s=0)
    claims = {"sub": dev_catalog.SUBJECT, "aud": "authenticated", "exp": int(time.time()) + 600}
    headers = {"Authorization": f"Bearer {jwt.encode(claims, secret, algorithm='HS256')}"}
    with TestClient(app) as client:
        client.portal.call(dev_catalog.seed, app)  # type: ignore[union-attr]
        yield client, headers


def finished(client: TestClient, headers: dict[str, str], note: str | None) -> dict[str, Any]:
    accepted = client.post(
        "/v1/products/import",
        files={"image": ("menu.png", PNG, "image/png")},
        data={"hint": note} if note else {},
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    for _ in range(1000):
        job: dict[str, Any] = client.get(
            f"/v1/jobs/{accepted.json()['id']}", headers=headers
        ).json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("the local job never ended")


def test_a_photo_becomes_drafts_and_a_misread_price_is_dropped(
    server: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = server

    job = finished(client, headers, None)

    drafts = client.get("/v1/products?status=draft", headers=headers).json()["items"]
    assert job["status"] == "succeeded"
    assert sorted((item["name"], item["price_cop"]) for item in drafts) == [
        ("Bandeja paisa", 250000),
        ("Jugo de lulo", 7000),
        ("Sancocho de gallina", 22000),
    ]


@pytest.mark.parametrize(
    ("note", "code"),
    [
        ("sin precios", "INSUFFICIENT_DATA"),
        ("falla", "PROVIDER_ERROR"),
        ("proveedor", "PROVIDER_ERROR"),
    ],
)
def test_the_failing_samples_fail_as_the_worker_would(
    server: tuple[TestClient, dict[str, str]], note: str, code: str
) -> None:
    client, headers = server

    job = finished(client, headers, note)

    assert (job["status"], job["error"]["code"]) == ("failed", code)


def test_a_draft_cannot_start_ads_here_either(server: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = server
    [draft] = client.get("/v1/products?status=draft", headers=headers).json()["items"]

    refused = client.post("/v1/creative/jobs", json={"product_id": draft["id"]}, headers=headers)

    assert refused.status_code == 400
    assert refused.json()["errors"][0]["field"] == "product_id"
