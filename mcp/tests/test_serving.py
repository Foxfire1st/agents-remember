"""Tests for the dashboard serving layer (slice 04, commits 4a + 4b).

Covers the pure per-entity projection diff (``serving.delta``), the shared projector's
prime/current/subscribe fan-out (``serving.projector``), the SSE event sequence
(``serving.app.stream_events`` -- snapshot then deltas), the FastAPI app endpoints via
TestClient (``/api/state``, ``/api/actions``, static mount), the shipped static-bundle
resolver (``serving.static``), and the umbrella ``agents-remember dashboard`` CLI parsing.

The 4b additions: the raw event channel's byte-offset tail + cursor resume
(``serving.events``), sim-mode fixture load + replay clock + progressive feeder +
determinism (``serving.sim``), and the POST action skeleton's availability/attribution
mapping (``serving.actions``).
"""

import asyncio
import contextlib
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import watchfiles.main
from fastapi.testclient import TestClient
from watchfiles._rust_notify import RustNotify

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))


from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.observer.lifecycle_state import State
from agents_remember.observer.projection import (
    EnclosureNode,
    LifecycleProjection,
    ProviderNode,
    WorkspaceProjection,
)
from agents_remember.serving import change_watcher as watcher_module
from agents_remember.serving import projector as projector_module
from agents_remember.serving.app import (
    LiveProjectionInputs,
    create_app,
    stream_events,
)
from agents_remember.serving.change_watcher import ChangePacer, ProjectionInputWatcher
from agents_remember.serving.projections import landing_state as landing_module
from agents_remember.serving.projections.landing_state import LandingStateRefresher
from agents_remember.serving.projections.projection_inputs import ProjectionDomain
from agents_remember.serving.projector import (
    ProjectionCadence,
    ProjectionRefreshers,
    Projector,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)

_TS = "2026-06-14T10:00:00Z"


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


def _lifecycle(ident: str, *, tokens: int = 0, state: State = "running") -> LifecycleProjection:
    return LifecycleProjection(
        id=ident,
        state=state,
        phase="build",
        fleeting=False,
        startedAt=_TS,
        lastEventTs=_TS,
        tokens=tokens,
    )


def _projection(
    *,
    lifecycles: tuple[LifecycleProjection, ...] = (),
    providers: tuple[ProviderNode, ...] = (),
    enclosures: tuple[EnclosureNode, ...] = (),
    active_worktree_groups: tuple[str, ...] = (),
) -> WorkspaceProjection:
    """A projection of the resolved tree at ``_TS``.

    ``WorkspaceProjection`` defaults every field, so the rolled-up ``metrics``/``analytics``
    cases construct the model directly rather than routing another two knobs through here.
    """
    return WorkspaceProjection(
        generatedAt=_TS,
        lifecycles=list(lifecycles),
        providers=list(providers),
        enclosures=list(enclosures),
        activeWorktreeGroups=list(active_worktree_groups),
    )


class StreamEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_snapshot_subscription_cannot_lose_an_interleaved_projection(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        gen = stream_events(projector)

        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "snapshot")
        self.assertEqual(len(projector._subscribers), 1)
        _, initial = projector.current()
        assert initial is not None
        projector._publish_projection(
            initial.model_copy(update={"lifecycles": [_lifecycle("handoff")]})
        )

        second = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(second.event, "lifecycle")
        self.assertEqual(
            second.data, _lifecycle("handoff").model_dump(by_alias=True, exclude_none=True)
        )
        await gen.aclose()
        self.assertEqual(len(projector._subscribers), 0)


class StateEtagTests(unittest.TestCase):
    """The /api/state change gate: 200 -> ETag -> 304, real change -> new ETag."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _client_with_held_projection(
        self, held: list[WorkspaceProjection], *, interval: float = 0.02
    ) -> TestClient:
        patcher = mock.patch(
            "agents_remember.serving.projector.project_and_write",
            side_effect=lambda config, *, now, tick=None, refresh=None: held[0],
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # watch_changes=False: this world changes only through the mocked project_and_write,
        # which no filesystem watcher can observe -- the tick loop must stay interval-paced
        # (the live contract for watcher-invisible changes is the heartbeat bound instead).
        return TestClient(
            create_app(
                _config(self.tmp),
                cadence=ProjectionCadence(interval=interval),
                live_inputs=LiveProjectionInputs(change_watch=False),
            )
        )

    def _get_until(self, client: TestClient, *, etag: str, want_status: int) -> httpx.Response:
        """Poll /api/state with If-None-Match until the tick loop publishes ``want_status``."""
        deadline = time.monotonic() + 5.0
        while True:
            response = client.get("/api/state", headers={"If-None-Match": etag})
            if response.status_code == want_status or time.monotonic() > deadline:
                return response
            time.sleep(0.02)

    def test_etag_304_cycle_then_new_etag_on_content_change(self) -> None:
        held = [_projection(lifecycles=(_lifecycle("L1"),))]
        with self._client_with_held_projection(held) as client:
            first = client.get("/api/state")
            self.assertEqual(first.status_code, 200)
            etag = first.headers["etag"]
            self.assertTrue(etag.startswith('W/"'))
            self.assertEqual(first.headers["cache-control"], "no-cache")

            unchanged = client.get("/api/state", headers={"If-None-Match": etag})
            self.assertEqual(unchanged.status_code, 304)
            self.assertEqual(unchanged.headers["etag"], etag)
            self.assertEqual(unchanged.content, b"")  # zero body: the whole point

            # Volatile-only movement (ages advance every tick) must NOT mint a new revision.
            held[0] = _projection(
                lifecycles=(_lifecycle("L1").model_copy(update={"staleSeconds": 77.0}),)
            )
            time.sleep(0.1)  # several ticks at interval=0.02
            still = client.get("/api/state", headers={"If-None-Match": etag})
            self.assertEqual(still.status_code, 304)
            self.assertEqual(still.headers["etag"], etag)

            # A real content change mints a new revision: 200 with a fresh ETag + fresh body.
            held[0] = _projection(lifecycles=(_lifecycle("L1", tokens=9),))
            changed = self._get_until(client, etag=etag, want_status=200)
            self.assertEqual(changed.status_code, 200)
            self.assertNotEqual(changed.headers["etag"], etag)
            body = changed.json()
            self.assertEqual(body["lifecycles"][0]["tokens"], 9)


class BackgroundProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_watcher_observes_nested_atomic_writes_without_wsl_polling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks"
            tasks.mkdir()
            changed = asyncio.Event()
            pacer = mock.Mock(spec=ChangePacer)
            pacer.notify_change.side_effect = lambda _domains: changed.set()
            native = mock.Mock(wraps=RustNotify)
            with (
                mock.patch.dict("os.environ", {"WATCHFILES_FORCE_POLLING": "true"}),
                mock.patch.object(watchfiles.main, "RustNotify", native),
            ):
                task = asyncio.create_task(
                    ProjectionInputWatcher(_config(root))._watch_once(
                        [tasks], pacer, reconcile=False
                    )
                )
                try:
                    async with asyncio.timeout(5):
                        while not native.call_count:
                            await asyncio.sleep(0)
                    self.assertIs(native.call_args.args[2], False)
                    nested = tasks / "leaf"
                    nested.mkdir()
                    draft = nested / ".task.json.tmp"
                    draft.write_text("{}", encoding="utf-8")
                    draft.rename(nested / "task.json")
                    await asyncio.wait_for(changed.wait(), timeout=5)
                    self.assertEqual(
                        pacer.notify_change.call_args.args[0], frozenset({ProjectionDomain.TASKS})
                    )
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def test_slow_tick_rest_is_capped_and_keeps_changes_received_during_work_and_rest(
        self,
    ) -> None:
        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            pacer.notify_change(frozenset({ProjectionDomain.LIFECYCLES}))
            clock[0] = 1
            self.assertEqual((await pacer.wait()).reason, "change")
            clock[0] = 2
            pacer.notify_change(frozenset({ProjectionDomain.TASKS}))
            clock[0] = 6
            pacer.projection_completed(5)
            self.assertEqual(pacer._next_deadline(), (9, "change"))
            clock[0] = 8
            pacer.notify_change(frozenset({ProjectionDomain.WORKSPACE}))
            clock[0] = 9
            self.assertEqual(
                (await pacer.wait()).domains,
                frozenset({ProjectionDomain.TASKS, ProjectionDomain.WORKSPACE}),
            )
            for duration, rest in ((0.2, 1), (2, 2), (30, 3)):
                pacer.projection_completed(duration)
                pacer.notify_change(frozenset({ProjectionDomain.TASKS}))
                self.assertEqual(pacer._next_deadline(), (9 + rest, "change"))
            pacer.set_watcher_healthy(False)
            self.assertEqual(pacer._next_deadline(), (12, "interval"))
            clock[0] = 12
            self.assertEqual((await pacer.wait()).reason, "interval")
            pacer.set_watcher_healthy(True)
            pacer.projection_completed(0.2)
            self.assertEqual(pacer._next_deadline(), (27, "heartbeat"))

    async def test_live_tick_completion_paces_success_failure_and_startup(self) -> None:
        projector = Projector(
            _config(Path("/tmp")),
            refreshers=ProjectionRefreshers(change_watcher=mock.Mock()),
        )
        assert projector._pacer is not None
        completed = mock.Mock()
        with (
            mock.patch.object(projector._pacer, "projection_completed", completed),
            mock.patch.object(projector_module, "time", SimpleNamespace(monotonic=lambda: 10)),
            mock.patch.object(projector, "_tick_sync", return_value=_projection()),
        ):
            await projector.prime()
            self.assertEqual(projector.current()[1], _projection())
            with (
                mock.patch.object(projector, "_tick_sync", side_effect=OSError("read failed")),
                self.assertRaisesRegex(OSError, "read failed"),
            ):
                await projector._tick(datetime.now(UTC), None)
        self.assertEqual(completed.call_count, 2)
        replay = Projector(_config(Path("/tmp")))
        self.assertIsNone(replay._pacer)


class BackgroundLandingTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_not_probed_and_open_landing_finishes_once_or_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = ContractTask("task", "repo", root, "light-task", "disabled")
            live = replace(
                default_contract(
                    task,
                    leaf=LeafIdentity("live"),
                    code=RepoBranchPlan(root / "code", "main", "work", "a" * 40),
                ),
                closeout_status="completed",
            )
            history = [
                replace(
                    live,
                    contract_path=root / "history" / str(index) / "series-contract.md",
                    cleanup="completed",
                )
                for index in range(40)
            ]
            for contract in [live, *history]:
                write_contract(contract.contract_path, contract)
            observe = mock.Mock(
                return_value=[
                    {"kind": "pr", "label": "PR", "state": "merged", "factState": "observed"}
                ]
            )
            refresher = LandingStateRefresher(_config(root), observe=observe)
            paths = [contract.contract_path for contract in [live, *history[:4]]]
            with mock.patch.object(
                landing_module, "iter_leaf_enclosure_contracts", return_value=paths
            ):
                now = datetime.now(UTC) + timedelta(seconds=1)
                await refresher.refresh_once(now=now)
                observe.assert_called_once_with(live)
                paths.extend(contract.contract_path for contract in history[4:])
                observe.reset_mock()
                await refresher.refresh_once(now=now + timedelta(seconds=0.5))
                observe.assert_called_once_with(live)
                finished = replace(live, cleanup="completed")
                write_contract(finished.contract_path, finished)
                final = finished.contract_path.parent / "landing-final.json"
                final.write_text(
                    '{"frozenAt":"2000-01-01T00:00:00+00:00","facts":[]}', encoding="utf-8"
                )
                old_mtime = final.stat().st_mtime_ns
                # Populate the cache with a previous arc's final file before the finishing probe.
                refresher.current(finished, now=now)
                atomic_write = landing_module.atomic_write_text

                def replace_at_same_mtime(path: Path, text: str) -> None:
                    atomic_write(path, text)
                    os.utime(path, ns=(old_mtime, old_mtime))

                with mock.patch.object(
                    landing_module, "atomic_write_text", side_effect=replace_at_same_mtime
                ):
                    await refresher.refresh_once(now=now + timedelta(seconds=1))
                self.assertEqual(final.stat().st_mtime_ns, old_mtime)
                self.assertEqual(observe.call_count, 2)
                await refresher.refresh_once(now=now + timedelta(seconds=2))
                self.assertEqual(observe.call_count, 2)
                final_rows = refresher.current(finished, now=now)
                historical_rows = refresher.current(history[0], now=now)
                assert final_rows is not None and historical_rows is not None
                self.assertEqual(final_rows[0]["state"], "merged")
                self.assertEqual(historical_rows[0]["factState"], "missing")
                building = replace(live, cleanup="reopened", closeout_status="not-started")
                write_contract(building.contract_path, building)
                await refresher.refresh_once(now=now + timedelta(seconds=3))
                self.assertEqual(observe.call_count, 2)
                reopened = replace(building, closeout_status="completed")
                write_contract(reopened.contract_path, reopened)
                reopened_rows = refresher.current(reopened, now=now)
                assert reopened_rows is not None
                self.assertEqual(reopened_rows[0]["factState"], "missing")
                await refresher.refresh_once(now=now + timedelta(seconds=4))
                self.assertEqual(observe.call_count, 3)

    async def test_failed_finishing_probe_stays_missing_and_does_not_retry_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = replace(
                default_contract(
                    ContractTask("task", "repo", root, "light-task", "disabled"),
                    leaf=LeafIdentity("live"),
                    code=RepoBranchPlan(root / "code", "main", "work", "a" * 40),
                ),
                closeout_status="completed",
            )
            write_contract(live.contract_path, live)
            observe = mock.Mock(
                return_value=[
                    {"kind": "pr", "label": "PR", "state": "open", "factState": "observed"}
                ]
            )
            refresher = LandingStateRefresher(_config(root), observe=observe)
            with mock.patch.object(
                landing_module, "iter_leaf_enclosure_contracts", return_value=[live.contract_path]
            ):
                now = datetime.now(UTC) + timedelta(seconds=1)
                await refresher.refresh_once(now=now)
                observe.side_effect = OSError("remote unavailable")
                finished = replace(live, cleanup="completed")
                write_contract(finished.contract_path, finished)
                with self.assertLogs(landing_module.logger, level="WARNING") as logs:
                    await refresher.refresh_once(now=now + timedelta(seconds=1))
                self.assertTrue(any("no final facts recorded" in row for row in logs.output))
                self.assertFalse((live.contract_path.parent / "landing-final.json").exists())
                rows = refresher.current(finished, now=now + timedelta(seconds=2))
                assert rows is not None
                self.assertTrue(all(row["factState"] == "missing" for row in rows))
                self.assertEqual(rows[0]["detail"], "no final observation recorded")
                await refresher.refresh_once(now=now + timedelta(seconds=3))
                self.assertEqual(observe.call_count, 2)
                final = live.contract_path.parent / "landing-final.json"
                final.write_text("invalid JSON", encoding="utf-8")
                await refresher.refresh_once(now=now + timedelta(seconds=4))
                self.assertEqual(observe.call_count, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
