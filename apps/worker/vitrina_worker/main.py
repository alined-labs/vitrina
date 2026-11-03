"""Worker entrypoint (``vitrina-worker``): runs the tasks the packages register (FR-083), all of
them unless WORKER_TASKS or WORKER_EXCLUDE_TASKS limits this process."""

import asyncio
import contextlib
import logging
import signal

from vitrina_core.db import create_engine, create_session_factory
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import PostgresLedger, SpendWatch
from vitrina_core.providers.compose import LiveProviders, compose
from vitrina_core.settings import Settings, get_settings
from vitrina_core.storage import blob_store
from vitrina_meta_model_api import live_providers

logger = logging.getLogger("vitrina.worker")


def job_providers(settings: Settings) -> LiveProviders:
    """Live adapters for jobs: chat waits PROVIDER_JOB_TIMEOUT_S. Turns run here too, but the
    engine's turn budget bounds them."""
    return live_providers(settings, chat_timeout_s=settings.provider_job_timeout_s)


def _names(setting: str | None) -> frozenset[str]:
    """Blank means unset; only separators is a typo that must not read as "every task"."""
    if setting is None or not setting.strip():
        return frozenset()
    names = frozenset(name.strip() for name in setting.split(",") if name.strip())
    if not names:
        raise ValueError(f"{setting!r} names no task")
    return names


def worker_registry(settings: Settings) -> TaskRegistry:
    """The tasks this process claims; a limit that cannot be right stops it at startup."""
    registry = TaskRegistry()
    load_tasks(registry)
    try:
        return registry.narrowed(
            only=_names(settings.worker_tasks), exclude=_names(settings.worker_exclude_tasks)
        )
    except ValueError as error:
        raise SystemExit(f"vitrina-worker: {error}") from error


async def serve(settings: Settings, stop: asyncio.Event | None = None) -> None:
    if not settings.database_url or not settings.db_schema:
        raise SystemExit("vitrina-worker needs DATABASE_URL and DB_SCHEMA (plan.md §3)")
    registry = worker_registry(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    ledger = PostgresLedger(sessions, SpendWatch(settings.spend_limit_usd))
    providers = compose(settings, ledger, lambda: job_providers(settings))
    storage = blob_store(settings)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    logger.info(
        "worker started in %s mode with tasks: %s; sweeps: %s",
        providers.mode,
        ", ".join(sorted(registry.names())) or "none",
        ", ".join(sorted(item.name for item in registry.periodic())) or "none",
    )
    try:
        worker = Worker(
            PostgresJobQueue(sessions),
            registry,
            sessions=sessions,
            providers=providers,
            storage=storage,
            settings=settings,
        )
        await worker.run(stop)
    finally:
        await storage.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(serve(get_settings()))
