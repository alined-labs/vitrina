from datetime import UTC, datetime

from vitrina_core.ids import NIL, new_id, uuid7, uuid7_time


def test_uuid7_has_version_7_and_the_rfc_9562_variant() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_sorts_by_creation_time() -> None:
    first = uuid7(at=datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
    second = uuid7(at=datetime(2026, 10, 27, 12, 0, 1, tzinfo=UTC))

    assert first < second
    assert str(first) < str(second)


def test_uuid7_round_trips_its_millisecond_timestamp() -> None:
    moment = datetime(2026, 11, 4, 18, 0, 0, 123000, tzinfo=UTC)

    assert uuid7_time(uuid7(at=moment)) == moment


def test_new_ids_are_unique_and_never_nil() -> None:
    ids = {new_id() for _ in range(1000)}

    assert len(ids) == 1000
    assert NIL not in ids
