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
    observer_root,
)
from agents_remember.observer.projection import (
    DriftSnapshotNode,
    EnclosureNode,
    ProviderNode,
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
    enclosure_actions,
    project_lifecycle,
    project_workspace,
    staleness_histogram,
    token_series,
)
from agents_remember.observer.snapshots import (
    read_drift_snapshots,
    read_enclosures,
    read_ledger,
    read_providers,
    read_route_coverage,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_sidecar_staleness,
    read_task_documents,
    read_tool_reports,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.current_state import current_state_path
from agents_remember.providers.setup_progress import PROGRESS_SCHEMA
from agents_remember.tasks import TaskDocument, write_task_doc
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
        self.assertEqual(len(proj.lifecycles), 2)
        self.assertEqual(proj.metrics.lifecycleCount, 2)
        self.assertEqual(proj.metrics.runningCount, 1)
        self.assertEqual(proj.metrics.blockedCount, 1)
        self.assertEqual(proj.generatedAt, FRESH.isoformat())
        self.assertTrue(_action(proj.enclosures[0].actions, "integrate").enabled)


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


if __name__ == "__main__":
    unittest.main()
