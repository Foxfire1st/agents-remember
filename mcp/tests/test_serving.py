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
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import MutableMapping
from datetime import (
    UTC,
    datetime,
)
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import inspect

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer import projection as projection_module
from agents_remember.observer.lifecycle_state import State
from agents_remember.observer.projection import (
    Analytics,
    EnclosureNode,
    LifecycleProjection,
    Metrics,
    ProviderNode,
    RouteCoverageNode,
    TaskDocNode,
    WorkspaceProjection,
)
from agents_remember.serving.app import (
    LiveProjectionInputs,
    ServingCollaborators,
    _if_none_match_matches,
    _ProjectionBodyCache,
    create_app,
    stream_events,
)
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.conversation.active.api import SSE_MEDIA_TYPE
from agents_remember.serving.delta import (
    VOLATILE_AGE_FIELDS,
    DeltaEvent,
    diff_projection,
    stable_projection_state,
)
from agents_remember.serving.projector import (
    ProjectionCadence,
    ProjectionRefreshers,
    ProjectionReplay,
    Projector,
)
from agents_remember.serving.terminal import TerminalHost
from pydantic import BaseModel
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES

_TS = "2026-06-14T10:00:00Z"
_FRESH_GATE_TS = "2999-01-01T10:00:00+00:00"
_FRESH_GATE_TS_LATER = "2999-01-01T10:05:00+00:00"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "sim"


def _build_wire(build: ServingBuild) -> dict[str, Any]:
    """The stamp exactly as the state body carries it.

    ``ServingBuild.payload()`` returns the declared ``ServingBuildPayload`` model; the wire
    form is that model under ``exclude_none=True``, which is where the honest-unknown rule
    (absent, never null, never a fabricated "clean") is applied -- see
    ``serving.served_state.served_state_tail``.
    """
    return build.payload().model_dump(mode="json", exclude_none=True)


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


def _provider(ident: str, *, state: str = "ready") -> ProviderNode:
    return ProviderNode(id=ident, state=state)


def _enclosure(name: str) -> EnclosureNode:
    return EnclosureNode(
        enclosure=name,
        taskId="t1",
        taskName="task",
        repoName="repo",
        lifecycleId="L1",
        worktreeGroup="grp",
        humanReviewStatus="approved",
        closeoutStatus="completed",
        integrationStatus="not-started",
        cleanup="pending",
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


class DeltaTests(unittest.TestCase):
    def test_first_tick_yields_no_deltas(self) -> None:
        self.assertEqual(diff_projection(None, _projection(lifecycles=(_lifecycle("L1"),))), [])

    def test_unchanged_yields_no_deltas(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),))
        self.assertEqual(
            diff_projection(
                prev, _projection(lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),))
            ),
            [],
        )

    def test_lifecycle_added(self) -> None:
        deltas = diff_projection(_projection(), _projection(lifecycles=(_lifecycle("L1"),)))
        self.assertEqual([(d.event, d.data) for d in deltas], [("lifecycle", _lifecycle("L1"))])

    def test_lifecycle_changed(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1", tokens=0),))
        deltas = diff_projection(prev, _projection(lifecycles=(_lifecycle("L1", tokens=9),)))
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].event, "lifecycle")
        self.assertEqual(deltas[0].data, _lifecycle("L1", tokens=9))

    def test_lifecycle_removed(self) -> None:
        deltas = diff_projection(_projection(lifecycles=(_lifecycle("L1"),)), _projection())
        self.assertEqual(deltas, [DeltaEvent("lifecycle.removed", {"id": "L1"})])

    def test_active_worktree_groups_changed_emits_whole_value_delta(self) -> None:
        prev = _projection(active_worktree_groups=("a-ar",))
        deltas = diff_projection(prev, _projection(active_worktree_groups=("a-ar", "b-ar")))
        self.assertEqual(
            deltas, [DeltaEvent("activeWorktreeGroups", {"activeWorktreeGroups": ["a-ar", "b-ar"]})]
        )

    def test_active_worktree_groups_unchanged_yields_no_delta(self) -> None:
        prev = _projection(active_worktree_groups=("a-ar",))
        self.assertEqual(diff_projection(prev, _projection(active_worktree_groups=("a-ar",))), [])

    def test_provider_added_and_removed(self) -> None:
        prev = _projection(providers=(_provider("a"),))
        cur = _projection(providers=(_provider("b"),))
        deltas = diff_projection(prev, cur)
        self.assertIn(DeltaEvent("provider", _provider("b")), deltas)
        self.assertIn(DeltaEvent("provider.removed", {"id": "a"}), deltas)

    def test_enclosure_removed(self) -> None:
        deltas = diff_projection(_projection(enclosures=(_enclosure("e1"),)), _projection())
        self.assertEqual(deltas, [DeltaEvent("enclosure.removed", {"enclosure": "e1"})])

    def test_metrics_changed(self) -> None:
        deltas = diff_projection(
            _projection(), WorkspaceProjection(generatedAt=_TS, metrics=Metrics(lifecycleCount=1))
        )
        self.assertEqual(deltas, [DeltaEvent("metrics", Metrics(lifecycleCount=1))])

    def test_analytics_changed(self) -> None:
        cur_analytics = Analytics(routeCoverage=[RouteCoverageNode(route="r")])
        deltas = diff_projection(
            _projection(), WorkspaceProjection(generatedAt=_TS, analytics=cur_analytics)
        )
        self.assertEqual(deltas, [DeltaEvent("analytics", cur_analytics)])

    def test_removals_are_sorted_for_determinism(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L2"), _lifecycle("L1"), _lifecycle("L3")))
        deltas = diff_projection(prev, _projection())
        self.assertEqual(
            [d.data for d in deltas],
            [{"id": "L1"}, {"id": "L2"}, {"id": "L3"}],
        )

    # ── The change gate: volatile now-relative ages never emit ──────────

    def test_volatile_only_lifecycle_change_yields_no_deltas(self) -> None:
        # staleSeconds is recomputed from the tick clock every projection; without the
        # stable-form compare it re-emitted every node every tick (~780 KB/s measured live).
        prev = _projection(lifecycles=(_lifecycle("L1"),))
        aged = _lifecycle("L1").model_copy(update={"staleSeconds": 99.5})
        self.assertEqual(diff_projection(prev, _projection(lifecycles=(aged,))), [])

    def test_volatile_only_analytics_change_yields_no_deltas(self) -> None:
        def doc(age: float) -> TaskDocNode:
            return TaskDocNode(
                id="t",
                repository="r",
                title="T",
                status="planning",
                kind="light",
                docPath="/t.json",
                ageSeconds=age,
            )

        prev = WorkspaceProjection(generatedAt=_TS, analytics=Analytics(taskDocuments=[doc(1.0)]))
        cur = WorkspaceProjection(generatedAt=_TS, analytics=Analytics(taskDocuments=[doc(2.0)]))
        self.assertEqual(diff_projection(prev, cur), [])

    def test_real_change_emits_with_fresh_ages_riding_along(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1"),))
        changed = _lifecycle("L1", tokens=9).model_copy(update={"staleSeconds": 42.0})
        deltas = diff_projection(prev, _projection(lifecycles=(changed,)))
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].event, "lifecycle")
        self.assertEqual(deltas[0].data, changed)  # the emitted node carries the fresh age

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_serving.py:260).
    def test_every_seconds_field_is_classified_volatile_or_content(
        self,
    ) -> None:  # pragma: no cover
        """The server<->client volatile-field sets are a
        two-sided lockstep guarded only by literal pins. This reflection guard catches the
        server side of the drift: every ``*Seconds`` field on every projection model must be
        EITHER in VOLATILE_AGE_FIELDS (now-relative, stripped from stable forms) OR in the
        curated content allow-list below (static durations, never now-relative). A new
        now-relative field added to the models without joining VOLATILE_AGE_FIELDS would
        silently re-degrade the SSE diff -- this test makes that addition loud."""
        content_seconds_allow_list = {"ttlSeconds"}  # static constant (AGENT_PICKUP_TTL_SECONDS)
        unclassified: set[str] = set()
        for _, model in inspect.getmembers(projection_module, inspect.isclass):
            if not issubclass(model, BaseModel):
                continue
            for field_name in getattr(model, "model_fields", {}):
                if not field_name.endswith("Seconds"):
                    continue
                if field_name in VOLATILE_AGE_FIELDS or field_name in content_seconds_allow_list:
                    continue
                unclassified.add(field_name)
        self.assertEqual(
            unclassified,
            set(),
            "unclassified *Seconds projection fields -- add each to VOLATILE_AGE_FIELDS "
            "(and the client mirror in dashboard/src/data/servedAges.ts) if now-relative, "
            "or to this test's content allow-list if a static duration: "
            f"{sorted(unclassified)}",
        )

    def test_precomputed_stable_states_match_the_pure_form(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),))
        cur = _projection(lifecycles=(_lifecycle("L1", tokens=3),))
        pure = diff_projection(prev, cur)
        cached = diff_projection(
            prev,
            cur,
            previous_state=stable_projection_state(prev),
            current_state=stable_projection_state(cur),
        )
        self.assertEqual(pure, cached)


class ProjectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_prime_sets_latest(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        seq, latest = projector.current()
        self.assertEqual(seq, 0)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.version, 2)

    async def test_subscribe_receives_broadcast(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        agen = projector.subscribe()
        pending = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.02)  # let subscribe() register its queue
        projector._broadcast((7, DeltaEvent("lifecycle", _lifecycle("L1"))))
        seq, delta = await asyncio.wait_for(pending, timeout=1)
        self.assertEqual(seq, 7)
        self.assertEqual(delta.event, "lifecycle")
        await agen.aclose()

    async def test_prime_runs_provider_refresher_before_projection(self) -> None:
        moment = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
        calls: list[datetime] = []

        class Refresher:
            def maybe_refresh(self, config: McpRuntimeConfig, *, now: datetime) -> None:
                _ = config
                calls.append(now)

        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100),
            replay=ProjectionReplay(now=lambda: moment),
            refreshers=ProjectionRefreshers(provider=Refresher()),
        )
        await projector.prime()
        self.assertEqual(calls, [moment])

    async def test_run_owns_landing_refresher_lifecycle(self) -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()

        class Refresher:
            # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_serving.py:352).
            def current(self, contract, *, now):  # type: ignore[no-untyped-def]  # pragma: no cover
                _ = contract, now

            async def run(self) -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100),
            refreshers=ProjectionRefreshers(landing=Refresher()),
        )
        task = asyncio.create_task(projector.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())


class StreamEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_snapshot_then_delta(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        gen = stream_events(projector)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "snapshot")

        pending = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.02)
        projector._broadcast((1, DeltaEvent("lifecycle", _lifecycle("L1"))))
        second = await asyncio.wait_for(pending, timeout=1)
        self.assertEqual(second.event, "lifecycle")
        await gen.aclose()

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

    async def test_failed_prime_recovery_emits_one_snapshot_then_normal_deltas(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with mock.patch.object(projector, "_tick_sync", side_effect=RuntimeError("forced failure")):
            await projector.prime()
        self.assertIsNone(projector.current()[1])

        build = ServingBuild(version="9.9.9", commit="abc1234", booted_at="2026-07-18T08:00:00Z")
        gen = stream_events(projector, build=build)
        pending = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)
        self.assertEqual(len(projector._subscribers), 1)

        recovered = _projection(lifecycles=(_lifecycle("recovered"),))
        projector._publish_projection(recovered)
        first = await asyncio.wait_for(pending, timeout=1)
        self.assertEqual(first.event, "snapshot")
        self.assertEqual(first.id, "1")
        assert isinstance(first.data, dict)
        self.assertEqual(first.data["servingBuild"], _build_wire(build))
        self.assertEqual(first.data["lifecycles"][0]["id"], "recovered")

        projector._publish_projection(recovered)
        projector._publish_projection(_projection(lifecycles=(_lifecycle("recovered", tokens=9),)))
        second = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(second.event, "lifecycle")
        self.assertEqual(second.id, "2")
        self.assertEqual(second.data["tokens"], 9)
        await gen.aclose()
        self.assertEqual(len(projector._subscribers), 0)

    async def test_cancelled_waiting_stream_releases_its_subscription(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        gen = stream_events(projector)
        pending = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)
        self.assertEqual(len(projector._subscribers), 1)

        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(len(projector._subscribers), 0)

    async def test_snapshot_carries_the_serving_build_stamp(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        build = ServingBuild(version="9.9.9", commit="abc1234", booted_at="2026-07-07T05:00:00Z")
        gen = stream_events(projector, build=build)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "snapshot")
        assert isinstance(first.data, dict)
        self.assertEqual(
            first.data["servingBuild"],
            {"version": "9.9.9", "bootedAt": "2026-07-07T05:00:00Z", "commit": "abc1234"},
        )
        await gen.aclose()


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_state_endpoint_serves_projection(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["lifecycles"], [])

    def test_root_serves_dashboard_bundle(self) -> None:
        # Rewritten: this used to read the committed bundle straight out of the repository.
        # The bundle is a release-built artifact that ships only inside the wheel, so the
        # test supplies its own stand-in and asserts the serving contract instead -- the SPA
        # mount point plus the revalidated-HTML cache header, both stable across rebuilds.
        bundle = self.tmp / "bundle"
        (bundle / "assets").mkdir(parents=True)
        (bundle / "index.html").write_text(
            '<title>Agents Remember</title><div id="root"></div>', encoding="utf-8"
        )
        with mock.patch("agents_remember.serving.static.dashboard_static_dir", return_value=bundle):
            app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<div id="root">', response.text)
        self.assertIn("Agents Remember", response.text)
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_root_diagnoses_a_missing_bundle_instead_of_a_bare_404(self) -> None:
        # A source checkout that never ran a frontend build now legitimately has no bundle.
        # The server must still boot, the API must still own /api ahead of the greedy mount,
        # and the static surface must name the remedy rather than 404 into silence.
        with mock.patch("agents_remember.serving.static.dashboard_static_dir", return_value=None):
            app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            root = client.get("/")
            api = client.get("/api/state")
        self.assertEqual(root.status_code, 503)  # unavailable, not "not found"
        self.assertIn("no built cockpit bundle", root.text)
        self.assertIn("npm --prefix dashboard run build", root.text)  # carries its own remedy
        self.assertEqual(root.headers["cache-control"], "no-store")
        self.assertEqual(api.status_code, 200)  # the API is unaffected by the missing bundle

    def test_terminal_host_shutdown_survives_dead_landing_refresher(self) -> None:
        failed = threading.Event()

        async def failed_run(_refresher) -> None:  # type: ignore[no-untyped-def]
            failed.set()
            raise RuntimeError("refresher died")

        host = TerminalHost()
        with (
            mock.patch(
                "agents_remember.serving.app.LandingStateRefresher.run",
                new=failed_run,
            ),
            mock.patch.object(host, "shutdown", wraps=host.shutdown) as shutdown,
            self.assertLogs("agents_remember.serving.projector", level="ERROR"),
        ):
            app = create_app(
                _config(self.tmp),
                cadence=ProjectionCadence(interval=100),
                collaborators=ServingCollaborators(terminal_host=host),
            )
            with TestClient(app):
                self.assertTrue(failed.wait(timeout=1))
        shutdown.assert_called_once_with()


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

    def test_state_body_carries_the_serving_build_stamp(self) -> None:
        held = [_projection()]
        with self._client_with_held_projection(held) as client:
            body = client.get("/api/state").json()
        self.assertIn("servingBuild", body)
        self.assertIn("version", body["servingBuild"])
        self.assertIn("bootedAt", body["servingBuild"])

    def test_if_none_match_comparison_is_weak_and_list_aware(self) -> None:
        self.assertTrue(_if_none_match_matches('W/"abc-3"', "abc-3"))
        self.assertTrue(_if_none_match_matches('"abc-3"', "abc-3"))
        self.assertTrue(_if_none_match_matches('W/"x-1", W/"abc-3"', "abc-3"))
        self.assertTrue(_if_none_match_matches("*", "abc-3"))
        self.assertFalse(_if_none_match_matches('W/"abc-2"', "abc-3"))
        self.assertFalse(_if_none_match_matches(None, "abc-3"))


class ProjectionBodyCacheTests(unittest.TestCase):
    """/api/state reuses the per-instance dump; ETag/304 gate unchanged."""

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
        # Same posture as StateEtagTests: the world changes only through the mocked
        # project_and_write, so the tick loop stays interval-paced.
        return TestClient(
            create_app(
                _config(self.tmp),
                cadence=ProjectionCadence(interval=interval),
                live_inputs=LiveProjectionInputs(change_watch=False),
            )
        )

    def test_cache_keys_on_instance_identity(self) -> None:
        cache = _ProjectionBodyCache()
        first = _projection()
        body = cache.body(first)
        self.assertIs(cache.body(first), body)  # same instance: memo hit, shared read-only dict
        refreshed = _projection(lifecycles=(_lifecycle("L2"),))
        new_body = cache.body(refreshed)  # a new instance refreshes the memo
        self.assertIsNot(new_body, body)
        self.assertEqual(new_body["lifecycles"][0]["id"], "L2")

    @staticmethod
    def _memoized_body(response: httpx.Response) -> dict[str, object]:
        """The served body minus ``supervisorHeartbeat`` (deliberately volatile, never cached)."""
        body = response.json()
        body.pop("supervisorHeartbeat", None)
        return body

    def test_repeat_polls_share_one_dump_per_published_instance(self) -> None:
        # interval=100: prime publishes once and the loop never ticks again during the test,
        # so the published instance (the memo key) is stable across these requests. Freshness
        # after a real content change is covered by StateEtagTests (a tick swaps the instance,
        # which refreshes the memo) and by StreamSnapshotCacheTests below.
        held = [_projection(lifecycles=(_lifecycle("L1"),))]
        with (
            # autospec+side_effect: count calls while calling the real dump through.
            mock.patch.object(
                WorkspaceProjection,
                "model_dump",
                autospec=True,
                side_effect=WorkspaceProjection.model_dump,
            ) as dump_spy,
            self._client_with_held_projection(held, interval=100) as client,
        ):
            first = client.get("/api/state")
            self.assertEqual(first.status_code, 200)
            etag = first.headers["etag"]
            self.assertEqual(dump_spy.call_count, 1)

            second = client.get("/api/state")
            self.assertEqual(second.status_code, 200)
            # Same memoized payload; only the volatile heartbeat tail may move.
            self.assertEqual(self._memoized_body(second), self._memoized_body(first))
            self.assertEqual(dump_spy.call_count, 1)  # memo hit: no re-dump

            cached = client.get("/api/state", headers={"If-None-Match": etag})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(dump_spy.call_count, 1)  # the 304 path never touches the body


class StreamSnapshotCacheTests(unittest.IsolatedAsyncioTestCase):
    """N SSE subscribers share the one per-instance snapshot dump."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_subscribers_share_one_snapshot_dump_until_content_changes(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        with mock.patch.object(
            WorkspaceProjection,
            "model_dump",
            autospec=True,
            side_effect=WorkspaceProjection.model_dump,
        ) as dump_spy:
            gen1 = stream_events(projector)
            first = await asyncio.wait_for(gen1.__anext__(), timeout=1)
            self.assertEqual(first.event, "snapshot")
            gen2 = stream_events(projector)
            second = await asyncio.wait_for(gen2.__anext__(), timeout=1)
            self.assertEqual(second.event, "snapshot")
            self.assertEqual(dump_spy.call_count, 1)  # one dump served both subscribers
            self.assertEqual(first.data, second.data)

            # The per-connect volatile injection rides a copy: mutating one subscriber's
            # payload never pollutes what the next subscriber is served.
            assert isinstance(first.data, dict)
            first.data["servingBuild"] = {"version": "injected"}
            gen3 = stream_events(projector)
            third = await asyncio.wait_for(gen3.__anext__(), timeout=1)
            self.assertNotIn("servingBuild", third.data)
            self.assertEqual(dump_spy.call_count, 1)

            # A content change mints a new revision -> the next snapshot re-dumps once.
            _, current = projector.current()
            assert current is not None
            projector._publish_projection(
                current.model_copy(update={"lifecycles": [_lifecycle("L2")]})
            )
            gen4 = stream_events(projector)
            fourth = await asyncio.wait_for(gen4.__anext__(), timeout=1)
            self.assertEqual(fourth.event, "snapshot")
            self.assertEqual(fourth.data["lifecycles"][0]["id"], "L2")
            self.assertEqual(dump_spy.call_count, 2)
        for gen in (gen1, gen2, gen3, gen4):
            await gen.aclose()
        self.assertEqual(len(projector._subscribers), 0)


class GzipMiddlewareTests(unittest.TestCase):
    """Gzip for big JSON GETs; SSE stays uncompressed and unbuffered."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _client_with_held_projection(self, held: list[WorkspaceProjection]) -> TestClient:
        patcher = mock.patch(
            "agents_remember.serving.projector.project_and_write",
            side_effect=lambda config, *, now, tick=None, refresh=None: held[0],
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(
            create_app(
                _config(self.tmp),
                cadence=ProjectionCadence(interval=0.02),
                live_inputs=LiveProjectionInputs(change_watch=False),
            )
        )

    def test_large_json_get_is_gzipped_for_gzip_clients(self) -> None:
        held = [_projection(lifecycles=tuple(_lifecycle(f"L{i}") for i in range(50)))]
        with self._client_with_held_projection(held) as client:
            identity = client.get("/api/state", headers={"Accept-Encoding": "identity"})
            self.assertEqual(identity.status_code, 200)
            self.assertNotIn("content-encoding", identity.headers)

            zipped = client.get("/api/state", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(zipped.status_code, 200)
            self.assertEqual(zipped.headers["content-encoding"], "gzip")
            # The wire body really shrank (httpx transparently decodes for .json()).
            self.assertLess(int(zipped.headers["content-length"]), len(identity.content))
            zipped_body, identity_body = zipped.json(), identity.json()
            # supervisorHeartbeat is deliberately volatile (computed at response time).
            zipped_body.pop("supervisorHeartbeat", None)
            identity_body.pop("supervisorHeartbeat", None)
            self.assertEqual(zipped_body, identity_body)

    def test_conversation_sse_media_type_is_gzip_excluded(self) -> None:
        # The conversation event streams ride plain StreamingResponse with the same media
        # type; pin that starlette's gzip exclusion keeps covering them. The live-flow proof
        # for the /api/stream channel is GzipSseFlowTests below (raw-ASGI: this starlette's
        # TestClient awaits app completion, so an infinite SSE stream cannot go through it).
        self.assertTrue(
            any(SSE_MEDIA_TYPE.startswith(excluded) for excluded in DEFAULT_EXCLUDED_CONTENT_TYPES)
        )


class GzipSseFlowTests(unittest.IsolatedAsyncioTestCase):
    """Through the real middleware stack, /api/stream flows uncompressed."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_snapshot_frame_flows_uncompressed_before_the_stream_ends(self) -> None:
        held = [_projection(lifecycles=(_lifecycle("L1"),))]
        patcher = mock.patch(
            "agents_remember.serving.projector.project_and_write",
            side_effect=lambda config, *, now, tick=None, refresh=None: held[0],
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        app = create_app(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100),
            live_inputs=LiveProjectionInputs(change_watch=False),
        )
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/stream",
            "raw_path": b"/api/stream",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"accept-encoding", b"gzip")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
        messages: list[MutableMapping[str, Any]] = []
        started = asyncio.Event()
        first_frame = asyncio.Event()

        requested = False

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_serving.py:867).
        async def receive() -> dict[str, Any]:  # pragma: no cover
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()  # no further client input; ends with the task
            raise AssertionError("unreachable")

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_serving.py:875).
        async def send(message: MutableMapping[str, Any]) -> None:  # pragma: no cover
            messages.append(message)
            if message["type"] == "http.response.start":
                started.set()
            elif message["type"] == "http.response.body" and b"\n\n" in message.get("body", b""):
                first_frame.set()  # a COMPLETE terminated frame while the stream is open

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(scope, receive, send))
            try:
                # If GZipMiddleware buffered the stream into a compression window these
                # waits would expire: no bytes could arrive before the connection ends.
                await asyncio.wait_for(started.wait(), timeout=2)
                await asyncio.wait_for(first_frame.wait(), timeout=2)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        start = next(m for m in messages if m["type"] == "http.response.start")
        headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        self.assertEqual(start["status"], 200)
        self.assertTrue(headers["content-type"].startswith("text/event-stream"))
        self.assertNotIn("content-encoding", headers)  # SSE is gzip-excluded
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        self.assertIn(b"event: snapshot", body)  # plain SSE text, not a gzip window


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
