"""A local API with a sample catalog, to work on the catalog screens without a database.

    export SUPABASE_JWT_SECRET="$(openssl rand -hex 32)"
    uv run python scripts/dev-catalog.py [--port 8000]

It runs the real API routes on the in-memory catalog and reads no ``.env``, so it cannot reach a
hosted service or a paid model. The queue and the worker need Postgres, so here a small stand-in
takes their place: it runs the real ``extract_products`` and ``save_drafts`` on a scripted model
answer. The note sent with the photo chooses what happens:

    (no note)     two new drafts, one item the catalog has, one price too big for the table
    repetida      everything read is already in the catalog: no new drafts
    sin precios   the photo has no priced products: the job fails with a reason for the seller
    falla         the model keeps answering nonsense: the job fails for good
    reintento     the first attempt fails and is retried, then two new drafts
    proveedor     the provider refuses: the job fails with developer text the seller must not see
    lenta         the job looks two minutes old while it runs
    atascada      the job never ends and looks twenty-one minutes old

"Crear anuncios" answers 503 here: creative jobs need the database.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr

from vitrina_api.main import create_app
from vitrina_catalog.api import FactCreate, FactUpdate, ProductCreate
from vitrina_catalog.importer import extract_products, save_drafts
from vitrina_catalog.sellers import SellerCreate
from vitrina_core.http import ProblemCode, ProblemError
from vitrina_core.ids import new_id
from vitrina_core.jobs import Job, JobErrorDetail, JobFailedError
from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderError,
)
from vitrina_core.settings import Settings
from vitrina_creative.api import InMemoryAssetStore

SUBJECT = "dev-seller"  # make dev-token SUB=dev-seller
MODEL = "local-scripted"
MENU: list[dict[str, Any]] = [
    {"name": "Sancocho de gallina", "price_cop": 22000, "description": "Con arroz y aguacate"},
    {"name": "Jugo de lulo", "price_cop": "$7.000", "description": ""},
]
KNOWN: dict[str, Any] = {"name": "Gorra bordada", "price_cop": 30000, "description": ""}
MISREAD: dict[str, Any] = {
    "name": "Precio mal leído",
    "price_cop": 99_999_999_999,
    "description": "",
}
SIGN_IN_HELP = """To sign in from the dashboard, with SUPABASE_JWT_SECRET still exported:
  1. export NEXT_PUBLIC_DEV_ACCESS_TOKEN="$(SUPABASE_URL= make -s dev-token SUB=dev-seller)"
  2. From apps/dashboard: API_PROXY_TARGET=http://localhost:<port> pnpm dev
  3. Open /catalogo and /catalogo/importar. The script's docstring lists the sample photo notes.
"""


class ScriptedSpark:
    """Answers each call with the next text: the only "model" this server has."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content=self.answers.pop(0)),
            finish_reason="stop",
            meta=CallMeta(provider="local_mock", model=request.model, purpose=request.purpose),
        )


class NoSession:
    """The route opens a transaction around ``enqueue``; here there is nothing to commit."""

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


@asynccontextmanager
async def no_sessions() -> AsyncIterator[NoSession]:
    yield NoSession()


def answer_for(items: list[dict[str, Any]]) -> str:
    return json.dumps({"products": items}, ensure_ascii=False)


class LocalJobs:
    """Stands in for the Postgres queue and the worker, for product imports only."""

    def __init__(self, app: FastAPI, *, step_s: float = 2.0) -> None:
        self.app = app
        self.step_s = step_s
        self.rows: dict[UUID, tuple[UUID, Job]] = {}
        self.running: set[asyncio.Task[None]] = set()

    async def enqueue(
        self, _session: Any, *, kind: str, payload: dict[str, Any], owner_seller_id: UUID, **_: Any
    ) -> UUID:
        if kind != "product_import":
            raise ProblemError(
                ProblemCode.PROVIDER_UNAVAILABLE, detail="this local server only imports photos"
            )
        job_id = new_id()
        note = str(payload.get("hint", ""))
        age = {"lenta": timedelta(minutes=2), "atascada": timedelta(minutes=21)}.get(note)
        self.rows[job_id] = (
            owner_seller_id,
            Job(
                id=job_id,
                kind="product_import",
                status="queued",
                created_at=datetime.now(UTC) - (age or timedelta()),
            ),
        )
        task = asyncio.create_task(self._run(job_id, owner_seller_id, payload, note))
        self.running.add(task)
        task.add_done_callback(self.running.discard)
        return job_id

    async def get_for_seller(self, seller_id: UUID, job_id: UUID) -> Job | None:
        owner, job = self.rows.get(job_id, (None, None))
        return job if owner == seller_id else None

    def _set(self, job_id: UUID, **values: Any) -> None:
        owner, job = self.rows[job_id]
        self.rows[job_id] = (owner, job.model_copy(update=values))

    def _fail(self, job_id: UUID, code: str, message: str) -> None:
        error = JobErrorDetail(code=code, message=message, retryable=False)
        self._set(job_id, status="failed", error=error, finished_at=datetime.now(UTC))

    async def _run(self, job_id: UUID, seller_id: UUID, payload: dict[str, Any], note: str) -> None:
        state = self.app.state
        await asyncio.sleep(self.step_s)
        now = datetime.now(UTC)
        self._set(job_id, status="running", stage="reading", progress=0.2, started_at=now)
        if note == "atascada":
            return
        await asyncio.sleep(self.step_s)
        if note == "reintento":
            error = JobErrorDetail(code="PROVIDER_UNAVAILABLE", message="503", retryable=True)
            self._set(job_id, status="queued", error=error, stage="reading")
            await asyncio.sleep(self.step_s * 3)
            self._set(job_id, status="running", error=None)
        chat = ScriptedSpark(
            *{
                "repetida": [answer_for([KNOWN])],
                "sin precios": [answer_for([])],
                "falla": ["no es json", "tampoco"],
            }.get(note, [answer_for([*MENU, KNOWN, MISREAD])])
        )
        try:
            if note == "proveedor":
                raise ProviderError("HTTP 400 from upstream", provider="local_mock")
            photo = await state.storage.get(payload["image_key"])
            drafts = await extract_products(photo, note or None, chat=chat, model=MODEL)
            self._set(job_id, stage="saving", progress=0.8)
            await asyncio.sleep(self.step_s)
            await save_drafts(state.catalog, seller_id, drafts)
        except JobFailedError as failure:
            self._fail(job_id, failure.code, failure.message)
        except ProviderError as failure:
            self._fail(job_id, "PROVIDER_ERROR", str(failure))
        else:
            self._set(
                job_id,
                status="succeeded",
                stage="done",
                progress=1.0,
                error=None,
                result_ref="/v1/products?status=draft",
                finished_at=datetime.now(UTC),
            )


async def seed(app: FastAPI) -> None:
    state = app.state
    seller = await state.sellers.create(
        SellerCreate(
            business_name="Tienda de prueba",
            owner_name="Dueña de prueba",
            whatsapp_number="+573000000701",
            city="Medellín",
        ),
        auth_subject=SUBJECT,
    )
    catalog = state.catalog
    samples = [
        ProductCreate(
            name="Mochila tejida", price_cop=85000, stock=4, description="Hilo de algodón"
        ),
        ProductCreate(name="Gorra bordada", price_cop=30000),
        ProductCreate(name="Bolso de temporada", price_cop=120000, stock=0, status="archived"),
    ]
    for product in samples:
        await catalog.create_product(seller.id, product)
    await catalog.create_product(
        seller.id,
        ProductCreate(name="Bandeja paisa", price_cop=250000, status="draft"),
        source="image_import",
    )
    await catalog.create_fact(
        seller.id, FactCreate(kind="shipping", text="Envío a Medellín por $12.000, en 2 días.")
    )
    draft = await catalog.create_fact(
        seller.id, FactCreate(kind="payment", text="Recibimos Nequi y efectivo.")
    )
    await catalog.update_fact(seller.id, draft.id, FactUpdate(status="draft"))


def local_app(secret: str, storage_dir: Path, *, step_s: float = 2.0) -> FastAPI:
    settings = Settings(
        _env_file=None,
        replay=True,
        record_cassettes=False,
        database_url=None,
        db_schema=None,
        supabase_url=None,
        supabase_jwt_secret=SecretStr(secret),
        supabase_service_role_key=None,
        storage_backend="local",
        storage_dir=storage_dir,
        whatsapp_mock=False,
        meta_api_key=None,
        baseten_api_key=None,
    )
    app = create_app(settings)
    app.state.assets = InMemoryAssetStore()
    app.state.sessions = no_sessions
    app.state.jobs = LocalJobs(app, step_s=step_s)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if len(secret.encode()) < 32:
        raise SystemExit("export SUPABASE_JWT_SECRET with a local random value of 32+ bytes")
    with tempfile.TemporaryDirectory(prefix="vitrina-dev-catalog-") as storage_dir:
        app = local_app(secret, Path(storage_dir))
        asyncio.run(seed(app))
        print(SIGN_IN_HELP)
        sys.stdout.flush()
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
