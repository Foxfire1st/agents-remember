"""The open streams end cleanly the moment the server begins to shut down.

A stream that never ends on its own ends without an error when the shutdown signal is heard, and
the handler the server installed for that signal still runs after it.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncGenerator

import pytest
from agents_remember.serving.stream_shutdown import StreamShutdown, until_shutdown
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


async def _endless(closed: list[bool]) -> AsyncGenerator[int]:
    try:
        count = 0
        while True:
            count += 1
            yield count
            await asyncio.sleep(0.01)
    finally:
        closed.append(True)


def test_an_endless_stream_ends_cleanly_when_the_shutdown_begins() -> None:
    async def run() -> tuple[list[int], list[bool]]:
        shutdown = StreamShutdown()
        shutdown.install()
        closed: list[bool] = []
        seen: list[int] = []
        source = _endless(closed)
        async with asyncio.timeout(HANG_GUARD_SECONDS):
            async for event in until_shutdown(source, shutdown):
                seen.append(event)
                if event == 3:
                    shutdown.begin()
        await source.aclose()
        return seen, closed

    seen, closed = asyncio.run(run())
    assert seen[:3] == [1, 2, 3] and len(seen) <= 4, seen  # it ended at the next step, no error
    assert closed == [True]


def test_a_stream_that_ends_by_itself_still_ends() -> None:
    async def finite() -> AsyncGenerator[str]:
        yield "snapshot"

    async def run() -> list[str]:
        shutdown = StreamShutdown()
        shutdown.install()
        async with asyncio.timeout(HANG_GUARD_SECONDS):
            return [event async for event in until_shutdown(finite(), shutdown)]

    assert asyncio.run(run()) == ["snapshot"]


def test_the_shutdown_signal_is_heard_and_the_servers_own_handler_still_runs() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    handled: list[int] = []
    signal.signal(signal.SIGTERM, lambda number, _frame: handled.append(number))
    try:

        async def run() -> bool:
            shutdown = StreamShutdown()
            shutdown.install()  # the main thread: the signal is heard
            signal.raise_signal(signal.SIGTERM)
            async with asyncio.timeout(HANG_GUARD_SECONDS):
                await shutdown.wait()
            return True

        assert asyncio.run(run())
        assert handled == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize("installed", [False, True])
def test_a_shutdown_never_heard_ends_nothing(installed: bool) -> None:
    async def run() -> bool:
        shutdown = StreamShutdown()
        if installed:
            shutdown.install()
        try:
            async with asyncio.timeout(0.05):
                await shutdown.wait()
        except TimeoutError:
            return False
        return True

    assert asyncio.run(run()) is False


def test_the_shutdown_hands_each_signal_back_unless_another_handler_took_it_since() -> None:
    previous = signal.getsignal(signal.SIGTERM)

    def mine(_number: int, _frame: object) -> None:
        return None

    def later(_number: int, _frame: object) -> None:
        return None

    signal.signal(signal.SIGTERM, mine)
    try:

        async def run(replace_after: bool) -> object:
            shutdown = StreamShutdown()
            shutdown.install()
            assert signal.getsignal(signal.SIGTERM) is not mine
            if replace_after:
                signal.signal(signal.SIGTERM, later)
            shutdown.uninstall()
            return signal.getsignal(signal.SIGTERM)

        assert asyncio.run(run(replace_after=False)) is mine
        assert asyncio.run(run(replace_after=True)) is later
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_a_client_leaving_mid_step_closes_the_stream_with_no_error() -> None:
    """A disconnect cancels the route while the stream awaits its next event: it closes cleanly."""

    async def run() -> list[bool]:
        shutdown = StreamShutdown()
        shutdown.install()
        closed: list[bool] = []

        async def waiting() -> AsyncGenerator[int]:
            try:
                yield 1
                await asyncio.Event().wait()  # the next event never comes
                yield 2
            finally:
                closed.append(True)

        source = waiting()
        started = asyncio.Event()

        async def route() -> None:
            async with contextlib.aclosing(source):
                async for _event in until_shutdown(source, shutdown):
                    started.set()

        task = asyncio.ensure_future(route())
        async with asyncio.timeout(HANG_GUARD_SECONDS):
            await started.wait()
        await asyncio.sleep(0)  # load-independent: lets the next step start awaiting; no clock
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task  # a RuntimeError here: the stream was closed while its step still ran
        shutdown.uninstall()
        return closed

    assert asyncio.run(run()) == [True]
