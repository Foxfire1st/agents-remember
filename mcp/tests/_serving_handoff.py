"""The virtual-clock harness that drives the two real serving loops for `LOCR-R04@v1`.

The handoff oracle this harness measures is documented at its proof site,
``test_serving_notifier_handoff``. This module owns only the instrument: one disposable serving
world in which the REAL ``_serving_lifespan`` creates the real ``_terminal_observation_loop`` and the
real ``_agent_notifier_loop``, the REAL ``TerminalCatalogLivenessSweeper.refresh`` runs over a REAL
``TerminalCatalog``, and the REAL ``run_agent_notifier_sweep`` runs over the context the notifier loop
builds for itself.

Nothing here replaces production behavior; every piece either records what production did or holds a
pass where a case needs to observe a scheduling phase:

* ``_DeadlineClock`` -- one virtual clock shared by ``asyncio.sleep`` and the sweeper's ``now()``,
  advancing to each released sleep's own deadline so two loops parked on different delays keep one
  coherent timeline.
* ``_SweepWitness`` -- the single entry point both loops call, recording every attempt's window and
  how many rows it probed, which is how a full sweep is told apart from a fast-path pass.
* ``_WitnessCatalog`` -- the real store with its one durable write path and its in-batch hook
  recorded, so a commit instant and an uncommitted in-memory observation are both observable.
* ``_CatalogReads`` / ``_NotifierPasses`` / ``_HeartbeatWitness`` -- the notifier's own port, pass,
  and end-of-pass tick, recorded so "this pass actually read the fact" is decidable per pass.
* ``_Adapter`` -- the adapter readers a sweep consumes, holding the one terminal fact a case can make
  readable and the gates that park a pass inside a full sweep, inside a fast-path pass, or inside the
  consuming sweep's own pre-commit work.
* ``_Handoff`` / ``_HandoffCase`` -- the oracle's terms read back out of those recordings, and the
  driver that releases one parked sleep at a time so a case chooses each pass's exact instant.

A case arms a gate immediately before the step whose pass it wants to park; the driver waits for the
loop to re-park its own sleep, which is what proves an attempt settled without racing the loop's
cadence. Only the loop whose sleep was released moves, so no case depends on a wall-clock sleep or on
the order two independent cadences happen to interleave in.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.serving._app_lifespan as lifespan_module
from _handoff_clock import _DeadlineClock, _Gate, _Sequence
from agents_remember.kernel.agentic_settings import (
    DEFAULT_AGENT_NOTIFIER_INTERVAL_SECONDS,
    AgenticSettings,
    AgentNotifierSettings,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    ControlState,
)
from agents_remember.models.terminal_catalog import (
    DEFAULT_LIVENESS_HYSTERESIS,
    TerminalCatalogEntry,
)
from agents_remember.serving.agent_notifier import run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.conversation.active.status import TurnTerminalEvidence
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_evidence import (
    TerminalEvidenceProjection,
    TerminalEvidenceRead,
)
from agents_remember.serving.terminal_liveness import (
    DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS,
    LivenessProbe,
    TerminalCatalogLivenessSweeper,
)
from test_serving_observation_loop import _LiveHost, _ServingFixture, _wait_until

_REAL_SLEEP = asyncio.sleep
"""The real sleep, captured before a fixture swaps ``asyncio.sleep`` for the virtual clock."""

P = DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS
"""The observer's completion-relative polling delay: one second by default."""

F = DEFAULT_LIVENESS_HYSTERESIS.sweep_interval_seconds
"""The full-sweep rate limit, measured from the start of the previous full sweep: ten seconds."""

N = DEFAULT_AGENT_NOTIFIER_INTERVAL_SECONDS
"""The notifier's completion-relative interval: ten seconds by default."""

WORST_PHASE_BOUND = F + P + N
"""21.0 s of logical scheduling for the default knobs, before any measured overrun."""

EVIDENCE_ROW = "seat-1"
"""The steady seat whose terminal settlement is the new fact every case relays."""

OVERRUN_ROW = "seat-2"
"""A second steady seat, observed after ``EVIDENCE_ROW``, a case can park the sweep inside."""

STARTING_ROW = "seat-starting"
"""A row still at ``control_state="starting"``, so the one-second fast path selects it."""

ARMED_EVIDENCE_ID = "turn-armed"
"""The terminal evidence identity that makes the new fact durable exactly once."""

_ROW_COUNT = 3
"""Live rows a full sweep probes; the fast path probes only its starting-row batch."""

_STEP_LIMIT = 2 * math.ceil(F / P) + 2
"""Bound on the polls one driver loop may take: a hung scenario fails instead of spinning.

The budget is sized from the observer's own grid. A full sweep that is due one whole ``F`` late costs
``F / P`` polls beyond the nominal ``F / P``, so a genuine lateness reaches ``measure()`` and fails
there on the bound assertion (``poll_delay <= P``) instead of aborting this driver with "the
observation owner never reached the requested state" -- an attribution failure, not a verdict. The
two extra polls are margin: this is a step budget, never a cadence.
"""

_PANE_TEXT = "pane text the canonical pane capturer reads"


@dataclass(frozen=True)
class _SweepCall:
    """One ``refresh()`` attempt: the prime, an observer poll, or a notifier pass's inline refresh."""

    index: int
    entered_at: float
    exited_at: float
    probed: int

    @property
    def full_sweep(self) -> bool:
        """A full sweep probes every live row; a fast-path pass probes only a starting-row batch."""

        return self.probed >= _ROW_COUNT


@dataclass(frozen=True)
class _Commit:
    """One durable catalog write through the store's own commit path."""

    index: int
    sequence: int
    at: float
    rows: tuple[TerminalCatalogEntry, ...]

    def holds(self, evidence_id: str) -> bool:
        return any(row.terminal_evidence_id == evidence_id for row in self.rows)


@dataclass(frozen=True)
class _CatalogRead:
    """One ``list()`` read issued by a notifier pass, and what it returned."""

    index: int
    attempted: int
    returned: int
    rows: tuple[TerminalCatalogEntry, ...]

    def holds(self, evidence_id: str) -> bool:
        return any(row.terminal_evidence_id == evidence_id for row in self.rows)


@dataclass(frozen=True)
class _NotifierCall:
    """One ``run_agent_notifier_sweep`` pass: its window and the catalog reads it made."""

    index: int
    entered_at: float
    exited_at: float
    reads: tuple[_CatalogRead, ...]

    def observed(self, evidence_id: str) -> bool:
        """Whether THIS pass read the fact -- merely being alive is not the claim."""

        return any(read.holds(evidence_id) for read in self.reads)


class _SweepWitness:
    """The one entry point both loops call, with every attempt's window recorded.

    The lifespan calls ``runtime.liveness_sweeper.refresh`` from the startup prime, from the
    observation owner, and from the notifier pass; this object IS that attribute, so recording here
    sees every caller without changing what any of them does.

    Those callers are different threads, so the attempt number is allocated under one lock together
    with the entry snapshot, and the finished record is appended under that same lock: a torn number
    would let two attempts share an index, and ``measure()`` compares that index to choose the
    consuming sweep.
    """

    def __init__(
        self,
        sweeper: TerminalCatalogLivenessSweeper,
        clock: _DeadlineClock,
        host: _LiveHost,
        timeline: list[str],
    ) -> None:
        self.timeline = timeline
        self.records: list[_SweepCall] = []
        self._sweeper = sweeper
        self._clock = clock
        self._host = host
        self._order = _Sequence()

    @property
    def calls(self) -> int:
        """How many ``refresh()`` attempts have started: the last number the sequence handed out."""

        return self._order.value

    def refresh(self) -> list[TerminalCatalogEntry]:
        with self._order.lock:
            index = self._order.next()
            entered = self._clock.seconds
            probed = self._host.probes
            self.timeline.append(f"call {index} started")
        try:
            return self._sweeper.refresh()
        finally:
            with self._order.lock:
                self.timeline.append(f"call {index} ended")
                self.records.append(
                    _SweepCall(index, entered, self._clock.seconds, self._host.probes - probed)
                )

    @property
    def full_sweeps(self) -> list[_SweepCall]:
        return [record for record in self.records if record.full_sweep]

    def in_flight_at(self, moment: float) -> _SweepCall | None:
        """The last refresh still running at ``moment``: the pass that can delay the next sweep."""

        running = [
            record for record in self.records if record.entered_at <= moment < record.exited_at
        ]
        return running[-1] if running else None


class _WitnessCatalog(TerminalCatalog):
    """The real catalog with its commit path and its one in-batch hook recorded, not replaced.

    ``_write_disk`` is the store's single durable write: inside a batch it IS the commit, and outside
    one it is the direct write. Recording there gives the commit's exact instant, order, and rows
    while ``batch()``, the RLock that spans it, and the atomic replace stay production code.
    ``upsert`` is sampled only when a case arms the hook, which is how a case holds a sweep inside its
    own open batch.
    """

    def __init__(self, path: Path, clock: _DeadlineClock, sequence: _Sequence) -> None:
        super().__init__(path)
        self._clock = clock
        self._sequence = sequence
        self.commits: list[_Commit] = []
        self.upsert_gate: _Gate | None = None

    def _write_disk(self, entries: list[TerminalCatalogEntry]) -> None:
        super()._write_disk(entries)
        self.commits.append(
            _Commit(
                len(self.commits) + 1, self._sequence.next(), self._clock.seconds, tuple(entries)
            )
        )

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        super().upsert(entry)
        # The hook fires only for the evidence row's own projection while a batch is open, which is
        # the one instant an in-memory observation exists that no reader may treat as truth yet.
        if self.upsert_gate is not None and self._batch is not None and entry.id == EVIDENCE_ROW:
            self.upsert_gate()

    def commit_holding(self, evidence_id: str) -> _Commit | None:
        """The first durable write whose rows carried the fact, or ``None``."""

        return next((commit for commit in self.commits if commit.holds(evidence_id)), None)

    def committed_holds(self, evidence_id: str) -> bool:
        """Whether the fact is durable right now: the committed read, never an in-batch buffer.

        ``include_terminated`` matches ``_Commit.rows`` (what the store actually wrote) and the
        notifier's own ``list(include_terminated=True)``: a filtered read would answer about a
        strictly narrower collection than the claim, and would read as "not durable" for a fact whose
        row had terminated.
        """

        return any(
            row.terminal_evidence_id == evidence_id
            for row in self.list_committed(include_terminated=True)
        )


class _Adapter:
    """The adapter surfaces one sweep reads, plus the one terminal fact a case can make readable.

    Terminal truth is produced outside the dashboard, so the observer is the only reader that can
    turn it into catalog truth: the fact lives here and becomes readable when a case arms it, which
    is what "evidence becomes readable at time T" means for the handoff oracle. The gates park a pass
    exactly where a case needs it: inside a full sweep after the evidence row was already read
    (``overrun``), inside a starting-row fast-path pass (``fastpath``), and inside the consuming
    sweep's own pre-commit work (``commit``).
    """

    def __init__(self, clock: _DeadlineClock) -> None:
        self.clock = clock
        self.readable = False
        self.readable_at: float | None = None
        self.overrun = _Gate()
        self.fastpath = _Gate()
        self.commit = _Gate()
        self.snapshot_reads: list[tuple[str, float]] = []
        self.terminal_reads: list[tuple[str, float]] = []
        self.pane_reads: list[tuple[str, float]] = []

    def make_readable(self) -> None:
        """Arm the terminal settlement: the fact becomes readable at this virtual instant."""

        self.readable = True
        self.readable_at = self.clock.seconds

    def pane(self, tmux_name: str) -> str:
        self.pane_reads.append((tmux_name, self.clock.seconds))
        if tmux_name == f"ar-{STARTING_ROW}":
            self.fastpath()
        return _PANE_TEXT

    def snapshot(self, entry: TerminalCatalogEntry) -> AdapterSnapshot:
        self.snapshot_reads.append((entry.id, self.clock.seconds))
        # A starting row keeps projecting "starting" so the fast path keeps selecting it, exactly as
        # a bridge that has not opened yet does.
        return AdapterSnapshot(
            identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
            control="starting" if entry.id == STARTING_ROW else "ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id="vendor-1",
            raw={},
        )

    def terminal(self, entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
        self.terminal_reads.append((entry.id, self.clock.seconds))
        if entry.id == EVIDENCE_ROW:
            if self.commit.armed:
                self.commit()
            if self.readable:
                return TerminalEvidenceRead(
                    projection=TerminalEvidenceProjection(
                        evidence=TurnTerminalEvidence(outcome="completed", turn_id="turn-1"),
                        evidence_id=ARMED_EVIDENCE_ID,
                        observed_at="2026-08-31T12:00:00+00:00",
                    ),
                    evidence_sequence=7,
                )
        if entry.id == OVERRUN_ROW:
            self.overrun()
        return TerminalEvidenceRead(projection=None)

    def evidence_reads(self) -> list[float]:
        """The virtual instants at which a sweep read the evidence row's terminal truth."""

        return [at for entry_id, at in self.terminal_reads if entry_id == EVIDENCE_ROW]


class _CatalogReads:
    """The notifier loop's own catalog port, delegated in full and recorded on every read.

    ``AgentNotifierContext.catalog`` is built by production code from ``runtime.catalog``; this object
    IS that attribute, so the notifier's real predicate reads land here. ``list()`` is the notifier's
    catalog entry point and the only recorded method; it returns exactly what the real store
    returned, and every other port method is forwarded untouched.
    """

    def __init__(
        self,
        catalog: _WitnessCatalog,
        clock: _DeadlineClock,
        sequence: _Sequence,
        timeline: list[str],
    ) -> None:
        self._catalog = catalog
        self._clock = clock
        self._sequence = sequence
        self._timeline = timeline
        self.reads: list[_CatalogRead] = []

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        attempted = self._sequence.next()
        rows = self._catalog.list(include_terminated=include_terminated)
        record = _CatalogRead(len(self.reads) + 1, attempted, self._sequence.next(), tuple(rows))
        self.reads.append(record)
        self._timeline.append(f"notifier read {record.index}")
        return rows

    def holds(self, evidence_id: str) -> bool:
        """Whether any read the notifier has made so far carried the fact."""

        return any(read.holds(evidence_id) for read in self.reads)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._catalog, name)


class _NotifierPasses:
    """The notifier loop's pass, recorded around the real sweep it runs."""

    def __init__(self, clock: _DeadlineClock, timeline: list[str], reads: _CatalogReads) -> None:
        self.calls = 0
        self.passes: list[_NotifierCall] = []
        self._clock = clock
        self._timeline = timeline
        self._reads = reads

    def __call__(self, ctx: object, *, now: datetime) -> object:
        self.calls += 1
        entered = self._clock.seconds
        first = len(self._reads.reads)
        self._timeline.append(f"notifier pass {self.calls} started")
        result = run_agent_notifier_sweep(cast(Any, ctx), now=now)
        self._timeline.append(f"notifier pass {self.calls} ended")
        self.passes.append(
            _NotifierCall(
                self.calls, entered, self._clock.seconds, tuple(self._reads.reads[first:])
            )
        )
        return result


class _HeartbeatWitness:
    """The real heartbeat store with its end-of-pass tick recorded and a case's gate honoured.

    The tick is the last act of a real sweep, after every catalog read: parking here holds a pass that
    has finished evaluating its snapshot, which is exactly the "in flight and has not consumed that
    truth" state the notifier overrun term is defined on.
    """

    def __init__(self, store: AgentNotifierHeartbeatStore, gate: _Gate) -> None:
        self._store = store
        self._gate = gate
        self.ticks = 0

    def tick(self, **values: object) -> object:
        self.ticks += 1
        self._gate()
        return self._store.tick(**cast(Any, values))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class _SettingsSource:
    """The notifier loop's settings source: every load returns the current frozen snapshot.

    The loop resolves ``enabled`` and the interval from whatever ``load_agentic_settings`` returns on
    that iteration, so replacing the snapshot between passes is how a case flips enablement in place
    -- exactly the production transition the requirement names. Handing out a fresh snapshot per load
    is what keeps a pass's settings its own: a loader that returned one live object would make a
    cached-settings loop indistinguishable from a re-reading one.
    """

    def __init__(self, settings: AgenticSettings) -> None:
        self.current = settings

    def set_enabled(self, *, enabled: bool) -> None:
        knobs = replace(self.current.agent_notifier, enabled=enabled)
        self.current = replace(self.current, agent_notifier=knobs)

    def load(self, _root: Path) -> AgenticSettings:
        return self.current


def _steady_row(
    session_id: str, *, created_at: str, control_state: ControlState = "ready"
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at=created_at,
        last_attached_at=created_at,
        status="running",
        control_state=control_state,
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        control_activity="idle",
        control_acceptance="immediate",
    )


def _seed_rows() -> list[TerminalCatalogEntry]:
    """Three rows in the order a full sweep observes them: evidence, second steady, starting."""

    return [
        _steady_row(EVIDENCE_ROW, created_at="2026-08-31T00:00:00+00:00"),
        _steady_row(OVERRUN_ROW, created_at="2026-08-31T00:01:00+00:00"),
        _steady_row(STARTING_ROW, created_at="2026-08-31T00:02:00+00:00", control_state="starting"),
    ]


@dataclass
class _World:
    """One disposable handoff world: the recorded stores, the adapter, and the real fixture."""

    root: Path
    timeline: list[str] = field(default_factory=list)
    sequence: _Sequence = field(default_factory=_Sequence)
    heartbeat_gate: _Gate = field(default_factory=_Gate)
    upsert_gate: _Gate = field(default_factory=_Gate)
    clock: _DeadlineClock = field(init=False)
    catalog: _WitnessCatalog = field(init=False)
    reads: _CatalogReads = field(init=False)
    passes: _NotifierPasses = field(init=False)
    adapter: _Adapter = field(init=False)
    host: _LiveHost = field(init=False)
    settings: _SettingsSource = field(init=False)
    fixture: _HandoffFixture = field(init=False)
    sweeps: _SweepWitness = field(init=False)

    def __post_init__(self) -> None:
        self.clock = _DeadlineClock(self.timeline)
        self.catalog = _WitnessCatalog(
            self.root / "terminal-sessions.json", self.clock, self.sequence
        )
        self.reads = _CatalogReads(self.catalog, self.clock, self.sequence, self.timeline)
        self.passes = _NotifierPasses(self.clock, self.timeline, self.reads)
        self.adapter = _Adapter(self.clock)
        self.host = _LiveHost()
        self.settings = _SettingsSource(
            AgenticSettings(agent_notifier=AgentNotifierSettings(enabled=True, interval_seconds=N))
        )
        for row in _seed_rows():
            self.catalog.upsert(row)
        self.fixture = _HandoffFixture(self.root, self)
        self.sweeps = self.fixture.sweeps


class _HandoffFixture(_ServingFixture):
    """The shared serving fixture wired to the real notifier loop and the real notifier sweep.

    ``_ServingFixture`` already enters the real ``_serving_lifespan`` under the virtual clock, parks
    every unrelated loop, and patches ``load_agentic_settings``; this subclass supplies the
    collaborators the real notifier loop and the real sweep read, and gives them the recorded
    catalog, host, and heartbeat store instead of mocks.
    """

    def __init__(self, root: Path, world: _World) -> None:
        sweeper = TerminalCatalogLivenessSweeper(
            world.catalog,
            world.host,
            now=world.clock.now,
            probe=LivenessProbe(
                pane_capturer=world.adapter.pane,
                snapshot_reader=world.adapter.snapshot,
                terminal_reader=world.adapter.terminal,
            ),
        )
        super().__init__(
            root,
            _SweepWitness(sweeper, world.clock, world.host, world.timeline),
            clock=world.clock,
        )
        world.host.shutdown = self.shutdown  # type: ignore[attr-defined]
        self.sweeps = cast(_SweepWitness, self.runtime.liveness_sweeper)
        vars(self.runtime).update(
            {
                "catalog": world.reads,
                "host": world.host,
                "paster": mock.Mock(),
                "heartbeat_store": _HeartbeatWitness(
                    AgentNotifierHeartbeatStore(root), world.heartbeat_gate
                ),
                "register_inbox_execution_evidence": None,
            }
        )
        self.world = world
        self.sweep = mock.Mock(side_effect=world.passes)

    def running(self) -> Any:
        return self._running()

    @contextlib.asynccontextmanager
    async def _running(self) -> AsyncIterator[None]:
        async with super().running(
            notifier=True,
            settings=self.world.settings.current,
            notifier_sweep=self.sweep,
        ):
            # The shared fixture patches the loader with one settings object; the loop must instead
            # receive a fresh frozen snapshot per load, which is what production's loader returns and
            # what makes a loop that reads its settings once observably different.
            with mock.patch.object(
                lifespan_module, "load_agentic_settings", self.world.settings.load
            ):
                yield


@dataclass(frozen=True)
class _Handoff:
    """Every term of the scheduling oracle for one measured handoff, from recorded events."""

    readable_at: float
    previous: _SweepCall
    release: float
    consuming: _SweepCall
    commit: _Commit
    consumption: _NotifierCall
    last_notifier_end: float

    @property
    def observer_term(self) -> float:
        """``release - T``: what the observer phase spent before the sweep was even due.

        Read by the default-phase case, which arms the fact strictly after the previous sweep's start
        and asserts the strict form of the phase bound; the oracle does not assert it (see
        ``assert_oracle``).
        """

        return self.release - self.readable_at

    @property
    def overrun(self) -> float:
        """``R_observer``: the in-flight refresh's remaining duration at eligibility."""

        return max(0.0, self.release - (self.previous.entered_at + F))

    @property
    def poll_delay(self) -> float:
        """How long after the release the first lifecycle poll ran; at most ``P``."""

        return self.consuming.entered_at - self.release

    @property
    def commit_duration(self) -> float:
        """``D_commit``: the consuming sweep's own duration up to its catalog commit."""

        return self.commit.at - self.consuming.entered_at

    @property
    def wait(self) -> float:
        """Commit to the notifier pass that consumed it: the remaining sleep, then any overrun."""

        return self.consumption.entered_at - self.commit.at

    @property
    def notifier_overrun(self) -> float:
        """``R_notifier``: the remaining duration of a notifier pass in flight at the commit."""

        return max(0.0, self.last_notifier_end - self.commit.at)

    @property
    def latency(self) -> float:
        """The observed handoff latency: evidence readable to the notifier pass starting."""

        return self.consumption.entered_at - self.readable_at

    @property
    def bound(self) -> float:
        """``F + P + R_observer + D_commit + N + R_notifier`` with every term as measured."""

        return F + P + self.overrun + self.commit_duration + N + self.notifier_overrun


class _HandoffCase(unittest.IsolatedAsyncioTestCase):
    """One disposable serving runtime whose two real loops a case steps one sleep at a time.

    The driver's primitives release one parked sleep each: ``observer_poll`` for the observation owner
    and ``notifier_poll`` for the notifier loop. Only the loop whose sleep was released moves, so a
    case chooses each pass's exact virtual instant instead of racing two cadences, and every
    measurement is a difference of virtual timestamps or sequence numbers.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.world = _World(Path(self._dir.name))
        self.clock = self.world.clock
        self.catalog = self.world.catalog
        self.adapter = self.world.adapter
        self.passes = self.world.passes
        self.reads = self.world.reads
        self.sweeps = self.world.sweeps
        self.fixture = self.world.fixture

    def tearDown(self) -> None:
        self._dir.cleanup()

    # --- driver ---------------------------------------------------------------------------------

    async def settle(self) -> None:
        """Both loops are between passes: each has its own sleep parked."""

        await _wait_until(lambda: self.clock.has_pending(P) and self.clock.has_pending(N))

    async def observer_poll(self, *, gate: _Gate | None = None) -> None:
        """Release the observer's parked poll; return when its attempt settled or parked in ``gate``."""

        if not self.clock.has_pending(P):
            # The owner may still be inside an attempt a gate just released: let it re-park first.
            await _wait_until(lambda: self.clock.has_pending(P))
        self.clock.release_delay(P)
        # The released sleep leaves the queue, so its loop re-parking is what proves this attempt
        # settled: waiting on the record alone would race the loop back to its own cadence.
        await _wait_until(
            lambda: (gate is not None and gate.entered.is_set()) or self.clock.has_pending(P)
        )

    async def notifier_poll(self, *, gate: _Gate | None = None) -> None:
        """Release the notifier's interval; return when its pass settled or parked in ``gate``."""

        if not self.clock.has_pending(N):
            # The loop may still be inside a pass a gate just released: let it re-park first.
            await _wait_until(lambda: self.clock.has_pending(N))
        self.clock.release_delay(N)
        # A disabled loop re-parks without running a pass at all, so re-parking -- not a new pass --
        # is what proves this interval was consumed.
        await _wait_until(
            lambda: (gate is not None and gate.entered.is_set()) or self.clock.has_pending(N)
        )

    async def poll_until(self, reached: Callable[[], bool], *, gate: _Gate | None = None) -> None:
        """Poll the observation owner until ``reached`` holds, or its pass parks in ``gate``."""

        for _ in range(_STEP_LIMIT):
            if reached():
                return
            await self.observer_poll(gate=gate)
        raise AssertionError("the observation owner never reached the requested state")

    async def wait_for_sweep_record(self, *, after: int) -> None:
        """Wait until one more ``refresh()`` attempt has returned."""

        await _wait_until(lambda: len(self.sweeps.records) > after)

    async def wait_for_notifier_pass(self, *, after: int) -> None:
        """Wait until one more notifier pass has returned."""

        await _wait_until(lambda: len(self.passes.passes) > after)

    async def drive_to_full_sweep(self) -> _SweepCall:
        """Poll until one more full sweep has completed, and return it."""

        before = len(self.sweeps.full_sweeps)
        await self.poll_until(lambda: len(self.sweeps.full_sweeps) > before)
        return self.sweeps.full_sweeps[-1]

    async def drive_to_commit(self) -> _Commit:
        """Poll until the fact is durable, and return its commit."""

        await self.poll_until(lambda: self.catalog.commit_holding(ARMED_EVIDENCE_ID) is not None)
        commit = self.catalog.commit_holding(ARMED_EVIDENCE_ID)
        assert commit is not None
        return commit

    def arm_the_fact_at(self, started_at: float) -> None:
        """Make the evidence readable in the instant ``started_at`` -- the previous sweep's start.

        The virtual clock does not move while a pass works, so arming here is exactly
        ``T = previous_full_sweep_start``: the worst phase the oracle bounds, where the evidence
        exists in the same instant as the sweep that must not consume it.
        """

        self.arm_the_fact_after(started_at, 0.0)

    def arm_the_fact_after(self, started_at: float, delay: float) -> None:
        """Make the evidence readable ``delay`` strictly after the previous sweep's start.

        The default-phase case arms here, which is the packet's conforming example -- evidence that
        becomes readable shortly after its row was visited -- and the only arming that makes the
        observer phase strictly smaller than its ``F + R_observer`` maximum.
        """

        self.clock.elapse(delay)
        self.adapter.make_readable()
        self.assertEqual(self.adapter.readable_at, started_at + delay)

    # --- measurement ----------------------------------------------------------------------------

    def consume(self) -> _NotifierCall:
        """The first notifier pass whose own read carried the fact."""

        for call in self.passes.passes:
            if call.observed(ARMED_EVIDENCE_ID):
                return call
        raise AssertionError("no notifier pass ever read the committed fact")

    def last_notifier_end_before(self, commit: _Commit) -> float:
        """When the last notifier pass that could not have seen the fact finished."""

        earlier = [
            call
            for call in self.passes.passes
            if call.entered_at <= commit.at and not call.observed(ARMED_EVIDENCE_ID)
        ]
        if not earlier:
            raise AssertionError("no notifier pass ran before the commit")
        return earlier[-1].exited_at

    def measure(self, previous: _SweepCall) -> _Handoff:
        """Read every oracle term out of the recorded events for one handoff."""

        commit = self.catalog.commit_holding(ARMED_EVIDENCE_ID)
        assert commit is not None, "the fact was never committed"
        eligibility = previous.entered_at + F
        in_flight = self.sweeps.in_flight_at(eligibility)
        release = eligibility if in_flight is None else max(eligibility, in_flight.exited_at)
        consuming = next(
            record
            for record in self.sweeps.full_sweeps
            if record.index > previous.index and record.entered_at >= release
        )
        assert self.adapter.readable_at is not None, "the evidence was never made readable"
        return _Handoff(
            readable_at=self.adapter.readable_at,
            previous=previous,
            release=release,
            consuming=consuming,
            commit=commit,
            consumption=self.consume(),
            last_notifier_end=self.last_notifier_end_before(commit),
        )

    def assert_oracle(self, handoff: _Handoff) -> None:
        """Assert the bound and its decomposition term by term, rather than illustrate it.

        Every assertion here can fail in a state this harness can reach; the relations that cannot
        are documented instead of asserted, and each is named with the reason it is unfalsifiable:

        * ``latency`` telescopes into its own terms -- it IS ``consumption.entered_at -
          readable_at`` and the terms sum to exactly that by their definitions -- so the
          decomposition ``latency = (release - T) + poll_delay + D_commit + wait`` holds in every
          state, reachable or not, and is recorded here rather than asserted.
        * The observer phase ``release - T`` is at most ``F + R_observer`` for the same reason
          ``overrun`` is derived from the same ``release``: writing
          ``release = previous.entered_at + F + R_observer`` makes the phase
          ``F + R_observer - (T - previous.entered_at)``, which holds for every ``T`` at or after the
          previous sweep's start. Only arming the fact BEFORE that start reverses it, and the
          oracle's own premise excludes that state -- so it is not asserted here. The strict form is
          asserted by the one case that arms later than that start, where it is a constraint on that
          case's scenario rather than on production.
        * ``bound`` is by definition the sum of the terms compared to ``latency`` below.
        * The selection minimality -- the measured pass IS the first full sweep eligible after the
          release -- is ``measure()``'s own selection filter, so restating it here would assert an
          identity; the requirement's clause is carried by the ``poll_delay`` bound and the cases'
          window assertions instead. The reason and the measured attempts to make it falsifiable are
          recorded at the point of use below.
        """

        # The selection minimality -- "the measured pass IS the first full sweep eligible after the
        # release" -- is NOT asserted here, because it cannot be: ``measure()`` already selected
        # ``consuming`` with ``next(record for record in self.sweeps.full_sweeps if record.index >
        # previous.index and record.entered_at >= release)``, so restating that filter below would
        # rebuild the byte-identical predicate out of the same record list, the same ``release`` and
        # the same ``previous`` -- an identity that holds in every state, reachable or not (filed as
        # ``F-L04-2``). The requirement's clause is carried by the assertions that CAN fail:
        # ``poll_delay <= P`` below (its "at most P later" half, the reached failure site for a late
        # or skipped poll) and each case's own window assertions.
        #
        # Re-anchoring the claim on the arming instant instead -- min over full sweeps with
        # ``entered_at >= handoff.readable_at`` -- was built and measured, and it is also implied in
        # every state these cases reach, because a full sweep admitted inside one ``F`` window after
        # the arming also commits the fact and so lands on a case's own premise assertion first.
        # Measured on /tmp copies: halving the rate limit (``_rate_limited`` against
        # ``sweep_interval_seconds / 2``) fails 5 of the 8 cases, the default-phase one on its own
        # premise assertion, and with those premise assertions relaxed so the case reaches this
        # method it passes here and the run fails later, on the case's own post-oracle assertion;
        # widening the fast path to probe every running row fails 5 of 8 the same way; arming the
        # default-phase fact after its release fails that case's premise in 1. So the relation is
        # recorded rather than asserted -- the disposition this docstring gives the other relations
        # that cannot fail.

        # ``D_commit`` is the time from the consuming sweep's START to the commit, so the commit
        # cannot precede that start. Nothing else here implies it: in the state that breaks it -- the
        # fact made durable by an earlier refresh (the notifier pass's own inline ``refresh()``, or an
        # observer poll) at an instant the sweeper was not rate-limited -- every other assertion in
        # this method still holds, so this line is the one that fires. Verified off-suite on that
        # state, where the definitional line it replaced passed.
        self.assertGreaterEqual(handoff.commit.at, handoff.consuming.entered_at)
        # The next notifier pass starts one whole interval after the last pass that could not have
        # seen the fact finished: completion-relative, never re-based on the commit and never queued.
        # This is the reached failure site when the full sweep runs late (verified: the
        # sweep-due-at-2F violation fails this equality in 2 cases).
        self.assertAlmostEqual(handoff.consumption.entered_at, handoff.last_notifier_end + N)
        # ``poll_delay`` is non-negative by the minimality above, so only its upper bound is a claim:
        # a skipped or late poll lands here, and it is the reached failure site for a late full sweep
        # (verified: the sweep-due-at-2F violation fails this line in 3 cases).
        self.assertLessEqual(handoff.poll_delay, P)
        # The wait for the consuming pass is its remaining notifier interval plus any pass still in
        # flight at the commit. It is IMPLIED by the cadence equality above -- ``wait`` is
        # ``consumption.entered_at - commit.at`` and the right-hand side floors that same difference
        # -- so a state that breaks it breaks the equality too and the equality fires first. It is
        # the composition's term bound, not an independent claim.
        self.assertLessEqual(handoff.wait, N + handoff.notifier_overrun)
        # ... and this is the composed headline the requirement names: implied by the term assertions
        # above and failing in the same reachable states they do (a late full sweep exceeds the bound
        # by exactly the ``poll_delay`` overshoot, which is why it is not the reached site there).
        # Kept as the claim stated over measured terms rather than assumed.
        self.assertLessEqual(handoff.latency, handoff.bound)
