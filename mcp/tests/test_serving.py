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

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import inspect

from agents_remember.cli import __main__ as cli_main
from agents_remember.cli import dashboard as cli_dashboard
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import create_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.mcp.config import ConfigError, McpRuntimeConfig
from agents_remember.observer import projection as projection_module
from agents_remember.observer.event_retention import (
    initial_event_offsets,
    prune_expired_lifecycle_event_logs,
)
from agents_remember.observer.lifecycle_state import State
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.projection import (
    ActionAvailability,
    Analytics,
    EnclosureNode,
    LifecycleProjection,
    Metrics,
    ProviderNode,
    RouteCoverageNode,
    TaskDocNode,
    WorkspaceProjection,
)
from agents_remember.observer.projection_store import project_and_write
from agents_remember.serving.actions import (
    DismissalIntent,
    GateDecisionIntent,
    evaluate_action,
)
from agents_remember.serving.app import _if_none_match_matches, create_app, stream_events
from agents_remember.serving.build_info import ServingBuild, resolve_serving_build
from agents_remember.serving.delta import (
    VOLATILE_AGE_FIELDS,
    DeltaEvent,
    diff_projection,
    stable_projection_state,
)
from agents_remember.serving.events import (
    decode_cursor,
    encode_cursor,
    read_new_events,
    stream_raw_events,
)
from agents_remember.serving.projector import Projector
from agents_remember.serving.sim import (
    ReplayClock,
    SimError,
    build_sim,
    load_fixture,
    parse_sim_speed,
)
from agents_remember.serving.static import dashboard_static_dir
from agents_remember.serving.terminal import TerminalHost
from pydantic import BaseModel

_TS = "2026-06-14T10:00:00Z"
_FRESH_GATE_TS = "2999-01-01T10:00:00+00:00"
_FRESH_GATE_TS_LATER = "2999-01-01T10:05:00+00:00"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "sim"


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
    metrics: Metrics | None = None,
    analytics: Analytics | None = None,
) -> WorkspaceProjection:
    return WorkspaceProjection(
        generatedAt=_TS,
        lifecycles=list(lifecycles),
        providers=list(providers),
        enclosures=list(enclosures),
        activeWorktreeGroups=list(active_worktree_groups),
        metrics=metrics or Metrics(),
        analytics=analytics or Analytics(),
    )


class DeltaTests(unittest.TestCase):
    def test_first_tick_yields_no_deltas(self) -> None:
        self.assertEqual(diff_projection(None, _projection(lifecycles=(_lifecycle("L1"),))), [])

    def test_unchanged_yields_no_deltas(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),))
        self.assertEqual(diff_projection(prev, _projection(
            lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),)
        )), [])

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
        deltas = diff_projection(_projection(), _projection(metrics=Metrics(lifecycleCount=1)))
        self.assertEqual(deltas, [DeltaEvent("metrics", Metrics(lifecycleCount=1))])

    def test_analytics_changed(self) -> None:
        cur_analytics = Analytics(routeCoverage=[RouteCoverageNode(route="r")])
        deltas = diff_projection(_projection(), _projection(analytics=cur_analytics))
        self.assertEqual(deltas, [DeltaEvent("analytics", cur_analytics)])

    def test_removals_are_sorted_for_determinism(self) -> None:
        prev = _projection(
            lifecycles=(_lifecycle("L2"), _lifecycle("L1"), _lifecycle("L3"))
        )
        deltas = diff_projection(prev, _projection())
        self.assertEqual(
            [d.data for d in deltas],
            [{"id": "L1"}, {"id": "L2"}, {"id": "L3"}],
        )

    # ── The change gate (260703-L15): volatile now-relative ages never emit ──────────

    def test_volatile_only_lifecycle_change_yields_no_deltas(self) -> None:
        # staleSeconds is recomputed from the tick clock every projection; without the
        # stable-form compare it re-emitted every node every tick (~780 KB/s measured live).
        prev = _projection(lifecycles=(_lifecycle("L1"),))
        aged = _lifecycle("L1").model_copy(update={"staleSeconds": 99.5})
        self.assertEqual(diff_projection(prev, _projection(lifecycles=(aged,))), [])

    def test_volatile_only_analytics_change_yields_no_deltas(self) -> None:
        def doc(age: float) -> TaskDocNode:
            return TaskDocNode(
                id="t", repository="r", title="T", status="planning", kind="light",
                docPath="/t.json", ageSeconds=age,
            )

        prev = _projection(analytics=Analytics(taskDocuments=[doc(1.0)]))
        cur = _projection(analytics=Analytics(taskDocuments=[doc(2.0)]))
        self.assertEqual(diff_projection(prev, cur), [])

    def test_real_change_emits_with_fresh_ages_riding_along(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1"),))
        changed = _lifecycle("L1", tokens=9).model_copy(update={"staleSeconds": 42.0})
        deltas = diff_projection(prev, _projection(lifecycles=(changed,)))
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].event, "lifecycle")
        self.assertEqual(deltas[0].data, changed)  # the emitted node carries the fresh age

    def test_every_seconds_field_is_classified_volatile_or_content(self) -> None:
        """L15 review follow-up (L15R-1): the server<->client volatile-field sets are a
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
        projector = Projector(_config(self.tmp), interval=100)
        await projector.prime()
        seq, latest = projector.current()
        self.assertEqual(seq, 0)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.version, 2)

    async def test_subscribe_receives_broadcast(self) -> None:
        projector = Projector(_config(self.tmp), interval=100)
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
            interval=100,
            now=lambda: moment,
            provider_refresher=Refresher(),
        )
        await projector.prime()
        self.assertEqual(calls, [moment])

    async def test_run_owns_landing_refresher_lifecycle(self) -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()

        class Refresher:
            def current(self, contract, *, now):  # type: ignore[no-untyped-def]
                _ = contract, now

            async def run(self) -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        projector = Projector(
            _config(self.tmp),
            interval=100,
            landing_refresher=Refresher(),
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
        projector = Projector(_config(self.tmp), interval=100)
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

    async def test_snapshot_carries_the_serving_build_stamp(self) -> None:
        projector = Projector(_config(self.tmp), interval=100)
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
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["lifecycles"], [])

    def test_root_serves_dashboard_bundle(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        # Slice 05 ships the built React bundle (the slice-04 placeholder is gone). The SPA
        # mount point and the app title are stable across rebuilds; hashed asset names are not.
        self.assertIn('<div id="root">', response.text)
        self.assertIn("Agents Remember", response.text)
        self.assertEqual(response.headers["cache-control"], "no-cache")

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
            app = create_app(_config(self.tmp), interval=100, terminal_host=host)
            with TestClient(app):
                self.assertTrue(failed.wait(timeout=1))
        shutdown.assert_called_once_with()


class StateEtagTests(unittest.TestCase):
    """The /api/state change gate (260703-L15): 200 -> ETag -> 304, real change -> new ETag."""

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
            side_effect=lambda config, *, now, provider_refresher=None, landing_state=None: held[0],
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # watch_changes=False: this world changes only through the mocked project_and_write,
        # which no filesystem watcher can observe -- the tick loop must stay interval-paced
        # (the live contract for watcher-invisible changes is the heartbeat bound instead).
        return TestClient(create_app(_config(self.tmp), interval=interval, watch_changes=False))

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


class BuildInfoTests(unittest.TestCase):
    def test_resolves_commit_in_a_git_checkout(self) -> None:
        build = resolve_serving_build()
        self.assertTrue(build.version)
        self.assertTrue(build.booted_at)
        # The test tree lives in a checkout, so the short hash resolves and rides the payload.
        self.assertIsNotNone(build.commit)
        payload = build.payload()
        self.assertEqual(payload["commit"], build.commit)
        self.assertEqual(payload["bootedAt"], build.booted_at)
        self.assertEqual(payload["dashboardBuild"], build.dashboard_build)

    def test_off_checkout_serves_version_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build = resolve_serving_build(anchor=Path(tmp))
        self.assertIsNone(build.commit)
        self.assertNotIn("commit", build.payload())  # never a faked hash

    def test_payload_shape_is_camel_case(self) -> None:
        build = ServingBuild(
            version="9.9.9",
            commit="abc1234",
            booted_at="2026-07-07T05:00:00Z",
            dashboard_build="dashboard-123",
        )
        self.assertEqual(
            build.payload(),
            {
                "version": "9.9.9",
                "bootedAt": "2026-07-07T05:00:00Z",
                "commit": "abc1234",
                "dashboardBuild": "dashboard-123",
            },
        )


class ActionGateTests(unittest.TestCase):
    """Slice 6b: gate-decision verbs carry an intent (pure) and the router records it."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_evaluate_action_emits_gate_intent_for_verbs(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "approve",
            "L1",
            actor="developer",
            now=_TS,
            gate_id="G1",
            note="Looks good.",
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.gate_decision,
            GateDecisionIntent(
                lifecycle_id="L1",
                decision="approve",
                gate_id="G1",
                note="Looks good.",
            ),
        )

    def test_evaluate_action_requires_rejection_reason(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "reject",
            "L1",
            actor="developer",
            now=_TS,
            gate_id="G1",
        )
        self.assertEqual(outcome.status_code, 400)
        self.assertEqual(outcome.body["status"], "missing-rejection-reason")
        self.assertIsNone(outcome.gate_decision)

    def test_evaluate_action_allows_gate_id_only_cancel(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "cancel",
            None,
            actor="developer",
            now=_TS,
            gate_id="G1",
            note="Cleared from attention queue.",
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.gate_decision,
            GateDecisionIntent(
                lifecycle_id=None,
                decision="cancel",
                gate_id="G1",
                note="Cleared from attention queue.",
            ),
        )

    def test_evaluate_action_transition_keeps_4b_skeleton(self) -> None:
        # a non-gate action on an unknown target stays the 4b no-mutation skeleton
        outcome = evaluate_action(_projection(), "integrate", "nope", actor="developer", now=_TS)
        self.assertIsNone(outcome.gate_decision)
        self.assertEqual(outcome.status_code, 404)

    def test_api_action_approve_records_developer_decision(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="G1", now=_FRESH_GATE_TS)
        )
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/reject",
                json={"target": "L1", "gateId": "G1", "note": "Needs another pass."},
            )
        self.assertEqual(response.status_code, 202)
        gate = response.json()["gate"]
        self.assertEqual(gate["state"], "rejected")
        self.assertEqual(gate["decidedBy"], "developer")  # un-forgeable vs. the agent's model path
        self.assertEqual(gate["decidedVia"], "dashboard")
        self.assertEqual(store.current("L1")["G1"].state, "rejected")
        self.assertEqual(store.current("L1")["G1"].decisionNote, "Needs another pass.")

    def test_api_action_with_stale_gate_id_is_409(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(kind="agent-question", lifecycle_id="L1", gate_id="A", now=_FRESH_GATE_TS)
        )
        store.append(
            create_gate(
                kind="closeout-approval",
                lifecycle_id="L1",
                gate_id="B",
                now=_FRESH_GATE_TS_LATER,
            )
        )
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post("/api/actions/approve", json={"target": "L1", "gateId": "A"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "stale-gate")
        self.assertEqual(store.current("L1")["A"].state, "open")
        self.assertEqual(store.current("L1")["B"].state, "open")

    def test_api_action_approve_without_open_gate_is_409(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post("/api/actions/approve", json={"target": "L1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "no-open-gate")

    def test_api_action_cancel_deletes_gate(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(kind="agent-question", lifecycle_id="L1", gate_id="G1", now=_FRESH_GATE_TS)
        )
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/cancel",
                json={"target": "L1", "gateId": "G1", "note": "Dismissed."},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["gate"]["state"], "cancelled")
        self.assertEqual(store.current("L1"), {})

    def test_api_action_cancel_deletes_workspace_gate_by_id_only(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(kind="agent-question", lifecycle_id=None, gate_id="G1", now=_FRESH_GATE_TS)
        )
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/cancel",
                json={"gateId": "G1", "note": "Cleared from attention queue."},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["gate"]["state"], "cancelled")
        self.assertEqual(store.current(None), {})

    def test_api_operator_inbox_records_developer_response(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/operator-inbox",
                json={
                    "lifecycleId": "L1",
                    "agentId": "agent-a",
                    "gateId": "G1",
                    "ask": "Continue?",
                    "response": "Yes, proceed.",
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["operation"], "operator_inbox_post")
        self.assertEqual(body["state"], "pending")

        store = OperatorInboxStore(observer_logs_root(self.tmp))
        entries = store.list_pending(lifecycle_id="L1", agent_id="agent-a")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].gateId, "G1")
        self.assertEqual(entries[0].ask, "Continue?")
        self.assertEqual(entries[0].response, "Yes, proceed.")
        self.assertEqual(entries[0].createdBy, "developer")
        self.assertEqual(entries[0].createdVia, "dashboard")

    def test_api_operator_inbox_dismiss_deletes_entry(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            posted = client.post(
                "/api/operator-inbox",
                json={
                    "lifecycleId": "L1",
                    "gateId": "G1",
                    "ask": "Continue?",
                    "response": "Yes, proceed.",
                },
            )
            entry_id = posted.json()["entryId"]
            response = client.post(f"/api/operator-inbox/{entry_id}/dismiss")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "dismissed")
        self.assertEqual(OperatorInboxStore(observer_logs_root(self.tmp)).read(), [])

    def test_api_operator_inbox_requires_address(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/operator-inbox",
                json={"ask": "Continue?", "response": "Yes, proceed."},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-address")


class ActionDismissTests(unittest.TestCase):
    """Leaf-28 S5.2: the ``dismiss`` verb records compact lifecycle acknowledgements,
    and pairs a ``gate-open`` dismissal with gate deletion."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_evaluate_action_emits_dismissal_intent(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            "L1",
            actor="developer",
            now=_TS,
            item_id="awaiting-developer:L1",
            kind="awaiting-developer",
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertIsNone(outcome.gate_decision)
        self.assertEqual(
            outcome.dismissal,
            DismissalIntent(
                item_id="awaiting-developer:L1",
                dismissed_at=_TS,
                kind="awaiting-developer",
                lifecycle_id="L1",
                gate_id=None,
                note=None,
            ),
        )

    def test_evaluate_action_dismiss_requires_item_id(self) -> None:
        outcome = evaluate_action(
            _projection(), "dismiss", "L1", actor="developer", now=_TS, kind="blocked-gate"
        )
        self.assertEqual(outcome.status_code, 400)
        self.assertEqual(outcome.body["status"], "missing-item")
        self.assertIsNone(outcome.dismissal)

    def test_evaluate_action_dismiss_requires_lifecycle_for_non_gate_item(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            None,
            actor="developer",
            now=_TS,
            item_id="provider-down:cgc",
            kind="provider-down",
        )
        self.assertEqual(outcome.status_code, 400)
        self.assertEqual(outcome.body["status"], "missing-lifecycle")
        self.assertIsNone(outcome.dismissal)

    def test_evaluate_action_allows_actionable_drift_without_lifecycle(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            None,
            actor="developer",
            now=_TS,
            item_id="actionable-drift:agents-remember:main",
            kind="actionable-drift",
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.dismissal,
            DismissalIntent(
                item_id="actionable-drift:agents-remember:main",
                dismissed_at=_TS,
                kind="actionable-drift",
                lifecycle_id=None,
                gate_id=None,
                note=None,
            ),
        )

    def test_attention_store_upserts_and_prunes_lifecycle_rows(self) -> None:
        store = AttentionDismissalStore(observer_logs_root(self.tmp))
        store.dismiss(
            AttentionDismissalRecord(
                itemId="stale-session:L1",
                kind="stale-session",
                lifecycleId="L1",
                dismissedAt=_TS,
            )
        )
        store.dismiss(
            AttentionDismissalRecord(
                itemId="stale-session:L1",
                kind="stale-session",
                lifecycleId="L1",
                dismissedAt="2026-06-14T10:01:00Z",
            )
        )
        self.assertEqual(len(store.read()), 1)
        self.assertEqual(store.current()["stale-session:L1"].dismissedAt, "2026-06-14T10:01:00Z")

        self.assertEqual(store.prune_lifecycles(set()), 1)
        self.assertEqual(store.current(), {})
        self.assertFalse(store.log_path().exists())

    def test_attention_store_keeps_actionable_drift_current_acknowledgements(self) -> None:
        store = AttentionDismissalStore(observer_logs_root(self.tmp))
        store.dismiss(
            AttentionDismissalRecord(
                itemId="actionable-drift:agents-remember:main",
                kind="actionable-drift",
                dismissedAt=_TS,
            )
        )
        self.assertEqual(store.prune_lifecycles(set()), 0)
        self.assertIn("actionable-drift:agents-remember:main", store.current())

    def test_attention_store_prune_compacts_legacy_duplicate_live_rows(self) -> None:
        store = AttentionDismissalStore(observer_logs_root(self.tmp))
        older = AttentionDismissalRecord(
            itemId="stale-session:L1",
            kind="stale-session",
            lifecycleId="L1",
            dismissedAt=_TS,
        )
        newer = AttentionDismissalRecord(
            itemId="stale-session:L1",
            kind="stale-session",
            lifecycleId="L1",
            dismissedAt="2026-06-14T10:01:00Z",
        )
        path = store.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            older.model_dump_json(by_alias=True, exclude_none=True)
            + "\n"
            + newer.model_dump_json(by_alias=True, exclude_none=True)
            + "\n",
            encoding="utf-8",
        )

        self.assertEqual(store.prune_lifecycles({"L1"}), 1)
        self.assertEqual(len(store.read()), 1)
        self.assertEqual(store.current()["stale-session:L1"].dismissedAt, "2026-06-14T10:01:00Z")

    def test_api_action_dismiss_records_lifecycle_acknowledgement(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={
                    "target": "L1",
                    "itemId": "stale-session:L1",
                    "kind": "stale-session",
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "received")
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertEqual(dismissals["stale-session:L1"].lifecycleId, "L1")

    def test_api_action_dismiss_records_actionable_drift_acknowledgement(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={
                    "itemId": "actionable-drift:agents-remember:main",
                    "kind": "actionable-drift",
                },
            )
        self.assertEqual(response.status_code, 202)
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertIsNone(dismissals["actionable-drift:agents-remember:main"].lifecycleId)

    def test_api_action_dismiss_gate_open_also_cancels_gate(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(kind="agent-question", lifecycle_id="L1", gate_id="G1", now=_FRESH_GATE_TS)
        )
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={
                    "target": "L1",
                    "itemId": "gate:G1",
                    "kind": "gate-open",
                    "gateId": "G1",
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["gate"]["state"], "cancelled")  # gate cancel still fires
        self.assertEqual(store.current("L1"), {})  # cancel deletes the gate
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertNotIn("gate:G1", dismissals)  # the deleted gate is the consumed source

    def test_api_action_dismiss_missing_gate_is_already_consumed(self) -> None:
        # A gate-open dismiss whose gate already vanished must not 500: the cancel KeyError
        # is swallowed because the missing gate means the source item is already gone.
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={"target": "L1", "itemId": "gate:gone", "kind": "gate-open", "gateId": "gone"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("gate", response.json())  # no gate payload when the cancel found nothing
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertNotIn("gate:gone", dismissals)


class StaticTests(unittest.TestCase):
    def test_static_dir_resolves_to_shipped_bundle(self) -> None:
        static_dir = dashboard_static_dir()
        self.assertIsNotNone(static_dir)
        assert static_dir is not None
        self.assertTrue((static_dir / "index.html").is_file())
        self.assertTrue((static_dir / "assets").is_dir())


class CliTests(unittest.TestCase):
    def test_dashboard_subcommand_parsing(self) -> None:
        namespace = cli_main.build_parser().parse_args(
            ["dashboard", "--config", "/abs/settings.json", "--port", "9999"]
        )
        self.assertEqual(namespace.config, "/abs/settings.json")
        self.assertEqual(namespace.port, 9999)
        self.assertEqual(namespace.host, "127.0.0.1")
        self.assertEqual(namespace.interval, 1.0)
        self.assertIsNone(namespace.heartbeat)
        self.assertIs(namespace.func, cli_dashboard.run)


class CliRunTests(unittest.TestCase):
    def _args(self, **overrides: object) -> argparse.Namespace:
        base = {
            "config": "/abs/settings.json",
            "host": "127.0.0.1",
            "port": 8765,
            "interval": 1.0,
            "heartbeat": None,
            "reload": False,
            "sim": None,
            "sim_speed": "1",
            "daemon": False,
            "status": False,
            "stop": False,
            "no_access_log": False,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_run_launches_server(self) -> None:
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=object()) as load,
            mock.patch.object(cli_dashboard, "create_app", return_value="APP") as create,
            mock.patch("uvicorn.run") as serve,
        ):
            result = cli_dashboard.run(self._args())
        self.assertEqual(result, 0)
        load.assert_called_once_with("/abs/settings.json")
        create.assert_called_once()
        serve.assert_called_once()

    def test_run_reports_config_error(self) -> None:
        with mock.patch.object(cli_dashboard, "load_config", side_effect=ConfigError("bad")):
            result = cli_dashboard.run(self._args())
        self.assertEqual(result, 1)

    def test_run_reload_launches_the_dev_factory(self) -> None:
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=object()),
            mock.patch.object(cli_dashboard, "create_app") as create,
            mock.patch("uvicorn.run") as serve,
        ):
            result = cli_dashboard.run(self._args(reload=True))
        self.assertEqual(result, 0)
        # --reload passes an import-string factory so uvicorn's reloader can re-import on
        # change; the app object is never pre-built in this branch.
        create.assert_not_called()
        serve.assert_called_once()
        args, kwargs = serve.call_args
        self.assertEqual(args[0], "agents_remember.cli.dashboard:_dev_app")
        self.assertTrue(kwargs["factory"])
        self.assertTrue(kwargs["reload"])

    def test_run_reload_with_sim_is_rejected(self) -> None:
        with mock.patch.object(cli_dashboard, "load_config", return_value=object()):
            result = cli_dashboard.run(self._args(reload=True, sim="/fix"))
        self.assertEqual(result, 1)

    def test_dev_app_factory_builds_from_env(self) -> None:
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=object()) as load,
            mock.patch.object(cli_dashboard, "create_app", return_value="APP") as create,
            mock.patch.dict(
                os.environ,
                {
                    cli_dashboard._DEV_CONFIG_ENV: "/abs/settings.json",
                    cli_dashboard._DEV_INTERVAL_ENV: "2.5",
                },
                clear=False,
            ),
        ):
            app = cli_dashboard._dev_app()
        self.assertEqual(app, "APP")
        load.assert_called_once_with("/abs/settings.json")
        _, kwargs = create.call_args
        self.assertEqual(kwargs["interval"], 2.5)

    def test_main_dispatches_to_subcommand(self) -> None:
        with mock.patch.object(cli_dashboard, "run", return_value=0) as run_stub:
            result = cli_main.main(["dashboard", "--config", "/abs/settings.json"])
        self.assertEqual(result, 0)
        run_stub.assert_called_once()


class RawEventTests(unittest.TestCase):
    """The raw ``event`` channel's pure byte-offset tail + cursor resume (``serving.events``)."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)  # acts as the observer root

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _append(self, lifecycle: str, *lines: str) -> Path:
        path = self.root / "lifecycles" / lifecycle / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        return path

    def _event_line(self, ident: str, kind: str, ts: str, **data: object) -> str:
        return json.dumps(
            {
                "schema": "ar-observer-event/v1",
                "id": ident,
                "ts": ts,
                "kind": kind,
                "trust": "observed",
                "actor": "system",
                "lifecycleId": ident.split("-", maxsplit=1)[0],
                "data": data,
            }
        )

    def test_cursor_round_trip(self) -> None:
        offsets = {"L1": 42, "workspace": 7}
        self.assertEqual(decode_cursor(encode_cursor(offsets)), offsets)

    def test_decode_garbage_is_empty(self) -> None:
        self.assertEqual(decode_cursor(None), {})
        self.assertEqual(decode_cursor("not-base64-@@@"), {})

    def test_reads_new_lines_then_nothing(self) -> None:
        self._append("L1", '{"a":1}', '{"a":2}')
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([(e.source, e.data) for e in events], [("L1", '{"a":1}'), ("L1", '{"a":2}')])
        again, offsets_again = read_new_events(self.root, offsets)
        self.assertEqual(again, [])
        self.assertEqual(offsets_again, offsets)

    def test_resume_from_cursor_skips_consumed(self) -> None:
        self._append("L1", '{"a":1}', '{"a":2}')
        events, _ = read_new_events(self.root, {})
        resumed, _ = read_new_events(self.root, decode_cursor(events[0].cursor))
        self.assertEqual([e.data for e in resumed], ['{"a":2}'])

    def test_mid_record_lifecycle_cursor_realigns_to_successor(self) -> None:
        path = self._append("L1", '{"id":"first"}', '{"id":"second"}')
        events, offsets = read_new_events(self.root, {"L1": 2})
        self.assertEqual([json.loads(event.data)["id"] for event in events], ["second"])
        self.assertEqual(offsets["L1"], path.stat().st_size)

    def test_mid_record_workspace_cursor_realigns_after_base_translation(self) -> None:
        workspace = self.root / "workspace" / "events.jsonl"
        workspace.parent.mkdir(parents=True)
        workspace.write_bytes(b'{"id":"first"}\n{"id":"second"}\n')
        base = 700
        (workspace.parent / "events.cursor.json").write_text(
            json.dumps({"baseOffset": base}) + "\n", encoding="utf-8"
        )
        events, offsets = read_new_events(self.root, {"workspace": base + 2})
        self.assertEqual([json.loads(event.data)["id"] for event in events], ["second"])
        self.assertEqual(offsets["workspace"], base + workspace.stat().st_size)

    def test_malformed_json_and_invalid_utf8_advance_without_retry(self) -> None:
        path = self.root / "lifecycles" / "L1" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"id":"one"}\nnot-json\n\xff\xfe\n{"id":"two"}\n')
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([json.loads(event.data)["id"] for event in events], ["one", "two"])
        self.assertEqual(offsets["L1"], path.stat().st_size)
        again, same_offsets = read_new_events(self.root, offsets)
        self.assertEqual(again, [])
        self.assertEqual(same_offsets, offsets)

    def test_valid_non_object_json_advances_without_emission(self) -> None:
        path = self._append(
            "L1",
            '{"id":"one"}',
            "null",
            "[]",
            "42",
            "true",
            '"scalar"',
            '{"id":"two"}',
        )
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([event.payload["id"] for event in events], ["one", "two"])
        self.assertEqual(offsets["L1"], path.stat().st_size)
        again, same_offsets = read_new_events(self.root, offsets)
        self.assertEqual(again, [])
        self.assertEqual(same_offsets, offsets)

    def test_cursor_beyond_eof_settles_at_current_eof(self) -> None:
        path = self._append("L1", '{"id":"one"}')
        events, offsets = read_new_events(self.root, {"L1": path.stat().st_size + 10_000})
        self.assertEqual(events, [])
        self.assertEqual(offsets["L1"], path.stat().st_size)
        self._append("L1", '{"id":"two"}')
        more, _ = read_new_events(self.root, offsets)
        self.assertEqual([json.loads(event.data)["id"] for event in more], ["two"])

    def test_partial_trailing_line_waits_for_newline(self) -> None:
        path = self.root / "lifecycles" / "L1" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"a":1}\n{"a":2}', encoding="utf-8")  # second line not terminated
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([e.data for e in events], ['{"a":1}'])
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        more, _ = read_new_events(self.root, offsets)
        self.assertEqual([e.data for e in more], ['{"a":2}'])

    def test_multi_source_ordering_with_workspace_last(self) -> None:
        self._append("L1", '{"a":1}')
        workspace = self.root / "workspace" / "events.jsonl"
        workspace.parent.mkdir(parents=True)
        workspace.write_text('{"w":1}\n', encoding="utf-8")
        events, _ = read_new_events(self.root, {})
        self.assertEqual(
            [(e.source, e.data) for e in events], [("L1", '{"a":1}'), ("workspace", '{"w":1}')]
        )

    def test_fresh_connection_offsets_skip_expired_terminal_lifecycles(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        old = self._append(
            "old",
            self._event_line("old-1", "lifecycle.started", "2026-06-14T09:00:00+00:00"),
            self._event_line(
                "old-2",
                "lifecycle.ended",
                "2026-06-14T09:30:00+00:00",
                outcome="completed",
            ),
        )
        self._append("active", self._event_line("active-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"))
        self._append(
            "recent",
            self._event_line("recent-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            self._event_line(
                "recent-2",
                "lifecycle.ended",
                "2026-06-14T11:30:00+00:00",
                outcome="completed",
            ),
        )

        offsets = initial_event_offsets(self.root, now=now)
        events, _ = read_new_events(self.root, offsets)

        self.assertEqual(offsets["old"], old.stat().st_size)
        self.assertEqual({event.source for event in events}, {"active", "recent"})

    def test_prunes_expired_terminal_lifecycle_event_logs(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        old = self._append(
            "old",
            self._event_line("old-1", "lifecycle.started", "2026-06-14T09:00:00+00:00"),
            self._event_line(
                "old-2",
                "lifecycle.ended",
                "2026-06-14T09:30:00+00:00",
                outcome="completed",
            ),
        )
        recent = self._append(
            "recent",
            self._event_line("recent-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            self._event_line(
                "recent-2",
                "lifecycle.ended",
                "2026-06-14T11:30:00+00:00",
                outcome="completed",
            ),
        )

        removed = prune_expired_lifecycle_event_logs(self.root, now=now)

        self.assertEqual(removed, [old])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_fresh_connection_offsets_workspace_events_by_age(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        workspace = self.root / "workspace" / "events.jsonl"
        workspace.parent.mkdir(parents=True)
        workspace.write_text(
            "\n".join(
                [
                    self._event_line("ws-1", "provider.status", "2026-06-14T09:00:00+00:00"),
                    self._event_line("ws-2", "provider.status", "2026-06-14T11:30:00+00:00"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        events, _ = read_new_events(self.root, initial_event_offsets(self.root, now=now))

        self.assertEqual([json.loads(event.data)["id"] for event in events], ["ws-2"])

    def test_fresh_connection_does_not_cap_parallel_active_lifecycle_history(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        noisy = tuple(
            self._event_line(
                f"noisy-{index}",
                "tool.completed",
                "2026-06-14T11:30:00+00:00",
                tool="noop",
                ok=True,
            )
            for index in range(500)
        )
        self._append(
            "noisy",
            self._event_line("noisy-start", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            *noisy,
        )
        self._append(
            "quiet",
            self._event_line("quiet-1", "lifecycle.started", "2026-06-14T11:05:00+00:00"),
        )

        events, _ = read_new_events(self.root, initial_event_offsets(self.root, now=now))
        ids = {json.loads(event.data)["id"] for event in events}

        self.assertIn("quiet-1", ids)
        self.assertEqual(len([event for event in events if event.source == "noisy"]), 501)

    def test_read_new_events_skips_heartbeats(self) -> None:
        self._append(
            "L1",
            self._event_line("L1-1", "lifecycle.started", "2026-06-14T11:00:00+00:00"),
            self._event_line(
                "L1-2", "lifecycle.heartbeat", "2026-06-14T11:00:16+00:00", state="running"
            ),
            self._event_line("L1-3", "tool.completed", "2026-06-14T11:00:30+00:00", tool="ping"),
        )
        events, offsets = read_new_events(self.root, {})
        self.assertEqual(
            [json.loads(e.data)["kind"] for e in events],
            ["lifecycle.started", "tool.completed"],
        )
        # The heartbeat is consumed (offset advanced past it), never re-read on resume.
        again, _ = read_new_events(self.root, offsets)
        self.assertEqual(again, [])

    def test_read_new_events_limit_bounds_batch(self) -> None:
        self._append(
            "L1",
            *(
                self._event_line(f"L1-{index}", "tool.completed", "2026-06-14T11:00:00+00:00", n=index)
                for index in range(10)
            ),
        )
        first, offsets = read_new_events(self.root, {}, limit=3)
        self.assertEqual([json.loads(e.data)["data"]["n"] for e in first], [0, 1, 2])
        more, _ = read_new_events(self.root, offsets, limit=3)
        self.assertEqual([json.loads(e.data)["data"]["n"] for e in more], [3, 4, 5])

    def test_dormant_promoted_lifecycle_pruned_without_terminal_event(self) -> None:
        # The keystone: an enclosure-backed (promoted) lifecycle that went quiet with NO
        # lifecycle.ended -- and whose heartbeat kept ticking until recently -- is still
        # retired. Retention keys on *real* activity, so the recent heartbeat does not keep
        # the log alive; last real activity (10:30, 1.5h before now) is past the TTL.
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        dead = self._append(
            "dead",
            self._event_line("dead-1", "lifecycle.started", "2026-06-14T09:00:00+00:00", fleeting=True),
            self._event_line("dead-2", "lifecycle.promoted", "2026-06-14T09:01:00+00:00", scope="r"),
            self._event_line("dead-3", "tool.completed", "2026-06-14T10:30:00+00:00", tool="x"),
            self._event_line("dead-hb", "lifecycle.heartbeat", "2026-06-14T11:50:00+00:00", state="running"),
        )
        removed = prune_expired_lifecycle_event_logs(self.root, now=now)
        self.assertEqual(removed, [dead])
        self.assertFalse(dead.exists())

    def test_dormant_fleeting_lifecycle_pruned_without_terminal_event(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        fleet = self._append(
            "fleet",
            self._event_line("fleet-1", "lifecycle.started", "2026-06-14T10:00:00+00:00", fleeting=True),
            self._event_line("fleet-hb", "lifecycle.heartbeat", "2026-06-14T11:55:00+00:00", state="running"),
        )
        # Only real activity is the start at 10:00 (2h ago); the recent heartbeat is ignored.
        self.assertEqual(prune_expired_lifecycle_event_logs(self.root, now=now), [fleet])

    def test_protected_lifecycle_log_survives_inactivity(self) -> None:
        # A dormant, enclosure-backed log that belongs to a not-yet-retired master series is exempt:
        # the dashboard passes its id in `protected_lifecycle_ids`, so a running durable task keeps its
        # (and its siblings') history regardless of how long since its last lifecycle event.
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        kept = self._append(
            "keepme",
            self._event_line("k-1", "lifecycle.started", "2026-06-14T09:00:00+00:00", fleeting=True),
            self._event_line("k-2", "lifecycle.promoted", "2026-06-14T09:01:00+00:00", scope="r"),
            self._event_line("k-3", "tool.completed", "2026-06-14T10:00:00+00:00", tool="x"),
        )
        # Protected -> not pruned even though last real activity (10:00) is 2h ago, past the TTL.
        self.assertEqual(
            prune_expired_lifecycle_event_logs(self.root, now=now, protected_lifecycle_ids={"keepme"}),
            [],
        )
        self.assertTrue(kept.exists())
        # Drop the protection and it IS pruned — proving the dormancy precondition held all along.
        self.assertEqual(prune_expired_lifecycle_event_logs(self.root, now=now), [kept])
        self.assertFalse(kept.exists())

    def test_active_lifecycle_with_recent_activity_not_pruned(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        alive = self._append(
            "alive",
            self._event_line("alive-1", "lifecycle.started", "2026-06-14T09:00:00+00:00", fleeting=True),
            self._event_line("alive-2", "lifecycle.promoted", "2026-06-14T09:01:00+00:00", scope="r"),
            self._event_line("alive-3", "tool.completed", "2026-06-14T11:50:00+00:00", tool="x"),
        )
        self.assertEqual(prune_expired_lifecycle_event_logs(self.root, now=now), [])
        self.assertTrue(alive.exists())

    def test_initial_offsets_bound_active_replay_to_recent_window(self) -> None:
        now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        # Active (recent activity) but with history older than the 1h replay window.
        self._append(
            "long",
            self._event_line("long-start", "lifecycle.started", "2026-06-14T08:00:00+00:00", fleeting=False),
            self._event_line("long-old", "tool.completed", "2026-06-14T09:00:00+00:00", tool="x"),
            self._event_line("long-recent", "tool.completed", "2026-06-14T11:50:00+00:00", tool="x"),
        )
        offsets = initial_event_offsets(self.root, now=now)
        events, _ = read_new_events(self.root, offsets)
        self.assertEqual([json.loads(e.data)["id"] for e in events], ["long-recent"])


class StreamRawEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_streams_backlog(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text('{"a":1}\n', encoding="utf-8")
        gen = stream_raw_events(_config(self.tmp), interval=0.01)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "event")
        # The line is parsed to an object so ServerSentEvent single-encodes it (matching the
        # state channel); emitting the raw JSON string would double-encode the SSE wire.
        self.assertEqual(first.data, {"a": 1})
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        self.assertEqual(ready.data, {"ready": True})
        await gen.aclose()

    async def test_mid_record_cursor_streams_successor_then_ready(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text('{"id":"first"}\n{"id":"second"}\n', encoding="utf-8")
        gen = stream_raw_events(
            _config(self.tmp),
            interval=0.01,
            last_event_id=encode_cursor({"L1": 2}),
        )
        successor = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(successor.data, {"id": "second"})
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        await gen.aclose()

    async def test_stream_skips_non_object_json_then_emits_object_and_ready(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(
            'null\n[]\n42\ntrue\n"scalar"\n{"id":"valid"}\n',
            encoding="utf-8",
        )
        gen = stream_raw_events(
            _config(self.tmp),
            interval=0.01,
            last_event_id=encode_cursor({"L1": 0}),
        )
        event = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(event.event, "event")
        self.assertEqual(event.data, {"id": "valid"})
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        self.assertEqual(decode_cursor(ready.id)["L1"], log.stat().st_size)
        await gen.aclose()

    async def test_stream_does_not_emit_heartbeats(self) -> None:
        log = self.tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"
        log.parent.mkdir(parents=True)
        ts = datetime.now(UTC).isoformat()
        log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"id": "hb", "ts": ts, "kind": "lifecycle.heartbeat", "data": {"state": "running"}}
                    ),
                    json.dumps(
                        {"id": "real", "ts": ts, "kind": "tool.completed", "data": {"tool": "ping"}}
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        gen = stream_raw_events(_config(self.tmp), interval=0.01)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "event")
        self.assertEqual(first.data["kind"], "tool.completed")
        ready = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(ready.event, "ready")
        await gen.aclose()

    async def test_invalid_cursor_uses_retained_fresh_offsets(self) -> None:
        log = self.tmp / "logs" / "observer" / "workspace" / "events.jsonl"
        log.parent.mkdir(parents=True)
        recent = datetime.now(UTC).isoformat()
        log.write_text(
            "\n".join(
                [
                    json.dumps({"id": "old", "ts": "2000-01-01T00:00:00+00:00"}),
                    json.dumps({"id": "recent", "ts": recent}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        gen = stream_raw_events(_config(self.tmp), interval=0.01, last_event_id="not-base64")
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.data["id"], "recent")
        await gen.aclose()


class SimFixtureTests(unittest.TestCase):
    def test_load_fixture_is_sorted(self) -> None:
        events = load_fixture(FIXTURE_DIR)
        self.assertEqual(len(events), 8)
        self.assertEqual(events[0].kind, "lifecycle.started")
        self.assertEqual([e.id for e in events[:3]], ["sim-e1", "sim-e2", "sim-e3"])

    def test_load_fixture_missing_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(load_fixture(Path(empty)), [])

    def test_parse_sim_speed(self) -> None:
        self.assertEqual(parse_sim_speed("paused"), 0.0)
        self.assertEqual(parse_sim_speed("10"), 10.0)
        with self.assertRaises(SimError):
            parse_sim_speed("fast")
        with self.assertRaises(SimError):
            parse_sim_speed("-1")

    def test_replay_clock_paused_is_frozen(self) -> None:
        start = datetime(2026, 6, 14, 9, 0, tzinfo=UTC)
        self.assertEqual(ReplayClock(start, speed=0.0).now(), start)

    def test_replay_clock_advances_from_start(self) -> None:
        start = datetime(2026, 6, 14, 9, 0, tzinfo=UTC)
        self.assertGreaterEqual(ReplayClock(start, speed=10.0).now(), start)


class SimReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._dir.name))

    def tearDown(self) -> None:
        self._dir.cleanup()

    @staticmethod
    def _at(second: int) -> datetime:
        return datetime(2026, 6, 14, 9, 0, second, tzinfo=UTC)

    def test_build_sim_overrides_root_to_a_fresh_dir(self) -> None:
        sim = build_sim(self.config, FIXTURE_DIR, speed=1.0)
        self.assertNotEqual(sim.config.coordination_root, self.config.coordination_root)
        self.assertEqual(sim.config.coordination_root, Path(sim.temp_dir.name))
        self.assertTrue(sim.config.coordination_root.is_dir())

    def test_build_sim_empty_fixture_raises(self) -> None:
        with tempfile.TemporaryDirectory() as empty, self.assertRaises(SimError):
            build_sim(self.config, Path(empty), speed=1.0)

    def test_feeder_is_progressive(self) -> None:
        sim = build_sim(self.config, FIXTURE_DIR, speed=1.0)
        before_any = datetime(2026, 6, 14, 8, 59, tzinfo=UTC)
        self.assertEqual(sim.feeder.feed(before_any), 0)
        self.assertEqual(sim.feeder.feed(self._at(10)), 3)  # e1, e2, e3
        self.assertEqual(sim.feeder.remaining, 5)
        projection = project_and_write(sim.config, now=self._at(10))
        self.assertEqual(len(projection.lifecycles), 1)
        lifecycle = projection.lifecycles[0]
        self.assertEqual(lifecycle.id, "sim-replay-lifecycle")
        self.assertEqual(lifecycle.phase, "build")
        self.assertEqual(lifecycle.state, "running")
        self.assertFalse(lifecycle.fleeting)

    def test_replay_drives_state_transitions(self) -> None:
        sim = build_sim(self.config, FIXTURE_DIR, speed=1.0)
        sim.feeder.feed(self._at(10))
        before = project_and_write(sim.config, now=self._at(10))
        sim.feeder.feed(self._at(30))  # through tool.completed + lifecycle.blocked
        after = project_and_write(sim.config, now=self._at(30))
        self.assertEqual(after.lifecycles[0].state, "blocked")
        self.assertEqual(after.lifecycles[0].tokens, 1200)
        self.assertIn("lifecycle", [d.event for d in diff_projection(before, after)])

    def test_replay_is_deterministic(self) -> None:
        moment = self._at(30)
        dumps = []
        for _ in range(2):
            sim = build_sim(self.config, FIXTURE_DIR, speed=1.0)
            sim.feeder.feed(moment)
            dumps.append(project_and_write(sim.config, now=moment).model_dump(by_alias=True))
        self.assertEqual(dumps[0], dumps[1])


class ActionTests(unittest.TestCase):
    """The POST skeleton's availability + attribution mapping (``serving.actions``)."""

    def _projection(self) -> WorkspaceProjection:
        blocked = _lifecycle("L1", state="blocked").model_copy(
            update={"actions": [ActionAvailability(action="resume", enabled=True)]}
        )
        running = _lifecycle("L2", state="running").model_copy(
            update={
                "actions": [
                    ActionAvailability(
                        action="resume",
                        enabled=False,
                        disabledReason="lifecycle is not blocked",
                        nextSafeAction="resume becomes safe once the gate is resolved",
                    )
                ]
            }
        )
        return _projection(lifecycles=(blocked, running))

    def test_enabled_action_is_received_with_attribution(self) -> None:
        outcome = evaluate_action(self._projection(), "resume", "L1", actor="developer", now=_TS)
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(outcome.body["status"], "received")
        intent = outcome.body["intent"]
        self.assertEqual(intent["source"], "dashboard")
        self.assertEqual(intent["actor"], "developer")
        self.assertEqual(intent["action"], "resume")
        self.assertEqual(intent["target"], "L1")
        self.assertEqual(intent["ts"], _TS)

    def test_disabled_action_is_conflict_with_reason(self) -> None:
        outcome = evaluate_action(self._projection(), "resume", "L2", actor="developer", now=_TS)
        self.assertEqual(outcome.status_code, 409)
        self.assertEqual(outcome.body["status"], "disabled")
        self.assertEqual(outcome.body["detail"], "lifecycle is not blocked")
        self.assertEqual(outcome.body["nextSafeAction"], "resume becomes safe once the gate is resolved")

    def test_unknown_action_is_conflict(self) -> None:
        outcome = evaluate_action(self._projection(), "frobnicate", "L1", actor="developer", now=_TS)
        self.assertEqual(outcome.status_code, 409)
        self.assertEqual(outcome.body["status"], "unavailable")

    def test_unknown_target_is_not_found(self) -> None:
        outcome = evaluate_action(self._projection(), "resume", "ZZZ", actor="developer", now=_TS)
        self.assertEqual(outcome.status_code, 404)

    def test_enclosure_target_resolves(self) -> None:
        enclosure = _enclosure("e1").model_copy(
            update={"actions": [ActionAvailability(action="integrate", enabled=True)]}
        )
        outcome = evaluate_action(
            _projection(enclosures=(enclosure,)), "integrate", "e1", actor="developer", now=_TS
        )
        self.assertEqual(outcome.status_code, 202)


class ActionEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_post_unknown_target_returns_404(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post("/api/actions/resume", json={"target": "nope"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-target")

    def test_post_rejects_unknown_actor(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post("/api/actions/resume", json={"target": "x", "actor": "intruder"})
        self.assertEqual(response.status_code, 422)


class CliSimTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _args(self, **overrides: object) -> argparse.Namespace:
        base = {
            "config": "/abs/settings.json",
            "host": "127.0.0.1",
            "port": 8765,
            "interval": 1.0,
            "heartbeat": None,
            "reload": False,
            "sim": None,
            "sim_speed": "1",
            "daemon": False,
            "status": False,
            "stop": False,
            "no_access_log": False,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_sim_args_parse(self) -> None:
        namespace = cli_main.build_parser().parse_args(
            ["dashboard", "--config", "/abs/settings.json", "--sim", "/fix", "--sim-speed", "10"]
        )
        self.assertEqual(namespace.sim, "/fix")
        self.assertEqual(namespace.sim_speed, "10")

    def test_run_sim_launches_with_clock_and_feeder(self) -> None:
        config = _config(self.tmp)
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=config),
            mock.patch.object(cli_dashboard, "create_app", return_value="APP") as create,
            mock.patch("uvicorn.run") as serve,
        ):
            result = cli_dashboard.run(self._args(sim=str(FIXTURE_DIR), sim_speed="10"))
        self.assertEqual(result, 0)
        serve.assert_called_once()
        _, kwargs = create.call_args
        self.assertIn("now", kwargs)
        self.assertIn("before_tick", kwargs)

    def test_run_sim_bad_speed_returns_1(self) -> None:
        config = _config(self.tmp)
        with mock.patch.object(cli_dashboard, "load_config", return_value=config):
            result = cli_dashboard.run(self._args(sim=str(FIXTURE_DIR), sim_speed="bogus"))
        self.assertEqual(result, 1)

    def test_run_sim_empty_fixture_returns_1(self) -> None:
        config = _config(self.tmp)
        empty = self.tmp / "empty"
        empty.mkdir()
        with mock.patch.object(cli_dashboard, "load_config", return_value=config):
            result = cli_dashboard.run(self._args(sim=str(empty), sim_speed="1"))
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
