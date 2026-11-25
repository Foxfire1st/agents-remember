"""Run blocking MCP request work and observe its outcome under cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from threading import Lock

from anyio import CancelScope


@dataclass(slots=True)
class WorkerCancellation:
    log_detached_error: Callable[[Exception], None]
    log_detached_wait: Callable[[], None]
    error: Exception | None = None


_worker_cancellation: ContextVar[WorkerCancellation | None] = ContextVar(
    "worker_cancellation", default=None
)


@contextmanager
def track_worker_cancellation(
    log_detached_error: Callable[[Exception], None],
    log_detached_wait: Callable[[], None],
) -> Iterator[WorkerCancellation]:
    """Bind the outcomes of cancelled workers to the request that started them."""
    outcome = WorkerCancellation(log_detached_error, log_detached_wait)
    token = _worker_cancellation.set(outcome)
    try:
        yield outcome
    finally:
        outcome.error = None
        _worker_cancellation.reset(token)


def _start_request_thread[**P, R](
    function: Callable[P, R], /, *arguments: P.args, **keywords: P.kwargs
) -> asyncio.Task[R]:
    return asyncio.create_task(asyncio.to_thread(function, *arguments, **keywords))


async def run_in_request_thread[**P, R](
    function: Callable[P, R], /, *arguments: P.args, **keywords: P.kwargs
) -> R:
    """Carry the caller's context and drain its thread before propagating cancellation."""

    worker = _start_request_thread(function, *arguments, **keywords)
    try:
        # Waiting for completion leaves the worker running when its waiter is cancelled.
        await asyncio.wait((worker,))
    except asyncio.CancelledError:
        # A thread cannot be cancelled. Keep the owning scopes and any write lock
        # alive until the thread exits, even under repeated transport cancellation.
        with CancelScope(shield=True):
            while not worker.done():
                try:
                    await asyncio.wait((worker,))
                except asyncio.CancelledError:
                    continue
        if not worker.cancelled():
            error = worker.exception()
            if isinstance(error, Exception):
                outcome = _worker_cancellation.get()
                if outcome is not None:
                    outcome.error = error
        raise
    return worker.result()


async def run_in_detached_request_thread[**P, R](
    function: Callable[P, R], /, *arguments: P.args, **keywords: P.kwargs
) -> R:
    """Propagate cancellation promptly and let the request log a late worker error."""
    outcome = _worker_cancellation.get()
    worker = _DetachedWorker(outcome.log_detached_error if outcome is not None else None)
    try:
        # Direct awaiting preserves cancellation of a submission still in the executor queue.
        return await asyncio.to_thread(worker.run, partial(function, *arguments, **keywords))
    except asyncio.CancelledError:
        if worker.cancel() and outcome is not None:
            outcome.log_detached_wait()
        raise


@dataclass(slots=True)
class _DetachedWorker:
    report_error: Callable[[Exception], None] | None
    _lock: Lock = field(default_factory=Lock, init=False)
    _started: bool = field(default=False, init=False)
    _cancelled: bool = field(default=False, init=False)
    _error: Exception | None = field(default=None, init=False)

    def run[R](self, function: Callable[[], R]) -> R:
        with self._lock:
            # The executor can dequeue the submission concurrently with request cancellation.
            if self._cancelled:
                raise asyncio.CancelledError
            self._started = True
        try:
            return function()
        except Exception as error:
            with self._lock:
                cancelled = self._cancelled
                if not cancelled:
                    self._error = error
            # Runner shutdown joins the executor after cancelling outstanding asyncio tasks.
            # Write from the running worker so the log does not depend on an asyncio callback.
            if cancelled and self.report_error is not None:
                self.report_error(error)
            raise

    def cancel(self) -> bool:
        """Claim an undelivered error and report whether the worker was still queued."""
        with self._lock:
            self._cancelled = True
            waiting = not self._started
            error = self._error
            self._error = None
        if error is not None and self.report_error is not None:
            self.report_error(error)
        return waiting
