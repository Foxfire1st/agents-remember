"""The leaf-wide view of a leaf's newest trees, built before the page asks for it (D569).

Every change source of a leaf ends in one build, off the open:

* **a turn end**: the moved watcher captures the leaf's four trees when one of its agents ends a
  turn (Git objects, no pin and no record) and hands the capture on (:meth:`offer`);
* **a watched change**: the process's worktree watch reports an edit in the leaf's code or memory
  worktree (:meth:`changed`). The leaf waits for a quiet moment (:data:`QUIET_SECONDS` without a
  further change) and never longer than :data:`CEILING_SECONDS` after the first change it waits
  for, so continuous editing still builds; then its trees are captured.
* **a page open**: the review page opening a leaf (:meth:`opened`, from the open's first read)
  captures it at once, skipping the quiet moment, so a build starts and the open's reads join it
  instead of computing beside it (D570). A stream open or reopen is not a page open: it captures
  nothing.

A capture whose trees are the leaf's latest recorded comparison mints nothing, and is built unless
those trees are built, being built or waiting already: another reader may have minted them a moment
ago and computed nothing -- the page's open, or the detail queue's live review, whose witness
captures the working views -- and each change is built (D571). Otherwise :class:`OverviewPrecompute` computes the
leaf-wide worklist for exactly those trees -- the page's own computation
(:func:`~.review_tree_knowledge._leaf_wide_parts`) in reviewer-worklist children of lowered priority
-- and the page's later comparison of the same trees finds it in the leaf-view memo under the same
key (:func:`~.review_leaf_view_memo.leaf_view_key`, which no comparison number enters). The worker
returns the view and this process keeps it by the memo's own rule; nothing that owns a stream, a
watch or a registration runs in the worker. A page that opens while the build runs joins it.

**One build at a time, at most one waiting per leaf, the latest wins.** A newer capture of a leaf
replaces the one waiting and supersedes the one being built. A superseded build is *stale*
(:class:`~agents_remember.kernel.read_cancellation.BuildToken`): a worklist child it is waiting for
lands and stays warm for the next build, and the build stops at its next safe point, publishing and
storing nothing, so none of its answers can be served under a later number. The supersession is
logged with both trees so it can never later read as a fast build.

**Then each changed invariant's detail (D574).** Once the opening answers are kept -- the open's
bar, and all a joining open waits for -- the build prepares the detail of each invariant with
changed locations, one by one, in the order :meth:`OverviewPrecompute.bind_details` names: the
two reads a click on it makes, through the same ports, so the click finds the answer kept or
joins its flight and nothing is computed twice. The queue never lengthens a page read: it starts
no item while one is being answered, and an item running when one arrives stops at its next safe
point (:class:`~agents_remember.kernel.read_cancellation.YieldsToPageReads`) and is asked again
once the reads are answered -- a click on that invariant computes it at once. An item stopped
:data:`DETAIL_YIELDS` times is left for a click and the queue moves on, so it always ends. A newer capture, a
shutdown or another leaf's build waiting ends the queue; whatever it has not prepared, a click
computes. The queue logs its own line, with its count.

**A page open is served whole (D585).** The queue also yields to each page open the serving layer
holds (:meth:`~agents_remember.kernel.read_cancellation.PageReads.hold`), between its reads as
during them: no item starts, and the one running stops, until the open is released. The wait is
bounded: an item that waited :data:`DETAIL_OPEN_WAIT_SECONDS` for opens is asked although one is
still held, then yielding to page reads only. Each such item is counted (``waited_out``) and logged
with the opens it waited for, so a hold that outlives its page is a visible fault.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from agents_remember.application.review_candidate_resolution import recorded_leaf_contract
from agents_remember.application.review_tree_comparison import drafted_review_trees
from agents_remember.application.review_tree_knowledge import _leaf_wide_parts
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.read_cancellation import (
    PAGE_READS,
    WAITER_POLL_SECONDS,
    BuildToken,
    ReadCancelled,
    YieldsToPageReads,
    cancellable_read,
    raise_if_read_cancelled,
)
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.explainer_watcher import LeafSelection

__all__ = [
    "BUILD_NICENESS",
    "CEILING_SECONDS",
    "DETAIL_OPEN_WAIT_SECONDS",
    "DETAIL_YIELDS",
    "QUIET_SECONDS",
    "OverviewPrecompute",
]

logger = logging.getLogger(__name__)

#: A watched leaf is captured once no further change arrived for this long ...
QUIET_SECONDS: Final = 2.0
#: ... and at the latest this long after the first change it waited for.
CEILING_SECONDS: Final = 15.0
#: The niceness of the build's worklist children.
BUILD_NICENESS: Final = 10
#: How many times one detail is stopped for a page read before the queue leaves it for a click.
DETAIL_YIELDS: Final = 3
#: How long one detail waits for held page opens before it is asked anyway (D585): longer than
#: the slowest open measured (a from-scratch open, ~30 s), so it bounds a hold, never an open.
DETAIL_OPEN_WAIT_SECONDS: Final = 30.0

type _Key = tuple[str, str, str]
type Capture = Callable[[LeafSelection], ReviewTreeComparisonRecord | None]
type Latest = Callable[[LeafSelection], ReviewTreeComparisonRecord | None]
#: Mint a captured draft's comparison number as the page's own open mints it.
type Mint = Callable[[LeafSelection, ReviewTreeComparisonRecord], ReviewTreeComparisonRecord | None]
#: Compute every answer the page opens with, for one minted comparison number.
#: The page's own answers for a comparison number; the third argument prepares the leaf-wide
#: view, which the answers call where their order puts it.
type Answers = Callable[[LeafSelection, int, Callable[[], None]], None]
#: The details to prepare for a comparison number, in their order, given the leaf's previous built
#: number: each invariant's key, the reads a click on it makes, and whether the move from the
#: previous number changed it (those come first).
type Details = Callable[
    [LeafSelection, int, int | None], Sequence[tuple[str, Callable[[], None], bool]]
]


def trees_digest(record: ReviewTreeComparisonRecord) -> str:
    """A short name of a comparison's four trees (and converted base), for the build's log."""

    converted = record.converted_base.tree if record.converted_base is not None else ""
    named = "/".join(
        (
            record.code_base.tree,
            record.code_candidate.tree,
            record.memory_base.tree,
            record.memory_candidate.tree,
            converted,
        )
    )
    return hashlib.sha256(named.encode()).hexdigest()[:12]


@dataclass
class _Building:
    selection: LeafSelection
    record: ReviewTreeComparisonRecord
    token: BuildToken = field(default_factory=BuildToken)


@dataclass
class _Waiting:
    selection: LeafSelection
    first: float
    last: float


@dataclass(frozen=True)
class _PageOrder:
    """The order one of a page's opens drew a comparison's invariants in (D585)."""

    number: int
    open_id: str | None
    keys: tuple[str, ...]


type _Item = tuple[str, Callable[[], None], bool]


class OverviewPrecompute:
    """Build the newest trees of each changed leaf, one at a time, off the request path."""

    def __init__(  # noqa: PLR0913 -- the build's three ports and its three clock bounds
        self,
        config: McpRuntimeConfig,
        processes: ReviewerWorklistProcesses,
        *,
        capture: Capture | None = None,
        latest: Latest | None = None,
        mint: Mint | None = None,
        quiet: float = QUIET_SECONDS,
        ceiling: float = CEILING_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._processes = processes
        self._capture = capture
        self._latest = latest
        self._mint = mint
        self._answers: Answers | None = None
        self._details: Details | None = None
        self._quiet = quiet
        self._ceiling = ceiling
        self._clock = clock
        self._changed = threading.Condition()
        self._pending: dict[_Key, tuple[LeafSelection, ReviewTreeComparisonRecord]] = {}
        self._waiting: dict[_Key, _Waiting] = {}
        self._building: _Building | None = None
        self._built: dict[_Key, str] = {}
        self._built_numbers: dict[_Key, int] = {}
        self._stopped = False
        self._threads: list[threading.Thread] = []
        self._orders: dict[_Key, _PageOrder] = {}  # each leaf's latest drawn order
        self.superseded = 0
        self.waited_out = 0  # details asked after waiting DETAIL_OPEN_WAIT_SECONDS for opens
        self.reordered = 0  # details a page's order moved in a queue already under way

    # -- change sources ---------------------------------------------------------------------------

    def bind_answers(self, answers: Answers) -> None:
        """The page's own answers for a comparison number, each where its order puts it."""

        self._answers = answers

    def bind_details(self, details: Details) -> None:
        """The details the build prepares after the opening answers, in their order (D574)."""

        self._details = details

    def offer(self, selection: LeafSelection, record: ReviewTreeComparisonRecord) -> None:
        """Take one capture of a leaf's trees (a turn end's): mint its number, then build it.

        The number is minted at capture, as the page's open would mint it, so one identity holds;
        trees the leaf's latest record already holds mint nothing.
        """

        minted = self._minted(selection, record)
        if minted is None:
            return
        with self._changed:
            if self._stopped:
                return
            self._admit(selection, minted)

    def _minted(
        self, selection: LeafSelection, record: ReviewTreeComparisonRecord
    ) -> ReviewTreeComparisonRecord | None:
        if self._mint is None:
            return record
        try:
            return self._mint(selection, record)
        except Exception:  # a mint never takes the dashboard down; the page's open mints
            logger.exception("minting the captured comparison failed for %s", selection.leaf_id)
            return None

    def changed(self, selection: LeafSelection) -> None:
        """A watched edit in the leaf's worktrees: capture it at its next quiet moment."""

        now = self._clock()
        with self._changed:
            if self._stopped:
                return
            waiting = self._waiting.get(selection.as_key())
            if waiting is None:
                self._waiting[selection.as_key()] = _Waiting(selection, now, now)
            else:
                waiting.last = now
            self._start()
            self._changed.notify_all()

    def opened(self, selection: LeafSelection) -> None:
        """A page open of the leaf: capture it now, without waiting for a quiet moment.

        The interactive trigger of the two: a watched change waits for quiet (:meth:`changed`); an
        open does not wait, so its trees are built while its own reads join the build. Trees the
        leaf's latest record or a build already holds mint nothing and build nothing.
        """

        with self._changed:
            if self._stopped:
                return
            key = selection.as_key()
            now = self._clock()
            waiting = self._waiting.get(key)
            due = now - max(self._quiet, self._ceiling)
            if waiting is None:
                self._waiting[key] = _Waiting(selection, due, due)
            else:
                waiting.first, waiting.last = due, due
            self._start()
            self._changed.notify_all()

    def order(
        self, selection: LeafSelection, number: int, open_id: str | None, keys: Sequence[str]
    ) -> None:
        """A page drew comparison ``number``'s invariants in ``keys``' order: its details are
        prepared in that order, by a queue under way too for what it has left (D585). The page's
        order is the order; the queue's own is kept only for what the page did not draw."""

        with self._changed:
            if not self._stopped:
                self._orders[selection.as_key()] = _PageOrder(number, open_id, tuple(keys))

    def shutdown(self) -> None:
        """Take nothing further; a build in flight is stopped, its child too, and keeps nothing."""

        with self._changed:
            self._stopped = True
            self._pending.clear()
            self._waiting.clear()
            self._orders.clear()
            if self._building is not None:
                self._building.token.stop()
            self._changed.notify_all()

    # -- the two loops ------------------------------------------------------------------------------

    def _admit(self, selection: LeafSelection, record: ReviewTreeComparisonRecord) -> None:
        """Under the lock: keep ``record`` as the leaf's latest build unless it is held already."""

        key = selection.as_key()
        digest = trees_digest(record)
        building = self._building
        if building is not None and building.selection.as_key() == key:
            if trees_digest(building.record) == digest:
                return
            if not building.token.stale:
                building.token.mark_stale()
                self.superseded += 1
                logger.info(
                    "superseded the leaf-wide build of %s trees %s for its newer trees %s: "
                    "it is stale and keeps nothing",
                    selection.leaf_id,
                    trees_digest(building.record),
                    digest,
                )
        elif self._built.get(key) == digest:
            return
        held = self._pending.get(key)
        if held is not None and trees_digest(held[1]) == digest:
            return
        self._pending[key] = (selection, record)
        self._start()
        self._changed.notify_all()

    def _start(self) -> None:
        if self._threads:
            return
        for target, name in (
            (_niced(self._run_builds), "overview-build"),
            (_niced(self._run_captures), "overview-capture"),
        ):
            # A build's own context, never a request's: the review open that first changed a leaf
            # lends it no observation, so a change landing mid-open is captured afresh and built.
            thread = threading.Thread(
                target=target, name=name, daemon=True, context=contextvars.Context()
            )
            thread.start()
            self._threads.append(thread)

    def _run_captures(self) -> None:
        while True:
            with self._changed:
                due = self._next_due()
                if due is None:
                    return
            try:
                self._captured(due.selection)
            except Exception:  # a capture never takes the dashboard down; the page captures
                logger.exception("the watched capture failed for %s", due.selection.leaf_id)

    def _next_due(self) -> _Waiting | None:
        """Under the lock: the next leaf whose quiet moment (or ceiling) came, waiting till then."""

        while not self._stopped:
            now = self._clock()
            due = None
            wake = None
            for key, waiting in self._waiting.items():
                at = min(waiting.last + self._quiet, waiting.first + self._ceiling)
                if at <= now:
                    due = key
                    break
                wake = at if wake is None else min(wake, at)
            if due is not None:
                return self._waiting.pop(due)
            self._changed.wait(None if wake is None else max(wake - now, 0.0))
        return None

    def _captured(self, selection: LeafSelection) -> None:
        if self._capture is None:
            return
        record = self._capture(selection)
        if record is None:
            return
        latest = self._latest(selection) if self._latest is not None else None
        if latest is not None and latest.same_trees(record):
            # The leaf's latest recorded comparison: nothing is minted, and it is built unless it
            # is built, being built or waiting (:meth:`_admit`). Whoever minted it may have computed
            # nothing: the page's open a moment ago, or the detail queue's live review (D571).
            with self._changed:
                if not self._stopped:
                    self._admit(selection, latest)
            return
        self.offer(selection, record)

    def _run_builds(self) -> None:
        while True:
            with self._changed:
                while not self._pending and not self._stopped:
                    self._changed.wait()
                if self._stopped:
                    return
                key = next(iter(self._pending))
                selection, record = self._pending.pop(key)
                building = self._building = _Building(selection, record)
            started, spent = time.monotonic(), time.thread_time()
            logger.info(
                "started the leaf-wide build of %s trees %s (comparison #%d)",
                selection.leaf_id,
                trees_digest(record),
                record.number,
            )
            try:
                with cancellable_read(building.token):
                    self.compute(selection, record)
            except ReadCancelled:
                logger.info(
                    "the stale leaf-wide build of %s trees %s kept nothing after %.2f s "
                    "(%.2f CPU-s in this thread)",
                    selection.leaf_id,
                    trees_digest(record),
                    time.monotonic() - started,
                    time.thread_time() - spent,
                )
            except Exception:  # a precompute never takes the dashboard down; the page computes
                logger.exception("the overview precompute failed for %s", selection.leaf_id)
            else:
                logger.info(
                    "built the leaf-wide view and answers of %s trees %s (comparison #%d) in "
                    "%.2f s, %.2f CPU-s in this thread%s",
                    selection.leaf_id,
                    trees_digest(record),
                    record.number,
                    time.monotonic() - started,
                    time.thread_time() - spent,
                    " (superseded; finished for an open that joined it)"
                    if building.token.stale
                    else "",
                )
                with self._changed:
                    self._built[key] = trees_digest(record)
                    previous = self._built_numbers.get(key)
                    if not building.token.stale:
                        self._built_numbers[key] = record.number
                if not building.token.stale:
                    self.prepare_details(selection, record, building.token, previous)
            finally:
                with self._changed:
                    self._building = None

    def compute(self, selection: LeafSelection, record: ReviewTreeComparisonRecord) -> None:
        """The page's own leaf-wide computation for the captured trees, kept by the memo's rule."""

        contract = recorded_leaf_contract(
            self._config, selection.repository_id, selection.master, selection.leaf_id
        )
        if contract is None:
            return
        trees = drafted_review_trees(self._config.coordination_root, contract, record)
        if trees is None:
            return

        def leaf_wide() -> None:
            _leaf_wide_parts(contract, trees, processes=self._processes)
            raise_if_read_cancelled()  # a superseded build computes and keeps no answer

        if self._answers is None:
            leaf_wide()
        else:
            # The answers that need no leaf-wide view come first, so an open joins them at once
            # (D569); each is cancellable at its safe points.
            self._answers(selection, record.number, leaf_wide)

    def prepare_details(
        self,
        selection: LeafSelection,
        record: ReviewTreeComparisonRecord,
        build: BuildToken,
        previous: int | None = None,
    ) -> int:
        """Each changed invariant's detail, in order, yielding to every page read; how many.

        ``previous`` is the leaf's last built number: the invariants the move from it changed are
        prepared first, then every other one.
        """

        if self._details is None:
            return 0
        started, spent, waited_out = time.monotonic(), time.thread_time(), self.waited_out
        token = YieldsToPageReads(build, waiting=lambda: bool(self._pending))
        try:
            with cancellable_read(build):
                details = tuple(self._details(selection, record.number, previous))
        except ReadCancelled:
            return 0
        except Exception:  # a detail never takes the dashboard down; a click computes it
            logger.exception("listing the details to prepare failed for %s", selection.leaf_id)
            return 0
        moved = sum(flag for _key, _ask, flag in details)
        logger.info(
            "preparing the details of %d changed invariants of %s (comparison #%d): the first %d "
            "are the move's changed invariants (from %s)%s",
            len(details),
            selection.leaf_id,
            record.number,
            moved,
            "no earlier build" if previous is None else f"#{previous}",
            f"; the first in order is {details[0][0]}, the last {details[-1][0]}"
            if details
            else "",
        )
        prepared = yielded = left = 0
        remaining, applied = list(details), None
        listed = frozenset(key for key, _ask, _flag in details)
        while remaining:
            remaining, applied = self._page_ordered(
                selection,
                record.number,
                remaining,
                applied,
                asked=len(details) - len(remaining),
                listed=listed,
            )
            key, ask, _flag = remaining.pop(0)
            outcome, stops = self._detail(selection, record.number, key, ask, token)
            yielded += stops
            if outcome == "ended":
                break
            prepared += outcome == "prepared"
            left += outcome == "left"
        logger.info(
            "prepared the details of %d of %d changed invariants of %s (comparison #%d) in "
            "%.2f s, %.2f CPU-s in this thread; %d stopped for a page read and asked again, "
            "%d left for a click after %d stops, %d asked after waiting out a held open; the "
            "first %d are the move's%s",
            prepared,
            len(details),
            selection.leaf_id,
            record.number,
            time.monotonic() - started,
            time.thread_time() - spent,
            yielded,
            left,
            DETAIL_YIELDS,
            self.waited_out - waited_out,
            moved,
            "; ended early: a newer build or a shutdown" if token.must_end() else "",
        )
        return prepared

    def _page_ordered(  # noqa: PLR0913 -- the queue's leaf, number, items and the three counts
        self,
        selection: LeafSelection,
        number: int,
        remaining: list[_Item],
        applied: _PageOrder | None,
        *,
        asked: int,
        listed: frozenset[str],
    ) -> tuple[list[_Item], _PageOrder | None]:
        """What the queue has left, in the order the page last drew for this comparison (D585).

        An order is applied once: the invariants it names come first, in its order, then the rest
        in the queue's own. How many it moved, and how many of its ids name no invariant the queue
        lists (dropped), is logged with the open that drew it.
        """

        with self._changed:
            drawn = self._orders.get(selection.as_key())
        if drawn is None or drawn is applied or drawn.number != number:
            return remaining, applied
        rank = {key: index for index, key in enumerate(dict.fromkeys(drawn.keys))}
        ordered = sorted(remaining, key=lambda item: (item[0] not in rank, rank.get(item[0], 0)))
        moved = sum(left[0] != right[0] for left, right in zip(remaining, ordered, strict=True))
        if asked:
            self.reordered += moved
        logger.info(
            "the page's order of comparison #%d (open %s) reached the queue %s: %d of the %d left "
            "moved; %d of its %d invariants dropped, not among the comparison's changed invariants",
            number,
            drawn.open_id or "unnamed",
            f"after {asked} were asked" if asked else "before it started",
            moved,
            len(remaining),
            sum(key not in listed for key in rank),
            len(rank),
        )
        return ordered, drawn

    def _detail(
        self,
        selection: LeafSelection,
        number: int,
        key: str,
        ask: Callable[[], None],
        token: YieldsToPageReads,
    ) -> tuple[str, int]:
        """One detail, asked again after each stop for a page read, at most DETAIL_YIELDS times."""

        started, spent = time.monotonic(), time.thread_time()
        outcome, stops = self._prepared(key, ask, token), 0
        while outcome == "yielded":
            stops += 1
            outcome = self._prepared(key, ask, token) if stops < DETAIL_YIELDS else "left"
        if outcome == "prepared":
            logger.info(
                "prepared the detail of %s for %s (comparison #%d) in %.2f s, %.2f CPU-s",
                key,
                selection.leaf_id,
                number,
                time.monotonic() - started,
                time.thread_time() - spent,
            )
        return outcome, stops

    def _prepared(self, key: str, ask: Callable[[], None], token: YieldsToPageReads) -> str:
        """One detail, once no page read is answered and no page open is served: prepared,
        yielded, ended or failed. Past DETAIL_OPEN_WAIT_SECONDS of waiting while an open is held,
        it is asked anyway and yields to page reads only."""

        since, opens = time.monotonic(), True
        while not PAGE_READS.wait_answered(WAITER_POLL_SECONDS, opens=opens):
            if token.must_end():
                return "ended"
            held = PAGE_READS.held() if opens else {}
            if held and time.monotonic() - since >= DETAIL_OPEN_WAIT_SECONDS:
                opens, token = False, token.past_opens()
                self._waited_out(key, time.monotonic() - since, held)
        if token.must_end():
            return "ended"
        try:
            with cancellable_read(token):
                ask()
        except ReadCancelled:
            return "ended" if token.must_end() else "yielded"
        except Exception:  # a detail never takes the dashboard down; a click computes it
            logger.exception("preparing the detail of %s failed", key)
            return "failed"
        return "prepared"

    def _waited_out(self, key: str, waited: float, held: Mapping[Hashable, float]) -> None:
        self.waited_out += 1
        logger.warning(
            "the detail of %s waited %.1f s for page opens still held (%s); it is asked anyway, "
            "yielding to page reads only (D585: a hold outlived its page or its open)",
            key,
            waited,
            ", ".join(f"{open_key} held {age:.1f} s" for open_key, age in held.items()),
        )


def _niced(run: Callable[[], None]) -> Callable[[], None]:
    """A build thread at the build's lowered priority (Linux: a thread is its own task)."""

    def niced() -> None:
        with contextlib.suppress(OSError, AttributeError):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), BUILD_NICENESS)
        run()

    return niced
