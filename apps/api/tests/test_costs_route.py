from datetime import date
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from vitrina_api.main import create_app
from vitrina_core.ledger import CostLine, CostReport
from vitrina_core.settings import Settings

SECRET = "costs-route-test-secret-with-32-bytes-plus"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")


class FakeLedger:
    def __init__(self) -> None:
        self.asked: list[tuple[UUID, date, date]] = []

    async def report(self, seller_id: UUID, period_from: date, period_to: date) -> CostReport:
        self.asked.append((seller_id, period_from, period_to))
        line = CostLine(
            provider="meta_model_api",
            model="agent-model",
            calls=3,
            tokens_in=300,
            tokens_out=90,
            cost_usd=0.0063,
        )
        return CostReport(
            period_from=period_from, period_to=period_to, total_usd=0.0063, by_provider_model=[line]
        )


def bearer() -> dict[str, str]:
    claims = {"sub": "subject", "aud": "authenticated", "exp": 4102444800, "seller_id": str(SELLER)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


def test_fr_084_costs_are_reported_for_the_sellers_period() -> None:
    app = create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET))  # type: ignore[call-arg]
    ledger = FakeLedger()
    app.state.ledger = ledger

    response = TestClient(app).get(
        "/v1/metrics/costs", params={"from": "2026-10-27", "to": "2026-10-28"}, headers=bearer()
    )

    assert response.status_code == 200
    assert response.json() == {
        "period_from": "2026-10-27",
        "period_to": "2026-10-28",
        "total_usd": 0.0063,
        "by_provider_model": [
            {
                "provider": "meta_model_api",
                "model": "agent-model",
                "calls": 3,
                "tokens_in": 300,
                "tokens_out": 90,
                "cost_usd": 0.0063,
            }
        ],
    }
    assert ledger.asked == [(SELLER, date(2026, 10, 27), date(2026, 10, 28))]


def test_the_period_is_required_and_ordered() -> None:
    app = create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET))  # type: ignore[call-arg]
    app.state.ledger = FakeLedger()
    client = TestClient(app)

    missing = client.get("/v1/metrics/costs", headers=bearer())
    reversed_period = client.get(
        "/v1/metrics/costs", params={"from": "2026-10-28", "to": "2026-10-27"}, headers=bearer()
    )

    assert (missing.status_code, missing.json()["code"]) == (400, "VALIDATION_ERROR")
    assert (reversed_period.status_code, reversed_period.json()["code"]) == (
        400,
        "VALIDATION_ERROR",
    )
