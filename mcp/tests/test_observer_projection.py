"""Tests for the observer projection read side (slice 3a).

Covers the pure fold (``project_lifecycle``) and its determinism, the inferred
layer (stale -> paused, dormant fleeting -> abandoned, terminal preserved),
append-only corrections, precomputed action availability, the workspace tree
assembly + metrics, the atomic projection write, and the structural surface
readers (provider current-state + worktree enclosures).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.records import create_gate, decide_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope, RepositoryScope
from agents_remember.memory_quality.integrity.onboarding_drift_check import summary
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import DriftRow
from agents_remember.observer.events import Event
from agents_remember.observer.paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
    observer_logs_root,
    observer_root,
)
from agents_remember.observer.projection import (
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    ProviderNode,
    SetupProgressNode,
    SidecarStaleNode,
    TaskDocNode,
    WorkspaceProjection,
)
from agents_remember.observer.projection_store import (
    project_and_write,
    read_lifecycle_logs,
    write_projection,
)
from agents_remember.observer.reducer import (
    build_analytics,
    build_attention_queue,
    build_engine_processes,
    enclosure_actions,
    project_lifecycle,
    project_workspace,
    staleness_histogram,
    token_series,
)
from agents_remember.observer.snapshots import (
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
    read_gates,
    read_ledger,
    read_providers,
    read_route_coverage,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_sidecar_staleness,
    read_start_progress_entries,
    read_task_documents,
    read_tool_reports,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.current_state import current_state_path
from agents_remember.providers.setup_progress import PROGRESS_SCHEMA
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.start_progress import (
    clear_start_progress,
    read_start_progress,
    start_progress_path,
    write_start_progress,
)
from agents_remember.worktrees.worktree_contract import default_contract, write_contract

T0 = "2026-06-13T18:00:00+00:00"
FRESH = datetime(2026, 6, 13, 18, 0, 30, tzinfo=UTC)  # 30s after T0  (< STALE)
STALE = datetime(2026, 6, 13, 18, 10, 0, tzinfo=UTC)  # 600s after T0 (> STALE, < TTL)
DORMANT = datetime(2026, 6, 13, 19, 30, 0, tzinfo=UTC)  # 5400s after T0 (> TTL)


def _event(
    kind: str,
    *,
    lifecycle_id: str = "LC1",
    ts: str = T0,
    trust: str = "declared",
    actor: str = "model",
    enclosure: str | None = None,
    repo_id: str | None = None,
    **data: object,
) -> Event:
    return Event(
        id=new_ulid(),
        ts=ts,
        kind=kind,
        trust=trust,  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        lifecycleId=lifecycle_id,
        enclosure=enclosure,
        repoId=repo_id,
        data=dict(data),
    )


def _started(
    *, ts: str = T0, fleeting: bool = True, phase: str = "request", lifecycle_id: str = "LC1"
) -> Event:
    return _event(
        "lifecycle.started", ts=ts, lifecycle_id=lifecycle_id, fleeting=fleeting, phase=phase
    )


def _enclosure(**overrides: str) -> EnclosureNode:
    base: dict[str, str] = {
        "enclosure": "/c.md",
        "taskId": "T",
        "taskName": "t",
        "repoName": "r",
        "lifecycleId": "",
        "worktreeGroup": "/g",
        "humanReviewStatus": "approved",
        "closeoutStatus": "completed",
        "integrationStatus": "not-started",
        "cleanup": "pending",
    }
    base.update(overrides)
    return EnclosureNode.model_validate(base)


class FoldTests(unittest.TestCase):
    def test_seed_from_started(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual((proj.id, proj.state, proj.phase, proj.fleeting), ("LC1", "running", "request", True))
        self.assertEqual(proj.startedAt, T0)
        self.assertFalse(proj.inferred)
        self.assertEqual(proj.tokens, 0)

    def test_phase_block_resume(self) -> None:
        log = [
            _started(ts=T0),
            _event("lifecycle.phase-changed", ts="2026-06-13T18:00:05+00:00", phase="build"),
            _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"}),
            _event("lifecycle.resumed", ts="2026-06-13T18:00:20+00:00"),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual((proj.state, proj.phase), ("running", "build"))
        self.assertIsNone(proj.ask)

    def test_blocked_keeps_ask(self) -> None:
        log = [_started(), _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "decision"})]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual(proj.state, "blocked")
        self.assertEqual(proj.ask, {"kind": "decision"})

    def test_tokens_aggregate(self) -> None:
        log = [
            _started(),
            _event("tool.completed", ts="2026-06-13T18:00:05+00:00", trust="observed", tool="a", tokens=100, ok=True),
            _event("tool.completed", ts="2026-06-13T18:00:10+00:00", trust="observed", tool="b", tokens=50, ok=True),
        ]
        self.assertEqual(project_lifecycle(log, now=FRESH).tokens, 150)

    def test_promote_makes_persistent(self) -> None:
        log = [
            _started(fleeting=True),
            _event(
                "lifecycle.promoted",
                ts="2026-06-13T18:00:05+00:00",
                trust="observed",
                actor="system",
                enclosure="/c.md",
                repo_id="repo-a",
                scope="repo-a",
            ),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertFalse(proj.fleeting)
        self.assertEqual((proj.scope, proj.enclosure, proj.repoId), ("repo-a", "/c.md", "repo-a"))

    def test_ended_outcomes(self) -> None:
        done = project_lifecycle([_started(), _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")], now=FRESH)
        dropped = project_lifecycle([_started(), _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="abandoned")], now=FRESH)
        self.assertEqual(done.state, "completed")
        self.assertEqual(dropped.state, "abandoned")

    def test_empty_log_raises(self) -> None:
        with self.assertRaises(ValueError):
            project_lifecycle([], now=FRESH)


class DeterminismTests(unittest.TestCase):
    def test_same_log_same_projection(self) -> None:
        log = [
            _started(),
            _event("lifecycle.phase-changed", ts="2026-06-13T18:00:05+00:00", phase="build"),
            _event("tool.completed", ts="2026-06-13T18:00:10+00:00", trust="observed", tool="a", tokens=7, ok=True),
        ]
        first = project_lifecycle(log, now=STALE).model_dump()
        second = project_lifecycle(log, now=STALE).model_dump()
        self.assertEqual(first, second)


class InferredLayerTests(unittest.TestCase):
    def test_fresh_running_stays_running(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual(proj.state, "running")
        self.assertFalse(proj.inferred)
        self.assertEqual(proj.staleSeconds, 30.0)

    def test_stale_running_projects_paused(self) -> None:
        proj = project_lifecycle([_started()], now=STALE)
        self.assertEqual(proj.state, "paused")
        self.assertTrue(proj.inferred)

    def test_fleeting_dormant_projects_abandoned(self) -> None:
        proj = project_lifecycle([_started(fleeting=True)], now=DORMANT)
        self.assertEqual(proj.state, "abandoned")
        self.assertTrue(proj.inferred)

    def test_persistent_dormant_not_abandoned(self) -> None:
        log = [
            _started(fleeting=True),
            _event("lifecycle.promoted", ts="2026-06-13T18:00:05+00:00", trust="observed", actor="system", scope="repo-a"),
        ]
        proj = project_lifecycle(log, now=DORMANT)
        self.assertEqual(proj.state, "paused")  # stale, but never auto-abandoned
        self.assertTrue(proj.inferred)

    def test_terminal_survives_staleness(self) -> None:
        proj = project_lifecycle([_started(), _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")], now=DORMANT)
        self.assertEqual(proj.state, "completed")
        self.assertFalse(proj.inferred)


class CorrectionTests(unittest.TestCase):
    def test_correction_overrides_state(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        correction = _event("correction.recorded", ts="2026-06-13T18:00:10+00:00", trust="inferred", actor="system", corrects=ended.id, state="abandoned")
        proj = project_lifecycle([_started(), ended, correction], now=FRESH)
        self.assertEqual(proj.state, "abandoned")

    def test_malformed_correction_ignored(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        bogus = _event("correction.recorded", ts="2026-06-13T18:00:10+00:00", trust="inferred", actor="system", corrects=ended.id, state="not-a-state")
        proj = project_lifecycle([_started(), ended, bogus], now=FRESH)
        self.assertEqual(proj.state, "completed")


class ActionAvailabilityTests(unittest.TestCase):
    def test_resume_enabled_only_when_blocked(self) -> None:
        blocked = project_lifecycle([_started(), _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"})], now=FRESH)
        running = project_lifecycle([_started()], now=FRESH)
        self.assertTrue(_action(blocked.actions, "resume").enabled)
        self.assertFalse(_action(running.actions, "resume").enabled)

    def test_integrate_available_after_closeout(self) -> None:
        actions = enclosure_actions(_enclosure())
        self.assertTrue(_action(actions, "integrate").enabled)
        self.assertFalse(_action(actions, "cleanup").enabled)

    def test_integrate_blocked_before_closeout(self) -> None:
        actions = enclosure_actions(_enclosure(closeoutStatus="not-started"))
        integrate = _action(actions, "integrate")
        self.assertFalse(integrate.enabled)
        self.assertEqual(integrate.disabledReason, "closeout not complete")

    def test_cleanup_available_after_integration(self) -> None:
        actions = enclosure_actions(_enclosure(integrationStatus="completed"))
        self.assertFalse(_action(actions, "integrate").enabled)
        self.assertTrue(_action(actions, "cleanup").enabled)


class WorkspaceTests(unittest.TestCase):
    def test_tree_and_metrics(self) -> None:
        logs = [
            [_started(lifecycle_id="LC1", ts=T0)],
            [_started(lifecycle_id="LC2", ts=T0), _event("lifecycle.blocked", lifecycle_id="LC2", ts="2026-06-13T18:00:05+00:00", ask={"kind": "question"})],
        ]
        proj = project_workspace(
            logs,
            enclosures=[_enclosure()],
            providers=[ProviderNode(id="cgc", state="ready", ok=True, watcherUp=True, indexingState="indexed")],
            now=FRESH,
        )
        # 2 event-backed (running, blocked) + 1 synthesized persistent paused from the enclosure
        self.assertEqual(len(proj.lifecycles), 3)
        self.assertEqual(proj.metrics.lifecycleCount, 3)
        self.assertEqual(proj.metrics.runningCount, 1)
        self.assertEqual(proj.metrics.blockedCount, 1)
        self.assertEqual(proj.metrics.pausedCount, 1)
        self.assertEqual(proj.generatedAt, FRESH.isoformat())
        self.assertTrue(_action(proj.enclosures[0].actions, "integrate").enabled)
        synthesized = next(lc for lc in proj.lifecycles if lc.state == "paused")
        self.assertEqual(
            (synthesized.id, synthesized.fleeting, synthesized.inferred, synthesized.phase, synthesized.lastEventTs),
            ("r/t", False, True, "close", ""),
        )

    def test_persistent_synthesis_skips_enclosure_with_event_lifecycle(self) -> None:
        # An enclosure already represented by an event-backed lifecycle is not duplicated.
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            enclosures=[_enclosure(lifecycleId="LC1")],
            providers=[],
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_dormant_persistent_worktree_stays_out_of_the_attention_queue(self) -> None:
        # A synthesized paused persistent worktree (no events) is the hangar's job, not the queue.
        proj = project_workspace([], enclosures=[_enclosure()], providers=[], now=FRESH)
        self.assertEqual([lc.state for lc in proj.lifecycles], ["paused"])
        self.assertEqual(proj.analytics.attentionQueue, [])


class StoreIOTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def test_read_lifecycle_logs_enumerates(self) -> None:
        store = EventStore(self.root)
        store.append(_started(lifecycle_id="LCa", ts=T0))
        store.append(_started(lifecycle_id="LCb", ts=T0))
        logs = read_lifecycle_logs(self.root)
        self.assertEqual(len(logs), 2)

    def test_read_lifecycle_logs_absent_is_empty(self) -> None:
        self.assertEqual(read_lifecycle_logs(self.root), [])

    def test_write_projection_round_trips_atomically(self) -> None:
        proj = project_workspace([[_started()]], enclosures=[], providers=[], now=FRESH)
        write_projection(self.root, proj)
        state = json.loads((self.root / "latest-state.json").read_text(encoding="utf-8"))
        WorkspaceProjection.model_validate(state)
        metrics = json.loads((self.root / "latest-metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["lifecycleCount"], 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])  # no torn temp left behind


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def _config(self) -> McpRuntimeConfig:
        coord = (self.tmp / "coord").resolve()
        coord.mkdir(parents=True, exist_ok=True)
        return McpRuntimeConfig(
            config_path=coord / "mcp.settings.json",
            coordination_root=coord,
            workspace_root=(self.tmp / "ws").resolve(),
            transcript_root=coord / "logs",
            providers={
                "codegraphcontext-code": ProviderScope(
                    provider_id="codegraphcontext-code",
                    runtime_root=coord / "rt",
                    log_root=coord / "lg",
                    instance_id="projects",
                    scope="workspace",
                )
            },
        )

    def test_read_providers_parses_snapshot_with_age(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {"id": "codegraphcontext-code", "state": "ready", "ok": True, "watcherUp": True, "indexingState": "indexed"},
                        "grepai-memory": {"id": "grepai-memory", "state": "stopped", "ok": False, "watcherUp": False, "indexingState": "unknown"},
                    },
                }
            ),
            encoding="utf-8",
        )
        nodes = {node.id: node for node in read_providers(config, now=STALE)}
        self.assertEqual(set(nodes), {"codegraphcontext-code", "grepai-memory"})
        self.assertEqual(nodes["codegraphcontext-code"].state, "ready")
        self.assertEqual(nodes["codegraphcontext-code"].snapshotStaleSeconds, 600.0)

    def test_read_providers_absent_is_empty(self) -> None:
        self.assertEqual(read_providers(self._config(), now=FRESH), [])

    def test_read_providers_includes_per_worktree_stacks(self) -> None:
        # Surfaces 1 + 4: the workspace snapshot plus each worktree's isolated CGC+GrepAI stack,
        # bound to its worktree group + repo + role.
        config = self._config()
        coord = config.coordination_root
        workspace = current_state_path(config)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        workspace.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {"id": "codegraphcontext-code", "state": "ready", "ok": True},
                    },
                }
            ),
            encoding="utf-8",
        )
        runtime = coord / "worktrees" / "device-management" / "260612-x-ar" / "provider-runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "provider-state.json").write_text(
            json.dumps(
                {
                    "schema": "ar-worktree-provider-state/v1",
                    "repoName": "device-management",
                    "worktreeGroup": str(runtime.parent),
                    "isolatedProviderSettings": {"providers": ["codegraphcontext-code", "grepai-memory"]},
                }
            ),
            encoding="utf-8",
        )
        # a malformed stack is skipped, never fatal
        other = coord / "worktrees" / "device-management" / "broken-ar" / "provider-runtime"
        other.mkdir(parents=True, exist_ok=True)
        (other / "provider-state.json").write_text(json.dumps({"schema": "nope"}), encoding="utf-8")

        nodes = {node.id: node for node in read_providers(config, now=FRESH)}
        self.assertEqual(
            set(nodes),
            {"codegraphcontext-code", "codegraphcontext-code@260612-x-ar", "grepai-memory@260612-x-ar"},
        )
        self.assertEqual(nodes["codegraphcontext-code"].scope, "workspace")
        code = nodes["codegraphcontext-code@260612-x-ar"]
        self.assertEqual(
            (code.scope, code.role, code.repoId, code.worktreeGroup),
            ("worktree", "code", "device-management", "260612-x-ar"),
        )
        self.assertEqual(nodes["grepai-memory@260612-x-ar"].role, "memory")

    def test_read_enclosures_from_contract(self) -> None:
        coord = (self.tmp / "coord").resolve()
        contract = default_contract(
            task_name="Observe Lifecycle",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=coord,
            code_repo_path=coord / "repo-a",
            code_source_branch="main",
            code_work_branch="ar/observe",
            code_base_commit="0" * 40,
            worktree_name="observe",
            lifecycle_id="LC-1",
        )
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        nodes = read_enclosures(coord)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].repoName, "repo-a")
        self.assertEqual(nodes[0].lifecycleId, "LC-1")

    def test_read_enclosures_absent_is_empty(self) -> None:
        self.assertEqual(read_enclosures((self.tmp / "nope").resolve()), [])

    def test_project_and_write_end_to_end(self) -> None:
        config = self._config()
        store = EventStore(observer_root(config))
        store.append(_started(lifecycle_id="LC1", ts=T0))
        proj = project_and_write(config, now=FRESH)
        self.assertEqual(proj.metrics.lifecycleCount, 1)
        self.assertTrue((observer_root(config) / "latest-state.json").exists())


class TokenSeriesTests(unittest.TestCase):
    def test_cumulative_series_from_tool_events(self) -> None:
        log = [
            _started(),
            _event("tool.completed", ts="2026-06-13T18:00:05+00:00", trust="observed", tool="a", tokens=100, ok=True),
            _event("tool.completed", ts="2026-06-13T18:00:10+00:00", trust="observed", tool="b", tokens=50, ok=True),
        ]
        self.assertEqual(
            [(s.ts, s.cumulative) for s in token_series(log)],
            [("2026-06-13T18:00:05+00:00", 100), ("2026-06-13T18:00:10+00:00", 150)],
        )

    def test_series_on_projection(self) -> None:
        log = [_started(), _event("tool.completed", ts="2026-06-13T18:00:05+00:00", trust="observed", tool="a", tokens=7, ok=True)]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual([s.cumulative for s in proj.tokenSeries], [7])

    def test_no_tool_events_is_empty(self) -> None:
        self.assertEqual(token_series([_started()]), [])


class StalenessHistogramTests(unittest.TestCase):
    def _node(self, age: float | None) -> SidecarStaleNode:
        return SidecarStaleNode(onboardingFile="x", repository="r", lastVerifiedDate="d", ageSeconds=age)

    def test_buckets_by_age(self) -> None:
        nodes = [
            self._node(3600.0),            # <7d
            self._node(10 * 86400.0),      # 7-30d
            self._node(60 * 86400.0),      # 30-90d
            self._node(200 * 86400.0),     # >90d
            self._node(None),              # unknown
        ]
        hist = staleness_histogram(nodes)
        self.assertEqual(hist, {"<7d": 1, "7-30d": 1, "30-90d": 1, ">90d": 1, "unknown": 1})


class AnalyticsAssemblyTests(unittest.TestCase):
    def _stale(self, age: float) -> SidecarStaleNode:
        return SidecarStaleNode(onboardingFile=f"f{age}", repository="r", lastVerifiedDate="d", ageSeconds=age)

    def test_stalest_leaderboard_is_bounded_and_oldest_first(self) -> None:
        nodes = [self._stale(float(i) * 86400.0) for i in range(20)]
        analytics = build_analytics(
            drift_snapshots=[], sidecar_staleness=nodes, setup_summaries=[], setup_progress=[],
            route_coverage=[], tool_reports=[], ledgers=[], stalest_limit=5,
        )
        self.assertEqual(len(analytics.stalestSidecars), 5)
        self.assertEqual(analytics.stalestSidecars[0].ageSeconds, 19 * 86400.0)

    def test_project_workspace_wires_analytics_and_histogram(self) -> None:
        sidecars = [self._stale(3600.0), self._stale(200 * 86400.0)]
        proj = project_workspace(
            [[_started()]], enclosures=[], providers=[], now=FRESH,
            sidecar_staleness=sidecars,
            drift_snapshots=[DriftSnapshotNode(repository="r", branch="main", counts={"drifted": 1}, actionableCount=1)],
        )
        self.assertEqual(proj.metrics.stalenessHistogram["<7d"], 1)
        self.assertEqual(proj.metrics.stalenessHistogram[">90d"], 1)
        self.assertEqual(len(proj.analytics.driftSnapshots), 1)
        self.assertEqual(len(proj.analytics.stalestSidecars), 2)

    def test_3a_callers_get_empty_analytics(self) -> None:
        proj = project_workspace([[_started()]], enclosures=[], providers=[], now=FRESH)
        self.assertEqual(proj.analytics.driftSnapshots, [])
        self.assertEqual(proj.metrics.stalenessHistogram, {})


class AttentionQueueTests(unittest.TestCase):
    def test_blocked_and_provider_down_rank_alarm_first(self) -> None:
        proj = project_workspace(
            [
                [_started(lifecycle_id="LC1")],
                [
                    _started(lifecycle_id="LC2"),
                    _event(
                        "lifecycle.blocked",
                        lifecycle_id="LC2",
                        ts="2026-06-13T18:00:05+00:00",
                        ask={"kind": "gate", "question": "Approve the plan?"},
                    ),
                ],
            ],
            enclosures=[],
            providers=[ProviderNode(id="cgc", state="stopped", ok=False)],
            now=FRESH,
        )
        queue = proj.analytics.attentionQueue
        self.assertEqual(queue[0].kind, "provider-down")  # alarm sorts above warn
        blocked = next(item for item in queue if item.kind == "blocked-gate")
        self.assertEqual((blocked.lifecycleId, blocked.detail), ("LC2", "Approve the plan?"))

    def test_stale_session_is_info(self) -> None:
        proj = project_workspace([[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=STALE)
        item = proj.analytics.attentionQueue[0]
        self.assertEqual((item.kind, item.severity, item.lifecycleId), ("stale-session", "info", "LC1"))

    def test_dormant_fleeting_is_info(self) -> None:
        proj = project_workspace(
            [[_started(fleeting=True, lifecycle_id="LC1")]], enclosures=[], providers=[], now=DORMANT
        )
        self.assertEqual(proj.analytics.attentionQueue[0].kind, "dormant-fleeting")

    def test_drift_and_failed_setup_surface(self) -> None:
        proj = project_workspace(
            [[_started()]],
            enclosures=[],
            providers=[],
            now=FRESH,
            drift_snapshots=[
                DriftSnapshotNode(repository="repo-a", branch="main", counts={"drifted": 2}, actionableCount=2)
            ],
            setup_progress=[SetupProgressNode(group="g1", state="ok", failedPhases=["cgc setup"])],
        )
        self.assertEqual(
            {item.kind for item in proj.analytics.attentionQueue}, {"actionable-drift", "failed-setup"}
        )

    def test_calm_tree_has_empty_queue(self) -> None:
        proj = project_workspace([[_started()]], enclosures=[], providers=[], now=FRESH)
        self.assertEqual(proj.analytics.attentionQueue, [])


class GateProjectionTests(unittest.TestCase):
    """Slice 6c: durable gates materialize onto the lifecycle + the attention queue."""

    LATER = "2026-06-13T18:05:00+00:00"

    def _open(self, *, gate_id: str = "G1", ts: str = T0):
        return create_gate(
            kind="closeout-approval", lifecycle_id="LC1", gate_id=gate_id, now=ts
        )

    def test_open_gate_materializes_onto_lifecycle(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=FRESH,
            gates=[self._open()],
        )
        gate = proj.lifecycles[0].gate
        assert gate is not None
        self.assertEqual((gate.id, gate.kind, gate.state), ("G1", "closeout-approval", "open"))
        self.assertEqual(gate.decisions, ["approve", "cancel", "reject", "request-revision"])

    def test_decided_gate_is_not_attached(self) -> None:
        decided = decide_gate(
            self._open(), decision="approve", by="developer", via="dashboard",
            note=None, now=self.LATER,
        )
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=FRESH,
            gates=[decided],
        )
        self.assertIsNone(proj.lifecycles[0].gate)

    def test_latest_open_gate_wins(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=FRESH,
            gates=[self._open(gate_id="A", ts=T0), self._open(gate_id="B", ts=self.LATER)],
        )
        gate = proj.lifecycles[0].gate
        assert gate is not None
        self.assertEqual(gate.id, "B")

    def test_open_gate_adds_attention_item(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=FRESH,
            gates=[self._open()],
        )
        item = next(i for i in proj.analytics.attentionQueue if i.kind == "gate-open")
        self.assertEqual((item.severity, item.lane, item.lifecycleId), ("warn", "lifecycle", "LC1"))

    def test_no_gates_leaves_lifecycle_and_queue_clean(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=FRESH
        )
        self.assertIsNone(proj.lifecycles[0].gate)
        self.assertEqual([i for i in proj.analytics.attentionQueue if i.kind == "gate-open"], [])


class GateReaderTests(unittest.TestCase):
    def test_reads_lifecycle_and_workspace_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            store = GateStore(observer_logs_root(coord))
            store.append(
                create_gate(kind="closeout-approval", lifecycle_id="LC1", gate_id="G1", now=T0)
            )
            store.append(create_gate(kind="alarm-ack", lifecycle_id=None, gate_id="W1", now=T0))
            self.assertEqual({g.id for g in read_gates(coord)}, {"G1", "W1"})

    def test_missing_root_reads_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_gates(Path(tmp)), [])


class DriftSnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _write(self, repo: str, branch: str, counts: dict, *, schema: str = DRIFT_SNAPSHOT_SCHEMA, actionable: int = 0) -> None:  # type: ignore[type-arg]
        directory = drift_snapshot_dir(self.coord)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{repo}__{branch}.json").write_text(
            json.dumps({"schema": schema, "repository": repo, "branch": branch, "checkedAt": T0, "counts": counts, "actionableCount": actionable, "rows": []}),
            encoding="utf-8",
        )

    def test_reads_counts_and_staleness(self) -> None:
        self._write("repo-a", "main", {"up to date": 5, "drifted": 2}, actionable=2)
        nodes = read_drift_snapshots(self.coord, now=STALE)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].repository, nodes[0].branch), ("repo-a", "main"))
        self.assertEqual(nodes[0].counts["drifted"], 2)
        self.assertEqual(nodes[0].actionableCount, 2)
        self.assertEqual(nodes[0].snapshotStaleSeconds, 600.0)

    def test_skips_wrong_schema(self) -> None:
        self._write("repo-a", "main", {"drifted": 1}, schema="other/v9")
        self.assertEqual(read_drift_snapshots(self.coord, now=FRESH), [])

    def test_absent_dir_is_empty(self) -> None:
        self.assertEqual(read_drift_snapshots(self.coord, now=FRESH), [])


class SidecarStalenessReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.onb = Path(self._dir.name) / "onboarding"
        self.onb.mkdir()

    def _write(self, rel: str, *, date: str, doc_type: str = "file-level-onboarding") -> None:
        path = self.onb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"| Field | Value |\n| --- | --- |\n| doc_type | `{doc_type}` |\n| lastVerifiedCommitDate | {date} |\n",
            encoding="utf-8",
        )

    def test_reads_age_per_sidecar(self) -> None:
        self._write("a.py.md", date="2026-06-13T17:00:00+00:00")
        nodes = read_sidecar_staleness(self.onb, repository="repo-a", now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].repository, nodes[0].onboardingFile), ("repo-a", "a.py.md"))
        self.assertEqual(nodes[0].ageSeconds, 3630.0)

    def test_unparseable_date_is_none(self) -> None:
        self._write("b.py.md", date="not-a-date")
        self.assertIsNone(read_sidecar_staleness(self.onb, repository="r", now=FRESH)[0].ageSeconds)

    def test_absent_root_is_empty(self) -> None:
        self.assertEqual(read_sidecar_staleness(self.onb / "nope", repository="r", now=FRESH), [])


class SetupSummaryReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)
        self.setup = self.coord / "logs" / "providers" / "setup"
        self.setup.mkdir(parents=True)

    def test_reads_latest_summary(self) -> None:
        (self.setup / "last-setup.json").write_text(
            json.dumps({"action": "setup", "ok": True, "ready": True, "state": "ok", "generatedAt": T0, "resultCounts": {"total": 3, "ok": 3, "failed": 0, "skipped": 0}}),
            encoding="utf-8",
        )
        nodes = read_setup_summaries(self.coord, now=STALE)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].action, nodes[0].ok, nodes[0].state), ("setup", True, "ok"))
        self.assertEqual(nodes[0].resultCounts["total"], 3)
        self.assertEqual(nodes[0].snapshotStaleSeconds, 600.0)

    def test_skips_full_debug_copy(self) -> None:
        (self.setup / "last-setup.json").write_text(json.dumps({"action": "setup", "ok": True}), encoding="utf-8")
        (self.setup / "last-setup-full.json").write_text(json.dumps({"action": "setup-full"}), encoding="utf-8")
        self.assertEqual([n.action for n in read_setup_summaries(self.coord, now=FRESH)], ["setup"])


class SetupProgressReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _write(self, group: str, payload: dict) -> None:  # type: ignore[type-arg]
        directory = self.coord / "worktrees" / "repo-a" / group / "provider-runtime"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "setup-progress.json").write_text(json.dumps({"schema": PROGRESS_SCHEMA, **payload}), encoding="utf-8")

    def test_reads_finished_progress(self) -> None:
        self._write("grp-ok", {"state": "ok", "startedAt": T0, "finishedAt": "2026-06-13T18:00:05+00:00", "completedPhases": [{"provider": "cgc", "action": "setup", "ok": True}]})
        nodes = read_setup_progress_nodes(self.coord, now=FRESH)
        self.assertEqual((nodes[0].group, nodes[0].state, nodes[0].completedCount), ("grp-ok", "ok", 1))

    def test_stale_running_projects_stale(self) -> None:
        self._write("grp-run", {"state": "running", "startedAt": T0, "updatedAt": T0, "currentPhase": {"provider": "cgc", "action": "index", "startedAt": T0}, "completedPhases": []})
        node = read_setup_progress_nodes(self.coord, now=STALE)[0]
        self.assertEqual(node.state, "stale")
        self.assertEqual(node.currentPhase, "cgc index")

    def test_absent_is_empty(self) -> None:
        self.assertEqual(read_setup_progress_nodes(self.coord, now=FRESH), [])


class RouteCoverageReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.onb = Path(self._dir.name) / "onboarding"
        self.onb.mkdir()

    def _write(self, rel: str, route: str, counts: dict) -> None:  # type: ignore[type-arg]
        path = self.onb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schemaVersion": 1, "repository": "repo-a", "route": route, "coverageCounts": counts}), encoding="utf-8")

    def test_reads_coverage_per_route(self) -> None:
        self._write("overview.index.json", "", {"sourceFilesInScope": 10, "fileSidecars": 7, "childRoutes": 2})
        self._write("mcp/overview.index.json", "mcp", {"sourceFilesInScope": 5, "fileSidecars": 5, "childRoutes": 0})
        by_route = {n.route: n for n in read_route_coverage(self.onb, repository="repo-a")}
        self.assertEqual(set(by_route), {"", "mcp"})
        self.assertEqual(by_route[""].sourceFilesInScope, 10)
        self.assertEqual(by_route["mcp"].fileSidecars, 5)
        self.assertEqual(by_route[""].repository, "repo-a")


class ToolReportsReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _write(self, tool: str, name: str) -> None:
        directory = self.coord / "temp" / "tool-reports" / tool
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("{}", encoding="utf-8")

    def test_newest_report_per_tool(self) -> None:
        self._write("provider_status", "20260613T180000Z-status.json")
        self._write("provider_status", "20260613T180500Z-status.json")
        self._write("drift_check", "20260613T120000Z-report.json")
        by_tool = {n.tool: n for n in read_tool_reports(self.coord, now=datetime.now(UTC))}
        self.assertEqual(set(by_tool), {"provider_status", "drift_check"})
        self.assertTrue(by_tool["provider_status"].path.endswith("20260613T180500Z-status.json"))
        self.assertEqual(by_tool["provider_status"].label, "status")
        self.assertIsNotNone(by_tool["provider_status"].ageSeconds)

    def test_absent_is_empty(self) -> None:
        self.assertEqual(read_tool_reports(self.coord, now=FRESH), [])


class LedgerReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.mem = Path(self._dir.name)

    def test_reads_count_and_currency(self) -> None:
        ledger = prepend_mapping(create_initial_ledger("repo-a", "aaaa", "bbbb"), "cccc", "dddd")
        write_ledger(self.mem / "memory.md", ledger)
        node = read_ledger(self.mem)
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual((node.repository, node.closeoutCount, node.lastVerifiedCodeCommit), ("repo-a", 2, "cccc"))

    def test_missing_ledger_is_none(self) -> None:
        self.assertIsNone(read_ledger(self.mem / "nope"))


class DriftSnapshotProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def test_producer_write_is_readable_by_reducer(self) -> None:
        repo = (self.tmp / "repo-x").resolve()
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "feat-x", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "--allow-empty", "-m", "init"],
            check=True, capture_output=True,
        )
        coord = (self.tmp / "coord").resolve()
        onboarding = (self.tmp / "onb").resolve()
        onboarding.mkdir()
        context = SimpleNamespace(coordination_root=coord, onboarding_root=onboarding)
        rows = [
            DriftRow("onboarding/a.md", "a.py", "repo-x", "external", "h", "d", "up to date", "high", "none", "ok"),
            DriftRow("onboarding/b.md", "b.py", "repo-x", "external", "h", "d", "drifted", "medium", "logic", "changed"),
        ]
        summary._write_drift_snapshot(repo, context, rows)
        nodes = read_drift_snapshots(coord, now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].repository, nodes[0].branch), ("repo-x", "feat-x"))
        self.assertEqual(nodes[0].counts["drifted"], 1)
        self.assertEqual(nodes[0].counts["up to date"], 1)
        self.assertEqual(nodes[0].actionableCount, 1)


class ProjectAndWriteAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)
        self.coord = (self.tmp / "coord").resolve()
        self.coord.mkdir()
        self.mem = (self.tmp / "mem-repo-a").resolve()
        (self.mem / "onboarding").mkdir(parents=True)

    def _config(self) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=self.coord / "mcp.settings.json",
            coordination_root=self.coord,
            workspace_root=(self.tmp / "ws").resolve(),
            transcript_root=self.coord / "logs",
            repositories={
                "repo-a": RepositoryScope(
                    repo_id="repo-a",
                    path=(self.tmp / "ws" / "repo-a").resolve(),
                    memory_root=self.mem,
                )
            },
            providers={
                "codegraphcontext-code": ProviderScope(
                    provider_id="codegraphcontext-code",
                    runtime_root=self.coord / "rt",
                    log_root=self.coord / "lg",
                    instance_id="projects",
                    scope="workspace",
                )
            },
        )

    def test_analytics_populated_end_to_end(self) -> None:
        config = self._config()
        EventStore(observer_root(config)).append(_started(lifecycle_id="LC1", ts=T0))
        directory = drift_snapshot_dir(self.coord)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "repo-a__main.json").write_text(
            json.dumps({"schema": DRIFT_SNAPSHOT_SCHEMA, "repository": "repo-a", "branch": "main", "checkedAt": T0, "counts": {"drifted": 1}, "actionableCount": 1, "rows": []}),
            encoding="utf-8",
        )
        write_ledger(self.mem / "memory.md", prepend_mapping(create_initial_ledger("repo-a", "aaaa", "bbbb"), "cccc", "dddd"))
        (self.mem / "onboarding" / "a.py.md").write_text(
            "| Field | Value |\n| --- | --- |\n| doc_type | `file-level-onboarding` |\n| lastVerifiedCommitDate | 2026-06-13T17:00:00+00:00 |\n",
            encoding="utf-8",
        )
        write_task_doc(
            self.coord / "tasks" / "repo-a" / "demo",
            TaskDocument.model_validate({
                "id": "D", "slug": "task", "title": "Demo", "kind": "light", "repo": "repo-a",
                "createdAt": "2026-01-01T00:00", "lifecycleId": "LC1",
                "steps": [{"id": "S1", "title": "a", "status": "done"}],
            }),
        )
        proj = project_and_write(config, now=FRESH)
        self.assertEqual(len(proj.analytics.driftSnapshots), 1)
        self.assertEqual(proj.analytics.driftSnapshots[0].counts["drifted"], 1)
        self.assertEqual(proj.analytics.ledgers[0].closeoutCount, 2)
        self.assertEqual(len(proj.analytics.stalestSidecars), 1)
        self.assertEqual(proj.metrics.stalenessHistogram["<7d"], 1)
        self.assertEqual(len(proj.analytics.taskDocuments), 1)
        self.assertEqual(proj.analytics.taskDocuments[0].lifecycleId, "LC1")
        state = json.loads((observer_root(config) / "latest-state.json").read_text(encoding="utf-8"))
        self.assertIn("analytics", state)


class TaskDocumentsReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _doc(self, **over: object) -> TaskDocument:
        base: dict[str, object] = {
            "id": "D", "slug": "task", "title": "Demo", "kind": "light",
            "repo": "repo-a", "createdAt": "2026-01-01T00:00",
        }
        base.update(over)
        return TaskDocument.model_validate(base)

    def test_reads_lifecycle_keyed_progress(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "demo"
        write_task_doc(root, self._doc(lifecycleId="LC1", steps=[
            {"id": "S1", "title": "a", "status": "done"},
            {"id": "S2", "title": "b", "status": "inProgress"},
        ]))
        nodes = read_task_documents(self.coord, now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(
            (nodes[0].lifecycleId, nodes[0].stepsDone, nodes[0].stepsTotal, nodes[0].currentStep),
            ("LC1", 1, 2, "S2 — b"),
        )

    def test_skips_docs_without_lifecycle_and_non_task_json(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "demo"
        write_task_doc(root, self._doc(slug="03c_x", kind="subTask"))  # no lifecycleId
        (root / "other.json").write_text('{"schema": "other/v1"}', encoding="utf-8")
        self.assertEqual(read_task_documents(self.coord, now=FRESH), [])

    def test_master_is_not_projected_as_a_lifecycle(self) -> None:
        # A master spans the series, carries no lifecycleId (schema-enforced), and must
        # never surface as a lifecycle node in the observer projection.
        root = self.coord / "tasks" / "repo-a" / "series"
        master = TaskDocument.model_validate({
            "id": "series", "slug": "series", "title": "Series", "kind": "master",
            "repo": "repo-a", "createdAt": "2026-01-01T00:00",
            "subTasks": [{"number": "1", "name": "A", "status": "inProgress"}],
            "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
        })
        write_task_doc(root, master)
        self.assertEqual(read_task_documents(self.coord, now=FRESH), [])

    def test_missing_tasks_dir_is_empty(self) -> None:
        self.assertEqual(read_task_documents(self.coord / "nope", now=FRESH), [])

    def test_build_analytics_includes_task_documents(self) -> None:
        node = TaskDocNode(
            lifecycleId="LC1", repository="repo-a", title="t", status="planning",
            kind="light", docPath="p",
        )
        analytics = build_analytics(
            drift_snapshots=[], sidecar_staleness=[], setup_summaries=[], setup_progress=[],
            route_coverage=[], tool_reports=[], ledgers=[], task_documents=[node],
        )
        self.assertEqual(len(analytics.taskDocuments), 1)
        self.assertEqual(analytics.taskDocuments[0].lifecycleId, "LC1")


def _action(actions: list, name: str):  # type: ignore[type-arg]
    return next(action for action in actions if action.action == name)


def _facts(
    *,
    contract: dict | None = None,  # type: ignore[type-arg]
    status: dict | None = None,  # type: ignore[type-arg]
    guidance: dict | None = None,  # type: ignore[type-arg]
) -> EngineProcessFacts:
    base_contract: dict[str, object] = {
        "contract_path": "/c.md",
        "task_id": "T",
        "task_name": "demo",
        "repo_name": "r",
        "worktree_group": "/w/r/grp",
        "memory_mode": "external",
        "code_source_branch": "main",
        "code_base_commit": "abc1234",
        "code_repo_path": "/repo",
        "code_work_branch": "ar/x",
        "code_worktree": "/w/r/grp/wt",
        "code_commit": "",
        "memory_source_branch": "main",
        "memory_base_commit": "def5678",
        "memory_repo_path": "/mrepo",
        "memory_work_branch": "ar/x",
        "memory_content_commit": "",
        "memory_worktree": "/w/r/grp/mem",
        "ledger_path": "/w/r/grp/mem/memory.md",
        "human_review_status": "pending-review",
        "closeout_status": "not-started",
        "integration_status": "not-started",
        "cleanup": "pending",
        "lifecycle_id": "LC1",
    }
    base_contract.update(contract or {})
    base_guidance: dict[str, object] = {
        "phase": "worktree-started",
        "summary": "continue",
        "nextOperation": "continue_work",
    }
    base_guidance.update(guidance or {})
    return EngineProcessFacts(contract=base_contract, guidance=base_guidance, status=status)


class EngineProcessTests(unittest.TestCase):
    """The slice-5e enclosure-centered process map (``build_engine_processes``)."""

    def test_successful_bootstrap_is_observed_and_complete(self) -> None:
        facts = _facts(
            status={
                "code_worktree_exists": True,
                "code_worktree_dirty": False,
                "memory_worktree_exists": True,
                "memory_worktree_dirty": False,
                "freshness": {"state": "current", "code": {"baseBehindSource": 0}},
                "providers": {
                    "state": "ok",
                    "completedPhases": ["codegraphcontext-code seed: ok", "grepai-memory clone: ok"],
                    "failedPhases": [],
                },
            }
        )
        cgc = ProviderNode(
            id="codegraphcontext-code@grp", state="configured", ok=True,
            scope="worktree", role="code", worktreeGroup="grp",
        )
        grepai = ProviderNode(
            id="grepai-memory@grp", state="configured", ok=True,
            scope="worktree", role="memory", worktreeGroup="grp",
        )
        nodes = build_engine_processes(
            [facts], [], [grepai, cgc], [SetupProgressNode(group="grp", state="ok", completedCount=4)]
        )
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.health, "nominal")
        self.assertEqual(node.codeWorktree.factState, "observed")
        self.assertEqual(node.memoryWorktree.factState, "observed")  # type: ignore[union-attr]
        self.assertEqual([p.role for p in node.providers], ["code", "memory"])  # code before memory
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["worktree-add"], "complete")
        self.assertEqual(states["cgc-seed"], "complete")
        self.assertEqual(states["grepai-clone"], "complete")
        self.assertEqual(node.missingFacts, [])

    def test_provider_setup_running(self) -> None:
        facts = _facts(status={"code_worktree_exists": True, "memory_worktree_exists": True})
        setup = SetupProgressNode(
            group="grp", state="running", currentPhase="grepai-memory clone", heartbeatAgeSeconds=2.0
        )
        node = build_engine_processes([facts], [], [], [setup])[0]
        self.assertEqual(node.phase, "provider-setup")
        self.assertEqual(node.health, "running")
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["cgc-seed"], "running")
        self.assertEqual(states["grepai-clone"], "running")

    def test_failed_setup_marks_failed(self) -> None:
        setup = SetupProgressNode(
            group="grp", state="failed", failedPhases=["grepai-memory clone: failed (stalled)"]
        )
        node = build_engine_processes(
            [_facts(status={"code_worktree_exists": True})], [], [], [setup]
        )[0]
        self.assertEqual(node.health, "failed")
        self.assertEqual({e.kind: e.state for e in node.edges}["grepai-clone"], "failed")

    def test_missing_status_degrades_without_crashing(self) -> None:
        node = build_engine_processes([_facts(status=None)], [], [], [])[0]
        self.assertEqual(node.codeWorktree.factState, "missing")
        self.assertTrue(any("not observed" in fact for fact in node.missingFacts))

    def test_disabled_memory_has_no_memory_lane(self) -> None:
        node = build_engine_processes(
            [_facts(contract={"memory_mode": "disabled"}, status={"code_worktree_exists": True})],
            [], [], [],
        )[0]
        self.assertIsNone(node.memorySource)
        self.assertIsNone(node.memoryWorktree)
        self.assertEqual(node.memoryMode, "disabled")
        self.assertNotIn("grepai-clone", {edge.kind for edge in node.edges})

    def test_sync_needed_when_behind_official(self) -> None:
        facts = _facts(
            status={
                "code_worktree_exists": True,
                "freshness": {"state": "behind-official", "code": {"baseBehindSource": 3}},
            }
        )
        node = build_engine_processes([facts], [], [], [])[0]
        self.assertEqual(node.phase, "sync-needed")
        self.assertEqual(node.health, "blocked")
        self.assertEqual(node.codeSource.behindSource, 3)
        self.assertIn("sync", {edge.kind for edge in node.edges})

    def test_join_uses_worktree_group_basename(self) -> None:
        facts = _facts(contract={"worktree_group": "/w/r/260610-grp"}, status={"code_worktree_exists": True})
        prov = ProviderNode(
            id="cgc@260610-grp", state="configured", ok=True,
            scope="worktree", role="code", worktreeGroup="260610-grp",
        )
        node = build_engine_processes([facts], [], [prov], [])[0]
        self.assertEqual([p.id for p in node.providers], ["cgc@260610-grp"])

    def test_actions_reuse_precomputed_enclosure_actions(self) -> None:
        enc = _enclosure(closeoutStatus="completed", integrationStatus="not-started")
        enriched = [enc.model_copy(update={"actions": enclosure_actions(enc)})]
        node = build_engine_processes(
            [_facts(status={"code_worktree_exists": True})], enriched, [], []
        )[0]
        self.assertTrue(any(action.action == "integrate" for action in node.actions))

    def test_deterministic(self) -> None:
        args = ([_facts(status={"code_worktree_exists": True})], [], [], [])
        self.assertEqual(
            build_engine_processes(*args)[0].model_dump(),
            build_engine_processes(*args)[0].model_dump(),
        )

    def test_project_workspace_wires_engine_processes(self) -> None:
        proj = project_workspace(
            [], enclosures=[], providers=[], now=FRESH,
            engine_process_facts=[_facts(status={"code_worktree_exists": True})],
        )
        self.assertEqual(len(proj.analytics.engineProcesses), 1)
        self.assertEqual(proj.version, 2)

    def test_3a_callers_get_empty_engine_processes(self) -> None:
        proj = project_workspace([[_started()]], enclosures=[], providers=[], now=FRESH)
        self.assertEqual(proj.analytics.engineProcesses, [])

    def test_reader_emits_one_fact_per_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                task_name="demo task", repo_name="r", workflow_kind="light", memory_mode="disabled",
                coordination_root=root, code_repo_path=root / "repo",
                code_source_branch="main", code_work_branch="ar/x", code_base_commit="abc",
                worktree_name="demo-wt",
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)
            facts = read_engine_process_facts(root)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].contract["task_name"], "demo task")
            self.assertIn("phase", facts[0].guidance)

    def test_start_progress_synthesizes_pre_contract_node(self) -> None:
        entry = {
            "schema": "ar-worktree-start-progress/v1",
            "repoName": "agents-remember",
            "taskName": "dm-v1.2",
            "worktreeName": "v12-feat",
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "phase": "memory-blocked",
            "memoryMode": "external",
            "codeSourceBranch": "main",
            "codeBaseCommit": "abc1234",
            "blockedReason": "no exact ledger mapping for selected code base commit",
            "completedPhases": ["preflight", "code-worktree"],
            "choices": ["reconciliation", "disabled-memory", "custom"],
            "sourceFile": "/w/temp/worktree-start/agents-remember/v12-feat.json",
        }
        nodes = build_engine_processes([], [], [], [], [entry])
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.phase, "memory-compatibility")
        self.assertEqual(node.health, "blocked")
        self.assertEqual(node.codeWorktree.factState, "observed")  # code-worktree completed
        self.assertEqual(node.memoryWorktree.factState, "missing")  # type: ignore[union-attr]
        self.assertTrue(any("contract not yet written" in fact for fact in node.missingFacts))
        self.assertEqual(node.nextAction, "reconciliation")

    def test_start_progress_skipped_when_contract_covers_the_group(self) -> None:
        facts = _facts(
            contract={"worktree_group": "/w/agents-remember/v12-feat-ar"},
            status={"code_worktree_exists": True},
        )
        entry = {
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "phase": "memory-blocked",
            "memoryMode": "external",
            "sourceFile": "x",
        }
        nodes = build_engine_processes([facts], [], [], [], [entry])
        self.assertEqual(len(nodes), 1)  # only the contract node — not double-rendered

    def test_start_progress_write_read_clear_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_start_progress(
                root, repo_name="r", task_name="t", worktree_name="wt",
                worktree_group="/w/r/wt-ar", phase="memory-blocked", memory_mode="external",
                blocked_reason="no ledger mapping", completed_phases=("preflight", "code-worktree"),
                choices=("reconciliation",),
            )
            payload = read_start_progress(start_progress_path(root, "r", "wt"))
            assert payload is not None
            self.assertEqual(payload["phase"], "memory-blocked")
            self.assertEqual(payload["blockedReason"], "no ledger mapping")
            entries = read_start_progress_entries(root, now=FRESH)
            self.assertEqual(len(entries), 1)
            self.assertIn("ageSeconds", entries[0])
            clear_start_progress(root, "r", "wt")
            self.assertIsNone(read_start_progress(start_progress_path(root, "r", "wt")))
            self.assertEqual(read_start_progress_entries(root, now=FRESH), [])

    def test_blocked_start_raises_attention_parity(self) -> None:
        # §9: a pre-contract blocked start raises the same master-caution the agent raises in chat.
        blocked = {
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "repoName": "agents-remember",
            "phase": "memory-blocked",
            "blockedReason": "no exact ledger mapping for selected code base commit",
        }
        happy = {"worktreeGroup": "/w/agents-remember/dm-ar", "phase": "code-worktree"}
        items = build_attention_queue([], [], [], [], [blocked, happy])
        self.assertEqual(len(items), 1)  # only the blocked start is an alarm
        item = items[0]
        self.assertEqual(item.kind, "blocked-start")
        self.assertEqual(item.id, "blocked-start:v12-feat-ar")
        self.assertEqual(item.severity, "warn")
        self.assertEqual(item.lane, "worktree")
        self.assertEqual(item.detail, "no exact ledger mapping for selected code base commit")
        self.assertEqual(item.repoId, "agents-remember")

    def test_project_workspace_threads_blocked_start_into_attention(self) -> None:
        # §9 wiring: project_workspace must thread engine_start_progress into the attention queue.
        blocked = {
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "repoName": "agents-remember",
            "phase": "memory-blocked",
            "blockedReason": "no ledger mapping",
        }
        proj = project_workspace(
            [], enclosures=[], providers=[], now=FRESH, engine_start_progress=[blocked]
        )
        kinds = [item.kind for item in proj.analytics.attentionQueue]
        self.assertIn("blocked-start", kinds)

    def test_happy_path_start_progress_is_observable_but_not_an_alarm(self) -> None:
        # §9 gap (a): a happy-path pre-contract beat (no blockedReason) is observable as a synthesized
        # node, but raises no attention item -- only blocked starts are alarms.
        happy = {
            "worktreeGroup": "/w/agents-remember/dm-ar",
            "repoName": "agents-remember",
            "taskName": "dm",
            "phase": "code-worktree",
            "memoryMode": "external",
            "completedPhases": ["preflight"],
        }
        nodes = build_engine_processes([], [], [], [], [happy])
        self.assertEqual(len(nodes), 1)  # observable as a (non-blocked) synthesized node
        self.assertEqual(build_attention_queue([], [], [], [], [happy]), [])  # not an alarm


if __name__ == "__main__":
    unittest.main()
