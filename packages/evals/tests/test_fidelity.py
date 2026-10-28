import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import (
    Blob,
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Segment,
    SegmentRequest,
    SegmentResponse,
)
from vitrina_core.providers.compose import LiveProviders, ProviderMode, compose
from vitrina_core.settings import Settings
from vitrina_evals import suites
from vitrina_evals.fidelity import FIDELITY_FIXTURES, load_fidelity, run_fidelity

RECORDED = {"model_agent": "muse-spark-1.3", "model_segment": "sam-3.1"}


async def test_as_7_2_the_recorded_verifier_passes_good_scenes_and_blocks_every_altered_one() -> (
    None
):
    settings = Settings(_env_file=None, **RECORDED)  # type: ignore[call-arg, arg-type]

    result = await suites.execute("fidelity", replay=True, settings=settings)

    assert result.status == "passed", result.notes
    assert result.metrics.verifier_pass_rate == 1.0
    assert result.metrics.verifier_block_rate_on_altered == 1.0
    assert len(result.cases) == 20


def test_the_fixtures_hold_ten_good_and_ten_altered_scenes_on_disk() -> None:
    fixtures = load_fidelity()

    kinds = [case.kind for case in fixtures.cases]
    assert (kinds.count("good"), kinds.count("altered")) == (10, 10)
    assert len({case.case_id for case in fixtures.cases}) == 20
    for name in [fixtures.source, fixtures.source_mask, *(case.scene for case in fixtures.cases)]:
        assert (FIDELITY_FIXTURES / name).is_file(), name
    assert all(case.change for case in fixtures.cases if case.kind == "altered")


async def test_without_the_recorded_model_ids_the_run_is_an_error_not_a_pass() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    result = await suites.execute("fidelity", replay=True, settings=settings)

    assert result.status == "error"
    assert "MODEL_SEGMENT" in result.notes


def square(color: tuple[int, int, int]) -> bytes:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    pixels[16:48, 16:48] = color
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


def square_mask() -> bytes:
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[16:48, 16:48] = 255
    buffer = io.BytesIO()
    Image.fromarray(mask, mode="L").save(buffer, format="PNG")
    return buffer.getvalue()


class SquareSegmenter:
    async def segment(self, request: SegmentRequest) -> SegmentResponse:
        segment = Segment(
            mask=Blob(data=square_mask(), mime_type="image/png"), bbox=(16, 16, 48, 48), score=0.9
        )
        return SegmentResponse(
            segments=(segment,),
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose="verify"),
        )


class Reviewer:
    def __init__(self, altered: bool) -> None:
        self.altered = altered

    async def complete(self, request: ChatRequest) -> ChatResponse:
        answer = {"altered": self.altered, "reason": "cambio" if self.altered else "sin cambios"}
        return ChatResponse(
            message=ChatMessage(role="assistant", content=json.dumps(answer)),
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose="verify"),
        )


def fixture_set(folder: Path, cases: list[dict[str, str]]) -> Path:
    (folder / "source.png").write_bytes(square((200, 30, 30)))
    (folder / "source-mask.png").write_bytes(square_mask())
    (folder / "same.png").write_bytes(square((200, 30, 30)))
    manifest = {
        "source": "source.png",
        "source_mask": "source-mask.png",
        "label": "box",
        "possessive": "de la caja",
        "cases": cases,
    }
    (folder / "cases.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


async def run(folder: Path, *, altered_review: bool) -> suites.SuiteResult:
    settings = Settings(_env_file=None, **RECORDED)  # type: ignore[call-arg, arg-type]
    ledger = InMemoryLedger()
    providers = compose(
        settings,
        ledger,
        lambda: LiveProviders(chat=Reviewer(altered_review), segmenter=SquareSegmenter()),
        mode=ProviderMode.live,
    )
    return await run_fidelity(
        load_fidelity(folder), providers=providers, settings=settings, ledger=ledger, folder=folder
    )


async def test_one_altered_scene_that_gets_through_fails_the_run(tmp_path: Path) -> None:
    folder = fixture_set(
        tmp_path, [{"case_id": "a1", "kind": "altered", "scene": "same.png", "change": "logo"}]
    )

    result = await run(folder, altered_review=False)

    assert result.status == "failed"
    assert result.metrics.verifier_block_rate_on_altered == 0.0
    assert "got through" in result.notes


async def test_blocking_good_scenes_fails_the_run(tmp_path: Path) -> None:
    folder = fixture_set(tmp_path, [{"case_id": "g1", "kind": "good", "scene": "same.png"}])

    result = await run(folder, altered_review=True)

    assert result.status == "failed"
    assert result.metrics.verifier_pass_rate == 0.0
