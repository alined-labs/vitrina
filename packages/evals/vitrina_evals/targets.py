"""What a conversations_es run must reach to pass (spec.md §7, checked on AS-7.1's metrics).

Each metric here has a target in §7, and these are its numbers. The invented-price rate has
its own rule in the runner: any one invented price fails the run (AS-7.2). The fidelity and
consent suites check their own §7 targets.

Two AS-7.1 metrics are not here. ``off_topic_refusal_rate`` is reported, but §7 sets no target
for it. ``brand_voice_mean`` has a §7 target, but no suite computes it, so it is never reported
and nothing can say it was met.
"""

from vitrina_evals.schemas import EvalMetrics

CONVERSATION_TARGETS: dict[str, float] = {
    "groundedness_rate": 0.95,
    "handoff_precision": 0.9,
    "handoff_recall": 0.9,
}


def missed_targets(metrics: EvalMetrics) -> list[str]:
    """One line per metric below its target; a metric the run had no case for is not a miss."""
    measured = (
        (name, getattr(metrics, name), minimum) for name, minimum in CONVERSATION_TARGETS.items()
    )
    return [
        f"{name} {value} is below the target {minimum}"
        for name, value, minimum in measured
        if isinstance(value, float) and value < minimum
    ]
