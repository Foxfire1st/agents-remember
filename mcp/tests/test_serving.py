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
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.cli import __main__ as cli_main
from agents_remember.cli import dashboard as cli_dashboard
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import create_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.mcp.config import ConfigError, McpRuntimeConfig
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
    WorkspaceProjection,
)
from agents_remember.observer.projection_store import project_and_write
from agents_remember.serving.actions import GateDecisionIntent, evaluate_action
from agents_remember.serving.app import create_app, stream_events
from agents_remember.serving.delta import DeltaEvent, diff_projection
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

_TS = "2026-06-14T10:00:00Z"
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
    metrics: Metrics | None = None,
    analytics: Analytics | None = None,
) -> WorkspaceProjection:
    return WorkspaceProjection(
        generatedAt=_TS,
        lifecycles=list(lifecycles),
        providers=list(providers),
        enclosures=list(enclosures),
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


class ActionGateTests(unittest.TestCase):
    """Slice 6b: gate-decision verbs carry an intent (pure) and the router records it."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_evaluate_action_emits_gate_intent_for_verbs(self) -> None:
        outcome = evaluate_action(_projection(), "approve", "L1", actor="developer", now=_TS)
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.gate_decision, GateDecisionIntent(lifecycle_id="L1", decision="approve")
        )

    def test_evaluate_action_transition_keeps_4b_skeleton(self) -> None:
        # a non-gate action on an unknown target stays the 4b no-mutation skeleton
        outcome = evaluate_action(_projection(), "integrate", "nope", actor="developer", now=_TS)
        self.assertIsNone(outcome.gate_decision)
        self.assertEqual(outcome.status_code, 404)

    def test_api_action_approve_records_developer_decision(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="G1", now=_TS)
        )
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post("/api/actions/approve", json={"target": "L1"})
        self.assertEqual(response.status_code, 202)
        gate = response.json()["gate"]
        self.assertEqual(gate["state"], "approved")
        self.assertEqual(gate["decidedBy"], "developer")  # un-forgeable vs. the agent's model path
        self.assertEqual(gate["decidedVia"], "dashboard")
        self.assertEqual(store.current("L1")["G1"].state, "approved")

    def test_api_action_approve_without_open_gate_is_409(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post("/api/actions/approve", json={"target": "L1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "no-open-gate")

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

    def test_api_operator_inbox_requires_address(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.post(
                "/api/operator-inbox",
                json={"ask": "Continue?", "response": "Yes, proceed."},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-address")


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
        self.assertIs(namespace.func, cli_dashboard.run)


class CliRunTests(unittest.TestCase):
    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(
            config="/abs/settings.json",
            host="127.0.0.1",
            port=8765,
            interval=1.0,
            sim=None,
            sim_speed="1",
        )

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
            "sim": None,
            "sim_speed": "1",
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
