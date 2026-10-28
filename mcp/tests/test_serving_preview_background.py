"""A preview serving process leaves the live dashboard's five background owners alone.

The preview backend is started from this worktree against the developer's real data. Before the
switch it ran every loop the live dashboard on 9785 runs: a second deliverer of held role messages,
a second archive recoverer, a second warmup that resumes agents, a second workspace-river compactor
and a second metrics writer, all of them acting on live data. Only the live dashboard may own
those, so a preview sets ``AR_SERVING_PREVIEW_NO_BACKGROUND`` and serves, builds and keeps its
review-tree projection without them.

Levels proved here:

* ``PreviewSuppressionDecisionTests`` calls the decision helper directly. With the switch absent or
  false nothing is suppressed; with it truthy the suppressed set is exactly the five live loop
  names -- the helper answers no other question, so the projection, the move watcher and the heap
  and malloc gates cannot be touched by it.
* ``PreviewLifespanTests`` drives the real ``_serving_lifespan`` against a stand-in runtime and
  records every ``asyncio.create_task`` the lifespan makes. Each live loop is replaced by a
  recorder carrying the loop's own name, so "no task exists for a suppressed loop" is asserted on
  the task the lifespan asked the event loop for, not on the helper's return value. The same runs
  show the projection, the explainer move watcher and the heap/malloc diagnostics still start, and
  that an absent switch creates the same task sequence as before the switch existed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import unittest
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.serving import _app_lifespan as serving
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.stream_shutdown import StreamShutdown
from fastapi import FastAPI

PREVIEW_ENV = "AR_SERVING_PREVIEW_NO_BACKGROUND"
"""The switch this file holds: the one the lifespan reads at startup."""

LIVE_LOOPS = (
    "held-delivery",
    "leaf-archive-recovery",
    "warmup-watch",
    "workspace-river-compaction",
    "metrics",
)
"""The five loops only the live dashboard may own, in the order the startup notice names them."""

KEPT_BACKGROUND = ("projection", "move-watch", "heap-diag", "malloc-trim")
"""What a preview still runs: serving, the review-tree projection, and the gated diagnostics."""


async def _parked_forever() -> None:
    """A background task that touches nothing until the lifespan's teardown cancels it."""

    await asyncio.Event().wait()


def _recorder(name: str) -> Callable[..., Any]:
    """A stand-in loop coroutine whose one observable fact is the name it carries."""

    async def loop(*_args: object, **_kwargs: object) -> None:
        await _parked_forever()

    loop.__qualname__ = name
    return loop


@contextlib.contextmanager
def _preview_env(value: str | None) -> Iterator[None]:
    """Set (or, with ``None``, remove) the switch for one run, whatever the ambient environment says."""

    with mock.patch.dict(os.environ, {}, clear=False):
        if value is None:
            os.environ.pop(PREVIEW_ENV, None)
        else:
            os.environ[PREVIEW_ENV] = value
        yield


class PreviewSuppressionDecisionTests(unittest.TestCase):
    """The helper's whole answer: which live-only loops this process must skip."""

    def test_absent_switch_suppresses_nothing(self) -> None:
        self.assertEqual(serving._preview_suppresses({}), frozenset())
        self.assertEqual(serving._preview_suppresses({PREVIEW_ENV: ""}), frozenset())

    def test_false_values_suppress_nothing(self) -> None:
        for value in ("0", "false", "no", "off", "  ", "preview"):
            with self.subTest(value=value):
                self.assertEqual(serving._preview_suppresses({PREVIEW_ENV: value}), frozenset())

    def test_truthy_values_suppress_exactly_the_five_live_loops(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on", " 1 "):
            with self.subTest(value=value):
                suppressed = serving._preview_suppresses({PREVIEW_ENV: value})
                self.assertEqual(suppressed, frozenset(LIVE_LOOPS))
                # Nothing a preview must keep is ever in the suppressed set.
                self.assertEqual(suppressed & set(KEPT_BACKGROUND), frozenset())

    def test_the_default_read_is_the_process_environment(self) -> None:
        """No argument means ``os.environ``, read when called rather than at import."""

        with _preview_env("1"):
            self.assertEqual(serving._preview_suppresses(), frozenset(LIVE_LOOPS))
        with _preview_env(None):
            self.assertEqual(serving._preview_suppresses(), frozenset())


class PreviewLifespanTests(unittest.IsolatedAsyncioTestCase):
    """The real lifespan: no task for a suppressed loop, every keeper task still created."""

    async def _run_lifespan(
        self, *, heap: bool, malloc: bool
    ) -> tuple[list[str], Mapping[str, mock.Mock]]:
        """Start the real lifespan; return the created task names and the patches used.

        The five live loops, the move watcher, the diagnostics and the projector's run are all
        replaced by named recorders, and ``compact_workspace_river`` is inert, so no case starts a
        container, reads live settings or writes a store. Only the task the lifespan asks the event
        loop for is observed; the real loop bodies never run here.
        """

        created: list[str] = []
        real_create_task = asyncio.create_task
        tmp = Path(self.tmp.name)

        def record_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
            name = getattr(coro, "__qualname__", type(coro).__name__)
            # Startup now owns prime and run in one projection task.
            created.append("projection" if name == "_project_until_shutdown" else name)
            return real_create_task(coro, **kwargs)

        runtime = cast(
            _ServingRuntime,
            SimpleNamespace(
                launcher_catalog_prepare=None,
                stream_shutdown=StreamShutdown(),
                config=SimpleNamespace(coordination_root=tmp),
                observer_root=tmp,
                projector=SimpleNamespace(
                    close_readiness=mock.Mock(), prime=mock.AsyncMock(), run=_recorder("projection")
                ),
                clock=lambda: datetime(2026, 10, 25, tzinfo=UTC),
                background_ledger=None,
                review_trees_shutdown=None,
                master_preparation=None,
                # The move watcher is admitted, so the switch's effect on it is visible.
                explainer_application=SimpleNamespace(watcher=object()),
            ),
        )
        replacements: dict[str, Any] = {
            "compact_workspace_river": mock.Mock(),
            "_metrics_loop": _recorder("metrics"),
            "_leaf_archive_recovery_loop": _recorder("leaf-archive-recovery"),
            "_held_delivery_loop": _recorder("held-delivery"),
            "_workspace_river_compaction_loop": _recorder("workspace-river-compaction"),
            "_warmup_watch_loop": _recorder("warmup-watch"),
            "heap_diag_loop": _recorder("heap-diag"),
            "_malloc_trim_loop": _recorder("malloc-trim"),
            "move_notice_watch": lambda *_args, **_kwargs: SimpleNamespace(
                run=_recorder("move-watch")
            ),
            "start_heap_tracing": lambda: heap,
            "malloc_trim_enabled": lambda: malloc,
        }
        log = mock.Mock()
        with contextlib.ExitStack() as stack:
            for name, replacement in replacements.items():
                stack.enter_context(mock.patch.object(serving, name, replacement))
            stack.enter_context(mock.patch.object(serving, "logger", log))
            stack.enter_context(mock.patch.object(asyncio, "create_task", record_task))
            lifespan = serving._serving_lifespan(runtime, cast(ProviderMetricsStore, mock.Mock()))
            async with lifespan(FastAPI()):
                # Let every created task take its first step and park there.
                for _ in range(3):
                    await asyncio.sleep(0)
        return created, {"log": log}

    def _observed(self, created: list[str]) -> list[str]:
        """The task names this file knows, in creation order; internals it does not own are ignored."""

        known = {*LIVE_LOOPS, *KEPT_BACKGROUND}
        return [name for name in created if name in known]

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def test_absent_or_false_switch_starts_every_loop_in_todays_order(self) -> None:
        for value in (None, "0", "false"):
            with self.subTest(value=value), _preview_env(value):
                created, patches = await self._run_lifespan(heap=False, malloc=False)
                self.assertEqual(
                    self._observed(created),
                    [
                        "projection",
                        "metrics",
                        "leaf-archive-recovery",
                        "held-delivery",
                        "workspace-river-compaction",
                        "warmup-watch",
                        "move-watch",
                    ],
                )
                # Nothing is said at startup on the live dashboard's own path.
                patches["log"].warning.assert_not_called()
                patches["log"].info.assert_not_called()

    async def test_preview_switch_creates_no_live_loop_task_and_keeps_the_rest(self) -> None:
        for value in ("1", "true", "yes", "on"):
            with self.subTest(value=value), _preview_env(value):
                created, patches = await self._run_lifespan(heap=True, malloc=True)
                observed = self._observed(created)
                self.assertEqual(observed, ["projection", "move-watch", "heap-diag", "malloc-trim"])
                # The five live owners are absent at the level the lifespan acts on: no task at all.
                self.assertEqual([name for name in created if name in LIVE_LOOPS], [])
                # The operator sees one line naming the switch and exactly what was skipped.
                patches["log"].warning.assert_called_once_with(
                    "preview background switch %s=%s: skipped %s",
                    PREVIEW_ENV,
                    value,
                    "held-delivery, leaf-archive-recovery, warmup-watch, "
                    "workspace-river-compaction, metrics",
                )

    async def test_heap_and_malloc_diagnostics_are_gated_exactly_as_before(self) -> None:
        """The switch suppresses the five loops and nothing else: each gate still decides alone."""

        cases = (
            (None, False, False),
            ("1", False, False),
            ("1", True, False),
            ("1", False, True),
        )
        for value, heap, malloc in cases:
            with self.subTest(value=value, heap=heap, malloc=malloc), _preview_env(value):
                created, _patches = await self._run_lifespan(heap=heap, malloc=malloc)
                observed = self._observed(created)
                self.assertEqual("heap-diag" in observed, heap)
                self.assertEqual("malloc-trim" in observed, malloc)
                self.assertIn("projection", observed)
