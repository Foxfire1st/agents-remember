"""Teardown proof for the serving lifespan's terminal-observation owner (``LOCR-R19@v1``).

Serving shutdown must cancel the recurring observer *and* wait for the off-loop
``TerminalCatalogLivenessSweeper.refresh`` worker it is running. Cancelling an ``asyncio.to_thread``
call does not stop the underlying thread, so a teardown that only cancelled the observer task could
return -- and close the terminal host -- while the sweeper was still reading panes or writing the
catalog. The drain boundary is the existing ``_to_thread_drained_on_cancel`` helper, applied at
every observer call site including the startup prime; the teardown that consumes it is the
lifespan's own ``background`` collection, cancelled as a group and then awaited one task at a time.

These cases drive the real ``_serving_lifespan`` under the shared serving fixture from
``test_serving_observation_loop`` (its virtual clock, recorders, and probe -- no HTTP request, no
browser, no real second) and park a real ``refresh`` worker in its thread, so "did shutdown wait?"
is answered by an ordered witness instead of by timing luck: cancellation is requested while the
worker is provably in flight, the worker is released a fixed interval later, and the host's own
shutdown callback reports the state it found.

The three cases are the packet's two required classes plus the startup-prime edge it was revised to
cover: a controlled blocking-refresh teardown (ordering, drain, and no post-shutdown write),
cancellation during the startup prime, and the final-commit restart proof (the last pass's
committed catalog truth stays durable and is evaluated by the next serving startup rather than
delivered by a teardown-time callback).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.serving._app_lifespan as lifespan_module
from agents_remember.kernel.agentic_settings import AgenticSettings, AgentNotifierSettings
from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.serving._app_lifespan import _serving_lifespan
from agents_remember.serving.terminal_catalog import TerminalCatalog
from test_serving_observation_loop import (
    _durable_tree,
    _entry,
    _observer_tasks,
    _RefreshProbe,
    _ServingFixture,
    _VirtualClock,
    _wait_until,
)

_REAL_SLEEP = asyncio.sleep
"""The real sleep, captured before the shared fixture swaps ``asyncio.sleep`` for its clock."""

_CATALOG_FILE = "terminal-sessions.json"
_SETTLE_SECONDS = 0.05
"""A real window for a leaked worker to write after the host closed; never a cadence."""

_RELEASE_DELAY_SECONDS = 0.25
"""How long after cancellation the parked worker is released.

Far longer than the teardown path itself, so "the drain waited for the worker" and "the drain
returned without it" are separated by an interval no scheduler noise can close.
"""


async def _parked_forever(*_args: object, **_kwargs: object) -> None:
    """A sibling serving loop that touches nothing: only the startup prime is under test here."""

    await asyncio.Event().wait()


def _observation_failure_lines(log_spy: mock.Mock) -> list[object]:
    """Every observation-failure line the lifespan logged, judged by the module's own message.

    Cancellation must not be recorded as a failed observation, and the only recording the lifespan
    has is this log call -- so the assertion is about the observer's own message rather than about
    "nothing was logged at all", which would also forbid unrelated sibling logging.
    """

    lines = []
    for call in log_spy.exception.call_args_list:
        message = call.args[0] if call.args else ""
        if isinstance(message, str) and "terminal catalog observation" in message:
            lines.append(message)
    return lines


def _committed_ids(path: Path) -> list[str]:
    """The row ids a reader would find in the catalog's last committed atomic snapshot."""

    return sorted(entry.id for entry in TerminalCatalog(path).list_committed())


class _DrainGate:
    """The steady pass teardown has to drain: parked in its worker thread, then committed.

    ``probe.inner`` runs inside the attempt, so parking here parks exactly the ``refresh``
    invocation the case names without teaching the probe a second blocking mode. Invocation 1 is
    always the pre-serve startup prime, so ``block_at=2`` parks the recurring owner's own first
    pass -- the in-flight worker shutdown must wait for.

    The worker is released by the case's monitor a fixed interval *after* cancellation is
    requested, and ``release`` is only ever set by that monitor. Nothing else can unblock it, so
    the worker is provably still in flight for the whole interval. ``on_release`` is the pass's own
    last durable act: the catalog commit the packet requires to survive cancellation.
    """

    def __init__(
        self,
        *,
        block_at: int,
        on_release: Callable[[], None] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.block_at = block_at
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.released = threading.Event()
        self._on_release = on_release
        self._events = [] if events is None else events

    def __call__(self) -> list[object]:
        self.calls += 1
        if self.calls != self.block_at:
            return []
        self._events.append("worker-entered")
        self.entered.set()
        self.release.wait(timeout=10)
        if self._on_release is not None:
            self._on_release()
        self._events.append("worker-released")
        self.released.set()
        return []


async def _release_after_cancellation(
    observer: asyncio.Task[object],
    gate: _DrainGate,
    events: list[str],
) -> None:
    """Release the parked worker one interval after the observer's cancellation was requested.

    ``Task.cancelling()`` only becomes non-zero through the lifespan's own ``task.cancel()``, so
    observing it is the witness that teardown had begun while the worker was still parked -- and
    the delay after it is what makes "host shutdown waited" and "host shutdown did not wait"
    separable without a race.
    """

    await _wait_until(lambda: observer.cancelling() >= 1)
    events.append("cancellation-requested")
    await _REAL_SLEEP(_RELEASE_DELAY_SECONDS)
    gate.release.set()


def _two_row_settings(*, enabled: bool, interval_seconds: float) -> AgenticSettings:
    return AgenticSettings(
        agent_notifier=AgentNotifierSettings(enabled=enabled, interval_seconds=interval_seconds)
    )


class ServingShutdownDrainTests(unittest.IsolatedAsyncioTestCase):
    """``LOCR-R19@v1``: teardown cancels the observer, drains its worker, then closes the host."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_teardown_drains_an_in_flight_refresh_before_the_host_closes(self) -> None:
        events: list[str] = []
        catalog = TerminalCatalog(self.tmp / _CATALOG_FILE)
        catalog.upsert(_entry("seat-1"))
        gate = _DrainGate(
            block_at=2,
            on_release=lambda: catalog.upsert(_entry("seat-late")),
            events=events,
        )
        probe = _RefreshProbe(inner=gate)
        fixture = _ServingFixture(self.tmp, probe)
        at_shutdown: list[tuple[int, bool, list[str]]] = []

        def _host_shutdown() -> None:
            events.append("host-shutdown")
            # Read inside the callback: this is the state the terminal host is closed over.
            at_shutdown.append((probe.active, gate.released.is_set(), _committed_ids(catalog.path)))

        fixture.shutdown.side_effect = _host_shutdown

        with mock.patch.object(lifespan_module, "logger") as log_spy:
            async with fixture.running():
                await _wait_until(gate.entered.is_set)
                observer = _observer_tasks(fixture.created)[0]
                monitor = asyncio.get_running_loop().create_task(
                    _release_after_cancellation(observer, gate, events)
                )
            await monitor

        # The ordering witness: cancellation arrived while the worker was parked, the worker was
        # released strictly after it, and the host closed strictly after the drain.
        self.assertEqual(
            events,
            ["worker-entered", "cancellation-requested", "worker-released", "host-shutdown"],
        )
        # The host closed with no worker running, its own pass's commit already durable, and the
        # observer task drained.
        self.assertEqual(at_shutdown, [(0, True, ["seat-1", "seat-late"])])
        self.assertTrue(observer.cancelled())
        # Cancellation is not recorded as an observation failure: the boundary is ``except
        # Exception``, and ``CancelledError`` is a ``BaseException``.
        self.assertEqual(_observation_failure_lines(log_spy), [])
        # No future pass starts, and nothing is written after the host closed.
        calls_at_teardown = probe.calls
        after_shutdown = _durable_tree(self.tmp)
        await _REAL_SLEEP(_SETTLE_SECONDS)
        self.assertEqual(probe.calls, calls_at_teardown)
        self.assertEqual(_durable_tree(self.tmp), after_shutdown)
        # Control for the identity above: this run really did commit durable catalog truth, so the
        # tree is a live surface rather than one nothing ever writes.
        self.assertEqual(_committed_ids(catalog.path), ["seat-1", "seat-late"])

    async def test_cancellation_during_the_startup_prime_waits_for_its_worker(self) -> None:
        probe = _RefreshProbe(block_first=True)
        fixture = _ServingFixture(self.tmp, probe)
        lifespan = _serving_lifespan(fixture.runtime, cast(ProviderMetricsStore, mock.Mock()))

        with (
            mock.patch.object(lifespan_module, "migrate_control_plane_identity_logs"),
            mock.patch.object(lifespan_module, "compact_workspace_river"),
            mock.patch.object(lifespan_module, "_metrics_loop", _parked_forever),
            mock.patch.object(lifespan_module, "relay_death_watch_loop", _parked_forever),
            mock.patch.object(lifespan_module, "_workspace_river_compaction_loop", _parked_forever),
            mock.patch.object(lifespan_module, "_agent_notifier_loop", _parked_forever),
            mock.patch.object(lifespan_module, "start_heap_tracing", lambda: False),
            mock.patch.object(lifespan_module, "malloc_trim_enabled", lambda: False),
            mock.patch.object(lifespan_module, "logger") as log_spy,
        ):
            # Entered by hand: the prime runs before the lifespan yields, so a case that cancels
            # *during* the prime cannot reach it from inside the ``async with`` body.
            startup = asyncio.get_running_loop().create_task(lifespan(fixture.app).__aenter__())
            await _wait_until(probe.first_call_entered.is_set)
            startup.cancel()
            # The drain is what holds this task open. A prime that merely cancelled its
            # ``to_thread`` call would already have returned here, with the worker still running.
            await _REAL_SLEEP(_SETTLE_SECONDS)
            self.assertFalse(startup.done())
            self.assertEqual(probe.active, 1)

            probe.release_first_call.set()
            with self.assertRaises(asyncio.CancelledError):
                await startup

        # The worker drained and the cancellation was re-raised only after it did.
        self.assertEqual(probe.active, 0)
        self.assertEqual(probe.outcomes, ["ok"])
        self.assertEqual(probe.calls, 1)
        self.assertEqual(_observation_failure_lines(log_spy), [])

    async def test_the_final_in_flight_commit_stays_relayable_after_the_next_startup(self) -> None:
        clock = _VirtualClock([])
        catalog = TerminalCatalog(self.tmp / _CATALOG_FILE)
        catalog.upsert(_entry("seat-1"))
        gate = _DrainGate(block_at=2, on_release=lambda: catalog.upsert(_entry("seat-late")))
        probe = _RefreshProbe(clock.timeline, inner=gate)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)
        stopped_sweep = mock.Mock()
        at_shutdown: list[list[str]] = []
        fixture.shutdown.side_effect = lambda: at_shutdown.append(_committed_ids(catalog.path))

        async with fixture.running(
            notifier=True,
            settings=_two_row_settings(enabled=False, interval_seconds=4.0),
            notifier_sweep=stopped_sweep,
        ):
            await _wait_until(gate.entered.is_set)
            observer = _observer_tasks(fixture.created)[0]
            monitor = asyncio.get_running_loop().create_task(
                _release_after_cancellation(observer, gate, [])
            )
        await monitor

        # The notifier had already stopped: its sweep is never synthesized as a teardown fallback.
        stopped_sweep.assert_not_called()
        # The last in-flight pass's commit was already durable when the host closed.
        self.assertEqual(at_shutdown, [["seat-1", "seat-late"]])

        # Restart: a fresh reader of the same durable catalog, then a second serving startup whose
        # notifier evaluates exactly that committed truth.
        resumed = _ServingFixture(self.tmp, _RefreshProbe(), clock=_VirtualClock([]))
        resumed_ns = cast(Any, resumed.runtime)
        resumed_ns.catalog = TerminalCatalog(catalog.path)
        resumed_ns.paster = mock.Mock()
        resumed_ns.heartbeat_store = mock.Mock()
        resumed_ns.register_inbox_execution_evidence = None
        sweep = mock.Mock()

        async with resumed.running(
            notifier=True,
            settings=_two_row_settings(enabled=True, interval_seconds=1.0),
            notifier_sweep=sweep,
        ):
            await _wait_until(lambda: sweep.call_count >= 1)

        delivered = cast(Any, sweep.call_args.args[0])
        self.assertEqual(
            sorted(row.id for row in delivered.catalog.list()), ["seat-1", "seat-late"]
        )
