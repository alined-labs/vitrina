"""Worker entrypoint (``vitrina-worker``): runs every task the packages register (FR-083)."""

import asyncio
import contextlib
import logging
import signal

from vitrina_core.db import create_engine, create_session_factory
from vitrina_core.jobs import PostgresJobQueue, TaskRegistry, Worker, load_tasks
from vitrina_core.ledger import PostgresLedger, SpendWatch
from vitrina_core.providers.compose import compose
from vitrina_core.settings import Settings, get_settings
from vitrina_core.storage import blob_store
from vitrina_meta_model_api import live_providers

logger = logging.getLogger("vitrina.worker")


async def serve(settings: Settings, stop: asyncio.Event | None = None) -> None:
    if not settings.database_url or not settings.db_schema:
        raise SystemExit("vitrina-worker needs DATABASE_URL and DB_SCHEMA (plan.md §3)")
    registry = TaskRegistry()
    load_tasks(registry)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    ledger = PostgresLedger(sessions, SpendWatch(settings.spend_limit_usd))
    providers = compose(settings, ledger, lambda: live_providers(settings))
    storage = blob_store(settings)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    logger.info(
        "worker started in %s mode with tasks: %s",
        providers.mode,
        ", ".join(sorted(registry.names())) or "none",
    )
    try:
        worker = Worker(
            PostgresJobQueue(sessions),
            registry,
            sessions=sessions,
            providers=providers,
            storage=storage,
        )
        await worker.run(stop)
    finally:
        await storage.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(serve(get_settings()))
