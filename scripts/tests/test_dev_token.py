import importlib.util
import sys
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from vitrina_api.main import create_app
from vitrina_core.auth import TokenVerifier
from vitrina_core.settings import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "dev-token.py"
_spec = importlib.util.spec_from_file_location("dev_token", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
dev_token = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dev_token
_spec.loader.exec_module(dev_token)

SECRET = "dev-token-test-secret-with-32-bytes-or-more"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, supabase_jwt_secret=SECRET, **values)  # type: ignore[arg-type]


async def test_the_token_passes_core_auth_with_the_claims_it_checks() -> None:
    token = dev_token.mint(settings(), "dev-seller", seller_id=SELLER, hours=2, now=1793116800)

    claims = await TokenVerifier(secret=SecretStr(SECRET), leeway_s=10**9).claims(token)

    assert claims["sub"] == "dev-seller"
    assert claims["aud"] == "authenticated"
    assert claims["exp"] - claims["iat"] == 2 * 3600
    assert claims["seller_id"] == str(SELLER)
    assert "iss" not in claims


async def test_a_local_supabase_url_becomes_the_issuer() -> None:
    local = settings(supabase_url="http://localhost:54321")

    token = dev_token.mint(local, "dev-seller")

    verifier = TokenVerifier(secret=SecretStr(SECRET), issuer="http://localhost:54321/auth/v1")
    assert (await verifier.claims(token))["iss"] == "http://localhost:54321/auth/v1"


@pytest.mark.parametrize(
    "url",
    [
        "https://abcdefghijklmnop.supabase.co",
        "https://abcdefghijklmnop.supabase.co/",
        "HTTPS://ABCDEFGHIJKLMNOP.SUPABASE.CO:443",
        # Pasted without a scheme, and as a fully qualified name: the slips found in review.
        "abcdefghijklmnop.supabase.co",
        "https://abcdefghijklmnop.supabase.co.",
    ],
)
def test_a_hosted_project_is_refused(url: str) -> None:
    with pytest.raises(SystemExit, match="hosted Supabase project"):
        dev_token.mint(settings(supabase_url=url), "dev-seller")


def test_a_local_secret_is_required() -> None:
    with pytest.raises(SystemExit, match="SUPABASE_JWT_SECRET"):
        dev_token.mint(Settings(_env_file=None), "dev-seller")  # type: ignore[call-arg]


def test_a_dev_token_signs_up_and_reaches_sellers_me() -> None:
    config = settings()
    client = TestClient(create_app(config))
    headers = {"Authorization": f"Bearer {dev_token.mint(config, 'dev-seller')}"}
    seller = {
        "business_name": "Tienda Ejemplo",
        "owner_name": "Ana",
        "whatsapp_number": "+573000000001",
        "city": "Bogotá",
    }

    before = client.get("/v1/sellers/me", headers=headers)
    created = client.post("/v1/sellers", json=seller, headers=headers)
    after = client.get("/v1/sellers/me", headers=headers)

    assert before.status_code == 403
    assert created.status_code == 201
    assert after.json()["id"] == created.json()["id"]
