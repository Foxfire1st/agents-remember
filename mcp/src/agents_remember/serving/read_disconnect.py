"""A read whose client went away stops computing (D569, mean 4): the routes' one disconnect watch.

The reviewer's and the explainer's reads run synchronous ports for seconds to minutes. A browser
abandons such a request often -- a React StrictMode remount, a superseded selection, a navigation
-- and a worker thread that computed on to the end used to spend that CPU inside the one GIL-bound
dashboard process, slowing every other read of the page.

:func:`read_while_connected` runs the port's blocking work off the event loop, inside the context
copied into its worker thread with one cancellation token bound
(:func:`agents_remember.kernel.read_cancellation.cancellable_read`). While it runs, the route looks
at ``request.is_disconnected()`` -- a non-blocking receive -- every
:data:`DISCONNECT_POLL_SECONDS`. When the client is gone it sets the token; the computation stops at
its next safe point with :class:`~agents_remember.kernel.read_cancellation.ReadCancelled`, which
no read-failure handler catches, so nothing is answered, kept or written for it. The route waits
for the worker to end (its children reaped), logs what it did, and answers ``499`` (the client
closed the request), a status no client reads. A worker whose read is shared with a client that
remains computes on for that client, and this request ends with it.

The work is the read's own, never a shared flight's: identical reads in flight share one
computation (:class:`~agents_remember.kernel.read_cancellation.SharedFlights`), which stops only
when every one of their clients is gone.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor
from functools import partial
from typing import Final

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from agents_remember.kernel.read_cancellation import PAGE_READS, ReadCancelled, cancellable_read

__all__ = ["CLIENT_CLOSED_REQUEST", "DISCONNECT_POLL_SECONDS", "read_while_connected"]

DISCONNECT_POLL_SECONDS: Final = 0.1
CLIENT_CLOSED_REQUEST: Final = 499

_log = logging.getLogger(__name__)


async def read_while_connected(
    request: Request,
    produce: Callable[[], Response],
    *,
    executor: Executor | None = None,
) -> Response:
    """``produce()`` off the loop under this request's cancellation token, or ``499`` once it left.

    ``executor`` names the route's own threads; without one the work runs on the threadpool a
    synchronous route runs on, so making a route watch its client changes no capacity.
    """

    cancellation = threading.Event()
    context = contextvars.copy_context()

    def bound() -> Response:
        # Counted while it is answered, so background work never lengthens it (D574).
        with PAGE_READS.answering(), cancellable_read(cancellation):
            return produce()

    started = time.monotonic()
    worker: asyncio.Future[Response] = asyncio.ensure_future(
        run_in_threadpool(context.run, bound)
        if executor is None
        else asyncio.get_running_loop().run_in_executor(executor, context.run, bound)
    )
    try:
        while not worker.done():
            await asyncio.wait((worker,), timeout=DISCONNECT_POLL_SECONDS)
            if not worker.done() and await request.is_disconnected():
                cancellation.set()
                # The request ends when its work does: a stopped read has reaped its Git and
                # worklist children by then, and a read shared with a remaining client has
                # answered that client.
                await asyncio.wait((worker,))
                _abandoned(request.url.path, started, worker)
                return Response(status_code=CLIENT_CLOSED_REQUEST)
        try:
            return worker.result()
        except ReadCancelled:
            # Only a flight every asker left raises this, and this asker had not left: it is
            # never answered as a failure, and never as an answer it did not compute.
            _log.warning("read %s was cancelled while its client was connected", request.url.path)
            return Response(status_code=CLIENT_CLOSED_REQUEST)
    except asyncio.CancelledError:
        # The server cancelled this request (shutdown, or a transport that cancels on
        # disconnect): the work stops at its next safe point, and the cancellation propagates.
        cancellation.set()
        worker.add_done_callback(partial(_abandoned, request.url.path, started))
        raise


def _abandoned(path: str, started: float, worker: asyncio.Future[Response]) -> None:
    _log.info(
        "read %s abandoned by its client: %s (%.1fs after it arrived)",
        path,
        _outcome(worker),
        time.monotonic() - started,
    )


def _outcome(worker: asyncio.Future[Response]) -> str:
    """What the abandoned worker did, retrieving its exception so none goes unobserved."""

    if worker.cancelled():
        return "its worker was cancelled"
    error = worker.exception()
    if error is None:
        return "it finished: it reached no safe point, or a remaining asker shared it"
    if isinstance(error, ReadCancelled):
        return "its computation stopped at a safe point"
    return f"it failed after its client left ({type(error).__name__}: {error})"
