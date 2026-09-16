"""The headless production chain: a worker's canonical turn truth wakes its current manager.

The relay suite proves the consumer after a test has already written ``turn-ended`` and
``completed`` into the catalog, so a missing production evidence producer cannot redden it. This
module is the composition contract instead: it starts the REAL serving lifespan
(``_serving_lifespan``) around a controlled test runtime, so the real ``_terminal_observation_loop``
and the real ``_agent_notifier_loop`` are the only producers of every fact asserted below.

The chain, link by link, and where each link is production code:

* the controlled adapter port publishes one native worker turn carrying the vendor's own end reason;
  the canonical projection is lifted by the production ``latest_native_terminal_evidence`` over the
  production projector registry -- this module never types a turn state, an outcome, or an evidence
  identity into a row;
* the real ``TerminalCatalogLivenessSweeper`` observes it into the real ``TerminalCatalog`` on the
  cadence the lifespan owns, and the assertion before that pass proves the row carried no terminal
  claim at all;
* the real ``run_agent_notifier_sweep``, run by the real notifier loop over the real notifier context
  the lifespan builds, derives the state-signal finding, resolves the worker's CURRENT manager by
  structure, and persists one durable inbox row;
* that row is delivered through the real delivery path to the manager's control port. The one fake
  on that path is the control process itself.

What is faked, and where the fakes stop: the tmux host and the adapter readers are doubles at the
external process/control boundary. Nothing inside the catalog, projection, notifier, inbox or
routing path is replaced, and the only callable substituted for a production one delegates to it in
full (``_NotifierSweep``).

The guards that keep the three exclusions honest:

1. no terminal-session GET: the case registers the PRODUCTION
   ``GET /api/terminal/sessions`` route on the app under test -- through the production registrar,
   against the same runtime the loops read -- so the dashboard surface really exists;
   ``_SessionsRouteTripwire`` replaces the payload helper the handler resolves as a module global,
   so a route invocation, by this test, a later edit, or production, fails this case instead of
   quietly becoming the producer. ``_sessions_route`` asserts the route is present, the case asserts
   the handler resolves that global, the refusal is asserted to fire when reached, and zero
   tripwire calls are asserted.
2. no seeded truth: both structural rows are created with no terminal claim; the assertion before
   the first observation pass proves it; and this module never writes ``terminal_outcome``,
   ``terminal_evidence_id``, or ``state_signal_emitted_for``.
3. clean shutdown: the lifespan's own teardown is asserted to cancel the observer and notifier it
   created, leave no clock cadence parked, and call ``host.shutdown()`` exactly once.

What this case does NOT cover, and who owns it: it drives no dashboard client (it proves the wake
never needed one rather than exercising the read route); it measures no latency bound
(``LOCR-R04@v1``); the busy-owner hold, replacement, restart, and role-specific wakes stay with
``LOCR-R09``, ``R08``, ``R10``, ``R05``/``R06``/``R07``; and it adds no durable artifact of its own
(``LOCR-R26@v1``): every fixture it needs lives in this module or in the shared test support it
imports.

The scheduler is virtual and step-driven (``_DeadlineClock``): each case releases one parked cadence
at a time, so the observer's poll is the only thing that advances the timeline until the notifier is
deliberately released. That is what makes "the observation loop is the producer" a property of the
run rather than of the order two real timers happened to interleave in.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _handoff_clock import _DeadlineClock
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.agentic_settings import AgenticSettings, AgentNotifierSettings
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    SubmissionReceipt,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    DEFAULT_LIVENESS_HYSTERESIS,
    TerminalCatalogEntry,
)
from agents_remember.serving import _app_terminal_routes
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_evidence import TerminalEvidenceRead
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    SnapshotReader,
    TerminalCatalogLivenessSweeper,
    TerminalEvidenceReader,
)
from agents_remember.serving.terminal_observer_health import TerminalObserverHealthPublisher
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from fastapi import FastAPI
from fastapi.routing import APIRoute
from test_lifecycle_owned_completion_relay_reviewer import _terminal_projection
from test_serving_observation_loop import _ServingFixture, _wait_until
from test_state_signal_relay import _accepted_paster, _write_task_topology

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
"""The virtual clock's own origin: the moment the serving lifespan starts."""

OBSERVER_POLL_SECONDS = 1.0
"""The lifespan's observer cadence: ``_terminal_observation_loop``'s own sleep, released one at a time."""

NOTIFIER_INTERVAL_SECONDS = 10.0
"""The notifier loop's production interval, parked on its own distinct delay."""

SPRINT = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")
MANAGER = "manager-1"
ORCHESTRATOR = "orchestrator-1"
WORKER = "worker-1"
HARNESS = "pi"
WORKER_TURN = "worker-turn-1"
WORKER_EVIDENCE_ID = f"native:{WORKER_TURN}"
TERMINAL_SESSIONS_PATH = "/api/terminal/sessions"

_DRIVE_LIMIT = 40
"""Cadences one case may release before a stage is declared unreached: a bound, never a cadence."""

_REAL_SLEEP = asyncio.sleep
"""The real sleep, captured before the serving fixture swaps ``asyncio.sleep`` for the virtual clock."""


class _SessionsRouteCalled(AssertionError):
    """The terminal-session GET payload path was reached, which this chain must never do."""


def _entry(session_id: str, **overrides: object) -> TerminalCatalogEntry:
    fields: dict[str, object] = dict(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness=HARNESS,
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=(HARNESS,),
        created_at=NOW.isoformat(),
        last_attached_at=NOW.isoformat(),
        status="running",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        control_activity="idle",
        control_acceptance="immediate",
    )
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)  # type: ignore[arg-type]


def _manager(session_id: str = MANAGER) -> TerminalCatalogEntry:
    """The master-scoped manager: at a turn boundary, so the durable row may land on this pass."""

    return _entry(
        session_id,
        task_document_ref=MASTER,
        seat_role="manager",
        spawn_role="manager",
        turn_state="turn-ended",
        turn_state_changed_at=NOW.isoformat(),
    )


def _worker(session_id: str = WORKER) -> TerminalCatalogEntry:
    """The subordinate leaf seat BEFORE any observation: no terminal claim of any kind.

    Nothing here carries ``turn_state``, ``terminal_outcome``, ``terminal_evidence_id`` or
    ``state_signal_emitted_for``: the whole point of the case is that the serving lifespan produces
    them, so the fixture may not hand them to it.
    """

    return _entry(
        session_id,
        task_document_ref=LEAF,
        seat_role="worker",
        spawn_role="worker",
        spawned_by_session=MANAGER,
    )


def _sprint_orchestrator(session_id: str = ORCHESTRATOR) -> TerminalCatalogEntry:
    """The sprint-scoped orchestrator: the manager's own live structural owner.

    Present so this world contains exactly the one signal under test: without a live occupant of the
    sprint seat, the notifier's own dead-upstream fact for the manager would put unrelated durable
    rows in the store the assertion below reads whole.
    """

    return _entry(
        session_id,
        task_document_ref=SPRINT,
        seat_role="orchestrator",
        spawn_role="orchestrator",
        turn_state="working",
        turn_state_changed_at=NOW.isoformat(),
    )


def _accepted_receipt(request_id: str) -> SubmissionReceipt:
    return SubmissionReceipt(
        request_id=request_id,
        acceptance="immediate",
        submitted_at=NOW.isoformat(),
        accepted_at=NOW.isoformat(),
    )


def _bridge_terminal_error() -> TerminalEvidenceRead:
    """The adapter's own answer while no worker turn has been published."""

    return TerminalEvidenceRead(projection=None)


class _Bridge:
    """One controlled adapter/control endpoint, at the external process boundary only.

    ``arm`` is the case's control over WHEN the worker's turn settles, which is how "evidence
    arrives after a sweep" is expressible at all. It models the two adapter surfaces a real turn
    leaves behind, in the order production sees them: the vendor's own terminal reason appears on the
    evidence stream while the process is still read as busy, and the pass AFTER that reads the seat
    idle with no new terminal claim -- which is what settles ``turn_state`` to ``turn-ended``. The
    projection itself is built by the production lift over the production projector registry, not by
    this module.

    Every OTHER observed seat answers from ``activities``, which is why the world contains exactly the
    signal under test: the manager reads idle-and-ready so it stays at the turn boundary its row was
    created with (a busy owner would hold the wake, which ``LOCR-R09@v1`` owns), and the sprint
    orchestrator keeps reading a live turn so the manager's own upstream is not a separate fact.
    """

    def __init__(self, session_id: str, *, activities: Mapping[str, str]) -> None:
        self.session_id = session_id
        self.activities = dict(activities)
        self.readable = False
        self.settled = False
        self.terminal_reads = 0

    def arm(self) -> None:
        self.readable = True

    def pane(self, _tmux_name: str) -> str:
        return "working on the leaf"

    def _snapshot(self, entry: TerminalCatalogEntry, activity: str) -> AdapterSnapshot:
        return AdapterSnapshot(
            identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
            control="ready",
            activity=cast(Any, activity),
            acceptance="immediate",
            vendor_session_id=f"vendor-{entry.id}",
            raw={},
        )

    def snapshot(self, entry: TerminalCatalogEntry) -> AdapterSnapshot:
        """A live, ready control connection: liveness and readiness are not terminal truth."""

        if entry.id != self.session_id:
            return self._snapshot(entry, self.activities.get(entry.id, "running"))
        return self._snapshot(entry, "idle" if self.settled else "running")

    def terminal(self, entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
        """One terminal claim per turn, then no new claim: the reader's cursor advanced."""

        if entry.id != self.session_id or not self.readable or self.settled:
            return _bridge_terminal_error()
        self.settled = True
        self.terminal_reads += 1
        return _terminal_projection("stop", WORKER_TURN)


class _RecordingHost:
    """The tmux/process port: every seat is alive, and NO session is attached to this process.

    ``get`` answers ``None`` for every id -- there is no live PTY client anywhere in this world --
    which is what makes the case headless by construction: the truth it observes comes from adapter
    evidence the sweep read, never from a terminal this process is holding open. ``probe_session``
    records the tmux liveness contact instead, so "the sweep really reached the host" stays measured.
    """

    def __init__(self) -> None:
        self.probed: list[str] = []
        self.shutdown = mock.Mock()

    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return self.probe_session(tmux_name).exists

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        self.probed.append(tmux_name)
        return TmuxProbeResult(exists=True, evidence="alive")


class _NotifierSweep:
    """The callable the notifier loop's own module global resolves to: the production sweep.

    The loop, the context it builds, the predicates, the stores and the durable row are all
    production code -- this object only records that a pass happened and forwards the call to
    ``run_agent_notifier_sweep`` in full, returning its result unchanged. It is bound at the same
    seam ``_ServingFixture`` already exposes (and that the handoff fixture binds the same way), so
    the substitution is visible as one decision at one call site rather than a replacement of the
    path being proven.
    """

    def __init__(self) -> None:
        self.passes = 0

    def __call__(self, ctx: AgentNotifierContext, *, now: datetime) -> object:
        self.passes += 1
        return run_agent_notifier_sweep(ctx, now=now)


class _SessionsRouteTripwire:
    """The terminal-session route's payload helper, replaced by a refusal.

    The route is registered from production code against the runtime the loops read, so the dashboard
    surface the waiver names really exists in this app object. Reaching its payload helper is the one
    way a request could produce catalog truth here, so the helper is where the refusal lives: the
    handler resolves ``_catalog_payload`` as a module global at call time, which the case proves.
    """

    def __init__(self) -> None:
        self.calls: list[TerminalCatalogEntry] = []

    def __call__(self, entry: TerminalCatalogEntry) -> dict[str, object]:
        self.calls.append(entry)
        raise _SessionsRouteCalled(
            "the GET /api/terminal/sessions payload path was reached; this chain must need no "
            "terminal-session request and must never call the route handler as a helper"
        )


def _sessions_route(app: FastAPI) -> APIRoute:
    """The production terminal-session route as registered on this app object, or a loud refusal."""

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == TERMINAL_SESSIONS_PATH:
            return route
    raise AssertionError(
        f"{TERMINAL_SESSIONS_PATH} is not registered on the app under test, so the no-GET guard "
        "would be vacuous"
    )


class _RelayWorld:
    """One disposable serving world: real stores, real observers, one controlled adapter port."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.coordination_root = root / "ar-coordination"
        _write_task_topology(self.coordination_root)
        self.observer_root = self.coordination_root / "logs" / "observer"
        self.timeline: list[str] = []
        self.clock = _DeadlineClock(self.timeline)
        self.catalog = TerminalCatalog(root / "terminal-sessions.json")
        self.inbox_store = OperatorInboxStore(self.observer_root)
        self.adapter = _Bridge(WORKER, activities={MANAGER: "idle"})
        self.host = _RecordingHost()
        self.sweep = _NotifierSweep()
        self.settings = AgenticSettings(
            agent_notifier=AgentNotifierSettings(
                enabled=True, interval_seconds=NOTIFIER_INTERVAL_SECONDS
            )
        )
        snapshot_reader: SnapshotReader = self.adapter.snapshot
        terminal_reader: TerminalEvidenceReader = self.adapter.terminal
        self.sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            cast(TerminalHost, self.host),
            now=self.clock.now,
            probe=LivenessProbe(
                hysteresis=DEFAULT_LIVENESS_HYSTERESIS,
                pane_capturer=self.adapter.pane,
                snapshot_reader=snapshot_reader,
                terminal_reader=terminal_reader,
            ),
        )
        for row in (_manager(), _sprint_orchestrator(), _worker()):
            self.catalog.upsert(row)
        self.fixture = _RelayFixture(root, self)

    def row(self, session_id: str) -> TerminalCatalogEntry:
        entry = self.catalog.get(session_id)
        if entry is None:
            raise AssertionError(f"the catalog no longer holds {session_id}")
        return entry

    def durable_rows(self) -> list[OperatorInboxEntry]:
        """Every durable inbox row, as written: the store's own read, not a folded projection."""

        return self.inbox_store.read()

    def state_signals(self) -> list[OperatorInboxEntry]:
        return [
            row for row in self.inbox_store.current().values() if row.messageKind == "state-signal"
        ]

    def describe(self) -> str:
        worker = self.catalog.get(WORKER)
        return (
            f"clock={self.clock.seconds}s observer_calls={len(self.timeline)} "
            f"notifier_passes={self.sweep.passes} worker="
            f"{(worker.turn_state, worker.terminal_outcome, worker.terminal_evidence_id) if worker else None} "
            f"durable_rows={len(self.durable_rows())}"
        )


class _RelayFixture(_ServingFixture):
    """The shared serving fixture wired to this world's real stores and adapter port.

    ``_ServingFixture`` already enters the real ``_serving_lifespan`` under the virtual clock with
    every unrelated loop parked; this subclass supplies the collaborators the real observer and the
    real notifier read, so the runtime under test is a controlled one and the composition around it
    is production code.
    """

    def __init__(self, root: Path, world: _RelayWorld) -> None:
        super().__init__(root, world.sweeper, clock=world.clock)
        vars(self.runtime).update(
            {
                "config": type("_Config", (), {"coordination_root": world.coordination_root})(),
                "observer_root": world.observer_root,
                "catalog": world.catalog,
                "host": world.host,
                "paster": _accepted_paster(),
                "heartbeat_store": AgentNotifierHeartbeatStore(world.observer_root),
                "liveness_config": DEFAULT_LIVENESS_HYSTERESIS,
                "observer_health": TerminalObserverHealthPublisher(
                    world.observer_root, world.clock.now
                ),
                "register_inbox_execution_evidence": None,
            }
        )
        self.world = world

    def serving(self) -> Any:
        """Enter the real lifespan with the real notifier loop running the production sweep."""

        return self.running(
            notifier=True,
            settings=self.world.settings,
            notifier_sweep=mock.Mock(side_effect=self.world.sweep),
        )


async def _release(clock: _DeadlineClock, delay: float, *, stage: str) -> None:
    """Release one parked cadence of exactly ``delay``, and let only its loop take the turn."""

    try:
        await _wait_until(lambda: clock.has_pending(delay))
    except AssertionError as error:
        raise AssertionError(
            f"{stage}: no serving loop ever parked its {delay}s cadence, so the production chain "
            f"has no producer to advance ({error})"
        ) from error
    clock.release_delay(delay)
    await _REAL_SLEEP(0)


async def _drive(
    world: _RelayWorld,
    reached: Any,
    *,
    delay: float,
    stage: str,
) -> None:
    """Release ``delay`` cadences until ``reached`` holds, or fail naming the stage that stalled."""

    for _ in range(_DRIVE_LIMIT):
        if reached():
            return
        await _release(world.clock, delay, stage=stage)
    raise AssertionError(f"{stage} was never reached; the chain stalled at: {world.describe()}")


def _loop_tasks(fixture: _RelayFixture, qualname: str) -> list[asyncio.Task[object]]:
    return [
        task for task in fixture.created if getattr(task.get_coro(), "__qualname__", "") == qualname
    ]


class LifecycleOwnedCompletionRelayTests(unittest.IsolatedAsyncioTestCase):
    """One worker chain, end to end, through the serving lifespan and nothing else."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.world = _RelayWorld(Path(self._dir.name))
        self.fixture = self.world.fixture
        self.tripwire = _SessionsRouteTripwire()
        # The production registrar, against the same runtime the background loops read: the route
        # this chain must not need is genuinely present on the app under test.
        _app_terminal_routes._register_terminal_session_routes(
            self.fixture.app, cast(Any, self.fixture.runtime)
        )

    async def test_worker_completion_reaches_current_manager_without_terminal_get(
        self,
    ) -> None:
        """A worker's canonical terminal truth reaches its current manager with no GET and no seeding.

        The catalog row starts with no terminal claim, no read route is called, and nothing but the
        serving lifespan's own loops runs. A relay fed by a typed-in ``completed`` row, a chain whose
        producer is the terminal-session read route, a catalog path bypassed by a direct row write,
        and a shutdown that leaves a loop running each redden here, at the stage they stall.
        """

        route = _sessions_route(self.fixture.app)
        handler = cast(Any, route.endpoint)
        # The guard is live: the handler resolves the tripwire's global at call time, so a route
        # invocation cannot avoid the refusal, and the refusal itself is proven to fire -- on a
        # separate instance, so the armed tripwire's own record stays a record of real traffic.
        self.assertIn("_catalog_payload", handler.__code__.co_names)
        with self.assertRaises(_SessionsRouteCalled):
            _SessionsRouteTripwire()(_worker())

        with mock.patch.object(_app_terminal_routes, "_catalog_payload", self.tripwire):
            async with self.fixture.serving():
                self.assert_started_truthless()
                await _drive(
                    self.world,
                    lambda: self.world.sweep.passes >= 1,
                    delay=NOTIFIER_INTERVAL_SECONDS,
                    stage="the notifier loop's own first pass",
                )
                self.assertEqual(self.world.durable_rows(), [])

                # The worker turn becomes readable only now: after the startup prime and after a
                # notifier pass have both already swept the same row without any terminal claim.
                self.world.adapter.arm()
                await _drive(
                    self.world,
                    lambda: (
                        self.world.row(WORKER).turn_state == "turn-ended"
                        and self.world.row(WORKER).terminal_evidence_id == WORKER_EVIDENCE_ID
                    ),
                    delay=OBSERVER_POLL_SECONDS,
                    stage="the observation loop producing canonical worker terminal truth",
                )
                self.assert_observed_truth_from_the_adapter()

                with mock.patch(
                    "agents_remember.serving.inbox_delivery.submit_control_prompt",
                    side_effect=lambda _target, _text, submission: _accepted_receipt(
                        submission.request_id
                    ),
                ) as submit:
                    await _drive(
                        self.world,
                        lambda: bool(self.world.state_signals()),
                        delay=NOTIFIER_INTERVAL_SECONDS,
                        stage="the notifier loop deriving and persisting the manager signal",
                    )
                    wake = self.assert_one_attributed_wake()
                    self.assertEqual(submit.call_count, 1)
                    self.assertEqual(wake.deliveryState, "delivered")
                    self.assertEqual(wake.adapterDeliveryState, "accepted")

                    # One more production cadence re-reads the same evidence identity: the durable
                    # signal is one row, not one row per sweep.
                    landed = sorted(row.id for row in self.world.state_signals())
                    await _release(
                        self.world.clock, NOTIFIER_INTERVAL_SECONDS, stage="the repeat sweep"
                    )
                    self.assertEqual(sorted(row.id for row in self.world.state_signals()), landed)
                    self.assertEqual(submit.call_count, 1)

                self.assertEqual(self.tripwire.calls, [])

        self.assert_shut_down_cleanly(route)

    # -- assertions -------------------------------------------------------------------------

    def assert_started_truthless(self) -> None:
        """No terminal claim, no evidence identity, and no marker exist before the steady pass.

        The row's own ``turn_state`` is not part of this claim: it is the adapter's LIVE-turn reading
        that the pre-serve observation prime already produced, and the case asserts it positively so
        the refusal below is about terminal truth rather than about a row nothing ever observed.
        """

        row = self.world.row(WORKER)
        self.assertEqual(row.turn_state, "working")
        self.assertIsNone(row.terminal_outcome)
        self.assertIsNone(row.terminal_evidence_id)
        self.assertIsNone(row.state_signal_emitted_for)
        self.assertEqual(self.world.durable_rows(), [])
        self.assertEqual(self.world.adapter.terminal_reads, 0)
        self.assertEqual(self.world.row(MANAGER).turn_state, "turn-ended")

    def assert_observed_truth_from_the_adapter(self) -> None:
        """The outcome and its identity were produced by the observation pass, not typed in."""

        row = self.world.row(WORKER)
        self.assertEqual(row.turn_state, "turn-ended")
        self.assertEqual(row.terminal_outcome, "completed")
        self.assertEqual(row.terminal_evidence_id, WORKER_EVIDENCE_ID)
        # The adapter port was actually read, so the truth above is an observation result rather
        # than a row this module wrote: the catalog's own claim and the port's agree.
        self.assertGreaterEqual(self.world.adapter.terminal_reads, 1)
        self.assertGreaterEqual(len(self.world.host.probed), 1)

    def assert_one_attributed_wake(self) -> OperatorInboxEntry:
        """Exactly one durable signal for this worker, addressed to its CURRENT structural manager.

        The store is read whole rather than filtered, so an unexpected extra row fails here. The
        world's one other signal is named explicitly: once the manager and its own subordinate are
        both at a boundary, the notifier's compound-idle episode for the MANAGER is a separate
        production finding about a different subject (it is addressed to the manager's own owner, the
        sprint orchestrator, and its subject is the manager), and it belongs to the sibling
        requirements rather than to this chain.
        """

        signals = self.world.state_signals()
        self.assertEqual(
            sorted(
                (row.messageKind, row.subjectAgentId, row.seatRole, row.agentId) for row in signals
            ),
            [
                ("state-signal", MANAGER, "manager", ORCHESTRATOR),
                ("state-signal", WORKER, "worker", MANAGER),
            ],
            signals,
        )
        wake = next(row for row in signals if row.subjectAgentId == WORKER)
        self.assertEqual(wake.agentId, MANAGER)
        self.assertEqual(wake.recipientRole, "manager")
        self.assertEqual(wake.seatRole, "worker")
        self.assertEqual(wake.subjectTaskDocumentRef, LEAF)
        self.assertEqual(wake.taskDocumentRef, MASTER)
        self.assertIn("completed", wake.ask)
        self.assertIn(WORKER_EVIDENCE_ID, wake.ask)
        self.assertIn(WORKER_EVIDENCE_ID, wake.response)
        self.assertEqual(self.world.row(WORKER).state_signal_emitted_for, WORKER_EVIDENCE_ID)
        # Durable, not merely in-memory: the store's own append-only read holds exactly these two
        # entries. The log records every transition, so it is compared by entry identity.
        self.assertEqual(
            sorted({row.id for row in self.world.durable_rows()}),
            sorted(row.id for row in signals),
        )
        return wake

    def assert_shut_down_cleanly(self, route: APIRoute) -> None:
        """The real lifespan's teardown took the loops it created with it."""

        observers = _loop_tasks(self.fixture, "_terminal_observation_loop")
        notifiers = _loop_tasks(self.fixture, "_agent_notifier_loop")
        self.assertEqual(len(observers), 1, observers)
        self.assertEqual(len(notifiers), 1, notifiers)
        self.assertTrue(observers[0].cancelled())
        self.assertTrue(notifiers[0].cancelled())
        self.assertTrue(all(task.done() for task in self.fixture.created))
        self.assertEqual(self.world.clock.pending, 0)
        self.world.host.shutdown.assert_called_once_with()
        # The dashboard surface outlives the case exactly as it was registered, and was never
        # entered: the chain needed no request, and the guard was never disarmed.
        self.assertIs(_sessions_route(self.fixture.app), route)
        self.assertEqual(self.tripwire.calls, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
