from vitrina_catalog.api import expire_reservations
from vitrina_catalog.tasks import EXPIRE_RESERVATIONS, register
from vitrina_core.jobs import TaskRegistry


def test_as_3_7_the_worker_expires_reservations_every_ten_minutes() -> None:
    registry = TaskRegistry()

    register(registry)

    [sweep] = [item for item in registry.periodic() if item.name == EXPIRE_RESERVATIONS]
    assert (sweep.sweep, sweep.every_s) == (expire_reservations, 600)
