"""Compare explainer bindings on page opens and turn ends, publishing in-process notices."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from agents_remember.canvas.explainer_paths import (
    leaf_workspace,
    seat_counter_path,
)
from agents_remember.canvas.explainer_state import (
    generation,
    read_binding,
)
from agents_remember.canvas.paths import PathRefused, task_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_refusal import ReviewRefusal
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.explainer_watcher import (
    CapturedComparison,
    ComparisonMeasurement,
    ComparisonOwner,
    GitComparisonOwner,
    LeafSelection,
    MovedNotice,
    MoveNoticeChannel,
    WatcherApplicationPorts,
    is_landed,
    is_planned,
    leaf_seats,
    measure_notice,
    notices,
    tracked_leaves,
    unexplained_turn_notice,
)
from agents_remember.serving.leaf_worktree_watch import process_worktree_watch
from agents_remember.serving.projections.host_activity import turn_ended_agents

logger = logging.getLogger(__name__)

# --- the moved watcher (MIK-R186 rule 7) -----------------------------------------------------------

#: How often the turn-end reader looks when its loop runs; the host writes one publication.
MOVE_NOTICE_TICK_SECONDS = 3.0

#: One review page opens (or reconnects) more than once; one capture serves that short window.
PAGE_OPEN_COOLDOWN_SECONDS = 5.0

#: Every this many ticks the tracked leaves are handed to the worktree watch (D569).
COVERAGE_TICKS = 10


@dataclass
class _TurnEndOwner:
    """A turn end's capture owner: it keeps what it captured and hands it on, unchanged."""

    owner: ComparisonOwner
    handed: Callable[[LeafSelection, ReviewTreeComparisonRecord], None] | None = None
    captured: ReviewTreeComparisonRecord | None = None

    def capture(self, selection: LeafSelection, /) -> CapturedComparison | ReviewRefusal | None:
        captured = self.owner.capture(selection)
        if isinstance(captured, CapturedComparison):
            self.captured = captured.record
            if self.handed is not None:
                self.handed(selection, captured.record)
        return captured


#: The one watch per coordination root: the route compares and publishes, the turn-end loop reads.
_WATCHES: dict[tuple[str, int], MoveNoticeWatch] = {}


def move_notice_watch(config: McpRuntimeConfig, ports: WatcherApplicationPorts) -> MoveNoticeWatch:
    """The process's watch for this coordination root and composition, made once to be shared."""

    key = (str(config.coordination_root), id(ports))
    watch = _WATCHES.get(key)
    if watch is None:
        watch = MoveNoticeWatch(config, ports)
        _WATCHES[key] = watch
    return watch


@dataclass(frozen=True)
class WatchOptions:
    """What a watch may replace: its capture owner and counter, its channel, its cooldown."""

    measurement: ComparisonMeasurement = field(default_factory=ComparisonMeasurement)
    channel: MoveNoticeChannel | None = None
    cooldown: float = PAGE_OPEN_COOLDOWN_SECONDS


class MoveNoticeWatch:
    """Show that a leaf moved past its explainer's binding, and wake nothing (MIK-R186 rule 7).

    Two triggers, one comparison. A review page opening or refreshing calls :meth:`page_opened`;
    :meth:`run` reads the host's turn ends with :func:`turn_ended_agents` and calls
    :meth:`after_turn_ends`. A ``planning`` leaf is checked only when the page opens: its planned
    binding is compared with the master line's two tips, and nothing is captured.

    The watch sends no message, writes no held store and makes no host call. Its only effects are
    the comparison owner's capture (Git objects, no pin and no record) and one notice on the
    in-process channel.
    """

    def __init__(
        self,
        config: McpRuntimeConfig,
        ports: WatcherApplicationPorts,
        *,
        options: WatchOptions | None = None,
    ) -> None:
        options = options or WatchOptions()
        self._config = config
        self._ports = ports
        self._owner = options.measurement.owner
        self._counter = options.measurement.counter
        self._channel = options.channel or notices
        self._cooldown = max(options.cooldown, 0.0)
        self._seen: dict[str, str] = {}
        self._checked: dict[tuple[str, str, str], float] = {}
        self._unexplained_baselines: dict[tuple[str, str, str], Any] = {}
        self._guard = threading.Lock()

    @property
    def channel(self) -> MoveNoticeChannel:
        """The one channel this watch publishes to; the stream route reads the same one."""

        return self._channel

    def clear(self, selection: LeafSelection | None = None) -> None:
        """Forget cached notice state and broadcast a cleared notice to open streams."""

        with self._guard:
            if selection is None:
                self._checked.clear()
            else:
                self._checked.pop(selection.as_key(), None)
        self._channel.clear(selection)

    def page_opened(self, selection: LeafSelection) -> MovedNotice | None:
        """The page's own read: the notice the watch already holds; opening never captures.

        Only real change sources capture: turn ends (:meth:`after_turn_ends`) and watch events. A
        stream that opens or reopens is sent the state the watch holds. A ``planning`` leaf is
        compared here, its planned binding against the master line's two tips, which captures
        nothing. A leaf with no environment is measured at its next turn end against its latest
        record.
        """

        if is_landed(self._config, selection, self._ports):
            self.clear(selection)
            return None
        if self._ports.coverage is not None and not process_worktree_watch().covered(
            selection.as_key()
        ):
            logger.info(
                "review open of %s is uncovered by the worktree watch: %s",
                selection.leaf_id,
                process_worktree_watch().uncovered_reason(selection.as_key()) or "not tracked",
            )
        key = selection.as_key()
        now = time.monotonic()
        current_key = self._current_binding_key(selection)
        if current_key is None:
            return self._channel.latest(selection)
        if not self._planning(selection):
            return self._channel.latest(selection, binding_key=current_key)
        with self._guard:
            last = self._checked.get(key)
            if last is not None and self._cooldown and now - last < self._cooldown:
                cached = self._channel.latest(selection, binding_key=current_key)
                if cached is not None:
                    return cached
            self._checked[key] = now
        return self.check(selection)

    def check(
        self,
        selection: LeafSelection,
        *,
        owner: ComparisonOwner | None = None,
        publishes: Callable[[], bool] | None = None,
    ) -> MovedNotice | None:
        """Compare one environment with its leaf's state and publish a notice when it moved.

        ``publishes``, when given, is asked before a moved notice is published; a notice it
        declines is neither published nor returned.
        """

        try:
            with self._guard:
                outcome, notice, bkey = measure_notice(
                    self._config,
                    selection,
                    self._ports,
                    measurement=ComparisonMeasurement(
                        owner=owner or self._owner, counter=self._counter
                    ),
                )
        except Exception:  # a page or a turn end is never broken by the watcher's own read
            logger.exception("the moved watcher failed to compare %s", selection.leaf_id)
            return None
        if outcome == "moved" and notice is not None:
            if publishes is not None and not publishes():
                return None
            # Stored under the key a page's open looks it up by (generation included), so a held
            # notice is found by the next open and a new generation never replays it.
            held_key = self._current_binding_key(selection) or bkey
            self._channel.publish(selection, notice, binding_key=held_key)
            return notice
        if outcome == "clean":
            with self._guard:
                self._checked.pop(selection.as_key(), None)
            self._channel.clear(selection)
        return None

    def after_turn_ends(self, ended: Iterable[str]) -> list[MovedNotice]:
        """The turn-end trigger: each leaf whose seat ended, once however many ended, never planning.

        A leaf's Worker, Reviewer and Curator may end their turns between two looks: the leaf is
        captured and compared once, so its page receives one event, not one per seat. A turn end
        publishes only when its capture would be a new comparison number -- trees that differ from
        the leaf's latest record (:meth:`_new_comparison`); identical trees publish nothing, so a
        turn end that changed nothing renews no page.
        """

        published: list[MovedNotice] = []
        seats = self.seats()
        handled: set[tuple[str, str, str]] = set()
        for agent in ended:
            selection = seats.get(agent)
            if selection is None or selection.as_key() in handled or self._planning(selection):
                continue
            handled.add(selection.as_key())
            if is_landed(self._config, selection, self._ports):
                self.clear(selection)
                self._unexplained_baselines.pop(selection.as_key(), None)
                continue
            owner = self._turn_end_owner()
            fresh = partial(self._new_comparison, selection, owner)
            if self._current_binding_key(selection) is not None:
                notice = self.check(selection, owner=owner, publishes=fresh)
            else:
                notice = unexplained_turn_notice(
                    self._config,
                    selection,
                    self._unexplained_baselines,
                    self._ports,
                    measurement=ComparisonMeasurement(owner=owner, counter=self._counter),
                )
                if notice is not None and fresh():
                    self._channel.publish(selection, notice)
                else:
                    notice = None
            if notice is not None:
                published.append(notice)
        return published

    def _turn_end_owner(self) -> _TurnEndOwner:
        """The capture owner of one leaf's turn end: the watch's own, keeping what it captured."""

        return _TurnEndOwner(
            self._owner or GitComparisonOwner(self._config, self._ports),
            self._ports.turn_end_captured,
        )

    def _new_comparison(self, selection: LeafSelection, owner: _TurnEndOwner) -> bool:
        """Whether the turn end's capture would be a new comparison number for its leaf.

        It would be when no record holds its trees as the latest: the review records a new number
        for exactly that case (``same_trees`` against the latest record). Records that cannot be
        read answer "new", as a turn end published before this rule did.
        """

        captured = owner.captured
        if captured is None:
            return True
        try:
            root = task_root(self._config, selection.repository_id, selection.master)
            latest = self._ports.latest_comparison(root, selection.leaf_id)
        except (PathRefused, OSError, ValueError):
            return True
        return latest is None or not latest.same_trees(captured)

    def seats(self) -> dict[str, LeafSelection]:
        """Every leaf with an environment or open stream, by its explainer and leaf-role seat ids."""

        return leaf_seats(
            self._config,
            self._ports,
            active_streams=lambda sel: (
                self._channel.has_subscribers(sel) or sel.as_key() in self._unexplained_baselines
            ),
        )

    async def run(self, *, tick: float = MOVE_NOTICE_TICK_SECONDS) -> None:
        """Read the host's turn ends forever; one failed tick never ends the watch."""

        ticks = 0
        while True:
            try:
                if self._ports.coverage is not None and ticks % COVERAGE_TICKS == 0:
                    await asyncio.to_thread(self.sync_coverage)
                ended = await asyncio.to_thread(turn_ended_agents, self._config, self._seen)
                if ended:
                    await asyncio.to_thread(self.after_turn_ends, ended)
            except Exception:  # a watcher must never take the dashboard down with it
                logger.exception("the moved watcher's turn-end read failed; retrying next tick")
            ticks += 1
            await asyncio.sleep(tick)

    def sync_coverage(self) -> None:
        """Hand the leaves this watch tracks, each with why, to the worktree watch (D569)."""

        if self._ports.coverage is not None:
            self._ports.coverage(tracked_leaves(self._config, self._ports))

    def _current_binding_key(self, selection: LeafSelection) -> str | None:
        try:
            workspace = leaf_workspace(
                self._config, selection.repository_id, selection.master, selection.leaf_id
            )
            binding = read_binding(workspace)
            number = generation(seat_counter_path(workspace))
        except Exception:
            return None
        if binding is None:
            return None
        held = binding.binding
        return (
            f"{number}:{held.comparison_owner}:{held.comparison_reference}:"
            f"{held.four_tree_digest}:{binding.packet_digest}"
        )

    def _planning(self, selection: LeafSelection) -> bool:
        try:
            workspace = leaf_workspace(
                self._config, selection.repository_id, selection.master, selection.leaf_id
            )
            binding = read_binding(workspace)
        except Exception:  # an unreadable environment is not a planning one to skip
            return False
        return binding is not None and is_planned(binding)
