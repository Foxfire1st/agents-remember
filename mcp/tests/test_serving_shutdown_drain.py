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

THE FINALIZER IS BOUNDED HERE, NOT IN PRODUCTION
------------------------------------------------
The lifespan's teardown awaits each background task with no deadline of its own, and that is
correct: a worker that has not drained must keep the terminal host open. It also means a task that
SWALLOWS its cancellation parks the case inside that ``await`` forever, so the run dies on the lane's
timeout with no assertion ever printed and the clause the case exists for is never reached. The
bound below is on the test's own await and on nothing else: same fixture, same real lifespan, same
patches, and the positive path still finishes in the same fraction of a second (the parked worker is
released one interval after cancellation). What changes is that a drain that never returns arrives
as a named failure, with the tasks that are still running reported by name.

That report is not yet a printed summary, and two separate leaks had to be closed before it could
become one. ``IsolatedAsyncioTestCase`` closes its runner by cancelling every remaining task and
waiting for them, so a task that catches ``BaseException`` -- cancellation included -- at every
suspension point hangs the SESSION there, after the named failure has been recorded and before
pytest can print it; nothing the test can call ends such a task (repeated cancellation,
``coroutine.close()`` and ``gather`` were each measured, and each is refused). The fixture's patches
are a second leak of the same kind: they live in an ``ExitStack`` inside the context manager this
exit is unwinding, so a finalizer that never returns leaves ``asyncio.sleep`` -- the fixture's
virtual clock -- installed for every later test in the process. A case that reported by name still
left the lane at exit 124. So the interposition restores those patched attributes itself, before
anything is raised, reports whether its loop still holds a task that will not end, and
``_tearDownAsyncioRunner`` declines to wait on that loop. What remains is a deliberate, measured
limit: the task itself is parked for the life of the process rather than collected, because
collection is what prints the coroutine-finalization warnings.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile
import threading
import unittest
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
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

_FINALIZER_BOUND_SECONDS = 5.0
"""How long the lifespan's own finalizer is given to return before a stuck drain is named.

Twenty times the interval the positive path needs (the worker is released 0.25s after cancellation,
and the drain returns as soon as it is), so no scheduler can reach it. A drain that DOES reach it is
one the production code is still waiting on: the case reports which task is still running instead of
parking the lane in an unbreakable ``await``.
"""

_DRAIN_RETRY_SECONDS = 1.0
_DRAIN_RETRIES = 4
"""The forced drain's own bound: cancel every background task, wait, repeat.

Test-only cleanup for the already-failing path. A loop that swallowed one cancellation is parked at
its next suspension point, so the next cancel ends it, and the rounds are what end a task that
swallows only the first repeat. They are best-effort and claim nothing more: a loop that catches
``BaseException`` at EVERY suspension point has no cancel-reachable point at all, and no number of
rounds ends it -- that residue is reported by name and then deliberately never awaited again (see
``_finalize_bounded`` and ``_tearDownAsyncioRunner``).
"""


def _pending_names(background: list[asyncio.Task[object]]) -> list[str]:
    """The still-running background tasks, by the coroutine name the fixture recorded them under."""

    return [
        getattr(task.get_coro(), "__qualname__", task.get_name())
        for task in background
        if not task.done()
    ]


_ASYNCIO_PATCH_SURFACE = ("sleep", "create_task")
"""The process-global attributes the serving fixture patches, named for the restore below."""

_MISSING = object()

_ABANDONED: list[asyncio.Task[Any]] = []
"""Tasks a stuck drain could not end, held referenced ON PURPOSE.

A loop that catches ``BaseException`` at every suspension point cannot be ended by anything this
test can call: repeated cancellation is swallowed, ``coroutine.close()`` is refused with
``RuntimeError: coroutine ignored GeneratorExit``, and a ``gather`` over it never completes -- all
three were measured. Dropping such a task is worse than keeping it: collection closes its coroutine,
that close is refused for the same reason, and the process prints ``RuntimeError`` and
``coroutine method 'aclose' ... was never awaited`` as unraisable warnings while unrelated later
tests run. Holding them for the life of the process costs a handful of frames, and is the difference
between a bounded failure and a lane that reports warnings nobody can act on.
"""


def _snapshot_patch_surfaces() -> tuple[dict[str, Any], dict[str, Any]]:
    """The state the fixture's patches are about to replace, taken before it installs them."""

    return (
        {name: getattr(asyncio, name) for name in _ASYNCIO_PATCH_SURFACE},
        dict(vars(lifespan_module)),
    )


def _restore_patch_surfaces(snapshot: tuple[dict[str, Any], dict[str, Any]]) -> list[str]:
    """Put every patched process-global back, and report what had to be put back.

    The fixture installs its patches inside an ``ExitStack`` that lives in the context manager this
    module's exit is unwinding, so a finalizer that never returns leaves that stack un-unwound and
    every patched attribute installed for the REST OF THE PROCESS: ``asyncio.sleep`` stays the
    fixture's virtual clock, and the next unrelated test that awaits it parks forever -- a lane at
    exit 124, after this module has already reported by name. The stack cannot be unwound (see
    ``_finalize_bounded``), so the attributes it replaced are restored directly instead. This is
    idempotent: on every path where the stack DID unwind, each value already equals the snapshot and
    nothing is touched, and a patcher that is later exited re-applies the same original.
    """

    asyncio_snapshot, module_snapshot = snapshot
    restored = []
    for name, value in asyncio_snapshot.items():
        if getattr(asyncio, name) is not value:
            setattr(asyncio, name, value)
            restored.append(f"asyncio.{name}")
    for name, value in module_snapshot.items():
        if name.startswith("__"):
            continue
        if getattr(lifespan_module, name, _MISSING) is not value:
            setattr(lifespan_module, name, value)
            restored.append(f"_app_lifespan.{name}")
    return restored


async def _finalizer_returned(finalizer: asyncio.Task[bool | None], timeout: float) -> bool:
    done, _ = await asyncio.wait({finalizer}, timeout=timeout)
    return finalizer in done


async def _finalize_bounded(
    lifespan_cm: AbstractAsyncContextManager[None],
    background: list[asyncio.Task[object]],
) -> tuple[bool, str | None]:
    """Await the lifespan's finalizer, and refuse to await it forever.

    ``asyncio.wait`` on the exit rather than a bare ``await`` on it: the production finalizer has no
    deadline (a task that has not drained must keep the host open), so a task that swallows
    cancellation would otherwise park this case in an ``await`` that no assertion can interrupt --
    the run times out with nothing printed. Everything else about the exit is unchanged; the bound
    is on this test's wait alone.

    Returns ``(unfinished, report)`` and never raises, because the caller has to learn the first
    value BEFORE anything is raised at it. ``unfinished`` says this loop still owns a task that will
    not end, which is not decoration: ``IsolatedAsyncioTestCase`` closes its runner by cancelling
    every remaining task and waiting for them, so a loop holding one that swallows its cancellation
    hangs the session there -- after the bound has already produced its report, which is exactly how
    a named failure still leaves a lane at exit 124. ``report`` is the named failure to raise, or
    ``None`` when the finalizer returned inside the bound.
    """

    finalizer = asyncio.get_running_loop().create_task(lifespan_cm.__aexit__(None, None, None))
    if await _finalizer_returned(finalizer, _FINALIZER_BOUND_SECONDS):
        finalizer.result()
        return False, None
    stuck = _pending_names(background)
    for _ in range(_DRAIN_RETRIES):
        for task in background:
            if not task.done():
                task.cancel()
        if await _finalizer_returned(finalizer, _DRAIN_RETRY_SECONDS):
            finalizer.result()
            break
    # A loop that catches ``BaseException`` at its every suspension point has no point cancellation
    # can reach, so no number of rounds ends it; the rounds are what end a task that swallowed only
    # the first cancel.
    if not finalizer.done():
        # One more attempt, because it is what an unwind by cancellation would look like: the
        # lifespan's own teardown suppresses cancellation per task, so a cancel that REACHES the
        # finalizer lets it walk the rest of its loop and close the host. It does not reach it when
        # the thing being awaited is the task that swallows cancellation -- ``Task.cancel`` forwards
        # to the awaited future -- so this is an attempt, and what follows reports which one happened.
        finalizer.cancel()
        if await _finalizer_returned(finalizer, _FINALIZER_BOUND_SECONDS):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                finalizer.result()
    returned = finalizer.done()
    unfinished = bool(_pending_names(background))
    if unfinished:
        # Hold the task that would not end -- and the finalizer parked on it, whose frames keep the
        # fixture's async generators alive -- instead of letting collection report them.
        _ABANDONED.extend(task for task in (finalizer, *background) if not task.done())
    return unfinished, (
        "the serving lifespan's finalizer did not return within "
        f"{_FINALIZER_BOUND_SECONDS:.1f}s of the host closing: "
        f"{stuck or ['an unnamed task']} never finished draining. Teardown cancels the observation "
        "owner and then awaits it, so a task that swallows its own cancellation keeps the terminal "
        "host open and the case never reaches its witness assertions. This bound is the test's, not "
        "production's: the production finalizer has no deadline of its own, and the forced drain "
        "attempted before this report is test-only cleanup. "
        + (
            "The exit then returned, so the fixture's context manager and its patch stack unwound "
            "with it. "
            if returned
            else "The exit never returned, so that patch stack could not unwind; it is restored "
            "directly before this failure is raised. "
        )
        + (
            "The task named above cannot be ended at all, so this test's runner teardown is skipped "
            "rather than left waiting for it."
            if unfinished
            else "It ended under the forced drain; the runner teardown is unaffected."
        )
    )


@contextlib.asynccontextmanager
async def _bounded_running(
    fixture: _ServingFixture,
    *,
    on_unfinished: Callable[[bool], None] | None = None,
    **kwargs: Any,
) -> AsyncIterator[None]:
    """``fixture.running()``, with the lifespan's own finalizer under a test-only bound.

    The run itself is unchanged -- the same fixture, the same real ``_serving_lifespan``, the same
    patches -- so every clause the case asserts is asserted against the same behaviour. See
    ``_finalize_bounded`` for why the exit has to be waited on rather than simply awaited, and for
    what ``on_unfinished`` receives: the exit can end in a raised report, so the caller is told
    BEFORE that happens whether its loop is still holding a task.
    """

    lifespan_cm = fixture.running(**kwargs)
    surfaces = _snapshot_patch_surfaces()
    await lifespan_cm.__aenter__()
    body_failed = False
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        unfinished, report = await _finalize_bounded(lifespan_cm, fixture.created)
        # BEFORE anything is raised: a finalizer that never returned leaves the fixture's patched
        # attributes installed process-wide, and that is a lane-level failure, not a case-level one.
        restored = _restore_patch_surfaces(surfaces)
        if restored:
            report = f"{report} (restored: {', '.join(restored)})" if report else None
        if on_unfinished is not None:
            on_unfinished(unfinished)
        if report is not None and not body_failed:
            # The case body already failed; its failure is the one that must be reported.
            raise AssertionError(report)


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
        self._loop_holds_a_task_that_will_not_end = False

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _tearDownAsyncioRunner(self) -> None:
        """Skip the runner's own teardown when its loop still holds a task nothing can cancel.

        ``IsolatedAsyncioTestCase`` closes the runner, and ``Runner.close`` cancels every remaining
        task and then WAITS for them. A background loop that catches ``BaseException`` swallows that
        cancellation at every suspension point it has, so the runner waits forever -- the bound in
        ``_finalize_bounded`` fires, its named failure is recorded, and the session still dies on the
        lane timeout with no summary printed, this time inside ``_tearDownAsyncioRunner``. Nothing
        this test can do ends such a task, so the one loop stops being waited on: it belongs to this
        test alone (``_setupAsyncioRunner`` makes a fresh one per test), the case has already
        reported by name, and leaving it unclosed is what lets that report reach pytest.

        The positive path never sets the flag, so the runner is closed exactly as before.
        """

        if self._loop_holds_a_task_that_will_not_end:
            return
        # unittest's own private teardown, absent from the type stubs, reached through an untyped
        # handle: this override adds the skip above and is otherwise the base implementation.
        cast(Any, super())._tearDownAsyncioRunner()

    def _note_unfinished(self, unfinished: bool) -> None:
        self._loop_holds_a_task_that_will_not_end = unfinished

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
            async with _bounded_running(fixture, on_unfinished=self._note_unfinished):
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

        async with _bounded_running(
            fixture,
            on_unfinished=self._note_unfinished,
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
