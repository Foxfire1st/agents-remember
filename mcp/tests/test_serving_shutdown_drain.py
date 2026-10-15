"""Teardown proof for the serving lifespan's leaf-archive recovery owner.

Serving shutdown must cancel the recurring owner *and* wait for the off-loop
``LeafArchiveRecovery.tick`` worker it is running. Cancelling an ``asyncio.to_thread`` call does not
stop the underlying thread, so a teardown that only cancelled the owner task could return while the
recovery was still reading receipts or writing an archive outcome. The drain boundary is the
``background_accounting.offload`` helper the owner calls its worker through; the teardown that
consumes it is the lifespan's own ``background`` collection, cancelled as a group and then awaited
one task at a time.

The case drives the real ``_serving_lifespan`` and the real ``_leaf_archive_recovery_loop`` under a
virtual clock (no HTTP request, no browser, no real second) and parks a real ``tick`` worker in its
thread, so "did shutdown wait?" is answered by an ordered witness instead of by timing luck:
cancellation is requested while the worker is provably in flight, the worker is released after its
cancellation drain is observed, and the lifespan's own return reports the state it found.

THE FINALIZER IS BOUNDED HERE, NOT IN PRODUCTION
------------------------------------------------
The lifespan's teardown awaits each background task with no deadline of its own, and that is
correct: a worker that has not drained must keep the lifespan open. It also means a task that
SWALLOWS its cancellation parks the case inside that ``await`` forever, so the run dies on the lane's
timeout with no assertion ever printed and the clause the case exists for is never reached. The
bound below is on the test's own await and on nothing else: same fixture, same real lifespan, same
patches, and the parked worker is released when the cancelled owner actually awaits it. A drain
that never returns arrives
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
import hashlib
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from contextvars import Context
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.serving._app_lifespan as lifespan_module
from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving._app_lifespan import _serving_lifespan
from fastapi import FastAPI

_REAL_SLEEP = asyncio.sleep
"""The real sleep, captured before the fixture swaps ``asyncio.sleep`` for its virtual clock."""

_STARTED_AT = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
_COMMITTED_DIRECTORY = "archive-outcomes"
_FINALIZER_BOUND_SECONDS = HANG_GUARD_SECONDS
"""The lifespan exit only has a test hang guard; production still owns an unbounded drain."""

_DRAIN_RETRY_SECONDS = HANG_GUARD_SECONDS
_DRAIN_RETRIES = 4
"""The forced drain's own bound: cancel every background task, wait, repeat.

Test-only cleanup for the already-failing path. A loop that swallowed one cancellation is parked at
its next suspension point, so the next cancel ends it, and the rounds are what end a task that
swallows only the first repeat. They are best-effort and claim nothing more: a loop that catches
``BaseException`` at EVERY suspension point has no cancel-reachable point at all, and no number of
rounds ends it -- that residue is reported by name and then deliberately never awaited again (see
``_finalize_bounded`` and ``_tearDownAsyncioRunner``).
"""


async def _parked_forever(*_args: object, **_kwargs: object) -> None:
    """A sibling background loop that touches nothing until lifespan teardown cancels it."""

    await asyncio.Event().wait()


async def _wait_until(
    predicate: Callable[[], bool], *, timeout: float = HANG_GUARD_SECONDS
) -> None:
    """Yield until ``predicate`` holds. The deadline bounds a hung test, never a cadence."""

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not reached before the test deadline")
        await _REAL_SLEEP(0.001)


class _VirtualClock:
    """``asyncio.sleep`` as a parked request, plus the serving clock the runtime reads.

    A sleep parks its caller until teardown cancels it, so ``pending`` counts the passes a loop
    has scheduled and not yet started: zero after teardown means no owner is waiting to run again.
    """

    def __init__(self) -> None:
        self._parked: list[asyncio.Future[None]] = []

    @property
    def pending(self) -> int:
        return len(self._parked)

    def now(self) -> datetime:
        return _STARTED_AT

    async def sleep(self, _delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._parked.append(future)
        try:
            await future
        except asyncio.CancelledError:
            self._parked = [item for item in self._parked if item is not future]
            raise


class _TickProbe:
    """The archive recovery the owner constructs: counts ``tick`` attempts and records ``close``.

    ``inner`` runs inside the attempt, so a gate there parks exactly the invocation the case
    names. ``active`` is the number of attempts currently inside their worker thread.
    """

    def __init__(self, *, inner: Callable[[], object]) -> None:
        self.inner = inner
        self.calls = 0
        self.active = 0
        self.close = mock.Mock()

    def tick(self) -> None:
        self.calls += 1
        self.active += 1
        try:
            self.inner()
        finally:
            self.active -= 1


class _ServingFixture:
    """One disposable serving runtime whose background loops run under a virtual clock.

    Only the archive recovery owner is real: the projector, metrics, held-delivery and compaction
    loops are replaced by parked coroutines, and the blocking startup compaction is inert, so a
    case cannot start containers, read live settings, or write stores. The owner builds its
    recovery from the lifespan module's own name, which resolves to ``recovery`` here.

    ``returned`` is called with no suspension after the lifespan's own exit returns, so its side
    effect reads the state the lifespan returned over.
    """

    def __init__(self, root: Path, recovery: _TickProbe) -> None:
        self.recovery = recovery
        self.clock = _VirtualClock()
        self.created: list[asyncio.Task[object]] = []
        self.returned = mock.Mock()
        self.review_trees_shutdown_threads: list[threading.Thread] = []
        self.review_trees_shutdown = mock.Mock(
            side_effect=lambda: self.review_trees_shutdown_threads.append(
                threading.current_thread()
            )
        )
        self.app = FastAPI()
        self.runtime = cast(
            _ServingRuntime,
            SimpleNamespace(
                config=SimpleNamespace(coordination_root=root),
                observer_root=root,
                projector=SimpleNamespace(prime=mock.AsyncMock(), run=_parked_forever),
                review_trees_shutdown=self.review_trees_shutdown,
                clock=self.clock.now,
                background_ledger=None,
            ),
        )

    def _record_task(
        self,
        coro: Coroutine[Any, Any, object],
        *,
        name: str | None = None,
        context: Context | None = None,
    ) -> asyncio.Task[object]:
        task = asyncio.get_running_loop().create_task(coro, name=name, context=context)
        self.created.append(task)
        return task

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        with contextlib.ExitStack() as stack:
            for name, replacement in (
                ("compact_workspace_river", mock.Mock()),
                ("_metrics_loop", _parked_forever),
                ("_held_delivery_loop", _parked_forever),
                ("_workspace_river_compaction_loop", _parked_forever),
                ("_warmup_watch_loop", _parked_forever),
                ("start_heap_tracing", lambda: False),
                ("malloc_trim_enabled", lambda: False),
                ("LeafArchiveRecovery", lambda _config: self.recovery),
            ):
                stack.enter_context(mock.patch.object(lifespan_module, name, replacement))
            stack.enter_context(mock.patch.object(asyncio, "sleep", self.clock.sleep))
            stack.enter_context(mock.patch.object(asyncio, "create_task", self._record_task))
            lifespan = _serving_lifespan(self.runtime, cast(ProviderMetricsStore, mock.Mock()))
            shutdowns = self.review_trees_shutdown.call_count
            async with lifespan(self.app):
                yield
            self.returned()
            assert self.review_trees_shutdown.call_count == shutdowns + 1
            assert all(
                thread is not threading.main_thread()
                for thread in self.review_trees_shutdown_threads
            )


def _recovery_tasks(tasks: list[asyncio.Task[object]]) -> list[asyncio.Task[object]]:
    def is_recovery_owner(task: asyncio.Task[object]) -> bool:
        coro = task.get_coro()
        return getattr(coro, "__qualname__", "") == "_leaf_archive_recovery_loop"

    return [task for task in tasks if is_recovery_owner(task)]


def _durable_tree(root: Path) -> dict[str, str]:
    """Every durable artifact under ``root`` as relative path -> sha256 of its bytes.

    A pass that writes anything changes this map, so byte-identity across teardown is the
    "nothing was written after the lifespan returned" assertion.
    """

    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _commit_outcome(root: Path, outcome_id: str) -> None:
    """One durable write of the kind a recovery pass ends with."""

    directory = root / _COMMITTED_DIRECTORY
    directory.mkdir(exist_ok=True)
    (directory / f"{outcome_id}.json").write_text(f'{{"id":"{outcome_id}"}}\n', encoding="utf-8")


def _committed_ids(root: Path) -> list[str]:
    """The outcome ids a reader would find committed under ``root``."""

    return sorted(path.stem for path in (root / _COMMITTED_DIRECTORY).glob("*.json"))


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
    deadline (a task that has not drained must keep the lifespan open), so a task that swallows
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
        # finalizer lets it walk the rest of its loop and return. It does not reach it when
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
        f"{_FINALIZER_BOUND_SECONDS:.1f}s of teardown starting: "
        f"{stuck or ['an unnamed task']} never finished draining. Teardown cancels the recovery "
        "owner and then awaits it, so a task that swallows its own cancellation keeps the lifespan "
        "open and the case never reaches its witness assertions. This bound is the test's, not "
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
) -> AsyncIterator[None]:
    """``fixture.running()``, with the lifespan's own finalizer under a test-only bound.

    The run itself is unchanged -- the same fixture, the same real ``_serving_lifespan``, the same
    patches -- so every clause the case asserts is asserted against the same behaviour. See
    ``_finalize_bounded`` for why the exit has to be waited on rather than simply awaited, and for
    what ``on_unfinished`` receives: the exit can end in a raised report, so the caller is told
    BEFORE that happens whether its loop is still holding a task.
    """

    lifespan_cm = fixture.running()
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


class _DrainGate:
    """The steady pass teardown has to drain: parked in its worker thread, then committed.

    ``probe.inner`` runs inside the attempt, so parking here parks exactly the ``tick``
    invocation the case names without teaching the probe a second blocking mode. Nothing ticks
    before the owner exists, so ``block_at=1`` parks the recurring owner's own first pass -- the
    in-flight worker shutdown must wait for.

    The monitor releases the worker only after its cancelled owner directly awaits that worker.
    ``on_release`` is the pass's own last durable act: the outcome commit that must survive
    cancellation. No scheduler interval supplies the ordering witness.
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
        if not self.release.wait(timeout=HANG_GUARD_SECONDS):
            raise AssertionError("the parked recovery worker was not released")
        if self._on_release is not None:
            self._on_release()
        self._events.append("worker-released")
        self.released.set()
        return []


@contextlib.contextmanager
def _observe_worker_drain(probe: _TickProbe, draining: asyncio.Event) -> Any:
    """Witness the cancelled product coroutine's direct await of its exact thread task."""
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()

    class ObservedWorker(asyncio.Task[object]):
        def __await__(self) -> Any:
            owner = asyncio.current_task()
            if owner is not None and owner.cancelling():
                draining.set()
            return super().__await__()

    def task_factory(owner_loop: asyncio.AbstractEventLoop, coroutine: Any, **kwargs: Any) -> Any:
        frame = getattr(coroutine, "cr_frame", None)
        func = frame.f_locals.get("func") if frame is not None else None
        # The production handoff measures the worker through a thin wrapper (F01): follow
        # the functools ``__wrapped__`` link so the witness still names the exact thread
        # task running the probe, wrapper or not.
        if func is not None and getattr(func, "__wrapped__", func) == probe.tick:
            return ObservedWorker(coroutine, loop=owner_loop, **kwargs)
        return asyncio.Task(coroutine, loop=owner_loop, **kwargs)

    real_wait = asyncio.wait

    async def observed_wait(futures: Any, **kwargs: Any) -> Any:
        owner = asyncio.current_task()
        if (
            owner is not None
            and owner.cancelling()
            and any(isinstance(task, ObservedWorker) for task in futures)
        ):
            draining.set()
        return await real_wait(futures, **kwargs)

    loop.set_task_factory(task_factory)
    try:
        with mock.patch.object(asyncio, "wait", observed_wait):
            yield
    finally:
        loop.set_task_factory(previous_factory)


async def _release_after_cancellation(
    owner: asyncio.Task[object],
    gate: _DrainGate,
    events: list[str],
    draining: asyncio.Event,
) -> None:
    """Release after the cancelled owner reaches the real worker-drain await."""
    await _wait_until(draining.is_set)
    assert owner.cancelling() >= 1, "the worker await did not follow cancellation"
    assert not gate.released.is_set(), "the worker escaped before its drain was observed"
    events.append("cancellation-requested")
    gate.release.set()


def _recovery_failure_lines(log_spy: mock.Mock) -> list[object]:
    """Every recovery-failure line the lifespan logged, judged by the module's own message.

    Cancellation must not be recorded as a failed pass, and the only recording the owner has is
    this log call -- so the assertion is about the owner's own message rather than about
    "nothing was logged at all", which would also forbid unrelated sibling logging.
    """

    lines = []
    for call in log_spy.exception.call_args_list:
        message = call.args[0] if call.args else ""
        if isinstance(message, str) and "leaf agent archive observation" in message:
            lines.append(message)
    return lines


class ServingShutdownDrainTests(unittest.IsolatedAsyncioTestCase):
    """Teardown cancels the recovery owner, drains its worker, then returns."""

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

    async def test_teardown_drains_an_in_flight_tick_before_the_lifespan_returns(self) -> None:
        events: list[str] = []
        _commit_outcome(self.tmp, "outcome-1")
        gate = _DrainGate(
            block_at=1,
            on_release=lambda: _commit_outcome(self.tmp, "outcome-late"),
            events=events,
        )
        probe = _TickProbe(inner=gate)
        fixture = _ServingFixture(self.tmp, probe)
        at_return: list[tuple[int, bool, list[str]]] = []
        returned_state: list[tuple[int, Any]] = []
        draining = asyncio.Event()

        def _lifespan_returned() -> None:
            events.append("lifespan-returned")
            returned_state.append((probe.calls, _durable_tree(self.tmp)))
            # Read inside the callback: this is the state the lifespan returned over.
            at_return.append((probe.active, gate.released.is_set(), _committed_ids(self.tmp)))

        fixture.returned.side_effect = _lifespan_returned
        probe.close.side_effect = lambda: events.append("recovery-closed")

        with (
            mock.patch.object(lifespan_module, "logger") as log_spy,
            _observe_worker_drain(probe, draining),
        ):
            async with _bounded_running(fixture, on_unfinished=self._note_unfinished):
                await _wait_until(gate.entered.is_set)
                owner = _recovery_tasks(fixture.created)[0]
                monitor = asyncio.get_running_loop().create_task(
                    _release_after_cancellation(owner, gate, events, draining)
                )
            await monitor

        # The ordering witness: cancellation arrived while the worker was parked, the worker was
        # released strictly after it, the owner closed its recovery strictly after the drain, and
        # the lifespan returned strictly after the owner ended.
        self.assertEqual(
            events,
            [
                "worker-entered",
                "cancellation-requested",
                "worker-released",
                "recovery-closed",
                "lifespan-returned",
            ],
        )
        # The lifespan returned with no worker running, its own pass's commit already durable, and
        # the owner task drained.
        self.assertEqual(at_return, [(0, True, ["outcome-1", "outcome-late"])])
        self.assertTrue(owner.cancelled())
        # Cancellation is not recorded as a recovery failure: the boundary is ``except
        # Exception``, and ``CancelledError`` is a ``BaseException``.
        self.assertEqual(_recovery_failure_lines(log_spy), [])
        # No future pass starts, and nothing is written after the lifespan returned.
        self.assertTrue(all(task.done() for task in fixture.created))
        self.assertEqual(fixture.clock.pending, 0)
        calls_at_teardown, after_return = returned_state[0]
        self.assertEqual(probe.calls, calls_at_teardown)
        self.assertEqual(_durable_tree(self.tmp), after_return)
        # Control for the identity above: this run really did commit a durable outcome, so the
        # tree is a live surface rather than one nothing ever writes.
        self.assertEqual(_committed_ids(self.tmp), ["outcome-1", "outcome-late"])
        for guard in (_FINALIZER_BOUND_SECONDS, _DRAIN_RETRY_SECONDS):
            with self.subTest(finalizer_guard=guard):
                result = mock.AsyncMock(return_value=({monitor}, set()))
                with mock.patch.object(asyncio, "wait", result):
                    self.assertTrue(await _finalizer_returned(monitor, guard))
                result.assert_awaited_once_with({monitor}, timeout=HANG_GUARD_SECONDS)
