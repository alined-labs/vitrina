import pytest

from vitrina_core.jobs import TaskRegistry, load_tasks
from vitrina_core.settings import Settings
from vitrina_meta_model_api import MetaChatModel
from vitrina_worker.main import job_providers, serve, worker_registry


async def test_the_worker_refuses_to_start_without_a_database() -> None:
    with pytest.raises(SystemExit, match="needs DATABASE_URL and DB_SCHEMA"):
        await serve(Settings(_env_file=None))  # type: ignore[call-arg]


def test_the_worker_gives_chat_calls_the_job_timeout() -> None:
    config = Settings(_env_file=None, meta_api_key="test-key", provider_job_timeout_s=75.0)  # type: ignore[call-arg]

    providers = job_providers(config)

    assert isinstance(providers.chat, MetaChatModel)
    assert providers.chat.client.timeout == 75.0


INBOUND = "conversation.process_inbound"


def everything() -> TaskRegistry:
    registry = TaskRegistry()
    load_tasks(registry)
    return registry


def lane(**values: str) -> TaskRegistry:
    return worker_registry(Settings(_env_file=None, **values))  # type: ignore[call-arg]


def test_with_nothing_set_the_worker_claims_every_task_and_runs_the_sweeps() -> None:
    registry = lane()

    assert INBOUND in registry.names()
    assert registry.names() == everything().names()
    assert {item.name for item in registry.periodic()} == {
        item.name for item in everything().periodic()
    }


def test_a_blank_setting_means_unset_as_in_a_copy_of_the_env_example() -> None:
    assert lane(worker_tasks="", worker_exclude_tasks=" ").names() == everything().names()


def test_the_production_lanes_split_the_tasks_with_nothing_shared_or_left_out() -> None:
    general = lane(worker_exclude_tasks=INBOUND)
    inbound = lane(worker_tasks=INBOUND)

    assert inbound.names() == {INBOUND}
    assert not general.names() & inbound.names()
    assert general.names() | inbound.names() == everything().names()


def test_exactly_one_production_lane_runs_the_sweeps() -> None:
    assert lane(worker_tasks=INBOUND).periodic() == ()
    assert {item.name for item in lane(worker_exclude_tasks=INBOUND).periodic()} == {
        item.name for item in everything().periodic()
    }


def test_several_task_names_are_comma_separated() -> None:
    names = lane(worker_tasks=f"{INBOUND}, compliance.erase_contact").names()

    assert names == {INBOUND, "compliance.erase_contact"}


@pytest.mark.parametrize("setting", ["worker_tasks", "worker_exclude_tasks"])
async def test_the_worker_refuses_to_start_on_a_task_that_does_not_exist(setting: str) -> None:
    config = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql://nobody@127.0.0.1:1/none",
        db_schema="none",
        **{setting: "conversation.process_inbund"},
    )

    with pytest.raises(SystemExit, match=r"unknown task.*conversation\.process_inbund"):
        await serve(config)


async def test_the_worker_refuses_to_start_with_both_settings() -> None:
    config = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql://nobody@127.0.0.1:1/none",
        db_schema="none",
        worker_tasks=INBOUND,
        worker_exclude_tasks=INBOUND,
    )

    with pytest.raises(SystemExit, match="either"):
        await serve(config)
