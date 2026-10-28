"""Outbound pacing: at least 6 s between two messages to one recipient (R12, Art. VIII.2).

Sends to one recipient are serialized, and each waits until the spacing has passed since
the previous one to that recipient ended; different recipients never wait for each other.
The pacer lives in the process: a deployment runs one worker; a second process would need
the spacing kept in the database.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

OUTBOUND_SPACING_S = 6.0  # R12; settings.outbound_spacing_s


class Pacer:
    def __init__(
        self,
        spacing_s: float = OUTBOUND_SPACING_S,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.spacing_s = spacing_s
        self.clock = clock
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def slot(self, recipient: str) -> AsyncIterator[None]:
        """Hold the recipient's turn to send; the next send to it waits out the spacing."""
        lock = self._locks.setdefault(recipient, asyncio.Lock())
        async with lock:
            last = self._last.get(recipient)
            if last is not None:
                wait = self.spacing_s - (self.clock() - last)
                if wait > 0:
                    await self.sleep(wait)
            try:
                yield
            finally:
                self._last[recipient] = self.clock()
