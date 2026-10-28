import asyncio
from datetime import UTC, datetime

import httpx2
import pytest

from vitrina_core.health import HealthCache, Outcome, http_check, mocked, reachable, run_checks

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


async def ok() -> Outcome:
    return Outcome("ok")


def answering(code: int) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(code)))


async def test_nfr_02_checks_report_their_status_latency_and_time() -> None:
    async def slow_but_answering() -> Outcome:
        await asyncio.sleep(0.01)
        return Outcome("degraded", "HTTP 429")

    report = await run_checks(
        {"database": ok, "meta_model_api": slow_but_answering}, timeout_s=1, now=lambda: NOW
    )

    assert report["database"].status == "ok"
    assert (report["meta_model_api"].status, report["meta_model_api"].detail) == (
        "degraded",
        "HTTP 429",
    )
    assert report["meta_model_api"].model_dump()["latency_ms"] >= 10
    assert report["database"].checked_at == NOW


async def test_a_check_that_hangs_or_raises_is_down_and_the_others_still_report() -> None:
    async def hangs() -> Outcome:
        await asyncio.sleep(10)
        return Outcome("ok")

    async def raises() -> Outcome:
        raise ConnectionError("refused")

    report = await run_checks(
        {"queue": hangs, "modal": raises, "database": ok}, timeout_s=0.05, now=lambda: NOW
    )

    assert (report["queue"].status, report["queue"].detail) == ("down", "no answer within 0.05 s")
    assert (report["modal"].status, report["modal"].detail) == ("down", "ConnectionError")
    assert report["database"].status == "ok"


async def test_nfr_02_mocked_rows_carry_no_latency() -> None:
    report = await run_checks({"whatsapp": mocked("local mock")}, timeout_s=1, now=lambda: NOW)

    assert report["whatsapp"].model_dump(mode="json") == {
        "status": "mocked",
        "checked_at": "2026-10-28T15:00:00Z",
        "detail": "local mock",
    }


async def test_the_report_is_reused_until_it_expires() -> None:
    runs: list[int] = []
    clock = [100.0]

    async def build() -> dict[str, object]:
        runs.append(1)
        return {"run": len(runs)}

    cache = HealthCache(ttl_s=30, monotonic=lambda: clock[0])

    first = await cache.get(build)
    clock[0] += 29
    assert await cache.get(build) is first
    clock[0] += 2
    assert await cache.get(build) == {"run": 2}


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (200, "ok"),
        (401, "down"),
        (403, "down"),
        (404, "down"),
        (429, "degraded"),
        (503, "degraded"),
    ],
)
async def test_a_resource_check_reads_the_answer(code: int, status: str) -> None:
    outcome = await http_check(answering(code), "https://graph.test/v26.0/1", headers={})

    assert outcome.status == status
    assert outcome.detail == (None if code == 200 else f"HTTP {code}")


@pytest.mark.parametrize(
    ("code", "status"), [(200, "ok"), (404, "ok"), (405, "ok"), (503, "degraded")]
)
async def test_a_reachability_check_takes_any_answer_below_500(code: int, status: str) -> None:
    assert (await reachable(answering(code), "https://modal.test/glimmer")).status == status
