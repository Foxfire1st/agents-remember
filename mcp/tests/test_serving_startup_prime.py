"""Production-lifespan proof for the one terminal-catalog observation prime before serving.

``_serving_lifespan`` must attempt exactly one catalog observation AFTER control-plane migration
and workspace-river compaction and BEFORE ``runtime.projector.prime()``, before any recurring loop
exists, and before the lifespan yields. Without it the initial projection and the first notifier
sweep could read a catalog no pass has refreshed, and initial truth would depend on the first
scheduled tick; left uncontained, a recoverable adapter error would turn observation degradation
into a serving outage. These cases drive the real lifespan under the shared serving fixture from
``test_serving_observation_loop`` -- no HTTP request, no browser, no real second -- and read that
fixture's ordered ``startup`` record, whose per-step sweep count is the ordering witness.

The prime is the same canonical pass the recurring owner runs: this module asserts the off-loop
boundary, the success and raise-once orderings, the committed truth the projection and the first
notifier sweep read, and that no startup-only reader or direct catalog mutation exists. The
recurring owner's steady cadence and its own per-pass failure boundary live in
``test_serving_observation_loop``.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.serving._app_lifespan as lifespan_module
from agents_remember.kernel.agentic_settings import AgenticSettings, AgentNotifierSettings
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    TerminalCatalogLivenessSweeper,
)
from agents_remember.serving.turn_state import classify_turn_state
from fastapi.routing import APIRoute
from test_serving_observation_loop import (
    _entry,
    _LiveHost,
    _observer_tasks,
    _RefreshProbe,
    _ServingFixture,
    _VirtualClock,
    _wait_until,
)

_PANE_TEXT = "pane text the canonical pane capturer reads"


def _seeded_catalog(root: Path, session_id: str) -> TerminalCatalog:
    """One durable catalog whose single harness row is alive but not yet observed."""

    catalog = TerminalCatalog(root / "terminal-sessions.json")
    catalog.upsert(_entry(session_id))
    return catalog


def _canonical_sweeper(
    catalog: TerminalCatalog, host: _LiveHost, clock: _VirtualClock
) -> TerminalCatalogLivenessSweeper:
    """The one observation owner: the readers, hysteresis, and clock every pass uses."""

    return TerminalCatalogLivenessSweeper(
        catalog,
        host,
        now=clock.now,
        probe=LivenessProbe(pane_capturer=lambda _tmux_name: _PANE_TEXT),
    )


def _startup_steps(fixture: _ServingFixture) -> list[str]:
    return [step for step, _ in fixture.startup]


def _add_collaborators(runtime: _ServingRuntime, **collaborators: object) -> None:
    """Give one fixture-built fake runtime the collaborators the real notifier loop reads.

    The fixture builds the runtime as a namespace, so the extra collaborators land beside the
    ones it already supplies; the annotation keeps the fixture's fake a checked ``_ServingRuntime``
    rather than an untyped bag.
    """

    vars(runtime).update(collaborators)


class ServingStartupPrimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_the_prime_is_taken_once_before_projection_and_every_recurring_loop(self) -> None:
        probe = _RefreshProbe()
        fixture = _ServingFixture(self.tmp, probe)

        async with fixture.running():
            # Startup is complete at this boundary and no created task has run yet.
            startup = fixture.startup
            self.assertEqual(probe.calls, 1)
            self.assertEqual(fixture.timeline, ["call 1 started", "call 1 ended"])
            # The first startup step dispatches the prime's own worker task while no sweep has
            # completed: the prime is dispatched off-loop rather than called on the loop thread.
            self.assertEqual(startup[0][1], 0)
            self.assertTrue(startup[0][0].startswith("task:"))
            # The projection prime is built after exactly one completed observation, and every
            # later startup step still observes that same single sweep: one prime, not zero and
            # not two.
            self.assertEqual(startup[1], ("projection-prime", 1))
            self.assertEqual({tag for _, tag in startup[1:]}, {1})
            self.assertEqual(startup[-1], ("lifespan-yield", 1))
            created = [step for step in _startup_steps(fixture) if step.startswith("task:")]
            self.assertIn("task:_terminal_observation_loop", created)
            self.assertGreaterEqual(len(created), 6)
            self.assertEqual(len(_observer_tasks(fixture.created)), 1)

            # The recurring owner's own first pass is a separate, later attempt: call 2.
            await _wait_until(lambda: probe.calls >= 2)

        self.assertEqual(
            fixture.timeline[:4],
            ["call 1 started", "call 1 ended", "call 2 started", "call 2 ended"],
        )

    async def test_the_prime_runs_off_the_event_loop_through_the_drained_helper(self) -> None:
        probe = _RefreshProbe(block_first=True)
        fixture = _ServingFixture(self.tmp, probe)
        helper_calls: list[Callable[..., object]] = []
        real_helper = lifespan_module._to_thread_drained_on_cancel
        loop = asyncio.get_running_loop()
        finished = threading.Event()
        ticks: list[int] = []
        ticks_while_prime_blocked: list[int] = []

        def tick() -> None:
            ticks.append(len(ticks) + 1)
            if probe.first_call_entered.is_set():
                # The prime is parked in its worker thread right now, so each of these timers is
                # the loop showing it is still scheduling while the observation pass runs.
                ticks_while_prime_blocked.append(len(ticks))
                if len(ticks_while_prime_blocked) == 3:
                    probe.release_first_call.set()
            if not finished.is_set():
                loop.call_later(0.005, tick)

        async def spy(
            function: Callable[..., object], /, *args: object, **kwargs: object
        ) -> object:
            helper_calls.append(function)
            return await real_helper(function, *args, **kwargs)

        loop.call_later(0.005, tick)
        try:
            with mock.patch.object(lifespan_module, "_to_thread_drained_on_cancel", spy):
                async with fixture.running():
                    # This body runs on the loop thread, so "not the main thread" below is
                    # exactly "not the event loop".
                    self.assertIs(threading.current_thread(), threading.main_thread())
                    self.assertTrue(probe.first_call_entered.is_set())
                    # The prime went through the drain-on-cancel helper, targeting the canonical
                    # sweeper's own entry point.
                    self.assertEqual(helper_calls[0], fixture.runtime.liveness_sweeper.refresh)
                    self.assertTrue(probe.threads)
                    self.assertTrue(
                        all(thread is not threading.main_thread() for thread in probe.threads)
                    )
                    # A bare call on the loop thread would have frozen these timers, so the pass
                    # would never have been released and this counter would be empty.
                    self.assertGreaterEqual(len(ticks_while_prime_blocked), 3)
        finally:
            finished.set()

    async def test_a_failed_prime_still_serves_and_the_owner_retries_on_its_cadence(self) -> None:
        probe = _RefreshProbe(failures=1)
        fixture = _ServingFixture(self.tmp, probe)

        # Entering the lifespan body at all is the containment assertion: an escaping prime
        # exception would propagate out of the lifespan's ``__aenter__`` and abort startup.
        async with fixture.running():
            self.assertEqual(fixture.timeline, ["call 1 started", "call 1 ended"])
            # Startup continued past the failed observation: the projection was still primed and
            # the recurring owner was still registered.
            self.assertEqual(fixture.prime.await_count, 1)
            self.assertEqual(len(_observer_tasks(fixture.created)), 1)

            # The first steady cadence retries and the owner stays alive on it.
            await _wait_until(lambda: len(fixture.clock.requested) == 1)
            self.assertEqual(len(fixture.clock.requested), 1)
            fixture.clock.release()
            await _wait_until(lambda: len(fixture.clock.requested) == 2)

        self.assertEqual(probe.calls, 3)
        self.assertEqual(fixture.clock.requested, [1.0, 1.0])
        self.assertEqual(
            fixture.timeline,
            [
                "call 1 started",
                "call 1 ended",
                "call 2 started",
                "call 2 ended",
                "sleep(1.0)",
                "call 3 started",
                "call 3 ended",
                "sleep(1.0)",
            ],
        )

    async def test_the_prime_publishes_truth_the_projection_and_first_notifier_sweep_read(
        self,
    ) -> None:
        clock = _VirtualClock([])
        catalog = _seeded_catalog(self.tmp, "seat-1")
        seed = catalog.get("seat-1")
        sweeper = _canonical_sweeper(catalog, _LiveHost(), clock)
        probe = _RefreshProbe(clock.timeline, inner=sweeper.refresh)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)
        projections: list[list[TerminalCatalogEntry]] = []
        sweeps: list[list[TerminalCatalogEntry]] = []
        recorded_prime = fixture.prime.side_effect

        async def capture_projection() -> None:
            projections.append(catalog.list())
            await recorded_prime()

        def capture_sweep(_ctx: object, *, now: object = None) -> None:
            sweeps.append(catalog.list())

        fixture.prime.side_effect = capture_projection
        # The real agent-notifier loop builds its context from these collaborators.
        _add_collaborators(
            fixture.runtime,
            catalog=catalog,
            paster=mock.Mock(),
            heartbeat_store=mock.Mock(),
            register_inbox_execution_evidence=None,
        )
        sweep = mock.Mock(side_effect=capture_sweep)
        settings = AgenticSettings(
            agent_notifier=AgentNotifierSettings(enabled=True, interval_seconds=4.0)
        )

        async with fixture.running(notifier=True, settings=settings, notifier_sweep=sweep):
            committed = catalog.get("seat-1")
            assert seed is not None
            assert committed is not None
            # The observed truth is the adapter's, not the seed a not-yet-observed projection
            # would have read.
            self.assertEqual(seed.control_state, "ready")
            self.assertEqual(committed.control_state, "unsupported")
            # The initial projection was built from exactly that committed truth ...
            self.assertEqual(projections, [[committed]])
            # ... and so was the first notifier sweep.
            await _wait_until(lambda: len(sweeps) == 1)

        self.assertEqual(sweeps, [[committed]])

    async def test_the_prime_is_taken_with_no_get_dashboard_or_model_message(self) -> None:
        clock = _VirtualClock([])
        catalog = _seeded_catalog(self.tmp, "seat-1")
        host = _LiveHost()
        sweeper = _canonical_sweeper(catalog, host, clock)
        probe = _RefreshProbe(clock.timeline, inner=sweeper.refresh)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)
        requests: list[str] = []
        sweep = mock.Mock()
        settings = AgenticSettings(
            agent_notifier=AgentNotifierSettings(enabled=False, interval_seconds=4.0)
        )

        @fixture.app.get("/api/terminal/sessions")
        async def _terminal_sessions() -> dict[str, str]:
            requests.append("GET /api/terminal/sessions")
            return {"status": "served"}

        async with fixture.running(notifier=True, settings=settings, notifier_sweep=sweep):
            # The app really does expose the terminal-session GET a request-driven design would
            # hang its initial observation on, and the lifespan really does run the agent-notifier
            # loop ...
            routes = [route.path for route in fixture.app.routes if isinstance(route, APIRoute)]
            self.assertEqual(routes, ["/api/terminal/sessions"])
            self.assertIn("task:_agent_notifier_loop", _startup_steps(fixture))
            # ... and the prime still ran before the lifespan yielded: no request was dispatched
            # and the notifier, which has published no message, did not sweep.
            self.assertEqual(probe.calls, 1)
            self.assertEqual(host.probes, 1)
            committed = catalog.get("seat-1")
            assert committed is not None
            self.assertEqual(committed.control_state, "unsupported")

            await _wait_until(lambda: probe.calls >= 2)

        sweep.assert_not_called()
        self.assertEqual(requests, [])

    async def test_the_prime_commits_through_the_same_canonical_sweeper_as_later_passes(
        self,
    ) -> None:
        clock = _VirtualClock([])
        catalog = _seeded_catalog(self.tmp, "seat-1")
        host = _LiveHost()
        sweeper = _canonical_sweeper(catalog, host, clock)
        probe = _RefreshProbe(clock.timeline, inner=sweeper.refresh)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)
        helper_calls: list[Callable[..., object]] = []
        real_helper = lifespan_module._to_thread_drained_on_cancel

        async def spy(
            function: Callable[..., object], /, *args: object, **kwargs: object
        ) -> object:
            helper_calls.append(function)
            return await real_helper(function, *args, **kwargs)

        with mock.patch.object(lifespan_module, "_to_thread_drained_on_cancel", spy):
            async with fixture.running():
                # The prime ran the canonical owner's own entry point, and only it.
                self.assertEqual(probe.calls, 1)
                self.assertEqual(helper_calls, [probe.refresh])
                # The row was judged by the canonical tmux probe and the canonical pane
                # classifier, so the committed truth is the evidence readers' output rather than
                # a value a startup-only writer could have seeded.
                self.assertEqual(host.probes, 1)
                committed = catalog.get("seat-1")
                assert committed is not None
                self.assertEqual(committed.control_state, "unsupported")
                self.assertEqual(
                    (committed.control_raw or {}).get("paneDiagnostic"),
                    classify_turn_state(_PANE_TEXT, harness="codex").state,
                )

                # The recurring owner's later pass goes through that same bound entry point.
                await _wait_until(lambda: probe.calls >= 2)

        self.assertGreaterEqual(len(helper_calls), 2)
        self.assertTrue(all(target == probe.refresh for target in helper_calls))

        # One canonical pass over an identical seed at the same clock instant commits exactly the
        # truth the prime committed: no startup-only reader, cursor, or write path took part.
        reference_root = self.tmp / "reference"
        reference_root.mkdir()
        reference_catalog = _seeded_catalog(reference_root, "seat-1")
        _canonical_sweeper(reference_catalog, _LiveHost(), clock).refresh()

        self.assertNotEqual(reference_catalog.get("seat-1"), _entry("seat-1"))
        self.assertEqual(committed, reference_catalog.get("seat-1"))
