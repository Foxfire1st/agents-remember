"""Condition waits whose deadline only guards against a hang."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

HANG_GUARD_SECONDS = 30.0


def wait_until(condition: Callable[[], object], what: str) -> None:
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {what}")
        time.sleep(0.01)


async def async_wait_until(condition: Callable[[], object], what: str) -> None:
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {what}")
        await asyncio.sleep(0.01)
