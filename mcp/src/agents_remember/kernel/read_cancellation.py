"""A read whose client went away stops computing (D569, mean 4).

The serving layer binds one cancellation token around the blocking work of one HTTP read, in the
context that is copied into its worker thread. The long computations check it at their safe points
-- before each Git child the one runner launches (and while that child runs), in the per-entry loops
and while a reviewer worklist child computes -- and raise :class:`ReadCancelled` once it is set.

**Exact, not lossy.** :class:`ReadCancelled` is a :class:`BaseException`, like asyncio's own
``CancelledError``: every ``except Exception`` that turns a failed read into an ``unavailable``
answer, a ``stale``/``unverifiable`` verdict or a refusal lets it pass, so a cancelled read answers
nothing, keeps nothing and writes nothing. Outside a bound token every check is a no-op and the
computations behave exactly as before.

**Shared flights.** Identical concurrent reads share one computation (:class:`SharedFlights`). Such
a computation is not its first asker's: it runs under its *flight's* token, which is set only when
every asker of the flight -- the owner's client and each waiter's -- is gone. A waiter whose own
client leaves stops waiting by itself; an owner whose client leaves keeps computing for the waiters
that remain. A flight whose every asker left is never joined again: a later asker starts its own.

**A superseded build.** A leaf's background build is cancelled by a newer change in a softer way
(:class:`BuildToken`): *stale*, it reads as set at every safe point -- so it stops there and keeps
nothing -- but a reviewer worklist child it is waiting for is let land (:func:`lets_children_land`),
so the child stays warm for the build that replaces it. Only a *stopped* build kills its child.

**Page reads first.** Background work that must never lengthen a page read (a build's detail
preparation, D574) watches :data:`PAGE_READS`: the serving layer counts every read it is answering
(:meth:`PageReads.answering`), and :class:`YieldsToPageReads` reads as set while one is, so that
work stops at its next safe point and starts again once the reads are answered.

**A page open is served whole** (D585). An open's reads come in waves, and between two of them no
read is being answered; work started in that gap would share the process with the next wave. So
the serving layer also holds each page open it is serving (:meth:`PageReads.hold`), from the open's
first read until the open ends, its page leaves or the page opens again
(``serving/review_open_observation``), and the background waits while one is held. Only background
work waits: a page read never waits on a hold.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Condition, Event, Lock
from typing import Final, Protocol

__all__ = [
    "PAGE_READS",
    "BuildToken",
    "CancellationToken",
    "PageReads",
    "ReadCancelled",
    "SharedFlights",
    "YieldsToPageReads",
    "cancellable_read",
    "lets_children_land",
    "raise_if_read_cancelled",
    "read_cancellation",
]

# How often a waiter of a shared flight looks at its own client's token. A waiter holds no
# process and no lock while it waits; this only bounds how late it notices that it may leave.
WAITER_POLL_SECONDS: Final = 0.05


class ReadCancelled(BaseException):
    """The read's every asker is gone: nothing is answered, kept or written for it.

    Deliberately not an :class:`Exception`, so no read-failure handler can catch it.
    """


class CancellationToken(Protocol):
    """Anything whose ``is_set`` says the read's askers are gone (a ``threading.Event`` is one)."""

    def is_set(self) -> bool: ...


class BuildToken:
    """A background build's token: *stale* once a newer change supersedes it, *stopped* at shutdown.

    Either reads as set at every safe point. A stale build lets a worklist child it is waiting for
    land, and its answer is discarded at the next safe point; a stopped build stops the child too.
    """

    def __init__(self) -> None:
        self._stale = Event()
        self._stopped = Event()

    def mark_stale(self) -> None:
        self._stale.set()

    def stop(self) -> None:
        self._stopped.set()

    @property
    def stale(self) -> bool:
        return self._stale.is_set()

    def is_set(self) -> bool:
        return self._stale.is_set() or self._stopped.is_set()

    def lets_children_land(self) -> bool:
        return self._stale.is_set() and not self._stopped.is_set()


class PageReads:
    """How many page reads this process is answering now, and the page opens it is serving."""

    def __init__(self) -> None:
        self._answering = 0
        self._held: dict[Hashable, float] = {}  # each open served, and since when (monotonic)
        self._changed = Condition()

    @contextmanager
    def answering(self) -> Iterator[None]:
        """Within the block, one page read is being answered."""

        with self._changed:
            self._answering += 1
        try:
            yield
        finally:
            with self._changed:
                self._answering -= 1
                self._changed.notify_all()

    def hold(self, key: Hashable) -> None:
        """One page open is being served until :meth:`release` of the same ``key``."""

        with self._changed:
            self._held.setdefault(key, time.monotonic())

    def release(self, key: Hashable) -> None:
        with self._changed:
            if self._held.pop(key, None) is not None:
                self._changed.notify_all()

    def held(self) -> dict[Hashable, float]:
        """The opens being served, each with how long ago it was held (seconds)."""

        now = time.monotonic()
        with self._changed:
            return {key: now - since for key, since in self._held.items()}

    def busy(self, *, opens: bool = True) -> bool:
        """A page read is being answered, or (with ``opens``) a page open is being served."""

        return self._answering > 0 or (opens and bool(self._held))

    def wait_answered(self, timeout: float, *, opens: bool = True) -> bool:
        """Whether nothing :meth:`busy` names is under way, waiting at most ``timeout`` for that."""

        with self._changed:
            if self.busy(opens=opens):
                self._changed.wait(timeout)
            return not self.busy(opens=opens)


#: The process's page reads, counted by the serving layer around each read it answers.
PAGE_READS: Final = PageReads()


class YieldsToPageReads:
    """Background work's token: set while ``build`` is, while a page read is being answered or a
    page open served (unless ``opens`` is off), or while ``waiting`` says other work waits its turn.

    The work it binds stops at its next safe point whenever a page read or open arrives. A
    worklist child it waits for is let land unless the build itself was stopped, so the child stays
    warm.
    """

    def __init__(
        self,
        build: BuildToken | None,
        waiting: Callable[[], bool] = lambda: False,
        reads: PageReads = PAGE_READS,
        *,
        opens: bool = True,
    ) -> None:
        self._build = build
        self._waiting = waiting
        self._reads = reads
        self._opens = opens

    def is_set(self) -> bool:
        return self.must_end() or self._reads.busy(opens=self._opens)

    def past_opens(self) -> YieldsToPageReads:
        """The same token, set by page reads only: for work that waited out an open's deadline."""

        return YieldsToPageReads(self._build, self._waiting, self._reads, opens=False)

    def must_end(self) -> bool:
        """Whether the work ends rather than waits: its build is superseded or stopped, or other
        work waits its turn."""

        return (self._build is not None and self._build.is_set()) or self._waiting()

    def lets_children_land(self) -> bool:
        build = self._build
        return build is None or not build.is_set() or build.lets_children_land()


def lets_children_land(token: CancellationToken | None) -> bool:
    """Whether a set ``token`` still waits for a child it started: only a stale build's does."""

    lands = getattr(token, "lets_children_land", None)
    return bool(callable(lands) and lands())


_READ_CANCELLATION: ContextVar[CancellationToken | None] = ContextVar(
    "read_cancellation", default=None
)


@contextmanager
def cancellable_read(token: CancellationToken | None) -> Iterator[None]:
    """Within the block, the safe points raise :class:`ReadCancelled` once ``token`` is set."""

    bound = _READ_CANCELLATION.set(token)
    try:
        yield
    finally:
        _READ_CANCELLATION.reset(bound)


def read_cancellation() -> CancellationToken | None:
    """The token of the read this context computes for, or ``None`` outside a cancellable read."""

    return _READ_CANCELLATION.get()


def raise_if_read_cancelled() -> None:
    """One safe point: stop here when the read's askers are gone."""

    token = _READ_CANCELLATION.get()
    if token is not None and token.is_set():
        raise ReadCancelled


class _Flight[T]:
    """One shared computation and the tokens of everyone who asked for it.

    It is itself a token: set once every asker's token is set. An asker with no token (a caller
    outside a cancellable read) can never leave, so its flight is never cancelled. The answer is
    latched, so a flight once gone stays gone and :meth:`join` refuses it.
    """

    def __init__(self, asker: CancellationToken | None) -> None:
        self._lock = Lock()
        self._askers: list[CancellationToken | None] = [asker]
        self._gone = False
        self.future: Future[T] = Future()

    def is_set(self) -> bool:
        with self._lock:
            return self._everyone_left()

    def lets_children_land(self) -> bool:
        """Everyone left, and one of them was a stale build: its child is let land (D569)."""

        with self._lock:
            return self._everyone_left() and any(
                lets_children_land(asker) for asker in self._askers
            )

    def join(self, asker: CancellationToken | None) -> bool:
        """Add one asker, unless everyone already left (then the caller starts its own)."""

        with self._lock:
            if self._everyone_left():
                return False
            self._askers.append(asker)
            return True

    def _everyone_left(self) -> bool:
        if not self._gone:
            self._gone = all(asker is not None and asker.is_set() for asker in self._askers)
        return self._gone

    def wait(self, asker: CancellationToken | None) -> T:
        """The owner's answer; :class:`ReadCancelled` as soon as this waiter's own client left."""

        if asker is None:
            return self.future.result()
        while True:
            try:
                return self.future.result(timeout=WAITER_POLL_SECONDS)
            except TimeoutError:
                if asker.is_set():
                    raise ReadCancelled from None


class SharedFlights[K, T]:
    """At most one computation per key in flight; the others who ask wait for its answer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._flights: dict[K, _Flight[T]] = {}

    def share(self, key: K, produce: Callable[[], T]) -> tuple[T, bool]:
        """The answer for ``key`` and whether this caller computed it (``False``: a waiter's).

        The owner runs ``produce`` under the flight's token, never under its own client's, so its
        client leaving stops nothing while a waiter remains. A waiter told :class:`ReadCancelled`
        while its own client is still here was not asked about it: it asks again, and computes.
        """

        asker = read_cancellation()
        while True:
            with self._lock:
                flight = self._flights.get(key)
                owner = flight is None or not flight.join(asker)
                if owner:
                    flight = self._flights[key] = _Flight(asker)
            assert flight is not None
            if owner:
                return self._own(key, flight, produce, cancellable=asker is not None), True
            try:
                return flight.wait(asker), False
            except ReadCancelled:
                if asker is not None and asker.is_set():
                    raise

    def _own(self, key: K, flight: _Flight[T], produce: Callable[[], T], *, cancellable: bool) -> T:
        # An owner outside any cancellable read never leaves, so its flight can never be
        # cancelled: it computes exactly as an uncancellable caller always did.
        try:
            with cancellable_read(flight if cancellable else None):
                value = produce()
        except BaseException as error:
            flight.future.set_exception(error)
            raise
        else:
            flight.future.set_result(value)
            return value
        finally:
            with self._lock:
                if self._flights.get(key) is flight:
                    del self._flights[key]
