import httpx2
import pytest

from vitrina_meta_model_api.health import check_models
from vitrina_meta_model_api.testing import FakeApi, error

MODELS = {
    "object": "list",
    "data": [{"id": "muse-spark-1.3", "object": "model", "created": 0, "owned_by": "meta"}],
}


async def test_nfr_02_the_meta_check_lists_the_models(api: FakeApi) -> None:
    api.answer(httpx2.Response(200, json=MODELS))

    outcome = await check_models(api.client())

    assert outcome.status == "ok"
    assert api.requests[0].url.path == "/v1/models"


@pytest.mark.parametrize(
    ("code", "status", "detail"),
    [
        (429, "degraded", "HTTP 429"),
        (500, "degraded", "HTTP 500"),
        (401, "down", "the key was refused (HTTP 401)"),
        (404, "down", "HTTP 404"),
    ],
)
async def test_the_meta_check_maps_refusals_and_outages(
    api: FakeApi, code: int, status: str, detail: str
) -> None:
    api.answer(error(code, "no"))

    outcome = await check_models(api.client())

    assert (outcome.status, outcome.detail) == (status, detail)


async def test_a_lost_connection_is_down(api: FakeApi) -> None:
    api.answer(httpx2.ConnectError("refused"))

    outcome = await check_models(api.client())

    assert (outcome.status, outcome.detail) == ("down", "no connection")
