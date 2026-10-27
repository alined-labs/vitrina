"""The eval job, discovered by the worker through the ``vitrina.tasks`` entry point."""

from uuid import UUID

from vitrina_core.jobs import JobContext, JobFailedError, TaskRegistry
from vitrina_evals.store import PostgresEvalRuns
from vitrina_evals.suites import current_git_sha, execute

EVAL_TASK = "evals.run"


async def run_eval(ctx: JobContext) -> str:
    """FR-070: run the suite the eval run names and store its result on the run."""
    if ctx.sessions is None:
        raise JobFailedError("INTERNAL_ERROR", "eval runs need the database")
    try:
        run_id = UUID(str(ctx.payload.get("run_id")))
    except ValueError as error:
        raise JobFailedError("VALIDATION_ERROR", "the job names no eval run") from error
    runs = PostgresEvalRuns(ctx.sessions)
    run = await runs.get(run_id)
    if run is None:
        raise JobFailedError("NOT_FOUND", f"no eval run {run_id}")
    await runs.start(run_id)
    await ctx.progress("running", 0.1)
    sample = ctx.payload.get("sample")
    result = await execute(
        run.suite,
        replay=run.replay,
        settings=ctx.settings,
        live=ctx.providers,
        sample=int(sample) if sample is not None else None,
    )
    await runs.finish(run_id, result, git_sha=current_git_sha())
    return f"/v1/evals/runs/{run_id}"


def register(registry: TaskRegistry) -> None:
    # 20 cases at the 20 s agent budget each, with room for retries (plan.md §4.1).
    registry.add(EVAL_TASK, run_eval, timeout_s=900)
