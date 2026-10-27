"""The checks that run outside the model on every answer (R2–R5; FR-026; ADR-0006).

``review`` judges one ``AgentTurnOutput`` against the active items it cites and decides what
the customer gets:

- R2 price: every amount in the reply (``vitrina_core.money``, the normalizer that filled the
  catalog's ``amounts_cop``) must be an amount of a cited item. A violation earns one guided
  retry; a second one gets the fallback and a ``price_unverified`` handoff (AS-3.4).
- R3 groundedness: a citation that is not an active item of this seller is dropped and
  reported, and an answer that cited only such ids counts as confidence 0. Greetings and
  clarifying questions need no citation.
- R4 confidence: below the seller's threshold (default 0.6) the fallback replaces the reply
  and the turn counts as low confidence; the second in a row hands off.
- R5: a handoff the model chose keeps its reason and its reply (still under R2).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from vitrina_catalog.api import Citation
from vitrina_conversation.contract import AgentTurnOutput, HandoffReason
from vitrina_core.money import amounts_cop, format_cop

FALLBACK_REPLY = "No tengo ese dato, te comunico con {owner_name}"
LOW_CONFIDENCE_THRESHOLD = 0.6  # data-model.md sellers.low_confidence_threshold default

Outcome = Literal["accept", "retry", "fallback"]


@dataclass(frozen=True)
class PriceCheck:
    passed: bool
    amounts_in_reply: tuple[int, ...]
    amounts_in_citations: tuple[int, ...]
    violations: tuple[int, ...]


@dataclass(frozen=True)
class GroundednessCheck:
    passed: bool
    uncited_claims: tuple[str, ...]


@dataclass(frozen=True)
class Review:
    """What the customer gets for one answer; ``final_reply`` is None when a retry is due."""

    outcome: Outcome
    final_reply: str | None
    handoff_reason: HandoffReason | None
    low_confidence_streak: int
    confidence: float
    price: PriceCheck
    groundedness: GroundednessCheck
    retried: bool


def check_prices(reply: str, citations: Sequence[Citation]) -> PriceCheck:
    """R2: the amounts in ``reply`` that no cited item carries are violations."""
    in_reply = tuple(dict.fromkeys(amounts_cop(reply)))
    allowed = sorted({amount for citation in citations for amount in citation.amounts_cop})
    violations = tuple(amount for amount in in_reply if amount not in allowed)
    return PriceCheck(not violations, in_reply, tuple(allowed), violations)


def check_groundedness(output: AgentTurnOutput, citations: Sequence[Citation]) -> GroundednessCheck:
    """R3: every citation must resolve to an active item of this seller."""
    known = {citation.id for citation in citations}
    problems = tuple(
        f"cites {fact_id}, which is not an active fact or product of this seller"
        for fact_id in output.cited_fact_ids
        if fact_id not in known
    )
    return GroundednessCheck(not problems, problems)


def review(
    output: AgentTurnOutput,
    citations: Sequence[Citation],
    *,
    owner_name: str,
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    low_confidence_streak: int = 0,
    retried: bool = False,
) -> Review:
    """Decide on one answer. ``citations`` are what the catalog resolved for its cited ids."""
    price = check_prices(output.reply, citations)
    groundedness = check_groundedness(output, citations)
    confidence = output.confidence if groundedness.passed else 0.0
    fallback = FALLBACK_REPLY.format(owner_name=owner_name)

    def decide(
        outcome: Outcome, reply: str | None, reason: HandoffReason | None, streak: int
    ) -> Review:
        return Review(outcome, reply, reason, streak, confidence, price, groundedness, retried)

    if not price.passed:
        if not retried:
            return decide("retry", None, None, low_confidence_streak)
        return decide("fallback", fallback, "price_unverified", low_confidence_streak)
    if output.handoff_reason is not None:
        return decide("accept", output.reply, output.handoff_reason, 0)
    if confidence < low_confidence_threshold:
        streak = low_confidence_streak + 1
        return decide("fallback", fallback, "low_confidence_twice" if streak >= 2 else None, streak)
    return decide("accept", output.reply, None, 0)


def retry_instruction(result: Review) -> str:
    """The note the guided retry adds to the context (R2): what was wrong, what to do."""
    amounts = ", ".join(format_cop(amount) for amount in result.price.violations)
    return (
        f"Tu respuesta anterior incluía montos que no aparecen en ningún dato citado: {amounts}. "
        "Responde otra vez usando solo precios y montos que estén en los datos que citas en "
        "cited_fact_ids. Si no tienes el dato, di que no lo tienes y pasa la conversación al "
        "dueño del negocio."
    )
