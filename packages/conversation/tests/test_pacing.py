"""AS-3.11, R12, Art. VIII.2: messages to one recipient are at least 6 s apart."""

import asyncio

from vitrina_conversation.pacing import Pacer

LAURA = "+573000000001"
ANDRES = "+573000000002"


class FakeClock:
    """Monotonic time that only moves when someone sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def send_times(pacer: Pacer, clock: FakeClock, *recipients: str) -> list[float]:
    times = []
    for recipient in recipients:
        async with pacer.slot(recipient):
            times.append(clock.now)
    return times


async def test_as_3_11_messages_to_one_recipient_are_6_seconds_apart() -> None:
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep)

    assert await send_times(pacer, clock, LAURA, LAURA, LAURA) == [0.0, 6.0, 12.0]


async def test_as_3_11_time_that_already_passed_counts() -> None:
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep)
    await send_times(pacer, clock, LAURA)
    clock.now = 4.5

    await send_times(pacer, clock, LAURA)

    assert clock.sleeps == [1.5]


async def test_as_3_11_other_recipients_never_wait() -> None:
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep)

    assert await send_times(pacer, clock, LAURA, ANDRES) == [0.0, 0.0]
    assert clock.sleeps == []


async def test_as_3_11_concurrent_sends_to_one_recipient_queue_up() -> None:
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep)
    times: list[float] = []

    async def send() -> None:
        async with pacer.slot(LAURA):
            times.append(clock.now)

    await asyncio.gather(send(), send())

    assert times == [0.0, 6.0]
