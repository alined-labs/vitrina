"""scripts/seed-demo.py: the demo shop from the eval fixture, through the API, twice."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_catalog.api import InMemoryCatalog
from vitrina_core.settings import Settings
from vitrina_creative.testing import source_photo

SCRIPT = Path(__file__).resolve().parents[1] / "seed-demo.py"
_spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
seed_demo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = seed_demo
_spec.loader.exec_module(seed_demo)

SECRET = "seed-demo-test-secret-of-32-bytes-or-more"
NUMBER = "+573000000999"
SHOP: dict[str, Any] = json.loads(seed_demo.FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, supabase_jwt_secret=SECRET, storage_dir=tmp_path)  # type: ignore[call-arg]
    app = create_app(settings)
    app.state.catalog = InMemoryCatalog()
    claims = {"sub": "seed-demo-owner", "aud": "authenticated", "exp": 4102444800}
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    return TestClient(
        app, base_url="http://testserver/v1", headers={"Authorization": f"Bearer {token}"}
    )


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "mochila.png"
    path.write_bytes(source_photo().image.data)
    return path


def test_the_demo_shop_is_made_from_the_fixture_and_a_second_run_keeps_it(
    client: TestClient, photo: Path
) -> None:
    first = seed_demo.seed(client, SHOP, whatsapp_number=NUMBER, photos={"mochila-azul": photo})

    assert first["seller"] == "created"
    assert first["products"] == {item["key"]: "created" for item in SHOP["products"]}
    assert first["facts"] == {"created": len(SHOP["facts"]), "kept": 0}
    assert first["photos"] == {"mochila-azul": "uploaded"}
    me = client.get("/sellers/me").json()
    assert (me["business_name"], me["whatsapp_number"]) == ("Mochilas Ana", NUMBER)
    facts = client.get("/facts", params={"limit": 100}).json()["items"]
    assert sorted(fact["status"] for fact in facts) == sorted(
        fact.get("status", "active") for fact in SHOP["facts"]
    )  # the draft promotion stays a draft, which the agent must never cite
    products = client.get("/products", params={"limit": 100}).json()["items"]
    blue = next(item for item in products if item["name"] == "Mochila tejida azul")
    assert len(blue["asset_ids"]) == 1

    second = seed_demo.seed(client, SHOP, whatsapp_number=NUMBER, photos={"mochila-azul": photo})

    assert second["seller"] == "kept"
    assert set(second["products"].values()) == {"kept"}
    assert second["facts"] == {"created": 0, "kept": len(SHOP["facts"])}
    assert second["photos"] == {"mochila-azul": "kept"}
    assert len(client.get("/products", params={"limit": 100}).json()["items"]) == len(products)


def test_a_photo_for_a_product_the_fixture_lacks_stops_before_any_call(
    client: TestClient, photo: Path
) -> None:
    with pytest.raises(seed_demo.SeedError, match="no product with the key bolso-rosa"):
        seed_demo.seed(client, SHOP, whatsapp_number=NUMBER, photos={"bolso-rosa": photo})
    assert client.get("/sellers/me").status_code == 403  # nothing was created


def test_without_a_token_the_script_says_how_to_get_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("VITRINA_TOKEN", raising=False)

    assert seed_demo.main(["--api", "http://127.0.0.1:9/v1", "--whatsapp-number", NUMBER]) == 1
    assert "set VITRINA_TOKEN or pass --sign-in" in capsys.readouterr().err
