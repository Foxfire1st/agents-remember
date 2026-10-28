"""One observation per open of the review page, shared by that open's reads as a snapshot (D569).

The page's opening makes six reads of one comparison, concurrently, and each of them stamped the
comparison's inputs for itself, several times over: the comparison's input witness, the answer
reuse's observation before and after, the disk identity. The page now sends one id with the reads
of one open (:data:`OPEN_HEADER`, a fresh id per open), and the server observes each input once
per id: the first read of the open to ask for an input's stamp observes it, and the open's other
reads -- concurrently asking ones wait for it -- take that stamp. The developer's binding:

* **one observation per open, never kept across opens.** An entry lives from the open's first read
  until the request the page marked as the open's last read ends (:meth:`OpenObservations.end`);
  a read with the same id after that observes afresh, so the next open -- or a read the
  page makes later -- sees a change made in between;
* **a read without an id stamps for itself**, exactly as before;
* **reads are never grouped by time**: only the page's id joins them;
* **capacity evicts, and is counted.** At most :data:`OPEN_CAPACITY` opens are held; when one is
  evicted while its reads still run, they observe again under a new entry rather than borrowing
  another open's -- the one way an open stamps an input twice, counted in ``evicted``.

Only the review's comparison observations take part (``ChangesetCache.open_snapshot``). The
changeset cache's own admission keeps its fresh re-observation, and the watch-event build runs in
no open, so a change landing mid-open is still captured and built.

**An open names its leaf once** (:meth:`OpenObservations.bind_opened`): the first read of each open
tells the leaf it reads to the bound callback -- the leaf build captures it at once and the open's
reads join that build (D570). A request without the open's header, the stream's included, names
nothing.

**An open is served whole (D585).** From its first read the open is held
(:meth:`~agents_remember.kernel.read_cancellation.PageReads.hold`), so the leaf's background
preparation waits through the gaps between its reads as well as during them. The hold follows the
page, not its requests -- between two of its reads no request exists, whether the page is waiting
for the first wave or has gone -- so the page names itself (:data:`PAGE_HEADER` on the opening
reads, ``page`` on its ``/api/stream``) and the hold is released on whichever comes first:

* **the open's normal end** (:meth:`OpenObservations.end`);
* **a new open of the same page**, for any leaf: it supersedes, so navigating ends the old one;
* **the page's last stream closing** (:meth:`OpenObservations.page_stream`): the page left;
* and, as the table's own backstop, **eviction** at capacity and :meth:`OpenObservations.clear`.

An open that never ends with its page still there (one of its six reads never sent) stays held;
the background's own deadline bounds that, counted and logged
(``review_overview_precompute.DETAIL_OPEN_WAIT_SECONDS``).
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from agents_remember.kernel.read_cancellation import PAGE_READS, PageReads

__all__ = [
    "LAST_MARK",
    "OPEN_CAPACITY",
    "OPEN_HEADER",
    "OPEN_OBSERVATIONS",
    "PAGE_HEADER",
    "OpenObservations",
    "ReviewOpenMiddleware",
    "answer_outcomes",
    "install_review_open",
    "note_answer",
    "note_outcome",
    "open_stamp",
]

logger = logging.getLogger(__name__)

#: The header the page's opening reads carry, naming their open; the open's last read adds
#: ``; last`` (dashboard data/reviewOpen.ts).
OPEN_HEADER: Final = "x-review-open"
LAST_MARK: Final = "last"
#: The header the page's opening reads carry, naming the page (one id per page load); the page's
#: ``/api/stream`` names the same id in its ``page`` query (dashboard data/reviewOpen.ts).
PAGE_HEADER: Final = "x-review-page"
#: How many opens are held at once; the oldest is evicted beyond this, and counted.
OPEN_CAPACITY: Final = 32
#: The longest open id admitted; a longer header is no open.
MAX_OPEN_ID: Final = 64
#: The explainer's ``/stale`` read names its leaf in its path, the review reads in their query.
_EXPLAINER_LEAF = re.compile(r"/api/explainer/([^/]+)/([^/]+)/([^/]+)/stale$")

type Leaf = tuple[str, str, str]


@dataclass
class _Open:
    """One open's stamps: each input observed once, and what its reads asked of it."""

    open_id: str
    page: str | None = None
    stamps: dict[Hashable, Future[str | None]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    observed: int = 0
    shared: int = 0
    reads: int = 0
    active: int = 0
    ended: bool = False
    #: Each answer the open's reads asked for: its name, how it was had, and how long that took.
    answers: list[tuple[str, str, float]] = field(default_factory=list)

    def leave(self) -> bool:
        """One read of the open finished; True when the open ended and this was its last read."""

        with self.lock:
            self.active -= 1
            return self.ended and self.active == 0

    def close(self) -> bool:
        """The open ended; True when no read of it is still running."""

        with self.lock:
            self.ended = True
            return self.active == 0

    def stamp(self, source: Hashable, observe: Callable[[], str | None]) -> str | None:
        with self.lock:
            held = self.stamps.get(source)
            if held is None:
                owned: Future[str | None] = Future()
                self.stamps[source] = owned
                self.observed += 1
            else:
                self.shared += 1
        if held is not None:
            try:
                return held.result()
            except BaseException:  # the observing read failed or was cancelled: never shared
                with self.lock:
                    self.shared -= 1
                return self.stamp(source, observe)
        try:
            value = observe()
        except BaseException as error:
            with self.lock:
                self.stamps.pop(source, None)  # forgotten before a waiter can look again
            owned.set_exception(error)
            raise
        owned.set_result(value)
        return value


_current: ContextVar[_Open | None] = ContextVar("review_open", default=None)


def open_stamp(source: Hashable, observe: Callable[[], str | None]) -> str | None:
    """This open's one stamp of ``source``; a read in no open observes for itself."""

    opened = _current.get()
    if opened is None:
        return observe()
    return opened.stamp(source, observe)


class OpenObservations:
    """The process's open entries, bounded by :data:`OPEN_CAPACITY`, the oldest evicted."""

    def __init__(self, capacity: int = OPEN_CAPACITY, reads: PageReads = PAGE_READS) -> None:
        self.capacity = capacity
        self._opens: OrderedDict[str, _Open] = OrderedDict()
        self._lock = threading.Lock()
        self.evicted = 0
        self._opened: Callable[[Leaf], None] | None = None
        self._reads = reads
        self._page_opens: dict[str, str] = {}  # each page's open being served
        self._page_streams: dict[str, int] = {}  # each page's connected streams

    def bind_opened(self, opened: Callable[[Leaf], None] | None) -> None:
        """Tell ``opened`` the leaf of each open, from its first read (none: tell nobody)."""

        self._opened = opened

    def entry(self, open_id: str) -> _Open:
        """The open's entry, made by its first read."""

        return self._entry(open_id)[0]

    def _entry(self, open_id: str, page: str | None = None) -> tuple[_Open, bool]:
        """The open's entry, and whether this read made it (the open's first read), held."""

        with self._lock:
            held = self._opens.get(open_id)
            if held is not None:
                return held, False
            made = self._opens[open_id] = _Open(open_id, page)
            self._reads.hold(open_id)
            superseded = self._page_opens.get(page) if page is not None else None
            if page is not None:
                self._page_opens[page] = open_id
            while len(self._opens) > self.capacity:
                _evicted_id, evicted = self._opens.popitem(last=False)
                self._forget(evicted)
                self.evicted += 1
                logger.info(
                    "review open %s evicted at capacity %d after %d reads (%d evicted so far); "
                    "its later reads observe again",
                    evicted.open_id,
                    self.capacity,
                    evicted.reads,
                    self.evicted,
                )
        if superseded is not None and superseded != open_id:
            self._end(superseded, "a new open of its page superseded it")
        return made, True

    def _forget(self, opened: _Open) -> None:
        """Release the open's hold and its page's claim on it (under the lock)."""

        self._reads.release(opened.open_id)
        if opened.page is not None and self._page_opens.get(opened.page) == opened.open_id:
            del self._page_opens[opened.page]

    def end(self, open_id: str) -> bool:
        """The page's reads of this open are done: forget its stamps. False when none was held."""

        return self._end(open_id, None)

    def _end(self, open_id: str, why: str | None) -> bool:
        with self._lock:
            ended = self._opens.pop(open_id, None)
            if ended is not None:
                self._forget(ended)
        if ended is None:
            return False
        if why is not None:
            logger.info("review open %s released: %s", open_id, why)
        if ended.close():
            _log_ended(ended)
        return True

    def held(self) -> int:
        with self._lock:
            return len(self._opens)

    def clear(self) -> None:
        with self._lock:
            for opened in self._opens.values():
                self._forget(opened)
            self._opens.clear()
            self._page_streams.clear()
            self.evicted = 0

    @contextmanager
    def page_stream(self, page: str | None) -> Iterator[None]:
        """One stream of the page ``page`` is connected; when its last one closes, the page left
        and its open being served is released."""

        if not page or len(page) > MAX_OPEN_ID:
            yield
            return
        with self._lock:
            self._page_streams[page] = self._page_streams.get(page, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._page_streams[page] - 1
                if left:
                    self._page_streams[page] = left
                else:
                    del self._page_streams[page]
                served = None if left else self._page_opens.get(page)
            if served is not None:
                self._end(served, "its page's stream closed")

    @contextmanager
    def reading(
        self, open_id: str | None, leaf: Leaf | None = None, page: str | None = None
    ) -> Iterator[None]:
        """One request of the open ``open_id`` (none: the request observes for itself) of the page
        ``page`` (none: an open no page names, released by its end or eviction only).

        The open's first read tells the bound callback the ``leaf`` it reads.
        """

        if not open_id:
            yield
            return
        opened, first = self._entry(open_id, page)
        if first and leaf is not None:
            self._tell_opened(leaf)
        with opened.lock:
            opened.reads += 1
            opened.active += 1
        token = _current.set(opened)
        try:
            yield
        finally:
            _current.reset(token)
            if opened.leave():
                _log_ended(opened)

    def _tell_opened(self, leaf: Leaf) -> None:
        told = self._opened
        if told is None:
            return
        try:
            told(leaf)
        except Exception:  # the open's reads never fail for it; they compute as before
            logger.exception("telling the leaf build of the open of %s failed", "/".join(leaf))


def _log_ended(opened: _Open) -> None:
    """The open's count and its answers, once it ended and its last read finished."""

    with opened.lock:
        answers = ", ".join(f"{name} {how} {spent:.2f}s" for name, how, spent in opened.answers)
    logger.info(
        "review open %s ended: %d reads, %d inputs observed, %d stamps shared; answers: %s",
        opened.open_id,
        opened.reads,
        opened.observed,
        opened.shared,
        answers or "none",
    )


_outcomes: ContextVar[list[str] | None] = ContextVar("review_answer_outcomes", default=None)


@contextmanager
def answer_outcomes() -> Iterator[list[str]]:
    """Collect how the answers asked for in here were had (``read``, ``restored``, ``computed``)."""

    held: list[str] = []
    token = _outcomes.set(held)
    try:
        yield held
    finally:
        _outcomes.reset(token)


def note_outcome(outcome: str) -> None:
    """One answer was had this way, for the :func:`answer_outcomes` collecting around it."""

    held = _outcomes.get()
    if held is not None:
        held.append(outcome)


def note_answer(name: str, outcome: str, seconds: float) -> None:
    """One answer of the current open: read, restored, computed or joined, and in how long."""

    opened = _current.get()
    if opened is not None:
        with opened.lock:
            opened.answers.append((name, outcome, seconds))


OPEN_OBSERVATIONS: Final = OpenObservations()


def _open_of(scope: Scope) -> tuple[str | None, bool]:
    """The request's open id, and whether the page marked it the open's last read."""

    for name, value in scope.get("headers") or ():
        if name == OPEN_HEADER.encode():
            open_id, _, mark = value.decode("latin-1").partition(";")
            open_id = open_id.strip()
            if 0 < len(open_id) <= MAX_OPEN_ID:
                return open_id, mark.strip() == LAST_MARK
    return None, False


def _page_of(scope: Scope) -> str | None:
    """The page an opening read names, if any."""

    for name, value in scope.get("headers") or ():
        if name == PAGE_HEADER.encode():
            page = value.decode("latin-1").strip()
            if 0 < len(page) <= MAX_OPEN_ID:
                return page
    return None


def _leaf_of(scope: Scope) -> Leaf | None:
    """The leaf an opening read reads: its query's task context, or the explainer path's."""

    path = scope.get("path") or ""
    named = _EXPLAINER_LEAF.search(path)
    if named is not None:
        repository, master, leaf = (unquote(part) for part in named.groups())
        return repository, master, leaf
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    values = [query.get(name, [""])[0] for name in ("repo", "master", "leaf")]
    return (values[0], values[1], values[2]) if all(values) else None


class ReviewOpenMiddleware:
    """Bind a request that names its open to that open's observations, for the request's life.

    When the page marked the request as its open's last read, the open ends once that request has
    ended. Reads of the open still running keep the stamps they hold; no later read names the id.
    """

    def __init__(self, app: ASGIApp, observations: OpenObservations = OPEN_OBSERVATIONS) -> None:
        self._app = app
        self._observations = observations

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        open_id, last = _open_of(scope) if scope["type"] == "http" else (None, False)
        leaf = _leaf_of(scope) if open_id is not None else None
        page = _page_of(scope) if open_id is not None else None
        try:
            with self._observations.reading(open_id, leaf, page):
                await self._app(scope, receive, send)
        finally:
            if open_id is not None and last:
                self._observations.end(open_id)


def install_review_open(app: FastAPI, observations: OpenObservations = OPEN_OBSERVATIONS) -> None:
    """Bind every request that names a review open to that open's observations."""

    app.add_middleware(ReviewOpenMiddleware, observations=observations)
