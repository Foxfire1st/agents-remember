"""Asyncio loops for tests whose sandbox can refuse the socket wake-up."""

from __future__ import annotations

import asyncio
import selectors

_MAX_IDLE_SECONDS = 0.05


class _BoundedSelector(selectors.DefaultSelector):
    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        # A cross-thread callback is queued even if asyncio's socket send fails.
        # Bound the next check of that queue, including while the runner joins its executor.
        wait = _MAX_IDLE_SECONDS if timeout is None else min(timeout, _MAX_IDLE_SECONDS)
        return super().select(wait)


def new_test_event_loop() -> asyncio.AbstractEventLoop:
    """Return a loop that processes queued work even when its wake-up byte is lost."""
    return asyncio.SelectorEventLoop(_BoundedSelector())
