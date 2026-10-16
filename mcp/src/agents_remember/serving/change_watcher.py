"""Change-driven projection pacing: the debounced input watcher + the wake scheduler.

260712-PTS-L3: the projector used to re-project the whole world unconditionally every
``--interval`` seconds (production 1.0s) even when nothing changed -- ~160% steady CPU on
a quiet-but-large tree. This module makes waking *change-driven*: a filesystem watcher
(``watchfiles``, inotify-backed) over the projection's actual input surfaces feeds a
small debounce/coalesce state machine (:class:`ChangePacer`), and a slow heartbeat
covers everything a watcher cannot see. Each accepted path now carries its reader domain
through the pacer so unrelated inputs stay retained instead of rebuilding the whole world.

The authoritative input list (R1), derived reader-by-reader from
``observer.projection_store.project_and_write``:

========================================  =====================================================
watched root                              readers it feeds
========================================  =====================================================
``<coord>/tasks``                         native task sources, contracts and engine facts
``activity_directory(config)``           native host activity publication
``<obs>/workspace``                       attention dismissals
``<obs>/drift``                           read_drift_snapshots
``<coord>/logs/providers/status``         workspace provider ``current.json`` (read_providers)
``<coord>/logs/providers/setup``          read_setup_summaries
``<coord>/temp/worktree-start``           read_start_progress_entries
``<coord>/temp/tool-reports``             read_tool_reports
========================================  =====================================================

(``<obs>`` = ``<coord>/logs/observer``.) Deliberately NOT watched -- heartbeat-covered
blind spots:

* the per-repo memory roots (sidecar staleness / route coverage / ledgers): they sit
  behind ``REPO_SURFACE_REFRESH_TTL_SECONDS`` (15s) already, and their onboarding trees
  are large, so their freshness bound is TTL + heartbeat;
* landing state: an in-memory 30s git-derived refresher with no file signal;
* the entire ``worktrees/`` tree: the checkouts are ~50k dirs (recursive watching would
  re-create the scan cost this change removes) and each task's ``provider-runtime`` holds
  live container data (Postgres/grepai) that is unreadable to the daemon user and churns on
  every container write -- watching it crashed the watcher on permission-denied and would
  have re-projected on WAL writes. Worktree ``provider-state.json`` / ``setup-progress.json``
  changes are infrequent and heartbeat-covered; central provider status is watched via
  ``logs/providers``.

Self-trigger safety: the projection's own per-tick outputs (``latest-state.json`` /
``latest-metrics.json`` and their ``*.tmp`` siblings) live at the observer root, *outside*
every watched subdirectory, so a tick cannot re-wake itself. Inside ``<obs>/workspace``
the non-input churn (the raw event river + cursor/lock) is name-filtered, and the
control-plane lockfiles are suffix-filtered in every watched directory (every durable-store
append and every rewrite opens one ``a+b``, and none is an input). TTL-gated writers that
run inside a tick (event retention, provider current-state refresh) cost at most one
debounced echo tick per TTL window, whose diff emits nothing.

Freshness bounds (unchanged SSE semantics): a write into a quiet world becomes an SSE
delta within ``debounce + projection time``. Live ticks rest after completing for the greater
of ``--interval`` and their duration capped at 3s, so sustained changes cannot keep the
projection continuously occupying the interpreter. Changes during a tick or its rest remain
pending; the rest adds at most 3s at the default interval, on top of running/next tick times.
With no writes at all, ``/api/state`` staleness -- and every
time-*derived* field or state flip (``ageSeconds``/``staleSeconds``, stale/overdue decays) --
is bounded by the heartbeat (R3/R4; default :data:`DEFAULT_HEARTBEAT_SECONDS`). The
volatile age fields were already stripped from the delta stream and advanced client-side
(``dashboard/src/data/servedAges.ts``), so heartbeat-cadence refresh does not change what
an SSE client displays between emissions.

Failure posture: ``watchfiles`` missing or a crashed watch degrades LOUDLY (error log)
to interval-driven full refreshes and keeps retrying; zero watchable roots also retain full
refreshes. Completed ticks still rest, so failed notifications cannot restore continuous work.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from time import time as _wall_now
from typing import TYPE_CHECKING, Protocol

from agents_remember.kernel.file_lock import lock_path_for
from agents_remember.kernel.own_writes import OWN_WRITES
from agents_remember.observer.store import WORKSPACE_CURSOR_FILE, WORKSPACE_LOCK_FILE
from agents_remember.serving.background_accounting import (
    OWNER_PROJECTION,
    BackgroundLedger,
    measured,
    offload,
)
from agents_remember.serving.projections.host_activity import activity_directory
from agents_remember.serving.projections.paths import drift_snapshot_dir, observer_logs_root
from agents_remember.serving.projections.projection_inputs import (
    ALL_PROJECTION_DOMAINS,
    HEALING_PROJECTION_DOMAINS,
    ProjectionDomain,
)
from agents_remember.serving.projections.projection_store import LATEST_METRICS, LATEST_STATE

if TYPE_CHECKING:
    from collections.abc import Callable

    from watchfiles import Change

    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )

try:  # R7: a missing wheel must degrade the daemon loudly, never crash it at import time.
    import watchfiles
except ImportError:  # pragma: no cover - exercised via the injected-None tests
    watchfiles = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _native_kind(change: object) -> str | None:
    """The watchfiles change kind as a plain string, None when unrecognized (F07).

    Duck-typed (``Change`` is only imported for typing and the wheel may be absent
    in tests): anything without a ``name`` reads as an unknown kind, which the echo
    decision treats with the legacy typeless semantics.
    """
    name = getattr(change, "name", None)
    kind = name.lower() if isinstance(name, str) else None
    return kind if kind in ("added", "modified", "deleted") else None


#: Idle re-projection cadence (R3): the self-heal bound for watcher blind spots and the
#: resolution of time-derived projection fields (R4). Configurable via ``--heartbeat``.
DEFAULT_HEARTBEAT_SECONDS = 15.0

#: Settle window after the last observed change before projecting (R2): long enough to
#: coalesce a multi-file write burst, short enough to feel immediate on the dashboard.
DEBOUNCE_SECONDS = 0.1

# A slow projection must yield time to request threads, without adding an unbounded
# scheduler delay to the dashboard's next state update.
MAX_PROJECTION_REST_SECONDS = 3.0

#: How the raw watchfiles batches are grouped before they reach the pacer. The library
#: default (1600ms) could delay first detection beyond the 1s max-delay bound under
#: sustained writes, so batching is kept well under ``--interval``.
_AWATCH_DEBOUNCE_MS = 200
_AWATCH_STEP_MS = 50

#: Cadence for re-deriving the watch-root set (new worktree groups / newly created input
#: dirs) and for retrying after a watch failure. Gaps in between are heartbeat-covered.
WATCH_REFRESH_SECONDS = 30.0

#: Basenames excluded everywhere (defensive: the projection's own atomic outputs).
_EXCLUDED_NAMES = frozenset({LATEST_STATE, LATEST_METRICS})

#: ``<obs>/workspace`` files that are NOT projection inputs: the raw event river (served
#: by ``/api/events``, never read by ``project_and_write``) with its cursor + lock. The
#: control-plane lockfiles are filtered by suffix instead -- see
#: :data:`_DURABLE_LOG_LOCK_SUFFIX`.
_EXCLUDED_WORKSPACE_NAMES = frozenset(
    {
        "events.jsonl",
        WORKSPACE_CURSOR_FILE,
        WORKSPACE_LOCK_FILE,
    }
)

#: The suffix ``kernel.file_lock.lock_path_for`` gives a ``.jsonl`` control-plane log,
#: DERIVED from that function rather than spelled out, so the filter cannot fall out of step
#: with the lock naming.
#:
#: Every durable rewrite takes its sibling lock, including attention cleanup.
#: These lockfiles are never projection inputs, wherever the store lives.
_DURABLE_LOG_LOCK_SUFFIX = lock_path_for(Path("log.jsonl")).name.removeprefix("log")


def projection_input_roots(config: McpRuntimeConfig) -> list[Path]:
    """The currently-existing watch roots, derived from what ``project_and_write`` reads.

    Only existing input roots are returned (``watchfiles`` refuses missing paths); dirs
    that appear later are picked up by the periodic watch-set refresh. See the module
    docstring for the reader-by-reader derivation and the deliberate omissions.
    """
    coordination_root = config.coordination_root
    observer = observer_logs_root(coordination_root)
    candidates: list[Path] = [
        coordination_root / "tasks",
        observer / "workspace",
        drift_snapshot_dir(coordination_root),
        coordination_root / "logs" / "providers" / "status",
        coordination_root / "logs" / "providers" / "setup",
        coordination_root / "temp" / "worktree-start",
        coordination_root / "temp" / "tool-reports",
    ]
    activity = activity_directory(config)
    if activity is not None:
        candidates.append(activity)
    # Deliberately NOT under coordination_root/worktrees: those trees carry each task's
    # live provider-runtime (Postgres data, grepai indexes) which the daemon user cannot
    # read and which churn on container writes -- watching them recursively both crashed
    # the watcher (permission-denied on the container data dir) and would have re-projected
    # on every WAL write. Worktree provider-state.json changes are infrequent and are
    # heartbeat-covered; central provider status is already watched via logs/providers.
    return [path for path in candidates if path.exists()]


def is_projection_input_event(path: str) -> bool:
    """Filter one raw watch event down to genuine projection inputs.

    Drops atomic-write temp files, dot-prefixed sidecar temps, the projection's own outputs,
    every control-plane lockfile (:data:`_DURABLE_LOG_LOCK_SUFFIX`, in any watched directory),
    and the ``workspace/`` non-input churn (see :data:`_EXCLUDED_WORKSPACE_NAMES`).

    Drops directory events (F08/L166): a directory mtime change carries no content no
    reader consumes -- every file write inside a watched root would otherwise wake the
    projector twice (once for the file, once for its parent). An unstatable path fails
    open to admission: a deleted file must still invalidate, and the own-echo decision
    below disposes of our own files.
    """
    name = os.path.basename(path.rstrip("/\\"))
    if name.endswith(".tmp") or name.startswith("."):
        return False
    if name.endswith(_DURABLE_LOG_LOCK_SUFFIX):
        return False
    if name in _EXCLUDED_NAMES:
        return False
    if name in _EXCLUDED_WORKSPACE_NAMES:
        parent = os.path.basename(os.path.dirname(path))
        if parent == "workspace":
            return False
    try:
        if os.path.isdir(path):
            return False
    except OSError:
        pass
    return True


def projection_domains_for_paths(
    config: McpRuntimeConfig, paths: set[str]
) -> frozenset[ProjectionDomain]:
    """Map accepted watcher paths to the reader domains they invalidate.

    Watch roots are intentionally explicit. If an accepted path does not match one of
    them, return every domain: watcher reconciliation must fail open to a full refresh,
    never silently retain an input whose source mapping was missed.
    """
    coordination_root = config.coordination_root.resolve()
    observer = observer_logs_root(coordination_root).resolve()
    roots = [
        (coordination_root / "tasks", ProjectionDomain.TASKS),
        (observer / "workspace", ProjectionDomain.WORKSPACE),
        (drift_snapshot_dir(coordination_root), ProjectionDomain.DRIFT),
        (
            coordination_root / "logs" / "providers" / "status",
            ProjectionDomain.PROVIDERS,
        ),
        (
            coordination_root / "logs" / "providers" / "setup",
            ProjectionDomain.PROVIDERS,
        ),
        (
            coordination_root / "temp" / "worktree-start",
            ProjectionDomain.START_PROGRESS,
        ),
        (
            coordination_root / "temp" / "tool-reports",
            ProjectionDomain.TOOL_REPORTS,
        ),
    ]
    activity = activity_directory(config)
    if activity is not None:
        roots.append((activity.resolve(), ProjectionDomain.HOST_ACTIVITY))
    domains: set[ProjectionDomain] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        matched = False
        for root, domain in roots:
            if path == root or root in path.parents:
                domains.add(domain)
                matched = True
                break
        if not matched:
            return ALL_PROJECTION_DOMAINS
    return frozenset(domains)


@dataclass(frozen=True)
class ProjectionWake:
    """One due projection, the reader domains accumulated for it, and the paths behind them.

    ``paths`` is a bounded sample of the accepted watcher paths that woke this
    projection (at most :data:`WAKE_PATH_SAMPLE`); ``path_count`` is how many
    there were in total. The sample is what the background ledger records as
    "which path woke it" (MIK-R81 rule 1); the count is what the quiet-window
    measurement counts (MIK-R81 rule 2).

    ``generation`` is the pacer's change generation when the wake was issued. The run loop
    hands it back to :meth:`ChangePacer.acknowledge` after a successful tick; a wake from
    an older generation (changes arrived during the tick, or the tick failed and never
    acknowledged) keeps its domains outstanding instead of dropping them (MIK-R81 rule 6).
    """

    reason: str
    domains: frozenset[ProjectionDomain]
    paths: tuple[str, ...] = ()
    path_count: int = 0
    generation: int = 0
    # Whether ``paths`` holds every distinct path behind the wake (F09): the sample
    # is bounded, so a wake with more distinct paths than the sample (or paths dropped
    # at the pacer cap) must not drive path-directed incremental readers — they fall
    # back to the full domain re-read instead of certifying a partial one.
    paths_complete: bool = False


#: How many waking paths one wake carries as its sample; the rest is a count.
WAKE_PATH_SAMPLE = 20

#: How many waking paths the pacer accumulates between wakes; beyond this the
#: sample stops growing but the count keeps the total honest.
PACER_PATH_CAP = 256


def _judge_input_changes(
    changes: set[tuple[Change, str]],
) -> tuple[set[str], set[tuple[Change, str]], bool]:
    """File checks run in the drained worker; pending effects survive the next native pass."""
    paths: set[str] = set()
    deferred: set[tuple[Change, str]] = set()
    overflow = False
    for change, path in changes:
        own = OWN_WRITES.is_own_echo(path, _native_kind(change))
        if own is None:
            if len(deferred) < PACER_PATH_CAP:
                deferred.add((change, path))
            else:
                overflow = True
        elif not own:
            paths.add(path)
    return paths, deferred, overflow


class WakeTarget(Protocol):
    """What a watcher drives: the pacer's change/health inputs (structural, test-fakeable)."""

    def notify_change(
        self,
        domains: frozenset[ProjectionDomain] = ALL_PROJECTION_DOMAINS,
        paths: frozenset[str] = frozenset(),
    ) -> None: ...

    def set_watcher_healthy(self, healthy: bool) -> None: ...


class ChangeWatch(Protocol):
    """Structural contract for projection change watchers (the projector's seam).

    Mirrors ``LandingStateRefresh``: the projector owns the task lifecycle, tests inject
    fakes, and :class:`ProjectionInputWatcher` is the live implementation.
    """

    async def run(self, pacer: WakeTarget) -> None: ...


#: A lost watch event heals within this bound (MIK-R81 rule 5): at least this
#: often one tick refreshes every domain, whatever the watcher saw or missed.
FULL_REFRESH_SECONDS = 300.0


class ChangePacer:
    """The wake scheduler: change-or-heartbeat waiting with debounce, max-delay and floor.

    One instance belongs to one projector run loop. The watcher task feeds it
    (:meth:`notify_change` / :meth:`set_watcher_healthy`); the run loop awaits
    :meth:`wait` once per tick. Scheduling rules (all monotonic-clock):

    * **floor** -- never two projections closer than ``interval`` apart; after a completed
      projection, also rest for ``max(interval, min(duration, 3s))``;
    * **debounce** -- a change projects ``debounce`` after the *last* change of its burst;
    * **max delay** -- a sustained burst still projects within ``max_delay`` (= ``interval``)
      of its *first* change, subject to the floor and completed projection's rest;
    * **task change** -- once per rest window, a task/host invalidation can run one interval
      after arrival; the original rest still holds further events, even after a quick early tick;
    * **heartbeat** -- with no changes, project every ``heartbeat`` seconds (R3/R4);
    * **degraded** -- while the watcher is unhealthy, request full refreshes at the
      interval floor, still subject to the completed projection's rest.
    """

    def __init__(
        self,
        *,
        interval: float,
        heartbeat: float = DEFAULT_HEARTBEAT_SECONDS,
        debounce: float = DEBOUNCE_SECONDS,
        max_delay: float | None = None,
    ) -> None:
        self._interval = max(interval, 0.0)
        self._heartbeat = max(heartbeat, self._interval)
        self._debounce = min(debounce, self._interval) if self._interval > 0 else debounce
        self._max_delay = max_delay if max_delay is not None else self._interval
        self._event = asyncio.Event()
        self._first_pending: float | None = None
        self._last_pending: float | None = None
        self._pending_domains: set[ProjectionDomain] = set()
        self._pending_paths: list[str] = []
        self._pending_path_count = 0
        # Distinct paths behind the pending wake, in first-seen order (F09): the wake
        # sample is drawn from these, so completeness is decided on distinct paths,
        # not on repeated notifications of the same file. Bounded by the same cap.
        self._pending_distinct_paths: list[str] = []
        self._pending_distinct_set: set[str] = set()
        # Whether paths were dropped at the pacer cap: the wake sample is then
        # partial however small it looks, so incremental readers must fall back.
        self._pending_paths_dropped = False
        # Start degraded: until the watcher reports its watches established, pace at the
        # fixed interval so there is no detection blind spot at boot (heartbeat would be
        # too slow a floor for changes racing the watcher startup).
        self._watcher_healthy = False
        # Whether the watcher ever reported unhealthy after booting: only a real
        # outage (down, then up) reconciles (F06). The boot flip from the initial
        # degraded state establishes the watches; nothing was watched yet, so no
        # coverage was missed and no every-domain re-cover is owed.
        self._saw_unhealthy = False
        # Whether an every-domain publication succeeded while degraded: the flip back to
        # healthy reconciles (re-covers every domain) only when the outage published no
        # full refresh, because interval fulls already covered what the watcher missed.
        self._full_published_while_degraded = False
        # One-shot recovery demand (F06): set on an uncovered down→up flip, consumed
        # by the next every-domain wake. While set, the next eligible wake is a full
        # refresh even with empty pending: changes may have gone unwatched during the
        # outage, so heartbeat-with-empty-domains must not stand in for the heal.
        self._recover_full = False
        # When the watcher last stopped being healthy, as wall-clock seconds, or None
        # while it is healthy: the served state says whether the watcher is running,
        # and since when it is not (MIK-R81 rule 5). Read through a direct binding
        # (not the module's ``time`` global) so wall-clock stamps keep working where
        # tests freeze the monotonic clock.
        self._watcher_down_since: float | None = _wall_now()
        self._last_wake = time.monotonic()
        self._not_before = self._last_wake
        # When the last every-domain refresh woke, monotonic: the 5-minute heal bound
        # (MIK-R81 rule 5) is measured from full wakes, not from wall-clock ticks, so a
        # long projection cannot silently stretch it.
        self._last_full_wake = self._last_wake
        # Change generation for wake acknowledgement (MIK-R81 rule 6): bumped on every
        # accepted change or health flip, snapshotted into each wake, compared back in
        # acknowledge so a failed tick's domains stay outstanding.
        self._generation = 0
        self._early_deadline: float | None = None
        self._early_window_end = self._last_wake
        self._early_used_until = self._last_wake

    def projection_completed(self, duration: float) -> None:
        """Give request threads a bounded rest after a live tick, including a failed tick."""
        rest = max(self._interval, min(duration, MAX_PROJECTION_REST_SECONDS))
        self._not_before = max(self._not_before, time.monotonic() + rest)

    def notify_change(
        self,
        domains: frozenset[ProjectionDomain] = ALL_PROJECTION_DOMAINS,
        paths: frozenset[str] = frozenset(),
    ) -> None:
        """Record a (batch of) input change(s); wakes :meth:`wait` to reschedule."""
        now = time.monotonic()
        if self._first_pending is None:
            self._first_pending = now
        self._last_pending = now
        self._pending_domains.update(domains)
        if domains and not paths:
            self._pending_paths_dropped = True
        self._pending_path_count += len(paths)
        for path in sorted(paths):
            if len(self._pending_paths) >= PACER_PATH_CAP:
                self._pending_paths_dropped = True
                break
            self._pending_paths.append(path)
            if path not in self._pending_distinct_set:
                self._pending_distinct_set.add(path)
                self._pending_distinct_paths.append(path)
        self._generation += 1
        if (
            self._early_deadline is None
            and self._early_used_until <= now < self._not_before
            and domains & {ProjectionDomain.TASKS, ProjectionDomain.HOST_ACTIVITY}
        ):
            self._early_deadline = now + self._interval
            self._early_window_end = self._not_before
        self._event.set()

    def _with_rest_exception(self, deadline: float) -> float:
        """An input can shorten one rest; it cannot move an already earlier ordinary wake."""
        if self._early_deadline is None:
            return deadline
        return min(deadline, max(self._last_wake + self._interval, self._early_deadline))

    def set_watcher_healthy(self, healthy: bool) -> None:
        """Flip between change-driven and interval-driven full-refresh pacing."""
        if healthy == self._watcher_healthy:
            if not healthy and self._watcher_down_since is None:
                self._watcher_down_since = _wall_now()
                self._saw_unhealthy = True
            return
        was_healthy = self._watcher_healthy
        self._watcher_healthy = healthy
        # The served state names since when the watcher is down (MIK-R81 rule 5):
        # the stamp is taken on the transition, in wall-clock for the wire.
        self._watcher_down_since = None if healthy else _wall_now()
        if not healthy:
            self._saw_unhealthy = True
            self._full_published_while_degraded = False
        elif not was_healthy and self._saw_unhealthy and not self._full_published_while_degraded:
            # Reconcile (F06): no every-domain publication covered the outage, so
            # changes may have gone unwatched — the next eligible wake is a full
            # refresh instead of trusting the possibly-incomplete pending set (or
            # a heartbeat with empty domains). An outage whose interval fulls
            # published needs no reconcile: they already healed it. The boot flip
            # from the initial degraded state never reconciles (see _saw_unhealthy).
            self._recover_full = True
        self._generation += 1
        self._event.set()

    def note_failed_full(self, wake: ProjectionWake) -> None:
        """Keep a failed interval/full wake's domains outstanding (MIK-R81 rule 6).

        A failed tick never acknowledges, so change wakes lose nothing. An
        interval/full wake may have found an empty pending set; retain exactly
        that wake's domains so the next eligible same-pacer cycle retries them
        instead of a heartbeat with empty domains. The failed wake also leaves
        path coverage incomplete for that retry.
        """

        if wake.reason not in ("interval", "full"):
            return
        now = time.monotonic()
        if self._first_pending is None:
            self._first_pending = now
        self._last_pending = now
        self._pending_domains.update(wake.domains)
        # A full retry covers paths no later partial notification can enumerate.
        self._pending_paths_dropped = True
        # No generation bump: the failed wake is dead and no other wake is in flight,
        # so there is nothing outstanding to invalidate; the merged domains simply wait
        # for the next eligible cycle, still bounded by floor and rest.
        self._event.set()

    @property
    def watcher_healthy(self) -> bool:
        """Whether the watcher's change detection is currently trusted."""
        return self._watcher_healthy

    @property
    def watcher_down_since(self) -> float | None:
        """Wall-clock seconds of when the watcher last went down, or None while healthy."""
        return self._watcher_down_since

    def _next_deadline(self, now: float | None = None) -> tuple[float, str]:
        """Pure scheduling core: (monotonic deadline, wake reason) for the current state.

        The 5-minute heal bound is independent of healthy partial-change traffic (MIK-R81
        rule 5): a due full refresh preempts the change branch, and a change whose
        debounce settles past the full deadline becomes a full wake instead of overshooting
        it. A quiet heartbeat likewise never overshoots: it wakes at the earlier of the
        heartbeat and the full deadline, with the matching reason.
        """
        at = time.monotonic() if now is None else now
        floor = max(self._last_wake + self._interval, self._not_before)
        if not self._watcher_healthy:
            return self._with_rest_exception(floor), "interval"
        full_at = self._last_full_wake + FULL_REFRESH_SECONDS
        if self._recover_full or at >= full_at:
            # An uncovered outage demands one every-domain refresh before ordinary
            # pacing resumes (F06); the flag is consumed when the full wake issues.
            return self._with_rest_exception(floor), "full"
        if self._first_pending is not None and self._last_pending is not None:
            settle = self._last_pending + self._debounce
            bound = self._first_pending + self._max_delay
            change_at = self._with_rest_exception(min(max(settle, floor), max(bound, floor)))
            if change_at >= full_at:
                return max(full_at, floor), "full"
            return change_at, "change"
        # A lost watch event heals within FULL_REFRESH_SECONDS (MIK-R81 rule 5): the
        # heartbeat in between stays as it is, but at least every 5 minutes one tick
        # refreshes every domain, whatever the watcher saw or missed. Measured from
        # the last every-domain wake against the caller's now, not against the last
        # wake, so a long-quiet world still heals on time.
        heartbeat_at = max(self._last_wake + self._heartbeat, self._not_before)
        if heartbeat_at >= full_at:
            return max(full_at, floor), "full"
        return heartbeat_at, "heartbeat"

    def _consume_pending_paths(self) -> tuple[tuple[str, ...], int, bool]:
        """The accumulated waking paths as a bounded sample, their total, completeness.

        The sample holds the first distinct paths in first-seen order; ``complete``
        is True only when every distinct path fits and nothing was dropped at the
        pacer cap. Incremental readers (F09) may trust the sample only then.
        """
        distinct = self._pending_distinct_paths
        complete = not self._pending_paths_dropped and len(distinct) <= WAKE_PATH_SAMPLE
        return tuple(distinct[:WAKE_PATH_SAMPLE]), self._pending_path_count, complete

    async def wait(self) -> ProjectionWake:
        """Sleep until the next projection is due; return its reason and domains.

        Pending changes are snapshotted into the wake, not consumed by it: the run loop
        hands the wake back to :meth:`acknowledge` after the tick succeeds, and only then
        do the snapshotted invalidations clear. Changes observed *during* the following
        projection bump the generation, so the acknowledgement keeps them outstanding for
        the next cycle instead of dropping arrivals the tick may not have seen. A tick that
        fails never acknowledges, so its domains stay outstanding for the next eligible
        cycle -- nothing is ever lost to a tick, failed or overlapping.
        """
        while True:
            now = time.monotonic()
            deadline, reason = self._next_deadline(now)
            if now >= deadline:
                if self._early_deadline is not None and deadline < self._not_before:
                    self._early_used_until = self._early_window_end
                self._early_deadline = None
                if reason == "change":
                    domains = frozenset(self._pending_domains)
                    paths, path_count, paths_complete = self._consume_pending_paths()
                elif reason in ("interval", "full"):
                    # An every-domain wake still carries the outstanding paths (F02): the
                    # full refresh covers them, so the wake records the evidence the
                    # acknowledgement then clears. The pending set itself clears only on
                    # acknowledged success, never on the wake.
                    domains = HEALING_PROJECTION_DOMAINS | self._pending_domains
                    paths, path_count, paths_complete = self._consume_pending_paths()
                else:
                    domains = frozenset()
                    paths, path_count, paths_complete = (), 0, True
                self._event.clear()
                self._last_wake = now
                if reason == "full":
                    # The demanded recovery refresh (or the due heal) issued: ordinary
                    # pacing resumes; a later outage sets its own demand.
                    self._recover_full = False
                return ProjectionWake(
                    reason=reason,
                    domains=domains,
                    paths=paths,
                    path_count=path_count,
                    generation=self._generation,
                    paths_complete=paths_complete,
                )
            self._event.clear()
            try:
                async with asyncio.timeout(deadline - now):
                    await self._event.wait()
            except TimeoutError:
                pass

    def acknowledge(self, wake: ProjectionWake) -> None:
        """Clear a wake's invalidations after its tick succeeded (MIK-R81 rule 6).

        Only a wake from the current generation clears: anything that arrived during the
        tick (or any health flip since the wake) bumps the generation, and the merged
        pending set -- the tick's domains plus the arrivals -- stays outstanding for the
        next eligible cycle. The next wake then re-covers the tick's domains together with
        the arrivals, so no invalidation is lost to overlapping work. Rest and floor still
        bound the re-wake through :meth:`projection_completed` and :meth:`_next_deadline`.
        """
        if wake.generation != self._generation:
            return
        self._first_pending = None
        self._last_pending = None
        self._pending_domains.clear()
        self._pending_paths.clear()
        self._pending_path_count = 0
        self._pending_distinct_paths.clear()
        self._pending_distinct_set.clear()
        self._pending_paths_dropped = False

    def confirm_full_published(self) -> None:
        """Mark a successful every-domain publication for the 5-minute heal bound.

        Only a tick that refreshed and published every domain advances the bound
        (MIK-R81 rule 5): waking full is not healing, publishing full is. A full
        published while degraded also covers the outage, so the flip back to
        healthy needs no reconcile.
        """
        self._last_full_wake = time.monotonic()
        self._recover_full = False
        if not self._watcher_healthy:
            self._full_published_while_degraded = True


class ProjectionInputWatcher:
    """Watch the projection-input roots and drive a :class:`ChangePacer`.

    Lifecycle mirrors the landing refresher: created by ``create_app`` for live serving
    (never for ``--sim`` -- replay must stay time-driven, its feeder only writes *inside*
    a tick), started/cancelled by ``Projector.run``. One watch failure never kills the
    projector: the pacer drops to interval-driven full refreshes (loudly) and the watch is
    retried every :data:`WATCH_REFRESH_SECONDS`.
    """

    def __init__(
        self,
        config: McpRuntimeConfig,
        *,
        refresh_seconds: float = WATCH_REFRESH_SECONDS,
        ledger: BackgroundLedger | None = None,
    ) -> None:
        self._config = config
        self._refresh_seconds = refresh_seconds
        self._ledger = ledger

    async def run(self, pacer: WakeTarget) -> None:
        """Watch forever, feeding ``pacer``; degrade loudly on any failure (R7)."""
        if watchfiles is None:
            logger.error(
                "watchfiles is not installed: change-driven projection is DISABLED and the "
                "projector uses interval-driven full refreshes (install the 'watchfiles' "
                "dependency to restore adaptive pacing)"
            )
            pacer.set_watcher_healthy(False)
            return
        established_before = False
        while True:
            try:
                # Root derivation sits INSIDE the retry guard: a transient stat/glob failure
                # must follow the same loud degrade-and-retry path as a watch failure -- not
                # escape run() and kill the task for good (losing the periodic self-heal).
                roots = await offload(
                    OWNER_PROJECTION, self._ledger, projection_input_roots, self._config
                )
                if not roots:
                    # A fresh/empty coordination tree: nothing to watch yet. Not an error --
                    # fixed-interval pacing until the first input dir appears.
                    pacer.set_watcher_healthy(False)
                    await asyncio.sleep(self._refresh_seconds)
                    continue
                # Returns when the root set changed (restart with the fresh set).
                await self._watch_once(roots, pacer, reconcile=established_before)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "projection input watcher FAILED; using interval-driven full refreshes "
                    "until the watch re-establishes (retry in %.0fs)",
                    self._refresh_seconds,
                )
                pacer.set_watcher_healthy(False)
                await asyncio.sleep(self._refresh_seconds)
            established_before = True

    async def _watch_once(self, roots: list[Path], pacer: WakeTarget, *, reconcile: bool) -> None:
        """One watch generation over a fixed root set; returns when the set moved."""
        assert watchfiles is not None
        stop = asyncio.Event()
        failure: list[BaseException] = []
        refresher = asyncio.create_task(self._supervise_roots(roots, stop, failure))
        registered = False
        deferred: set[tuple[Change, str]] = set()
        try:
            async for changes in watchfiles.awatch(
                *roots,
                watch_filter=self._watch_filter(),
                debounce=_AWATCH_DEBOUNCE_MS,
                step=_AWATCH_STEP_MS,
                stop_event=stop,
                recursive=True,
                # Coordination writers require a local POSIX filesystem. WSL's automatic
                # polling scans that entire tree even though native inotify covers it.
                force_polling=False,
                # Defence-in-depth: the watched roots are deliberately container-free, but an
                # unreadable subdir must degrade to skipping it, never crash the whole watch.
                ignore_permission_denied=True,
                yield_on_timeout=True,
            ):
                if not registered:
                    pacer.set_watcher_healthy(True)
                    registered = True
                    if reconcile:
                        pacer.notify_change(ALL_PROJECTION_DOMAINS)
                if changes or deferred:
                    # Our own files never wake our own projector (MIK-R81 rule 3): drop
                    # the echo of a file this process wrote itself. An outside write to
                    # the same file changes its stat identity, so only a file nobody
                    # else touched is suppressed; a wrong drop heals on the next full
                    # refresh (MIK-R81 rule 5). The native kind travels into the echo
                    # decision: arrivals never ride a directory voucher and a delete
                    # is never a write echo (F07).
                    deferred = await self._consume_input_changes(changes | deferred, pacer)
            if not stop.is_set():
                raise RuntimeError("native projection watch ended unexpectedly")
        finally:
            stop.set()
            refresher.cancel()
            try:
                await refresher
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("watch-root refresher failed during watcher shutdown")
        if failure:
            # The root refresh died while the watch was live (F10): the root set may have
            # moved unwatched, so reconcile everything, then ride the standard outage
            # path -- reported down with a stable downSince plus the 30-second retry --
            # instead of staying live on possibly-obsolete roots with running=True.
            pacer.notify_change(ALL_PROJECTION_DOMAINS)
            raise failure[0]

    async def _consume_input_changes(
        self, changes: set[tuple[Change, str]], pacer: WakeTarget
    ) -> set[tuple[Change, str]]:
        paths, deferred, overflow = await offload(
            OWNER_PROJECTION, self._ledger, _judge_input_changes, changes
        )
        with measured(OWNER_PROJECTION, self._ledger):
            if overflow:
                pacer.notify_change(ALL_PROJECTION_DOMAINS)
            if paths:
                domains = projection_domains_for_paths(self._config, paths)
                pacer.notify_change(domains, frozenset(paths))
        return deferred

    def _watch_filter(self) -> Callable[[Change, str], bool]:
        assert watchfiles is not None
        default = watchfiles.DefaultFilter()

        def _filter(change: Change, path: str) -> bool:
            return default(change, path) and is_projection_input_event(path)

        return _filter

    async def _supervise_roots(
        self, current: list[Path], stop: asyncio.Event, failure: list[BaseException]
    ) -> None:
        """Run the root refresh, routing its death into the outage path (F10).

        A roots-changed stop returns normally through ``stop``. Any other failure is
        recorded and stops the watch too, so the generation ends loudly instead of
        staying live on possibly-obsolete roots.
        """
        try:
            await self._stop_when_roots_change(current, stop)
            if not stop.is_set():
                failure.append(RuntimeError("watch-root refresher ended unexpectedly"))
                stop.set()
        except asyncio.CancelledError:
            if not stop.is_set():
                failure.append(RuntimeError("watch-root refresher was cancelled unexpectedly"))
                stop.set()
            raise
        except BaseException as exc:
            failure.append(exc)
            stop.set()

    async def _stop_when_roots_change(self, current: list[Path], stop: asyncio.Event) -> None:
        """Re-derive the root set periodically; stop the watch (to restart) when it moved."""
        while True:
            await asyncio.sleep(self._refresh_seconds)
            fresh = await offload(
                OWNER_PROJECTION, self._ledger, projection_input_roots, self._config
            )
            if fresh != current:
                stop.set()
                return
