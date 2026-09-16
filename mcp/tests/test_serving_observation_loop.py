"""Fake-clock proof for the serving lifespan's steady-state terminal observation owner.

Adapter terminal evidence is produced outside the dashboard, so the serving lifetime must own one
recurring caller of ``TerminalCatalogLivenessSweeper.refresh``: a closed browser, a headless
process, or a disabled agent notifier must not stop catalog turn truth from advancing. These cases
enter the real ``_serving_lifespan`` finalizer under a virtual event-loop clock -- no HTTP request,
no browser, no real second -- so the completion-relative attempt cadence, attempt non-overlap, and
the sweeper's own full-sweep rate limit are observed deterministically.

The second half of the module pins ``LOCR-R11@v1``: an exception escaping one pass neither ends the
recurring owner nor touches a sibling serving task, publishes nothing durable of its own (a failed
pass records no success fact and no diagnostic row, event, or payload -- ``LOCR-R17@v1`` owns the
only structured observer-failure publication), leaves already-committed catalog rows, workspace
cursors, terminal evidence, and emitted-signal markers exactly as they were, retries on the next
cadence from that persisted state with no request, restart, or catalog edit, and never swallows
cancellation.

``LOCR-R17@v1`` has since landed the one observer-health row those identities must account for:
every completed observer call -- successful or failed -- atomically rewrites
``observer_root/workspace/terminal-observer-health.json``, so a failed pass legitimately changes
that ONE path and nothing else. ``_durable_tree_without_observer_health`` is how these cases keep
the strong claim ("every other durable artifact is byte-identical") without either weakening it to
"almost nothing changed" or pretending the packet's own diagnostic is not durable.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import AsyncIterator, Callable, Coroutine
from contextvars import Context
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.serving._app_lifespan as lifespan_module
import httpx
from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.kernel.agentic_settings import AgenticSettings, AgentNotifierSettings
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.observer.store import (
    WORKSPACE_CURSOR_FILE,
    WORKSPACE_SOURCE,
    workspace_base_offset,
)
from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving._app_lifespan import _serving_lifespan
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    TerminalCatalogLivenessSweeper,
)
from agents_remember.serving.terminal_observer_health import (
    TerminalObserverHealthPublisher,
    TerminalObserverHealthStore,
    terminal_observer_health_path,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from fastapi import FastAPI
from fastapi.routing import APIRoute

_REAL_SLEEP = asyncio.sleep
"""The real sleep, captured before a case swaps ``asyncio.sleep`` for its virtual clock."""

_STARTED_AT = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


async def _parked_forever(*_args: object, **_kwargs: object) -> None:
    """A sibling background loop that touches nothing until lifespan teardown cancels it."""

    await asyncio.Event().wait()


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 10.0) -> None:
    """Yield until ``predicate`` holds. The deadline bounds a hung test, never a cadence."""

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not reached before the test deadline")
        await _REAL_SLEEP(0.001)


async def _advance(
    clock: _VirtualClock,
    predicate: Callable[[], bool],
    *,
    limit: int = 200,
) -> None:
    """Release parked sleeps (each advancing virtual time) until ``predicate`` holds."""

    for _ in range(limit):
        if predicate():
            return
        await _wait_until(lambda: clock.pending > 0)
        if predicate():
            return
        clock.release()
        await _REAL_SLEEP(0)
    raise AssertionError("the observation owner never reached the requested state")


class _VirtualClock:
    """One timeline shared by ``asyncio.sleep`` and the sweeper's own datetime clock.

    A sleep parks its caller until the case releases that request; releasing advances virtual time
    by the requested delay, so ``seconds`` carries the loop's cadence while ``now()`` is what the
    sweeper's rate limits read. Every call and every sleep lands in one ordered timeline, which is
    what makes "the sleep happened after the call returned" a single assertion.
    """

    def __init__(self, timeline: list[str]) -> None:
        self.seconds = 0.0
        self.timeline = timeline
        self.requested: list[float] = []
        self._parked: list[tuple[float, asyncio.Future[None]]] = []

    @property
    def pending(self) -> int:
        return len(self._parked)

    def now(self) -> datetime:
        return _STARTED_AT + timedelta(seconds=self.seconds)

    def elapse(self, seconds: float) -> None:
        """Advance virtual time without completing a sleep: a pass that outran its tick."""

        self.seconds += seconds

    def release(self) -> None:
        """Complete the oldest parked sleep and advance virtual time by its delay."""

        delay, future = self._parked.pop(0)
        self.seconds += delay
        if not future.done():
            future.set_result(None)

    async def sleep(self, delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.requested.append(delay)
        self.timeline.append(f"sleep({delay})")
        self._parked.append((delay, future))
        try:
            await future
        except asyncio.CancelledError:
            self._parked = [item for item in self._parked if item[1] is not future]
            raise


class _RefreshProbe:
    """Records the attempt timeline of the ``refresh`` the observation owner calls.

    ``inner`` optionally delegates to a real sweeper, so one probe serves both the isolated-cadence
    cases and the real ``TerminalCatalog`` rate-limit case without a second harness. ``failures``
    fails the leading attempts; ``fail_on`` fails exactly one later attempt, which is what makes
    "a durable commit, then an independent failure, then a retry" expressible on one probe.
    ``outcomes`` records each attempt's own verdict for the assertions that the failure was the
    second pass and the retry the third.
    """

    def __init__(
        self,
        timeline: list[str] | None = None,
        *,
        inner: Callable[[], object] | None = None,
        block_first: bool = False,
        failures: int = 0,
        fail_on: int | None = None,
    ) -> None:
        self.timeline = [] if timeline is None else timeline
        self.inner = inner
        self.block_first = block_first
        self.failures = failures
        self.fail_on = fail_on
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.outcomes: list[str] = []
        self.threads: list[threading.Thread] = []
        self.first_call_entered = threading.Event()
        self.release_first_call = threading.Event()

    def refresh(self) -> object:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.threads.append(threading.current_thread())
        self.timeline.append(f"call {self.calls} started")
        try:
            if self.block_first and self.calls == 1:
                self.first_call_entered.set()
                self.release_first_call.wait(timeout=10)
            if self.failures > 0 or self.fail_on == self.calls:
                self.failures = max(self.failures - 1, 0)
                self.outcomes.append("failed")
                raise RuntimeError("terminal observation pass failed")
            result = [] if self.inner is None else self.inner()
            self.outcomes.append("ok")
            return result
        finally:
            self.active -= 1
            self.timeline.append(f"call {self.calls} ended")


class _LiveHost:
    """A tmux host whose catalog rows are alive, so one full sweep probes each row once."""

    def __init__(self) -> None:
        self.probes = 0
        self.probed: list[str] = []

    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return self.probe_session(tmux_name).exists

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        self.probes += 1
        self.probed.append(tmux_name)
        return TmuxProbeResult(exists=True, evidence="alive")


class _Gate:
    """An ``inner`` callable that parks one chosen ``refresh`` invocation in its worker thread.

    The probe's ``inner`` runs inside the attempt, so gating here parks exactly the invocation the
    case names without teaching the probe a second blocking mode. Invocation 1 is always the
    pre-serve startup prime, so a case that wants the recurring owner's own first pass to be the
    slow one gates invocation 2.
    """

    def __init__(self, *, block_at: int) -> None:
        self.block_at = block_at
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> list[object]:
        self.calls += 1
        if self.calls == self.block_at:
            self.entered.set()
            self.release.wait(timeout=10)
        return []


class _ServingFixture:
    """One disposable serving runtime whose background loops run under a virtual clock.

    Only the observation owner is real: the projector, metrics, notifier, death-watch and
    compaction loops are replaced by parked coroutines, and the two blocking startup calls are
    inert, so a case cannot start containers, read live settings, or write stores.

    ``startup`` is the ordered account of the lifespan's pre-serve startup, one ``(step,
    sweeps_completed)`` entry per step. The sweep count is the ordering witness: the one
    pre-serve observation prime is the only sweep that may already have completed when the
    projection prime and the first background tasks are created.
    """

    def __init__(
        self,
        root: Path,
        sweeper: object,
        *,
        clock: _VirtualClock | None = None,
    ) -> None:
        self.timeline: list[str] = getattr(sweeper, "timeline", [])
        self.sweeper = sweeper
        self.startup: list[tuple[str, int]] = []
        self.clock = _VirtualClock(self.timeline) if clock is None else clock
        self.created: list[asyncio.Task[object]] = []
        self.prime = mock.AsyncMock(side_effect=self._record_projection_prime)
        self.shutdown = mock.Mock()
        self.app = FastAPI()
        self.runtime = cast(
            _ServingRuntime,
            SimpleNamespace(
                config=SimpleNamespace(coordination_root=root),
                observer_root=root,
                projector=SimpleNamespace(prime=self.prime, run=_parked_forever),
                host=SimpleNamespace(shutdown=self.shutdown),
                liveness_clock=self.clock.now,
                liveness_sweeper=sweeper,
                # The real publisher on the fixture's own root and virtual clock, so a case reads
                # exactly the bytes the lifespan published under the same clock it drove.
                observer_health=TerminalObserverHealthPublisher(root, self.clock.now),
            ),
        )

    def _record_startup(self, step: str) -> None:
        self.startup.append((step, getattr(self.sweeper, "calls", 0)))

    async def _record_projection_prime(self) -> None:
        self._record_startup("projection-prime")

    def _record_task(
        self,
        coro: Coroutine[Any, Any, object],
        *,
        name: str | None = None,
        context: Context | None = None,
    ) -> asyncio.Task[object]:
        self._record_startup(f"task:{getattr(coro, '__qualname__', '?')}")
        task = asyncio.get_running_loop().create_task(coro, name=name, context=context)
        self.created.append(task)
        return task

    @contextlib.asynccontextmanager
    async def running(
        self,
        *,
        notifier: bool = False,
        settings: AgenticSettings | None = None,
        notifier_sweep: mock.Mock | None = None,
    ) -> AsyncIterator[None]:
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(lifespan_module, "migrate_control_plane_identity_logs")
            )
            stack.enter_context(mock.patch.object(lifespan_module, "compact_workspace_river"))
            stack.enter_context(
                mock.patch.object(lifespan_module, "_metrics_loop", _parked_forever)
            )
            stack.enter_context(
                mock.patch.object(lifespan_module, "relay_death_watch_loop", _parked_forever)
            )
            stack.enter_context(
                mock.patch.object(
                    lifespan_module, "_workspace_river_compaction_loop", _parked_forever
                )
            )
            stack.enter_context(
                mock.patch.object(lifespan_module, "start_heap_tracing", lambda: False)
            )
            stack.enter_context(
                mock.patch.object(lifespan_module, "malloc_trim_enabled", lambda: False)
            )
            stack.enter_context(mock.patch.object(asyncio, "sleep", self.clock.sleep))
            stack.enter_context(mock.patch.object(asyncio, "create_task", self._record_task))
            if notifier:
                stack.enter_context(
                    mock.patch.object(
                        lifespan_module, "load_agentic_settings", lambda _root: settings
                    )
                )
                stack.enter_context(
                    mock.patch.object(lifespan_module, "run_agent_notifier_sweep", notifier_sweep)
                )
            else:
                stack.enter_context(
                    mock.patch.object(lifespan_module, "_agent_notifier_loop", _parked_forever)
                )
            lifespan = _serving_lifespan(self.runtime, cast(ProviderMetricsStore, mock.Mock()))
            async with lifespan(self.app):
                # Startup is complete here: the prime has returned (or raised) and no created
                # task has run yet, so this step is the boundary the startup-order cases read.
                self._record_startup("lifespan-yield")
                yield


def _observer_tasks(tasks: list[asyncio.Task[object]]) -> list[asyncio.Task[object]]:
    def is_observer(task: asyncio.Task[object]) -> bool:
        coro = task.get_coro()
        return getattr(coro, "__qualname__", "") == "_terminal_observation_loop"

    return [task for task in tasks if is_observer(task)]


def _entry(session_id: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-08-31T00:00:00+00:00",
        last_attached_at="2026-08-31T00:00:00+00:00",
        status="running",
        control_state="ready",
        control_endpoint=None,
        control_activity="idle",
        control_acceptance="immediate",
    )


async def _serving_probe_route() -> dict[str, str]:
    """One trivially answering route: the HTTP surface the observer must not take down with it."""

    return {"status": "serving"}


def _background_tasks(tasks: list[asyncio.Task[object]]) -> list[asyncio.Task[object]]:
    """The lifespan's own background loops, excluding the off-loop worker tasks they spawn."""

    return [task for task in tasks if getattr(task.get_coro(), "__qualname__", "") != "to_thread"]


def _durable_tree(root: Path) -> dict[str, str]:
    """Every durable artifact under ``root`` as relative path -> sha256 of its bytes.

    A pass that publishes anything -- a success fact, a diagnostic row, a fresh event -- changes
    this map, so byte-identity across a failed pass is the "published nothing" assertion. The
    control cases below prove a *successful* pass does change it, so the identity is a result about
    the failure path rather than about a tree nothing ever writes.
    """

    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _durable_tree_without_observer_health(root: Path) -> dict[str, str]:
    """Every durable artifact under ``root`` except this lifetime's observer-health row.

    ``LOCR-R17@v1`` publishes the one structured observer diagnostic on EVERY completed observer
    call, so a failed pass must rewrite that single row. Excluding exactly that path keeps these
    R11 identities a claim about every other durable artifact -- no catalog row, no cursor, no
    terminal evidence, and no emitted-signal marker moves -- while the case that owns the failure
    still proves positively that the diagnostic itself was published.
    """

    excluded = terminal_observer_health_path(root).relative_to(root).as_posix()
    return {key: value for key, value in _durable_tree(root).items() if key != excluded}


_SIGNAL_MARKER_LINE = (
    '{"schemaVersion":"1.0","schema":"ar-agent-notifier-signal/v2","id":"sig-l11-1",'
    '"ts":"2026-08-31T11:59:00+00:00","state":"sent","findingKind":"owner-wake",'
    '"detail":"turn-report","deliveryState":"delivered"}\n'
)
_WORKSPACE_CURSOR_BYTES = '{"baseOffset":4096}\n'


def _seed_emitted_signal_marker(root: Path) -> Path:
    """Leave one already-emitted signal marker on disk, as the notifier's cooldown store writes it."""

    path = AgentNotifierSignalCooldownStore(root).log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SIGNAL_MARKER_LINE, encoding="utf-8")
    return path


def _seed_workspace_cursor(root: Path) -> Path:
    """Leave the workspace river's already-committed virtual base offset on disk."""

    path = root / WORKSPACE_SOURCE / WORKSPACE_CURSOR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_WORKSPACE_CURSOR_BYTES, encoding="utf-8")
    return path


class ServingObservationLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_each_attempt_sleeps_the_observation_interval_after_the_call_returns(
        self,
    ) -> None:
        probe = _RefreshProbe()
        fixture = _ServingFixture(self.tmp, probe)

        # The sentinel stands in for the named constant so the assertion fails if the loop copies
        # a literal, or borrows the agent notifier's interval, instead of reading the
        # starting-row cadence the sweeper's fast path is built on.
        with mock.patch.object(lifespan_module, "DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS", 2.5):
            async with fixture.running():
                await _wait_until(lambda: len(fixture.clock.requested) == 1)
                self.assertEqual(
                    fixture.timeline,
                    [
                        # Call 1 is the pre-serve prime, taken before this owner existed at all.
                        "call 1 started",
                        "call 1 ended",
                        "call 2 started",
                        "call 2 ended",
                        "sleep(2.5)",
                    ],
                )

                fixture.clock.release()
                await _wait_until(lambda: len(fixture.clock.requested) == 2)

        self.assertEqual(
            fixture.timeline,
            [
                "call 1 started",
                "call 1 ended",
                "call 2 started",
                "call 2 ended",
                "sleep(2.5)",
                "call 3 started",
                "call 3 ended",
                "sleep(2.5)",
            ],
        )
        self.assertEqual(fixture.clock.requested, [2.5, 2.5])

    async def test_a_slow_attempt_delays_the_next_pass_instead_of_queueing_ticks(self) -> None:
        gate = _Gate(block_at=2)
        probe = _RefreshProbe(inner=gate)
        fixture = _ServingFixture(self.tmp, probe)

        async with fixture.running():
            # The pre-serve prime (invocation 1) already returned; the gate parks the recurring
            # owner's own first pass, which is the attempt whose lateness must not queue ticks.
            await _wait_until(gate.entered.is_set)
            fixture.clock.elapse(3.5)  # the pass outran three nominal one-second ticks
            await _REAL_SLEEP(0.01)

            self.assertEqual(probe.max_active, 1)
            self.assertEqual(probe.calls, 2)
            self.assertEqual(fixture.clock.timeline.count("sleep(1.0)"), 0)

            gate.release.set()
            await _wait_until(lambda: len(fixture.clock.requested) == 1)
            self.assertEqual(probe.calls, 2)  # a queued tick would already be a third attempt

            fixture.clock.release()
            await _wait_until(lambda: len(fixture.clock.requested) == 2)

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
        self.assertEqual(probe.max_active, 1)

    async def test_the_sweeper_keeps_its_own_full_sweep_rate_limit(self) -> None:
        clock = _VirtualClock([])
        catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        catalog.upsert(_entry("seat-1"))
        host = _LiveHost()
        sweeper = TerminalCatalogLivenessSweeper(
            catalog,
            host,
            now=clock.now,
            probe=LivenessProbe(pane_capturer=lambda _tmux_name: ""),
        )
        probe = _RefreshProbe(clock.timeline, inner=sweeper.refresh)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)

        async with fixture.running():
            await _advance(clock, lambda: probe.calls == 26)

        # The pre-serve prime plus twenty-five one-second attempts, three full sweeps: the
        # ten-second clock stayed inside the sweeper instead of being re-implemented (or
        # bypassed) by the loop, and the prime's extra attempt did not buy an extra sweep.
        self.assertEqual(probe.calls, 26)
        self.assertEqual(clock.seconds, 24.0)
        self.assertEqual(host.probes, 3)
        self.assertEqual(len(catalog.list()), 1)

    async def test_observation_continues_while_the_agent_notifier_is_disabled(self) -> None:
        probe = _RefreshProbe()
        fixture = _ServingFixture(self.tmp, probe)
        sweep = mock.Mock()
        settings = AgenticSettings(
            agent_notifier=AgentNotifierSettings(enabled=False, interval_seconds=4.0)
        )

        async with fixture.running(notifier=True, settings=settings, notifier_sweep=sweep):
            await _advance(
                fixture.clock,
                lambda: probe.calls >= 3 and fixture.clock.requested.count(1.0) >= 3,
            )

        # The real notifier loop is alive but inert; catalog progress is the observer's own.
        self.assertIn(4.0, fixture.clock.requested)
        sweep.assert_not_called()
        observation_sleeps = [delay for delay in fixture.clock.requested if delay != 4.0]
        self.assertEqual(observation_sleeps, [1.0, 1.0, 1.0])

    async def test_observation_needs_no_http_request_and_runs_through_the_drained_helper(
        self,
    ) -> None:
        probe = _RefreshProbe()
        fixture = _ServingFixture(self.tmp, probe)
        helper_calls: list[Callable[[], object]] = []
        real_helper = lifespan_module._to_thread_drained_on_cancel

        async def spy(
            function: Callable[..., object], /, *args: object, **kwargs: object
        ) -> object:
            helper_calls.append(function)
            return await real_helper(function, *args, **kwargs)

        with mock.patch.object(lifespan_module, "_to_thread_drained_on_cancel", spy):
            async with fixture.running():
                await _advance(fixture.clock, lambda: probe.calls >= 2)

        self.assertEqual(
            helper_calls, [fixture.runtime.liveness_sweeper.refresh] * len(helper_calls)
        )
        self.assertGreaterEqual(len(helper_calls), 2)
        self.assertTrue(all(thread is not threading.main_thread() for thread in probe.threads))
        # The premise that makes this an HTTP-free proof: the app whose lifespan is entered here
        # carries no route beyond the ones FastAPI mounts on every instance, so no request could
        # have reached the sweeper whose polls this case recorded. Compared against a fresh app's
        # own route table rather than filtered for "terminal" paths: a filter over an app with no
        # registered route matches nothing whatever the fixture or a later case does, while this
        # fails the moment a route -- any kind, path-carrying or not -- is added, and the case stops
        # isolating the observation owner.
        self.assertEqual(len(fixture.app.routes), len(FastAPI().routes))
        self.assertEqual(
            [route.path for route in fixture.app.routes if isinstance(route, APIRoute)],
            [route.path for route in FastAPI().routes if isinstance(route, APIRoute)],
        )

    async def test_the_observer_task_is_registered_and_cancelled_by_lifespan_teardown(self) -> None:
        probe = _RefreshProbe()
        fixture = _ServingFixture(self.tmp, probe)

        async with fixture.running():
            observers = _observer_tasks(fixture.created)
            self.assertEqual(len(observers), 1)
            # Call 1 is the already-returned startup prime; call 2 is the owner's own first pass.
            await _wait_until(lambda: probe.calls >= 2)

        self.assertTrue(observers[0].cancelled())
        self.assertEqual(fixture.clock.pending, 0)
        calls_at_teardown = probe.calls
        await _REAL_SLEEP(0.05)
        self.assertEqual(probe.calls, calls_at_teardown)
        fixture.shutdown.assert_called_once_with()

    async def test_a_failed_pass_keeps_the_owner_alive_on_the_same_cadence(self) -> None:
        # Two leading failures: the contained pre-serve prime (call 1) and the recurring owner's
        # own first pass (call 2). Neither may end the owner or move its cadence.
        probe = _RefreshProbe(failures=2)
        fixture = _ServingFixture(self.tmp, probe)

        async with fixture.running():
            await _wait_until(lambda: len(fixture.clock.requested) == 1)
            self.assertEqual(
                fixture.timeline,
                [
                    "call 1 started",
                    "call 1 ended",
                    "call 2 started",
                    "call 2 ended",
                    "sleep(1.0)",
                ],
            )

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


class ServingObservationFailureIsolationTests(unittest.IsolatedAsyncioTestCase):
    """``LOCR-R11@v1``: one unexpected pass failure is isolated, retried, and preserves truth.

    Every case drives the real ``_serving_lifespan`` and the real task collection, and the sweeper
    cases run the real ``TerminalCatalogLivenessSweeper`` over a real ``TerminalCatalog``, so the
    durable state under assertion is the production store's own committed bytes.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _live_stack(
        self, clock: _VirtualClock, session_ids: tuple[str, ...]
    ) -> tuple[TerminalCatalog, _LiveHost, TerminalCatalogLivenessSweeper]:
        """A real catalog and sweeper in this case's root, seeded with live rows."""

        catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        for session_id in session_ids:
            catalog.upsert(_entry(session_id))
        host = _LiveHost()
        sweeper = TerminalCatalogLivenessSweeper(
            catalog,
            host,
            now=clock.now,
            probe=LivenessProbe(pane_capturer=lambda _tmux_name: ""),
        )
        return catalog, host, sweeper

    async def test_a_failed_pass_leaves_every_sibling_loop_and_the_shutdown_intact(self) -> None:
        # ``fail_on=2``: call 1 is the pre-serve startup prime, so the injected failure lands on
        # the recurring owner's own first pass -- the pass this case isolates.
        probe = _RefreshProbe(fail_on=2)
        fixture = _ServingFixture(self.tmp, probe)
        fixture.app.get("/l11-serving-probe")(_serving_probe_route)
        sweep = mock.Mock()
        settings = AgenticSettings(
            agent_notifier=AgentNotifierSettings(enabled=False, interval_seconds=4.0)
        )

        async with fixture.running(notifier=True, settings=settings, notifier_sweep=sweep):
            await _wait_until(lambda: probe.calls == 2 and fixture.clock.requested.count(1.0) == 1)
            observers = _observer_tasks(fixture.created)
            siblings = [
                task for task in _background_tasks(fixture.created) if task not in observers
            ]
            self.assertEqual(len(observers), 1)
            self.assertFalse(observers[0].done())
            # projector.run, metrics, notifier, death-watch, river compaction.
            self.assertEqual(len(siblings), 5)
            self.assertTrue(all(not task.done() for task in siblings))

            # The retry completes and parks again, so the sibling assertions below are made with
            # the owner between attempts rather than mid-pass.
            await _advance(
                fixture.clock, lambda: probe.calls >= 3 and fixture.clock.requested.count(1.0) >= 2
            )

            self.assertFalse(observers[0].done())
            self.assertTrue(all(not task.done() for task in siblings))
            # The real notifier loop kept reaching its own cadence; its sweep stayed off.
            self.assertGreaterEqual(fixture.clock.requested.count(4.0), 1)
            sweep.assert_not_called()

            # And the HTTP surface still answers: this request is served while the observer is in
            # its failed-then-retried state, through the real app object.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=fixture.app), base_url="http://serving"
            ) as client:
                response = await client.get("/l11-serving-probe")
            self.assertEqual((response.status_code, response.json()), (200, {"status": "serving"}))

        self.assertTrue(observers[0].cancelled())
        self.assertTrue(all(task.cancelled() for task in siblings))
        fixture.shutdown.assert_called_once_with()

    async def test_a_failed_pass_publishes_no_durable_fact_at_all(self) -> None:
        clock = _VirtualClock([])
        catalog, _host, sweeper = self._live_stack(clock, ("seat-1",))
        _seed_emitted_signal_marker(self.tmp)
        _seed_workspace_cursor(self.tmp)
        probe = _RefreshProbe(clock.timeline, inner=sweeper.refresh, fail_on=3)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)
        health_key = terminal_observer_health_path(self.tmp).relative_to(self.tmp).as_posix()
        seeded = _durable_tree_without_observer_health(self.tmp)

        async with fixture.running():
            await _wait_until(lambda: probe.calls == 2 and clock.requested.count(1.0) == 1)
            after_success = _durable_tree_without_observer_health(self.tmp)
            # The health row exactly as the two SUCCESSFUL passes left it, read from the same
            # full-tree map the post-failure reading comes from. Held as one map KEY so the two
            # sides of the assertion below are like-scoped.
            row_after_success = _durable_tree(self.tmp)[health_key]
            # Control: the successful passes DID commit durable catalog truth, so the identity
            # below is a result about the failure path, not about a tree nothing ever writes.
            self.assertNotEqual(after_success, seeded)
            self.assertEqual(probe.outcomes, ["ok", "ok"])

            clock.release()
            await _wait_until(lambda: probe.calls == 3 and clock.requested.count(1.0) == 2)

            self.assertEqual(probe.outcomes, ["ok", "ok", "failed"])
            # No success fact, no diagnostic row, no event, no payload of the observer's OWN. The
            # claim is exact, so it is asserted in two LIKE-SCOPED halves: the filtered map against
            # the filtered map (nothing OUTSIDE the health row moved) and the same map KEY before
            # against after (the health row itself DID move). The earlier formulation compared the
            # FULL map against the FILTERED one, so its extra key made the inequality hold in every
            # reachable state -- including the state where the row never moved at all.
            after_failure = _durable_tree(self.tmp)
            self.assertEqual(_durable_tree_without_observer_health(self.tmp), after_success)
            self.assertNotEqual(after_failure[health_key], row_after_success)
            health = TerminalObserverHealthStore(self.tmp).read()
            assert health is not None
            self.assertEqual(health.activeFailureCategory, "steady-state-refresh-failed")
            self.assertFalse(_observer_tasks(fixture.created)[0].done())
            self.assertEqual([row.id for row in catalog.list()], ["seat-1"])

    async def test_the_retry_needs_no_request_restart_or_catalog_edit(self) -> None:
        # ``fail_on=2``: call 1 is the pre-serve startup prime, so the owner's own first pass is
        # the failed one this case retries from.
        probe = _RefreshProbe(fail_on=2)
        fixture = _ServingFixture(self.tmp, probe)
        TerminalCatalog(self.tmp / "terminal-sessions.json").upsert(_entry("seat-1"))
        _seed_emitted_signal_marker(self.tmp)
        _seed_workspace_cursor(self.tmp)
        frozen = _durable_tree_without_observer_health(self.tmp)

        async with fixture.running():
            await _wait_until(lambda: probe.calls == 2 and fixture.clock.requested.count(1.0) == 1)
            observer = _observer_tasks(fixture.created)[0]
            self.assertEqual(_durable_tree_without_observer_health(self.tmp), frozen)

            fixture.clock.release()  # the cadence is the only trigger released here

            await _wait_until(lambda: probe.calls == 3 and fixture.clock.requested.count(1.0) == 2)
            # Same task object: the retry is the same owner's next attempt, not a restart.
            self.assertIs(_observer_tasks(fixture.created)[0], observer)
            # And every durable artifact except the R17 diagnostic row is still byte-identical:
            # no catalog edit was needed either.
            # No HTTP request can be involved structurally: this case enters the ASGI lifespan
            # directly, with no server and no HTTP client used anywhere in it.
            self.assertEqual(_durable_tree_without_observer_health(self.tmp), frozen)

        self.assertEqual(probe.calls, 3)
        self.assertEqual(probe.outcomes, ["ok", "failed", "ok"])

    async def test_retry_resumes_from_the_persisted_catalog_and_preserves_committed_truth(
        self,
    ) -> None:
        clock = _VirtualClock([])
        catalog, host, sweeper = self._live_stack(clock, ("seat-1",))
        marker = _seed_emitted_signal_marker(self.tmp)
        cursor = _seed_workspace_cursor(self.tmp)
        # Controls: the seeded artifacts are what the real stores' own readers accept.
        self.assertEqual(
            [row.id for row in AgentNotifierSignalCooldownStore(self.tmp).read()], ["sig-l11-1"]
        )
        self.assertEqual(workspace_base_offset(self.tmp), 4096)
        seeded = _durable_tree_without_observer_health(self.tmp)
        marker_key = marker.relative_to(self.tmp).as_posix()
        cursor_key = cursor.relative_to(self.tmp).as_posix()
        probe = _RefreshProbe(clock.timeline, inner=sweeper.refresh, fail_on=3)
        fixture = _ServingFixture(self.tmp, probe, clock=clock)

        async with fixture.running():
            await _wait_until(lambda: probe.calls == 2 and clock.requested.count(1.0) == 1)

            # A later independent durable commit: a second row lands after the first pass.
            clock.elapse(10.0)
            catalog.upsert(_entry("seat-2"))
            before_failure = _durable_tree_without_observer_health(self.tmp)

            clock.release()
            await _wait_until(lambda: probe.calls == 3 and clock.requested.count(1.0) == 2)

            self.assertEqual(probe.outcomes, ["ok", "ok", "failed"])
            # The failed pass cleared no row, cursor, terminal evidence, or emitted-signal marker
            # (the R17 diagnostic row is the one artifact it must move, by contract).
            self.assertEqual(_durable_tree_without_observer_health(self.tmp), before_failure)
            self.assertEqual([row.id for row in catalog.list()], ["seat-1", "seat-2"])
            self.assertFalse(_observer_tasks(fixture.created)[0].done())

            clock.elapse(10.0)
            clock.release()
            await _wait_until(lambda: probe.calls == 4 and clock.requested.count(1.0) == 3)

            self.assertEqual(probe.outcomes, ["ok", "ok", "failed", "ok"])
            # The retry began from the CURRENT persisted catalog: the row committed ahead of the
            # failed pass is observed only after it, and the pre-existing row is probed again.
            self.assertEqual(host.probed, ["ar-seat-1", "ar-seat-1", "ar-seat-2"])

        self.assertEqual([row.id for row in catalog.list()], ["seat-1", "seat-2"])
        self.assertEqual(
            [(row.id, row.last_attached_at) for row in catalog.list()],
            [("seat-1", "2026-08-31T00:00:00+00:00"), ("seat-2", "2026-08-31T00:00:00+00:00")],
        )
        # Same scope on both sides as well: the two keys read below are non-health keys, so the
        # filtered map carries them identically -- but keeping the variable scoped like ``seeded``
        # means no future edit can read a key only one of the two maps has.
        final = _durable_tree_without_observer_health(self.tmp)
        self.assertEqual(final[marker_key], seeded[marker_key])
        self.assertEqual(final[cursor_key], seeded[cursor_key])

    async def test_cancellation_still_passes_through_the_failure_boundary(self) -> None:
        # The boundary is ``except Exception``; cancellation is not an ``Exception`` subclass, which
        # is the whole reason one boundary can isolate failures without swallowing shutdown.
        self.assertFalse(issubclass(asyncio.CancelledError, Exception))
        # ``block_at=2``: the pre-serve startup prime is invocation 1, so the parked pass is the
        # recurring owner's own in-flight attempt -- the one this case cancels mid-write.
        gate = _Gate(block_at=2)
        probe = _RefreshProbe(inner=gate)
        fixture = _ServingFixture(self.tmp, probe)

        async with fixture.running():
            await _wait_until(gate.entered.is_set)
            observer = _observer_tasks(fixture.created)[0]

            observer.cancel()
            gate.release.set()

            # Not ``await observer``: a boundary that swallowed the cancellation would park the loop
            # in its next cadence and never finish, so this bounded wait is the falsifiable form.
            await _wait_until(observer.done)

            self.assertTrue(observer.cancelled())
            self.assertEqual(probe.calls, 2)
            # The in-flight pass drained instead of being abandoned mid-write.
            self.assertEqual(probe.active, 0)
            await _REAL_SLEEP(0.05)
            self.assertEqual(probe.calls, 2)  # the loop did not resume after the boundary

        fixture.shutdown.assert_called_once_with()
