"""The ``price_validator`` suite (T072; FR-026, AS-7.2): property cases through the real engine.

Each case is a reply the agent might write about the seller's own items, the amounts in it
rendered in the ways customers and sellers write money ($85.000, 85 mil, 85k, ochenta y cinco
mil pesos, COP 85000). Half the cases quote only what the cited items say: the engine must let
them through. The other half slip in an amount no cited item has (a nearby number, a total, a
discount, a draft product's price): the engine must stop them. A scripted Spark gives each
reply twice, so a stopped reply ends in the fallback after the guided retry. The cases come
from a fixed seed, so every run is the same set; no model is called.

``invented_price_rate`` is the share of slipped-in amounts that reached the customer and must
be 0.0 (AS-7.2); ``groundedness_rate`` is the share of honest replies that got through.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid5

from vitrina_conversation import api as conversation
from vitrina_conversation.testing import ScriptedChat, final_answer
from vitrina_evals.cases import Scene, build_scene, fixture_id, load_business
from vitrina_evals.runner import SuiteResult
from vitrina_evals.schemas import EvalMetrics
from vitrina_evals.scoring import CaseScore

BUSINESS = "mochilas_ana"
SEED = 2026_10_28
CASES = 120
NAMESPACE = UUID("0192f330-5b7e-7c3d-9a41-6e2f8b0c1d77")

_UNITS = ["", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
_TEENS = [
    "diez", "once", "doce", "trece", "catorce", "quince",
    "dieciséis", "diecisiete", "dieciocho", "diecinueve",
]  # fmt: skip
_TWENTIES = [
    "veinte", "veintiún", "veintidós", "veintitrés", "veinticuatro",
    "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve",
]  # fmt: skip
_TENS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
_HUNDREDS = [
    "", "ciento", "doscientos", "trescientos", "cuatrocientos",
    "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos",
]  # fmt: skip


def _below_thousand(number: int) -> str:
    hundreds, rest = divmod(number, 100)
    words = [] if hundreds == 0 else ["cien" if number == 100 else _HUNDREDS[hundreds]]
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    elif 20 <= rest < 30:
        words.append(_TWENTIES[rest - 20])
    elif rest >= 30:
        tens, units = divmod(rest, 10)
        words.append(_TENS[tens] + (f" y {_UNITS[units]}" if units else ""))
    elif rest:
        words.append(_UNITS[rest])
    return " ".join(words)


def spoken(amount: int) -> str:
    """Whole thousands below a million in words: 85000 → "ochenta y cinco mil"."""
    thousands, rest = divmod(amount, 1000)
    if rest or not 1 <= thousands <= 999:
        raise ValueError(f"{amount} is not whole thousands below a million")
    return "mil" if thousands == 1 else f"{_below_thousand(thousands)} mil"


def render(amount: int, style: int) -> str:
    """One of the ways an amount is written in a Colombian chat (FR-026's examples first)."""
    dotted = f"{amount:,}".replace(",", ".")
    thousands = amount // 1000
    return [
        f"${dotted}",
        f"{thousands} mil",
        f"{thousands}k",
        f"{spoken(amount)} pesos",
        f"COP {amount}",
        f"{dotted} pesos",
        f"$ {dotted}",
        f"{amount} COP",
    ][style % 8]


@dataclass(frozen=True)
class PriceCase:
    case_id: str
    message: str
    reply: str
    cited: tuple[UUID, ...]
    invented: int | None  # the slipped-in amount; None for an honest reply


def price_cases(scene: Scene, *, count: int = CASES, seed: int = SEED) -> list[PriceCase]:
    """``count`` cases, the same on every run: even ones honest, odd ones with an invented amount."""
    rng = random.Random(seed)
    names = {
        fixture_id(BUSINESS, key): label
        for key, label in (
            ("mochila-azul", "la mochila tejida azul"),
            ("mochila-roja", "la mochila tejida roja"),
            ("bolso-cuero", "el bolso de cuero"),
            ("manilla", "la manilla tejida"),
        )
    }
    quotable = {item: amounts for item, amounts in scene.allowed.items() if item in names}
    known = set().union(*quotable.values())
    draft = 120_000  # the draft special edition's price: never citable while it is a draft
    cases: list[PriceCase] = []
    for number in range(count):
        cited = rng.sample(sorted(quotable, key=str), k=rng.choice((1, 2)))
        parts = [
            f"{names[item].capitalize() if index == 0 else names[item]} vale "
            f"{render(min(quotable[item]), rng.randrange(8))}"
            for index, item in enumerate(cited)
        ]
        reply = " y ".join(parts) + "."
        invented: int | None = None
        if number % 2:
            base = min(quotable[cited[0]])
            options = [base + 1000 * rng.randint(1, 15), base - 1000 * rng.randint(1, 5), 2 * base]
            options.append(draft)
            invented = next(
                amount for amount in rng.sample(options, len(options)) if amount not in known
            )
            reply += rng.choice(
                (
                    f" Con descuento te queda en {render(invented, rng.randrange(8))}.",
                    f" Las dos te salen en {render(invented, rng.randrange(8))}.",
                    f" La edición especial vale {render(invented, rng.randrange(8))}.",
                )
            )
        cases.append(
            PriceCase(
                case_id=f"p{number + 1:03d}",
                message=f"¿Cuánto vale {names[cited[0]]}?",
                reply=reply,
                cited=tuple(cited),
                invented=invented,
            )
        )
    return cases


async def _verdict(case: PriceCase, scene: Scene) -> CaseScore:
    chat = ScriptedChat([final_answer(case.reply, *case.cited)] * 2)
    result = await conversation.run_turn(
        conversation.TurnRequest(
            seller=scene.seller,
            conversation_id=uuid5(NAMESPACE, case.case_id),
            message=case.message,
            dry_run=True,
        ),
        conversation.AgentDeps(chat=chat, catalog=scene.catalog, model="price-suite"),
    )
    delivered = result.final_reply == case.reply
    honest = case.invented is None
    passed = delivered if honest else not delivered
    notes = (
        "ok"
        if passed
        else ("an honest reply was stopped" if honest else f"{case.invented} reached the customer")
    )
    return CaseScore(
        case_id=case.case_id,
        passed=passed,
        notes=notes,
        expected={"delivered": honest, **({"invented": case.invented} if not honest else {})},
        actual={"reply": result.final_reply},
        grounded=delivered if honest else None,
        invented_amounts=() if honest or not delivered else (case.invented or 0,),
    )


async def run_price_validator(cases: Sequence[PriceCase] | None = None) -> SuiteResult:
    scene = await build_scene(load_business(BUSINESS))
    scores = [await _verdict(case, scene) for case in cases or price_cases(scene)]
    honest = [score for score in scores if score.grounded is not None]
    traps = [score for score in scores if score.grounded is None]
    reached = sum(bool(score.invented_amounts) for score in traps)
    metrics = EvalMetrics(
        invented_price_rate=round(reached / len(traps), 4) if traps else 0.0,
        groundedness_rate=(
            round(sum(bool(score.grounded) for score in honest) / len(honest), 4) if honest else 0.0
        ),
    )
    if reached:
        return SuiteResult("failed", metrics, scores, f"{reached} invented amounts got through")
    return SuiteResult("passed", metrics, scores)
