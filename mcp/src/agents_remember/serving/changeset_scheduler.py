"""Dedicated change-set workers with bounded admission and removable pending work."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections import deque
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import Response

from agents_remember.kernel.git_command import cancellable_git_commands

DISCONNECT_POLL_SECONDS = 0.05


class _Admission:
    """A slot belongs to a physical job until withdrawal or worker completion."""

    def __init__(self, reads: _BoundedReads) -> None:
        self._reads = reads
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._reads.release()


class _BoundedReads:
    """Bound running plus pending jobs; queued jobs can be withdrawn immediately.

    ThreadPoolExecutor cannot remove its canceled work items. Releasing admission
    while leaving those items queued would let abandoned requests grow the queue.
    The condition protects only this family's queue and admission counter.
    """

    def __init__(self, *, threads: int, pending: int, name: str) -> None:
        self._queue: deque[_Work] = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._limit = threads + pending
        self._admitted = 0
        for index in range(threads):
            threading.Thread(target=self._run, name=f"{name}-{index}", daemon=True).start()

    def try_admit(self) -> _Admission | None:
        with self._condition:
            if self._closed or self._admitted >= self._limit:
                return None
            self._admitted += 1
            return _Admission(self)

    def release(self) -> None:
        with self._condition:
            self._admitted -= 1

    def submit(self, work: _Work) -> None:
        with self._condition:
            self._queue.append(work)
            self._condition.notify()

    def reclaim(self, work: _Work) -> bool:
        with self._condition:
            try:
                self._queue.remove(work)
            except ValueError:
                return False
            return True

    def queued(self) -> int:
        with self._condition:
            return len(self._queue)

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            while self._queue:
                work = self._queue.popleft()
                work.cancellation.set()
                work.admission.release()
                work.deliver(work.future.cancel)
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                work = self._queue.popleft()
            work.run()
            del work


class _Work:
    """One copied request context and its eventual response, owned by one slot."""

    def __init__(self, admission: _Admission, produce: Callable[[threading.Event], Response]):
        self.admission = admission
        self.produce = produce
        self.context = contextvars.copy_context()
        self.cancellation = threading.Event()
        self.loop = asyncio.get_running_loop()
        self.future: asyncio.Future[Response] = self.loop.create_future()

    def run(self) -> None:
        try:
            if not self.cancellation.is_set():
                response = self.context.run(self._produce)
                self.deliver(lambda: self.settle(response))
        except BaseException as error:
            self.deliver(lambda error=error: self.settle(error))
        finally:
            # A running producer may still be unwinding after its client leaves.
            # It keeps its slot until it physically ends, bounding all retained work.
            self.admission.release()

    def _produce(self) -> Response:
        with cancellable_git_commands(self.cancellation):
            return self.produce(self.cancellation)

    def deliver(self, callback: Callable[[], object]) -> None:
        try:
            self.loop.call_soon_threadsafe(callback)
        except RuntimeError:
            # An abandoned request can close its loop before its worker ends.
            return

    def settle(self, value: Response | BaseException) -> None:
        if not self.future.done():
            if isinstance(value, BaseException):
                self.future.set_exception(value)
            else:
                self.future.set_result(value)

    def abandon(self, reads: _BoundedReads) -> None:
        self.cancellation.set()
        if reads.reclaim(self):
            self.admission.release()
        self.future.cancel()


async def _read_off_the_loop(
    request: Request,
    reads: _BoundedReads,
    produce: Callable[[threading.Event], Response],
) -> Response:
    admission = reads.try_admit()
    if admission is None:
        return Response(status_code=503, headers={"Retry-After": "1"})
    work = _Work(admission, produce)
    reads.submit(work)
    try:
        while not work.future.done():
            await asyncio.wait((work.future,), timeout=DISCONNECT_POLL_SECONDS)
            if await request.is_disconnected():
                work.abandon(reads)
                return Response(status_code=499)
        return await work.future
    except asyncio.CancelledError:
        work.abandon(reads)
        raise
