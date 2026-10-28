import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_api.main import create_app
from vitrina_core.events import emit
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import CallMeta, ChatMessage, ChatRequest, ChatResponse
from vitrina_core.providers.compose import ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_creative.testing import source_photo
from vitrina_learning.api import AttributionConsumer, BriefContent, PostgresBriefs
from vitrina_learning.briefs import BriefDataset, DatasetProduct
from vitrina_learning.testing import approved_ad, conversation

SECRET = "briefs-routes-test-secret-with-32-bytes+"
SELLER = UUID("00000000-0000-7000-8000-00000000000a")
OTHER = UUID("00000000-0000-7000-8000-00000000000b")
TODAY = datetime.now(UTC).date()
PERIOD = {"period_from": (TODAY - timedelta(days=7)).isoformat(), "period_to": TODAY.isoformat()}


def bearer(seller: UUID = SELLER) -> dict[str, str]:
    claims = {"sub": "s", "aud": "authenticated", "exp": 4102444800, "seller_id": str(seller)}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, supabase_jwt_secret=SECRET)))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/briefs"),
        ("post", "/v1/briefs/generate"),
        ("get", f"/v1/briefs/{SELLER}"),
        ("post", f"/v1/briefs/{SELLER}/apply"),
    ],
)
def test_briefs_need_a_seller(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize(
    ("path", "body"),
    [("/v1/briefs/generate", PERIOD), (f"/v1/briefs/{SELLER}/apply", None)],
)
def test_fr_050_generating_and_applying_need_the_database(
    client: TestClient, path: str, body: dict[str, str] | None
) -> None:
    response = client.post(path, json=body, headers=bearer())

    assert response.status_code == 503
    assert response.json()["code"] == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    "body",
    [
        {"period_from": "2026-10-28", "period_to": "2026-10-01"},
        {**PERIOD, "min_conversations": 0},
    ],
)
def test_a_bad_period_or_threshold_is_refused(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/v1/briefs/generate", json=body, headers=bearer())

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_without_a_database_there_are_no_briefs(client: TestClient) -> None:
    assert client.get("/v1/briefs", headers=bearer()).json() == {"items": [], "next_cursor": None}
    assert client.get(f"/v1/briefs/{SELLER}", headers=bearer()).status_code == 404


class Wonder:
    """Answers with a brief built from the dataset it is sent: the seller's own product."""

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        dataset = json.loads(str(request.messages[-1].content))
        brief = {
            "headline": "El ángulo 'precio' trajo todos los chats: el próximo lote lo repite.",
            "insights": [
                {
                    "text": "Todos los chats vinieron del anuncio A7.",
                    "evidence": [
                        {"metric": "attributed_chats", "value": dataset["attributed_chats"]}
                    ],
                }
            ],
            "recommended_angles": [{"angle": "price", "rationale": "Trajo los chats."}],
            "creative_directives": [
                {
                    "product_id": dataset["products"][0]["id"],
                    "angle": "price",
                    "hook": "question",
                    "scene_brief": "La mochila sobre una mesa de madera.",
                    "offer": None,
                }
            ],
            "reactivation_segments": [],
        }
        return ChatResponse(
            message=ChatMessage(role="assistant", content=json.dumps(brief, ensure_ascii=False)),
            finish_reason="stop",
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose="brief"),
        )


@pytest.mark.db
async def test_db_as_5_1_ten_attributed_chats_give_a_brief_the_seller_reads(
    migrated_schema: tuple[str, str], db_sessions: async_sessionmaker[AsyncSession]
) -> None:
    url, schema = migrated_schema
    async with db_sessions() as session, session.begin():
        ad = await approved_ad(session, SELLER, "+573000000001")
        await approved_ad(session, OTHER, "+573000000002")
        for _ in range(10):
            chat = await conversation(session, SELLER)
            await emit(
                session,
                "ad.chat_started",
                {"ad_id": str(ad), "conversation_id": str(chat)},
                seller_id=SELLER,
            )
    await AttributionConsumer().sweep(db_sessions, datetime.now(UTC))
    settings = Settings(
        _env_file=None,
        database_url=url,
        db_schema=schema,
        supabase_jwt_secret=SECRET,
        model_strategist="wonder-test",
    )  # type: ignore[call-arg]

    with TestClient(create_app(settings)) as client:
        short = client.post(
            "/v1/briefs/generate", json={**PERIOD, "min_conversations": 11}, headers=bearer()
        )
        assert short.status_code == 422
        assert short.json()["code"] == "INSUFFICIENT_DATA"

        started = client.post("/v1/briefs/generate", json=PERIOD, headers=bearer())
        assert started.status_code == 202
        job = started.json()
        assert (job["kind"], job["status"]) == ("brief", "queued")

        wonder = Wonder()
        providers = compose(settings, InMemoryLedger(), mode=ProviderMode.replay)
        registry = TaskRegistry()
        load_tasks(registry)
        worker = Worker(
            PostgresJobQueue(db_sessions),
            registry,
            sessions=db_sessions,
            providers=providers.__class__(**{**providers.__dict__, "chat": wonder}),
            settings=settings,
        )
        assert await worker.run_once()

        done = client.get(f"/v1/jobs/{job['id']}", headers=bearer()).json()
        assert done["status"] == "succeeded", done
        brief = client.get(done["result_ref"], headers=bearer()).json()
        assert brief["status"] == "ready"
        assert brief["insights"][0]["evidence"] == {"attributed_chats": 10}
        assert brief["model_calls"][0]["model"] == "wonder-test"
        assert [
            item["id"] for item in client.get("/v1/briefs", headers=bearer()).json()["items"]
        ] == [brief["id"]]
        assert client.get(done["result_ref"], headers=bearer(OTHER)).status_code == 404

    assert wonder.requests[0].model == "wonder-test"


def owner(subject: str = "owner-apply") -> dict[str, str]:
    claims = {"sub": subject, "aud": "authenticated", "exp": 4102444800}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.mark.db
async def test_db_as_5_2_applying_a_brief_queues_its_creative_jobs_and_a_campaign_preview(
    migrated_schema: tuple[str, str], db_sessions: async_sessionmaker[AsyncSession]
) -> None:
    url, schema = migrated_schema
    settings = Settings(
        _env_file=None, database_url=url, db_schema=schema, supabase_jwt_secret=SECRET
    )  # type: ignore[call-arg]

    with TestClient(create_app(settings)) as client:
        seller = client.post(
            "/v1/sellers",
            json={
                "business_name": "Tejidos Vecina",
                "owner_name": "Ana",
                "whatsapp_number": "+573000000003",
                "city": "Medellín",
            },
            headers=owner(),
        )
        assert seller.status_code == 201, seller.text
        seller_id = UUID(seller.json()["id"])
        product = client.post(
            "/v1/products", json={"name": "Bolso tejido", "price_cop": 85000}, headers=owner()
        ).json()
        photo = client.post(
            "/v1/assets",
            files={"file": ("bolso.png", source_photo().image.data, "image/png")},
            data={"product_id": product["id"]},
            headers=owner(),
        )
        assert photo.status_code == 201, photo.text
        content = BriefContent.model_validate(
            {
                "headline": "El bolso tejido vende con el ángulo 'hecho a mano'.",
                "insights": [{"text": "Tres chats.", "evidence": {"chats": 3}}],
                "recommended_angles": [{"angle": "handmade", "rationale": "Más chats."}],
                "creative_directives": [
                    {"product_id": product["id"], "angle": "handmade", "scene_brief": "Telar."}
                ],
                "reactivation_segments": [
                    {"objection_tag": "shipping", "message_angle": "envío claro"}
                ],
            }
        )
        dataset = BriefDataset(
            business_name="Tejidos Vecina",
            period_from=TODAY - timedelta(days=7),
            period_to=TODAY,
            attributed_chats=3,
            ads=[],
            by_angle={},
            objections={},
            products=[DatasetProduct(id=product["id"], name="Bolso tejido", price_cop=85000)],
            ads_by_status={},
        )
        brief = await PostgresBriefs(db_sessions).create(seller_id, dataset, content, [])

        applied = client.post(f"/v1/briefs/{brief.id}/apply", headers=owner())
        assert applied.status_code == 202, applied.text
        [job_id] = applied.json()["creative_job_ids"]
        campaign_id = applied.json()["reactivation_campaign_id"]

        job = client.get(f"/v1/jobs/{job_id}", headers=owner()).json()
        assert (job["kind"], job["status"]) == ("creative", "queued")
        campaign = client.get(f"/v1/reactivation/campaigns/{campaign_id}", headers=owner()).json()
        assert (campaign["status"], campaign["brief_id"]) == ("preview", str(brief.id))
        assert client.get(f"/v1/briefs/{brief.id}", headers=owner()).json()["status"] == "applied"

        again = client.post(f"/v1/briefs/{brief.id}/apply", headers=owner())
        assert again.status_code == 409
        stranger = client.post(f"/v1/briefs/{brief.id}/apply", headers=owner("someone-else"))
        assert stranger.status_code in (403, 404)
