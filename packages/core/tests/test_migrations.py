"""The Alembic baseline must create every table in data-model.md §1 (T005).

Offline tests render the SQL without a database. ``db`` tests run the migrations against a
real Postgres with pgvector (CI's service container, or Supabase with DATABASE_URL set) in a
throwaway schema each.
"""

import asyncio
import io
import os
import re
from collections.abc import Iterator
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from vitrina_core.settings import REPO_ROOT

TABLES = {
    "sellers", "products", "facts", "assets", "creative_jobs", "creatives", "copy_variants", "ads",
    "ad_code_counters", "contacts", "consent_records", "conversations", "messages", "webhook_payloads",
    "handoffs", "objections", "reservations", "bookings", "attribution_events", "briefs",
    "reactivation_campaigns", "send_decisions", "erasure_receipts", "jobs", "idempotency_keys",
    "model_calls", "eval_runs", "public_showcases", "public_rate_limits", "events",
}  # fmt: skip


def alembic_config(schema: str, url: str | None = None) -> tuple[Config, io.StringIO]:
    buffer = io.StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=buffer)
    config.attributes["schema"] = schema
    if url is not None:
        config.attributes["url"] = url
    return config, buffer


def offline_sql(schema: str = "ci_offline", direction: str = "upgrade") -> str:
    config, buffer = alembic_config(schema)
    if direction == "upgrade":
        command.upgrade(config, "head", sql=True)
    else:
        command.downgrade(config, "head:base", sql=True)
    return buffer.getvalue()


def test_baseline_creates_every_data_model_table() -> None:
    created = set(re.findall(r"CREATE TABLE (\w+) \(", offline_sql()))

    assert created == TABLES


def test_baseline_enables_pgvector_and_trigram_search() -> None:
    sql = offline_sql()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in sql
    assert re.search(r"embedding VECTOR\(1024\)", sql)
    assert re.search(r"image_embedding VECTOR\(768\)", sql)
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "GENERATED ALWAYS AS (to_tsvector('spanish'" in sql


def test_baseline_versions_and_searches_inside_the_machine_schema() -> None:
    sql = offline_sql(schema="dev_mx0")

    assert 'CREATE SCHEMA IF NOT EXISTS "dev_mx0"' in sql
    assert 'SET search_path TO "dev_mx0", public, extensions' in sql
    assert "CREATE TABLE dev_mx0.alembic_version" in sql


def test_baseline_enforces_the_invariants_in_the_database() -> None:
    sql = offline_sql()

    assert "consent_records is append-only" in sql  # invariant 4, FR-060
    assert "CREATE CONSTRAINT TRIGGER ads_require_passed_creative" in sql  # invariant 2, AS-2.3
    assert "next_ad_code" in sql  # FR-014
    assert "pg_notify('vitrina_events'" in sql  # plan.md §3 events


def test_downgrade_drops_every_table() -> None:
    dropped = set(re.findall(r"DROP TABLE (\w+);", offline_sql(direction="downgrade")))

    assert dropped == TABLES


# ── against a real Postgres ───────────────────────────────────────────────────────────


@pytest.fixture
def database() -> Iterator[tuple[str, str]]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        if os.environ.get("CI_DB_REQUIRED"):
            pytest.fail(
                "CI_DB_REQUIRED is set but DATABASE_URL is not: the db job must reach Postgres"
            )
        pytest.skip("DATABASE_URL is not set (CI runs these against its Postgres service)")
    schema = f"test_{uuid4().hex[:12]}"
    yield url, schema
    asyncio.run(_drop_schema(url, schema))


def _plain(url: str) -> str:
    return re.sub(r"^postgres(ql)?(\+asyncpg)?://", "postgresql://", url)


async def _drop_schema(url: str, schema: str) -> None:
    connection = await asyncpg.connect(_plain(url))
    try:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await connection.close()


async def _connect(url: str, schema: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        _plain(url), server_settings={"search_path": f'"{schema}", public, extensions'}
    )


@pytest.mark.db
def test_db_upgrade_creates_every_table_and_downgrade_removes_them(
    database: tuple[str, str],
) -> None:
    url, schema = database
    config, _ = alembic_config(schema, url)

    command.upgrade(config, "head")

    async def tables() -> set[str]:
        connection = await _connect(url, schema)
        try:
            rows = await connection.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = $1", schema
            )
        finally:
            await connection.close()
        return {row["table_name"] for row in rows}

    assert asyncio.run(tables()) >= TABLES | {"alembic_version"}
    command.downgrade(config, "base")
    assert asyncio.run(tables()) == {"alembic_version"}


@pytest.mark.db
def test_db_invariants_hold(database: tuple[str, str]) -> None:
    url, schema = database
    command.upgrade(alembic_config(schema, url)[0], "head")

    async def check() -> None:
        connection = await _connect(url, schema)
        try:
            seller = await connection.fetchval(
                """INSERT INTO sellers (id, business_name, owner_name, whatsapp_number, city)
                   VALUES ($1, 'Bolsos Ana', 'Ana', '+573000000001', 'Medellín') RETURNING id""",
                uuid4(),
            )
            product = await connection.fetchval(
                "INSERT INTO products (id, seller_id, name, price_cop) VALUES ($1, $2, 'Bolso tejido', 85000) RETURNING id",
                uuid4(),
                seller,
            )
            job = await connection.fetchval(
                "INSERT INTO creative_jobs (id, seller_id, product_id, params) VALUES ($1, $2, $3, '{}') RETURNING id",
                uuid4(),
                seller,
                product,
            )

            async def ad(verification: str) -> object:
                creative = await connection.fetchval(
                    """INSERT INTO creatives (id, job_id, seller_id, product_id, format, verification)
                       VALUES ($1, $2, $3, $4, 'catalog_1x1', $5::jsonb) RETURNING id""",
                    uuid4(), job, seller, product, verification,
                )  # fmt: skip
                variant = await connection.fetchval(
                    """INSERT INTO copy_variants (id, job_id, seller_id, hook, angle, text)
                       VALUES ($1, $2, $3, 'question', 'handmade', '¿Lo quieres?') RETURNING id""",
                    uuid4(), job, seller,
                )  # fmt: skip
                return await connection.fetchval(
                    """INSERT INTO ads (id, seller_id, product_id, creative_id, copy_variant_id)
                       VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                    uuid4(), seller, product, creative, variant,
                )  # fmt: skip

            # FR-014: approval assigns A1, A2 … and the wa.me link.
            first, second = await ad('{"status": "passed"}'), await ad('{"status": "passed"}')
            for ad_id in (first, second):
                await connection.execute("UPDATE ads SET status = 'approved' WHERE id = $1", ad_id)
            rows = await connection.fetch("SELECT code, wa_link FROM ads ORDER BY code")
            assert [row["code"] for row in rows] == ["A1", "A2"]
            assert (
                rows[0]["wa_link"]
                == "https://wa.me/573000000001?text=Hola%2C%20vi%20el%20anuncio%20%23A1"
            )

            # Invariant 2 / AS-2.3: a blocked creative cannot back an approved ad.
            blocked = await ad('{"status": "blocked"}')
            with pytest.raises(asyncpg.RaiseError, match="CREATIVE_BLOCKED"):
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE ads SET status = 'approved' WHERE id = $1", blocked
                    )

            # Invariant 4 / FR-060: the consent ledger is append-only.
            contact = await connection.fetchval(
                "INSERT INTO contacts (id, seller_id, wa_id_hash) VALUES ($1, $2, $3) RETURNING id",
                uuid4(), seller, b"hash",
            )  # fmt: skip
            await connection.execute(
                """INSERT INTO consent_records (id, seller_id, contact_id, purpose, status, text_shown,
                   response_text, channel, recorded_by) VALUES ($1, $2, $3, 'marketing', 'granted',
                   '¿Te aviso cuando haya promo? Responde SÍ', 'sí', 'whatsapp', 'agent')""",
                uuid4(), seller, contact,
            )  # fmt: skip
            for statement in (
                "UPDATE consent_records SET status = 'revoked'",
                "DELETE FROM consent_records",
            ):
                with pytest.raises(asyncpg.RaiseError, match="append-only"):
                    await connection.execute(statement)

            # plan.md §3: inserting an event notifies vitrina_events with schema:id.
            received: list[str] = []
            await connection.add_listener("vitrina_events", lambda *args: received.append(args[3]))
            event_id = await connection.fetchval(
                "INSERT INTO events (seller_id, type, payload) VALUES ($1, 'ad.chat_started', '{}') RETURNING id",
                seller,
            )
            await asyncio.sleep(0.2)
            assert received == [f"{schema}:{event_id}"]

            # Spanish keyword search is generated on write.
            assert await connection.fetchval(
                "SELECT search_tsv @@ plainto_tsquery('spanish', 'bolsos') FROM products WHERE id = $1",
                product,
            )
        finally:
            await connection.close()

    asyncio.run(check())
