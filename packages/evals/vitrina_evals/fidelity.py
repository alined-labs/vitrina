"""The fidelity suite (T072, AS-7.2, FR-012): does the verifier pass real scenes and block altered ones?

Each case is a scene in ``evals/fixtures/fidelity`` made from one synthetic product photo:
``good`` scenes are Muse Image output that keeps the product unchanged, ``altered`` scenes change
its shape, colour, logo, material or count. The verifier runs as in production: the shape and
colour signals are computed here from the pixels, while SAM's mask of each scene and Spark's
visual review come from cassettes in replay (ADR-0010). A case passes when a good scene is
approved or an altered one is blocked. The run fails below ``PASS_RATE_MIN`` on good scenes or
when a single altered scene gets through (README targets: 80 % and 100 %).
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers import Blob
from vitrina_core.providers.compose import Providers
from vitrina_core.settings import REPO_ROOT, Settings
from vitrina_creative.api import SourceProduct, Thresholds, VerifierModels, verify_scene
from vitrina_evals.runner import SuiteResult
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import CaseScore

FIDELITY_FIXTURES = REPO_ROOT / "evals" / "fixtures" / "fidelity"
PASS_RATE_MIN = 0.8
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class FidelityCase(BaseModel):
    case_id: str
    kind: Literal["good", "altered"]
    scene: str  # relative to the fixture folder
    change: str | None = None  # what an altered scene changed, for the report


class FidelitySet(BaseModel):
    """``cases.json``: the source product as the segment stage found it, and the scenes."""

    source: str
    source_mask: str
    label: str  # the English noun SAM looks for (vitrina_creative.concept)
    possessive: str
    cases: list[FidelityCase]


def load_fidelity(folder: Path = FIDELITY_FIXTURES) -> FidelitySet:
    return FidelitySet.model_validate_json((folder / "cases.json").read_text(encoding="utf-8"))


def read_image(path: Path) -> Blob:
    return Blob(data=path.read_bytes(), mime_type=MIME[path.suffix.lower()])


async def run_fidelity(
    fixtures: FidelitySet,
    *,
    providers: Providers,
    settings: Settings,
    ledger: InMemoryLedger,
    folder: Path = FIDELITY_FIXTURES,
    sample: int | None = None,
) -> SuiteResult:
    try:
        models = VerifierModels.from_settings(settings)
    except LookupError:  # the cassettes are keyed by model: replay needs the recorded IDs
        return SuiteResult(
            "error", EvalMetrics(), [], "set MODEL_SEGMENT and MODEL_AGENT to the recorded models"
        )
    source = SourceProduct(
        image=read_image(folder / fixtures.source),
        mask=read_image(folder / fixtures.source_mask),
        label=fixtures.label,
        possessive=fixtures.possessive,
    )
    thresholds = Thresholds.from_settings(settings)
    cases = fixtures.cases[:sample] if sample else fixtures.cases
    scores = [
        await _run_case(case, source, folder, providers, models, thresholds) for case in cases
    ]
    metrics = _metrics(cases, scores, ledger)
    if not scores or any(score.error for score in scores):
        return SuiteResult("error", metrics, scores, "some scenes could not be verified")
    blocked = metrics.verifier_block_rate_on_altered
    if isinstance(blocked, float) and blocked < 1.0:
        return SuiteResult("failed", metrics, scores, "an altered product got through (FR-012)")
    passed = metrics.verifier_pass_rate
    if isinstance(passed, float) and passed < PASS_RATE_MIN:
        return SuiteResult("failed", metrics, scores, "the verifier blocks too many good scenes")
    return SuiteResult("passed", metrics, scores)


async def _run_case(
    case: FidelityCase,
    source: SourceProduct,
    folder: Path,
    providers: Providers,
    models: VerifierModels,
    thresholds: Thresholds,
) -> CaseScore:
    wanted = "passed" if case.kind == "good" else "blocked"
    expected = {"status": wanted} | ({"change": case.change} if case.change else {})
    try:
        verification = await verify_scene(
            source,
            read_image(folder / case.scene),
            segmenter=providers.segmenter,
            reviewer=providers.chat,
            models=models,
            thresholds=thresholds,
        )
    except Exception as error:  # one broken case must not hide the others
        return CaseScore(
            case.case_id, False, f"error: {type(error).__name__}: {error}", expected, error=True
        )
    return CaseScore(
        case_id=case.case_id,
        passed=verification.status == wanted,
        notes=verification.reason,
        expected=expected,
        actual={
            "status": verification.status,
            "mask_iou": verification.mask_iou,
            "color_delta": verification.color_delta,
            "visual_review": verification.visual_review.model_dump(),
        },
    )


def _metrics(
    cases: Sequence[FidelityCase], scores: Sequence[CaseScore], ledger: InMemoryLedger
) -> EvalMetrics:
    def rate(kind: str) -> float | None:
        mine = [s for c, s in zip(cases, scores, strict=True) if c.kind == kind and not s.error]
        return round(sum(s.passed for s in mine) / len(mine), 4) if mine else None

    values: dict[str, float] = {
        "cost_usd_total": sum(entry.cost_usd for entry in ledger.entries if not entry.replayed)
    }
    if (good := rate("good")) is not None:
        values["verifier_pass_rate"] = good
    if (altered := rate("altered")) is not None:
        values["verifier_block_rate_on_altered"] = altered
    return EvalMetrics.model_validate(values)
