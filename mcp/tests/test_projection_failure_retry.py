"""F05/F06: real projection reads, publication failures and drained tick retries."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.serving import change_watcher as watcher_module
from agents_remember.serving import projector as projector_module
from agents_remember.serving.delta import stable_projection_state
from agents_remember.serving.projections.projection_inputs import (
    HEALING_PROJECTION_DOMAINS,
    ProjectionDomain,
    ProjectionInputState,
    ProjectionRefresh,
)
from agents_remember.serving.projections.projection_store import (
    ProjectionTickState,
    project_and_write,
)
from agents_remember.serving.projector import ProjectionRefreshers, Projector
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until
from test_projection_incremental import DOC, _config

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def _world(root: Path, count: int) -> Path:
    for index in range(count):
        doc = root / "tasks" / "r" / f"t{index}" / "task.json"
        doc.parent.mkdir(parents=True)
        doc.write_text(json.dumps({**DOC, "id": f"t{index}", "slug": f"t{index}"}))
    return root / "tasks" / "r" / "t0" / "task.json"


def _lost_marker(root: Path, name: str) -> None:
    doc = root / "tasks" / "r" / name / "task.json"
    doc.parent.mkdir(parents=True)
    doc.write_text(json.dumps({**DOC, "id": name, "slug": name}))


class ProjectionReaderRetryTests(unittest.TestCase):
    def test_before_and_after_each_reader_failure_retries_complete_projection(self) -> None:
        for count in (1, 9):
            for method in (
                "_reproject_task_documents",
                "_refresh_providers",
                "_refresh_engine_facts",
                "_refresh_drift",
                "_refresh_workspace",
                "_refresh_progress",
            ):
                for after in (False, True):
                    with (
                        self.subTest(count=count, method=method, after=after),
                        tempfile.TemporaryDirectory() as tmp,
                    ):
                        root = Path(tmp)
                        doc = _world(root, count)
                        config = _config(root)
                        state = ProjectionInputState()
                        tick = ProjectionTickState(input_state=state)
                        project_and_write(config, now=NOW, tick=tick)
                        doc.write_text(
                            json.dumps({**DOC, "id": "t0", "slug": "t0", "title": "edited"})
                        )
                        contract = root / "tasks" / "enclosures" / "l1" / "series-contract.md"
                        contract.parent.mkdir(parents=True)
                        contract.write_text("invalid new contract\n")
                        refresh = ProjectionRefresh.change(
                            frozenset({ProjectionDomain.TASKS}),
                            paths=(str(doc), str(contract)),
                            paths_complete=True,
                        )
                        reader = getattr(state, method)

                        def fail(*args, after=after, reader=reader, **kwargs):
                            if after:
                                reader(*args, **kwargs)
                            raise OSError("injected reader failure")

                        with (
                            mock.patch.object(state, method, side_effect=fail),
                            self.assertRaisesRegex(OSError, "injected reader"),
                        ):
                            project_and_write(config, now=NOW, refresh=refresh, tick=tick)
                        self.assertTrue(state._task_refresh_pending)
                        retried = project_and_write(config, now=NOW, refresh=refresh, tick=tick)
                        reference = project_and_write(config, now=NOW)
                        self.assertFalse(state._task_refresh_pending)
                        self.assertEqual(
                            stable_projection_state(retried), stable_projection_state(reference)
                        )
                        self.assertIn("edited", [node.title for node in retried.tasks])


class ProjectionHealAndDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_sizes_two_heals_recover_lost_markers_through_failed_full(self) -> None:
        for count in (1, 9):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                doc = _world(root, count)
                clock = [0.0]
                with mock.patch.object(
                    watcher_module, "time", SimpleNamespace(monotonic=lambda clock=clock: clock[0])
                ):
                    projector = Projector(
                        _config(root), refreshers=ProjectionRefreshers(change_watcher=mock.Mock())
                    )
                    pacer = projector._pacer
                    assert pacer is not None
                    pacer.set_watcher_healthy(True)
                    await projector.prime()
                    for cycle in (1, 2):
                        name = f"lost-{cycle}"
                        _lost_marker(root, name)
                        start = clock[0]
                        for offset in (10, 100, 200):
                            clock[0] = start + offset - 1
                            doc.write_text(
                                json.dumps(
                                    {**DOC, "id": "t0", "slug": "t0", "title": str(clock[0])}
                                )
                            )
                            pacer.notify_change(
                                frozenset({ProjectionDomain.TASKS}), frozenset({str(doc)})
                            )
                            clock[0] = start + offset
                            wake = await asyncio.wait_for(pacer.wait(), HANG_GUARD_SECONDS)
                            self.assertEqual(wake.reason, "change")
                            current = await projector._tick(NOW, projector._next_refresh(wake))
                            self.assertNotIn(name, [node.id for node in current.tasks])
                            self.assertTrue(projector._publish_wake(wake, current))
                        clock[0] = start + 300
                        wake = await asyncio.wait_for(pacer.wait(), HANG_GUARD_SECONDS)
                        self.assertEqual(wake.reason, "full")
                        state = projector._input_state
                        assert state is not None
                        with (
                            mock.patch.object(
                                state, "_refresh_tasks_full", side_effect=OSError("heal failed")
                            ),
                            self.assertRaisesRegex(OSError, "heal failed"),
                        ):
                            await projector._tick(NOW, projector._next_refresh(wake))
                        pacer.note_failed_full(wake)
                        # A later partial arrival cannot certify the failed full's path coverage.
                        pacer.notify_change(
                            frozenset({ProjectionDomain.TASKS}), frozenset({str(doc)})
                        )
                        clock[0] += 3
                        retry = await asyncio.wait_for(pacer.wait(), HANG_GUARD_SECONDS)
                        self.assertEqual(retry.domains, HEALING_PROJECTION_DOMAINS)
                        self.assertFalse(retry.paths_complete)
                        current = await projector._tick(NOW, projector._next_refresh(retry))
                        self.assertIn(name, [node.id for node in current.tasks])
                        with mock.patch.object(
                            projector, "_publish_projection", side_effect=OSError("publish failed")
                        ):
                            self.assertFalse(projector._publish_wake(retry, current))
                        clock[0] += 3
                        retry = await asyncio.wait_for(pacer.wait(), HANG_GUARD_SECONDS)
                        current = await projector._tick(NOW, projector._next_refresh(retry))
                        self.assertTrue(projector._publish_wake(retry, current))
                        reference = project_and_write(_config(root), now=NOW)
                        self.assertEqual(
                            stable_projection_state(current), stable_projection_state(reference)
                        )

    async def test_repeated_cancel_drains_actual_tick_and_retains_arrivals_and_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = _world(root, 1)
            projector = Projector(
                _config(root), refreshers=ProjectionRefreshers(change_watcher=mock.Mock())
            )
            await projector.prime()
            pacer = projector._pacer
            assert pacer is not None
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            real_project = projector_module.project_and_write

            def held(*args, **kwargs):
                entered.set()
                if not release.wait(HANG_GUARD_SECONDS):
                    raise AssertionError("worker was not released")
                try:
                    return real_project(*args, **kwargs)
                finally:
                    finished.set()

            pacer.set_watcher_healthy(True)
            pacer.notify_change(frozenset({ProjectionDomain.TASKS}), frozenset({str(doc)}))
            wake = await asyncio.wait_for(pacer.wait(), HANG_GUARD_SECONDS)
            with mock.patch.object(projector_module, "project_and_write", held):
                task = asyncio.create_task(projector._tick(NOW, projector._next_refresh(wake)))
                try:
                    await async_wait_until(entered.is_set, "projection worker entered")
                    pacer.notify_change(frozenset({ProjectionDomain.DRIFT}))
                    task.cancel()
                    await asyncio.sleep(0)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(finished.is_set())
                    self.assertGreater(pacer._next_deadline()[0], watcher_module.time.monotonic())
                    retry = await asyncio.wait_for(pacer.wait(), HANG_GUARD_SECONDS)
                    self.assertEqual(
                        retry.domains,
                        frozenset({ProjectionDomain.TASKS, ProjectionDomain.DRIFT}),
                    )
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
