from datetime import UTC, datetime

from vitrina_core.ids import NIL, new_id, seeded_ids, uuid7, uuid7_time


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


def test_as_7_3_seeded_ids_repeat_inside_a_block_and_stay_unique_outside() -> None:
    # Replay needs a tool's new id to be the same on every run: it goes back to the model.
    with seeded_ids("case-1:0"):
        first = [new_id(), new_id()]
    with seeded_ids("case-1:0"):
        again = [new_id(), new_id()]
    with seeded_ids("case-2:0"):
        other = new_id()

    assert first == again
    assert first[0] != first[1]
    assert other not in first
    assert new_id() != new_id()
    assert new_id().version == 7
