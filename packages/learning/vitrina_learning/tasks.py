"""Learning sweeps and jobs, found by the worker through the ``vitrina.tasks`` entry point."""

from vitrina_core.jobs import TaskRegistry
from vitrina_learning.attribution import AttributionConsumer
from vitrina_learning.briefs import GENERATE_TASK, generate_brief

ATTRIBUTION_SWEEP = "learning.attribution"
# The dashboard's attribution counts trail the chats by at most this long (T044).
ATTRIBUTION_EVERY_S = 30.0


def register(registry: TaskRegistry) -> None:
    registry.every(
        ATTRIBUTION_SWEEP, every_s=ATTRIBUTION_EVERY_S, sweep=AttributionConsumer().sweep
    )
    # The dataset, one Wonder call within PROVIDER_JOB_TIMEOUT_S, and one more on a bad answer.
    registry.add(GENERATE_TASK, generate_brief, timeout_s=300)
