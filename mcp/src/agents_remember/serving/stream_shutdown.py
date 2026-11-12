"""The open streams end cleanly the moment the server begins to shut down.

``/api/stream`` and ``/api/events`` never end on their own, so a shutdown used to wait out the
bounded graceful window and then cancel them, and a page reconnecting meanwhile was answered with
an error status. An ``EventSource`` treats an error status as final and never retries, so the page
stayed dead until reloaded. Now the serving lifespan learns of the shutdown signal itself, beside
the server's own handler, and every open stream ends at once: a stream that ends cleanly is one an
``EventSource`` reconnects to by itself, and the page reopens its stream after any other failure
too (``dashboard/src/data/stream.ts``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import threading
from collections.abc import AsyncGenerator, Callable
from types import FrameType
from typing import Any

__all__ = ["StreamShutdown", "until_shutdown"]

logger = logging.getLogger(__name__)

#: The signals a server stops on; a handler already installed for one keeps running after ours.
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class StreamShutdown:
    """Whether the server began to shut down, for the streams that must end then."""

    def __init__(self) -> None:
        self._ended: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._installed: dict[int, tuple[Any, Any]] = {}

    def install(self) -> None:
        """At serving startup: hear the shutdown signals, then hand each on to its old handler.

        Signal handlers belong to the main thread; a server running elsewhere (a test client's
        portal) has no signals to hear, and its streams end with their connections as before.
        """

        self._loop = asyncio.get_running_loop()
        self._ended = asyncio.Event()
        if threading.current_thread() is not threading.main_thread():
            return
        for number in SHUTDOWN_SIGNALS:
            previous = signal.getsignal(number)
            heard = self._handler(previous)
            signal.signal(number, heard)
            self._installed[number] = (heard, previous)

    def uninstall(self) -> None:
        """At serving shutdown: hand each signal back to the handler it had, if ours still holds it."""

        if threading.current_thread() is not threading.main_thread():
            return
        for number, (heard, previous) in self._installed.items():
            if signal.getsignal(number) is heard:
                signal.signal(number, previous)
        self._installed.clear()

    def _handler(self, previous: Any) -> Callable[[int, FrameType | None], None]:
        def heard(number: int, frame: FrameType | None) -> None:
            self.begin()
            if callable(previous):
                previous(number, frame)

        return heard

    def begin(self) -> None:
        """The server is shutting down: every open stream ends."""

        if self._loop is None or self._ended is None:
            return
        logger.info("the server is shutting down: its open streams end")
        with contextlib.suppress(RuntimeError):  # a loop already closed has no stream left open
            self._loop.call_soon_threadsafe(self._ended.set)

    async def wait(self) -> None:
        if self._ended is None:
            await asyncio.Event().wait()  # never installed: no shutdown is ever heard
        else:
            await self._ended.wait()


async def until_shutdown[T](
    stream: AsyncGenerator[T], shutdown: StreamShutdown
) -> AsyncGenerator[T]:
    """``stream``'s events until it ends or the server begins to shut down; then it ends cleanly."""

    ended = asyncio.ensure_future(shutdown.wait())
    running: list[asyncio.Future[Any]] = [ended]
    try:
        while True:
            step = asyncio.ensure_future(anext(stream))
            running.append(step)
            done, _ = await asyncio.wait({step, ended}, return_when=asyncio.FIRST_COMPLETED)
            if step not in done:
                return
            running.remove(step)
            try:
                event = step.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        # A step still awaiting the stream's next event -- a shutdown, or the client leaving and
        # the route cancelled -- ends before the stream is closed: an async generator cannot be
        # closed while it runs.
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
