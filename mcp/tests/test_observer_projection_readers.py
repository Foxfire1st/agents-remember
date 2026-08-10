from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.records import GateAnchor, GateVerdict, create_gate, decide_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.observer.reducer import AnalyticalInputs, WorkspaceStructure, project_workspace
from agents_remember.providers.setup_progress import PROGRESS_SCHEMA
from agents_remember.serving.projections.paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
    observer_logs_root,
)
from agents_remember.serving.projections.snapshots import (
    read_drift_snapshots,
    read_gates,
    read_route_coverage,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_sidecar_staleness,
    read_tool_reports,
)
from test_observer_projection import FRESH, STALE, T0, _event, _started


class GateProjectionTests(unittest.TestCase):
    """Slice 6c: durable gates materialize onto the lifecycle + the attention queue."""

    LATER = "2026-06-13T18:05:00+00:00"

    def _open(self, *, gate_id: str = "G1", ts: str = T0):
        return create_gate(
            "closeout-approval", gate_id=gate_id, now=ts, anchor=GateAnchor(lifecycle_id="LC1")
        )

    def test_open_gate_materializes_onto_lifecycle(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(gates=[self._open()]),
        )
        gate = proj.lifecycles[0].gate
        assert gate is not None
        self.assertEqual((gate.id, gate.kind, gate.state), ("G1", "closeout-approval", "open"))
        self.assertEqual(gate.decisions, ["approve", "cancel", "reject", "request-revision"])

    def test_decided_gate_is_not_attached(self) -> None:
        decided = decide_gate(
            self._open(),
            GateVerdict(decision="approve", by="developer", via="dashboard", note=None),
            now=self.LATER,
        )
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(gates=[decided]),
        )
        self.assertIsNone(proj.lifecycles[0].gate)

    def test_latest_open_gate_wins(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(
                gates=[self._open(gate_id="A", ts=T0), self._open(gate_id="B", ts=self.LATER)]
            ),
        )
        gate = proj.lifecycles[0].gate
        assert gate is not None
        self.assertEqual(gate.id, "B")

    def test_open_gate_adds_attention_item(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(gates=[self._open()]),
        )
        item = next(i for i in proj.analytics.attentionQueue if i.kind == "gate-open")
        self.assertEqual((item.severity, item.lane, item.lifecycleId), ("warn", "lifecycle", "LC1"))

    def test_no_gates_leaves_lifecycle_and_queue_clean(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertIsNone(proj.lifecycles[0].gate)
        self.assertEqual([i for i in proj.analytics.attentionQueue if i.kind == "gate-open"], [])

    def _blocked_log(self) -> list:  # type: ignore[type-arg]
        return [
            _started(lifecycle_id="LC1"),
            _event(
                "lifecycle.blocked",
                lifecycle_id="LC1",
                ts="2026-06-13T18:00:05+00:00",
                ask={"kind": "decision", "question": "Approve the plan?"},
            ),
        ]

    def test_blocked_with_open_gate_dedups_to_gate_open(self) -> None:
        # The gate-open/blocked-gate double-emission fix: a blocked lifecycle that
        # also has a durable open gate (the lifecycle_gate path: block() + GateRecord)
        # yields ONE lifecycle-lane item -- the gate-open -- not two.
        proj = project_workspace(
            [self._blocked_log()],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(gates=[self._open()]),
        )
        lane_items = [i for i in proj.analytics.attentionQueue if i.lane == "lifecycle"]
        self.assertEqual(len(lane_items), 1)
        self.assertEqual(lane_items[0].kind, "gate-open")
        self.assertEqual([i for i in proj.analytics.attentionQueue if i.kind == "blocked-gate"], [])

    def test_bare_block_without_gate_still_yields_blocked_gate(self) -> None:
        # PARK, not delete: a bare block() with no GateRecord still raises blocked-gate.
        proj = project_workspace(
            [self._blocked_log()],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        kinds = [i.kind for i in proj.analytics.attentionQueue]
        self.assertEqual(kinds, ["blocked-gate"])


class GateReaderTests(unittest.TestCase):
    def test_reads_lifecycle_and_workspace_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            store = GateStore(observer_logs_root(coord))
            store.append(
                create_gate(
                    "closeout-approval", gate_id="G1", now=T0, anchor=GateAnchor(lifecycle_id="LC1")
                )
            )
            store.append(
                create_gate("alarm-ack", gate_id="W1", now=T0, anchor=GateAnchor(lifecycle_id=None))
            )
            self.assertEqual({g.id for g in read_gates(coord)}, {"G1", "W1"})

    def test_missing_root_reads_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_gates(Path(tmp)), [])


class DriftSnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _write(
        self,
        repo: str,
        branch: str,
        counts: dict,
        *,
        schema: str = DRIFT_SNAPSHOT_SCHEMA,
        actionable: int = 0,
    ) -> None:  # type: ignore[type-arg]
        directory = drift_snapshot_dir(self.coord)
        directory.mkdir(parents=True, exist_ok=True)
        drift_snapshot_path(self.coord, repository=repo, branch=branch).write_text(
            json.dumps(
                {
                    "schema": schema,
                    "repository": repo,
                    "branch": branch,
                    "checkedAt": T0,
                    "counts": counts,
                    "actionableCount": actionable,
                    "rows": [],
                }
            ),
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
            json.dumps(
                {
                    "action": "setup",
                    "ok": True,
                    "ready": True,
                    "state": "ok",
                    "generatedAt": T0,
                    "resultCounts": {"total": 3, "ok": 3, "failed": 0, "skipped": 0},
                }
            ),
            encoding="utf-8",
        )
        nodes = read_setup_summaries(self.coord, now=STALE)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].action, nodes[0].ok, nodes[0].state), ("setup", True, "ok"))
        self.assertEqual(nodes[0].resultCounts["total"], 3)
        self.assertEqual(nodes[0].snapshotStaleSeconds, 600.0)

    def test_skips_full_debug_copy(self) -> None:
        (self.setup / "last-setup.json").write_text(
            json.dumps({"action": "setup", "ok": True}), encoding="utf-8"
        )
        (self.setup / "last-setup-full.json").write_text(
            json.dumps({"action": "setup-full"}), encoding="utf-8"
        )
        self.assertEqual([n.action for n in read_setup_summaries(self.coord, now=FRESH)], ["setup"])


class SetupProgressReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _write(self, group: str, payload: dict) -> None:  # type: ignore[type-arg]
        directory = self.coord / "worktrees" / "repo-a" / group / "provider-runtime"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "setup-progress.json").write_text(
            json.dumps({"schema": PROGRESS_SCHEMA, **payload}), encoding="utf-8"
        )

    def test_reads_finished_progress(self) -> None:
        self._write(
            "grp-ok",
            {
                "state": "ok",
                "startedAt": T0,
                "finishedAt": "2026-06-13T18:00:05+00:00",
                "completedPhases": [{"provider": "cgc", "action": "setup", "ok": True}],
            },
        )
        nodes = read_setup_progress_nodes(self.coord, now=FRESH)
        self.assertEqual(
            (nodes[0].group, nodes[0].state, nodes[0].completedCount), ("grp-ok", "ok", 1)
        )

    def test_stale_running_projects_stale(self) -> None:
        self._write(
            "grp-run",
            {
                "state": "running",
                "startedAt": T0,
                "updatedAt": T0,
                "currentPhase": {"provider": "cgc", "action": "index", "startedAt": T0},
                "completedPhases": [],
            },
        )
        node = read_setup_progress_nodes(self.coord, now=STALE)[0]
        self.assertEqual(node.state, "stale")
        self.assertEqual(node.currentPhase, "cgc index")

    def test_active_group_filter_skips_parked_progress(self) -> None:
        self._write(
            "grp-parked",
            {
                "state": "running",
                "startedAt": T0,
                "updatedAt": T0,
                "currentPhase": {"provider": "cgc", "action": "index", "startedAt": T0},
                "completedPhases": [],
            },
        )

        nodes = read_setup_progress_nodes(self.coord, now=FRESH, active_worktree_groups=set())

        self.assertEqual(nodes, [])

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
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "repository": "repo-a",
                    "route": route,
                    "coverageCounts": counts,
                }
            ),
            encoding="utf-8",
        )

    def test_reads_coverage_per_route(self) -> None:
        self._write(
            "overview.index.json",
            "",
            {"sourceFilesInScope": 10, "fileSidecars": 7, "childRoutes": 2},
        )
        self._write(
            "mcp/overview.index.json",
            "mcp",
            {"sourceFilesInScope": 5, "fileSidecars": 5, "childRoutes": 0},
        )
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
