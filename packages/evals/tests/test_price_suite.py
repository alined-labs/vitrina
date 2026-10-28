"""T072: the price_validator suite (FR-026, AS-7.2)."""

import pytest

from vitrina_core.money import amounts_cop
from vitrina_evals import price_suite, suites
from vitrina_evals.cases import build_scene, load_business
from vitrina_evals.price_suite import PriceCase, price_cases, render, run_price_validator, spoken


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        (1000, "mil"),
        (15000, "quince mil"),
        (21000, "veintiún mil"),
        (85000, "ochenta y cinco mil"),
        (100000, "cien mil"),
        (145000, "ciento cuarenta y cinco mil"),
        (290000, "doscientos noventa mil"),
    ],
)
def test_amounts_are_spoken_in_spanish(amount: int, words: str) -> None:
    assert spoken(amount) == words


@pytest.mark.parametrize("amount", [1000, 12000, 15000, 21000, 85000, 120000, 145000, 290000])
@pytest.mark.parametrize("style", range(8))
def test_fr_026_every_rendering_reads_back_as_its_amount(amount: int, style: int) -> None:
    assert amounts_cop(render(amount, style)) == [amount]


async def test_the_cases_are_the_same_on_every_run_and_half_carry_an_invented_amount() -> None:
    scene = await build_scene(load_business("mochilas_ana"))

    first, again = price_cases(scene), price_cases(scene)

    assert first == again
    assert len(first) == 120
    assert sum(case.invented is not None for case in first) == 60


async def test_as_7_2_the_validator_stops_every_invented_amount() -> None:
    result = await run_price_validator()

    assert result.status == "passed", [score.notes for score in result.cases if not score.passed]
    assert result.metrics.invented_price_rate == 0.0
    assert result.metrics.groundedness_rate == 1.0


async def test_as_7_2_an_invented_amount_that_gets_through_fails_the_run() -> None:
    scene = await build_scene(load_business("mochilas_ana"))
    [honest, trap] = price_cases(scene, count=2)
    # A case whose "invented" amount is in fact cited: the engine delivers it, as it should,
    # and the suite must count that as an invented price reaching the customer.
    disguised = PriceCase(trap.case_id, honest.message, honest.reply, honest.cited, invented=1)

    result = await run_price_validator([honest, disguised])

    assert result.status == "failed"
    assert result.metrics.invented_price_rate == 1.0


def test_the_suite_is_registered() -> None:
    assert "price_validator" not in suites.PLANNED
    assert price_suite.CASES == 120
