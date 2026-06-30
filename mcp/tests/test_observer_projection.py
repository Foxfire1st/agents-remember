"""Tests for the observer projection read side (slice 3a).

Covers the pure fold (``project_lifecycle``) and its determinism, the inferred
layer (stale -> paused, dormant fleeting -> abandoned, terminal preserved),
append-only corrections, precomputed action availability, the workspace tree
assembly + metrics, the atomic projection write, and the structural surface
readers (provider current-state + worktree enclosures).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
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
from agents_remember.observer.drift_snapshots import drift_snapshot_path
from agents_remember.observer.events import Event
from agents_remember.observer.paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
    observer_logs_root,
    observer_root,
)
from agents_remember.observer.projection import (
    LEDGER_WINDOW,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    LedgerRefNode,
    ProviderNode,
    SeriesNode,
    SeriesSubTaskNode,
    SetupProgressNode,
    SidecarStaleNode,
    TaskDocNode,
    WorkspaceProjection,
)
from agents_remember.observer.projection_store import (
    _gather_repo_surfaces_cached,
    _repo_surface_cache,
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
    _git_commit_meta,
    _inspect_result_map,
    _ledger_window,
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
    read_gates,
    read_ledger,
    read_providers,
    read_route_coverage,
    read_series_documents,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_sidecar_staleness,
    read_start_progress_entries,
    read_task_documents,
    read_tool_reports,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.observer.worktree_provider_admission import (
    active_enclosure_worktree_groups,
    admitted_worktree_groups,
    series_retained_lifecycle_ids,
)
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


class WorktreeProviderAdmissionTests(unittest.TestCase):
    def test_admits_active_enclosure_backed_build_lifecycle(self) -> None:
        enclosures = [
            _enclosure(
                lifecycleId="LC1",
                worktreeGroup="/coord/worktrees/repo/active-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            )
        ]
        logs = [[_started(phase="build", lifecycle_id="LC1")]]

        self.assertEqual(admitted_worktree_groups(enclosures, logs, now=FRESH), {"active-ar"})

    def test_rejects_parked_terminal_and_non_provider_phase_groups(self) -> None:
        enclosures = [
            _enclosure(
                lifecycleId="LC1",
                worktreeGroup="/coord/worktrees/repo/active-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC2",
                worktreeGroup="/coord/worktrees/repo/parked-ar",
                closeoutStatus="completed",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC3",
                worktreeGroup="/coord/worktrees/repo/terminal-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC4",
                worktreeGroup="/coord/worktrees/repo/close-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            ),
        ]
        logs = [
            [_started(phase="build", lifecycle_id="LC1")],
            [_started(phase="build", lifecycle_id="LC2")],
            [
                _started(phase="build", lifecycle_id="LC3"),
                _event("lifecycle.ended", lifecycle_id="LC3", outcome="completed"),
            ],
            [
                _started(phase="build", lifecycle_id="LC4"),
                _event("lifecycle.phase-changed", lifecycle_id="LC4", phase="close"),
            ],
        ]

        self.assertEqual(admitted_worktree_groups(enclosures, logs, now=FRESH), {"active-ar"})

    def test_active_enclosure_groups_keep_nonterminal_close_phase_for_engine_room(self) -> None:
        enclosures = [
            _enclosure(
                lifecycleId="LC1",
                worktreeGroup="/coord/worktrees/repo/close-ar",
                closeoutStatus="completed",
                integrationStatus="not-started",
                cleanup="pending",
            ),
            _enclosure(
                lifecycleId="LC2",
                worktreeGroup="/coord/worktrees/repo/done-ar",
                closeoutStatus="completed",
                integrationStatus="completed",
                cleanup="pending",
            ),
        ]
        logs = [
            [
                _started(phase="build", lifecycle_id="LC1"),
                _event("lifecycle.phase-changed", lifecycle_id="LC1", phase="close"),
            ],
            [
                _started(phase="build", lifecycle_id="LC2"),
                _event("lifecycle.ended", lifecycle_id="LC2", outcome="completed"),
            ],
        ]

        self.assertEqual(active_enclosure_worktree_groups(enclosures, logs, now=FRESH), {"close-ar"})

    def test_active_group_survives_a_pruned_lifecycle_log(self) -> None:
        # The regression: a running worktree (cleanup pending) whose lifecycle event log was retired for
        # inactivity must STILL be active — admission keys on the durable enclosure, not the prunable
        # log. Both the Engine Room set and the provider set recover the live group with NO log present.
        enclosures = [
            _enclosure(
                lifecycleId="LCGONE",
                worktreeGroup="/coord/worktrees/repo/live-ar",
                closeoutStatus="not-started",
                integrationStatus="not-started",
                cleanup="pending",
            )
        ]
        logs: list[list[Event]] = []  # the log was pruned -> no events project for LCGONE
        self.assertEqual(active_enclosure_worktree_groups(enclosures, logs, now=FRESH), {"live-ar"})
        self.assertEqual(admitted_worktree_groups(enclosures, logs, now=FRESH), {"live-ar"})


class SeriesRetentionTests(unittest.TestCase):
    """`series_retained_lifecycle_ids`: a master series' events survive until the series is retired."""

    def _leaf(
        self, lifecycle_id: str, master: str, cleanup: str, *, enclosure: str = "/c.md", repo: str = "r"
    ) -> EnclosureNode:
        return _enclosure(
            lifecycleId=lifecycle_id,
            taskName=master,
            repoName=repo,
            cleanup=cleanup,
            enclosure=enclosure,
        )

    def test_live_master_protects_every_leaf_including_archived_siblings(self) -> None:
        enclosures = [
            self._leaf("LCA", "260628_x", "pending"),
            self._leaf("LCB", "260628_x", "completed"),  # archived sibling of a LIVE master
            self._leaf("LCC", "260628_y", "pending"),  # a different, also-live master
        ]
        self.assertEqual(
            series_retained_lifecycle_ids(enclosures, now=FRESH), {"LCA", "LCB", "LCC"}
        )

    def test_fully_archived_master_without_readable_timestamp_is_released(self) -> None:
        enclosures = [
            self._leaf("LCA", "260628_x", "completed", enclosure="/does-not-exist.md"),
            self._leaf("LCB", "260628_x", "abandoned", enclosure="/nope.md"),
        ]
        self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), set())

    def test_archived_master_is_retained_within_grace_then_released_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = Path(tmp) / "series-contract.md"
            contract.write_text("x", encoding="utf-8")
            enclosures = [self._leaf("LCA", "260628_z", "completed", enclosure=str(contract))]

            # Finalized one day before now -> inside the one-week grace -> still retained.
            within = (FRESH - timedelta(days=1)).timestamp()
            os.utime(contract, (within, within))
            self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), {"LCA"})

            # Finalized eight days before now -> past the grace -> released for pruning.
            past = (FRESH - timedelta(days=8)).timestamp()
            os.utime(contract, (past, past))
            self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), set())

    def test_enclosure_without_taskname_is_not_series_protected(self) -> None:
        # A fleeting/standalone enclosure (no master task) keeps the ordinary inactivity TTL.
        enclosures = [self._leaf("LCA", "", "pending")]
        self.assertEqual(series_retained_lifecycle_ids(enclosures, now=FRESH), set())


class FoldTests(unittest.TestCase):
    def test_seed_from_started(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual(
            (proj.id, proj.state, proj.phase, proj.fleeting), ("LC1", "running", "request", True)
        )
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
        log = [
            _started(),
            _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "decision"}),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual(proj.state, "blocked")
        self.assertEqual(proj.ask, {"kind": "decision"})

    def test_awaiting_developer_then_resume(self) -> None:
        # NOTIFY-AND-CONTINUE turn end (leaf-28): non-terminal awaiting-developer
        # carries the summary on the ask carrier; resume clears it back to running.
        await_only = project_lifecycle(
            [
                _started(),
                _event(
                    "lifecycle.awaiting-developer",
                    ts="2026-06-13T18:00:10+00:00",
                    summary="Turn complete; your move.",
                ),
            ],
            now=FRESH,
        )
        self.assertEqual(await_only.state, "awaiting-developer")
        self.assertEqual(await_only.ask, {"summary": "Turn complete; your move."})
        resumed = project_lifecycle(
            [
                _started(),
                _event("lifecycle.awaiting-developer", ts="2026-06-13T18:00:10+00:00", summary="s"),
                _event("lifecycle.resumed", ts="2026-06-13T18:00:20+00:00"),
            ],
            now=FRESH,
        )
        self.assertEqual(resumed.state, "running")
        self.assertIsNone(resumed.ask)

    def test_tokens_aggregate(self) -> None:
        log = [
            _started(),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:05+00:00",
                trust="observed",
                tool="a",
                tokens=100,
                ok=True,
            ),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:10+00:00",
                trust="observed",
                tool="b",
                tokens=50,
                ok=True,
            ),
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
        done = project_lifecycle(
            [
                _started(),
                _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed"),
            ],
            now=FRESH,
        )
        dropped = project_lifecycle(
            [
                _started(),
                _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="abandoned"),
            ],
            now=FRESH,
        )
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
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:10+00:00",
                trust="observed",
                tool="a",
                tokens=7,
                ok=True,
            ),
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
            _event(
                "lifecycle.promoted",
                ts="2026-06-13T18:00:05+00:00",
                trust="observed",
                actor="system",
                scope="repo-a",
            ),
        ]
        proj = project_lifecycle(log, now=DORMANT)
        self.assertEqual(proj.state, "paused")  # stale, but never auto-abandoned
        self.assertTrue(proj.inferred)

    def test_terminal_survives_staleness(self) -> None:
        proj = project_lifecycle(
            [
                _started(),
                _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed"),
            ],
            now=DORMANT,
        )
        self.assertEqual(proj.state, "completed")
        self.assertFalse(proj.inferred)


class CorrectionTests(unittest.TestCase):
    def test_correction_overrides_state(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        correction = _event(
            "correction.recorded",
            ts="2026-06-13T18:00:10+00:00",
            trust="inferred",
            actor="system",
            corrects=ended.id,
            state="abandoned",
        )
        proj = project_lifecycle([_started(), ended, correction], now=FRESH)
        self.assertEqual(proj.state, "abandoned")

    def test_malformed_correction_ignored(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        bogus = _event(
            "correction.recorded",
            ts="2026-06-13T18:00:10+00:00",
            trust="inferred",
            actor="system",
            corrects=ended.id,
            state="not-a-state",
        )
        proj = project_lifecycle([_started(), ended, bogus], now=FRESH)
        self.assertEqual(proj.state, "completed")


class ActionAvailabilityTests(unittest.TestCase):
    def test_resume_enabled_only_when_blocked(self) -> None:
        blocked = project_lifecycle(
            [
                _started(),
                _event(
                    "lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"}
                ),
            ],
            now=FRESH,
        )
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
    def test_active_worktree_groups_passthrough_sorted(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            enclosures=[_enclosure()],
            providers=[],
            now=FRESH,
            active_worktree_groups=["b-ar", "a-ar"],
        )
        # The Topology active set is exposed deterministically (sorted) on the projection.
        self.assertEqual(proj.activeWorktreeGroups, ["a-ar", "b-ar"])

    def test_active_worktree_groups_default_empty(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            enclosures=[_enclosure()],
            providers=[],
            now=FRESH,
        )
        self.assertEqual(proj.activeWorktreeGroups, [])

    def test_tree_and_metrics(self) -> None:
        logs = [
            [_started(lifecycle_id="LC1", ts=T0)],
            [
                _started(lifecycle_id="LC2", ts=T0),
                _event(
                    "lifecycle.blocked",
                    lifecycle_id="LC2",
                    ts="2026-06-13T18:00:05+00:00",
                    ask={"kind": "question"},
                ),
            ],
        ]
        proj = project_workspace(
            logs,
            enclosures=[_enclosure()],
            providers=[
                ProviderNode(
                    id="cgc", state="ready", ok=True, watcherUp=True, indexingState="indexed"
                )
            ],
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
            (
                synthesized.id,
                synthesized.fleeting,
                synthesized.inferred,
                synthesized.phase,
                synthesized.lastEventTs,
            ),
            ("r/t", False, True, "close", ""),
        )

    def test_series_token_total_sums_linked_leaf_lifecycles(self) -> None:
        logs = [
            [
                _started(lifecycle_id="LC1", ts=T0),
                _event(
                    "tool.completed",
                    lifecycle_id="LC1",
                    ts="2026-06-13T18:00:05+00:00",
                    tool="task_doc",
                    tokens=100,
                    ok=True,
                ),
            ],
            [
                _started(lifecycle_id="LC2", ts=T0),
                _event(
                    "tool.completed",
                    lifecycle_id="LC2",
                    ts="2026-06-13T18:00:06+00:00",
                    tool="read_ar_files",
                    tokens=50,
                    ok=True,
                ),
            ],
        ]
        task_documents = [
            TaskDocNode(
                id="1",
                lifecycleId="LC1",
                repository="repo-a",
                title="Leaf A",
                status="inProgress",
                kind="subTask",
                docPath="/tasks/repo-a/series/01_a.json",
            ),
            TaskDocNode(
                id="2",
                lifecycleId="LC2",
                repository="repo-a",
                title="Leaf B",
                status="inProgress",
                kind="subTask",
                docPath="/tasks/repo-a/series/02_b.json",
            ),
        ]
        series = [
            SeriesNode(
                seriesId="series",
                repository="repo-a",
                title="Series",
                status="inProgress",
                docPath="/tasks/repo-a/series/task.json",
                subTasks=[
                    SeriesSubTaskNode(
                        number="1", name="Leaf A", file="01_a.md", status="inProgress"
                    ),
                    SeriesSubTaskNode(
                        number="2", name="Leaf B", file="02_b.md", status="inProgress"
                    ),
                    SeriesSubTaskNode(
                        number="3", name="Missing doc", file="03_c.md", status="planning"
                    ),
                ],
            )
        ]

        proj = project_workspace(
            logs,
            enclosures=[],
            providers=[],
            now=FRESH,
            task_documents=task_documents,
            series=series,
        )

        self.assertEqual(proj.analytics.series[0].seriesTokenTotal, 150)

    def test_persistent_synthesis_skips_enclosure_with_event_lifecycle(self) -> None:
        # An enclosure already represented by an event-backed lifecycle is not duplicated.
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0)]],
            enclosures=[_enclosure(lifecycleId="LC1")],
            providers=[],
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_stale_persistent_lifecycle_without_enclosure_is_removed(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        trust="observed",
                        actor="system",
                        enclosure="/deleted.md",
                        repo_id="repo-a",
                    ),
                ]
            ],
            enclosures=[],
            providers=[],
            now=STALE,
        )
        self.assertEqual(proj.lifecycles, [])
        self.assertEqual(proj.metrics.lifecycleCount, 0)

    def test_terminal_persistent_lifecycle_without_enclosure_is_removed(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        trust="observed",
                        actor="system",
                        enclosure="/deleted.md",
                        repo_id="repo-a",
                    ),
                    _event(
                        "lifecycle.ended",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:10+00:00",
                        outcome="completed",
                    ),
                ]
            ],
            enclosures=[],
            providers=[],
            now=FRESH,
        )
        self.assertEqual(proj.lifecycles, [])
        self.assertEqual(proj.metrics.lifecycleCount, 0)

    def test_fresh_promotion_window_without_enclosure_is_kept(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        trust="observed",
                        actor="system",
                        enclosure="/incoming.md",
                        repo_id="repo-a",
                    ),
                ]
            ],
            enclosures=[],
            providers=[],
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_fresh_blocked_promotion_window_without_enclosure_is_kept(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        trust="observed",
                        actor="system",
                        enclosure="/incoming.md",
                        repo_id="repo-a",
                    ),
                    _event(
                        "lifecycle.blocked",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:20+00:00",
                        ask={"kind": "decision", "prompt": "Approve?"},
                    ),
                ]
            ],
            enclosures=[],
            providers=[],
            now=FRESH,
        )
        self.assertEqual([(lc.id, lc.state) for lc in proj.lifecycles], [("LC1", "blocked")])

    def test_reowned_enclosure_removes_old_event_lifecycle(self) -> None:
        logs = [
            [
                _started(lifecycle_id="OLD", ts=T0),
                _event(
                    "lifecycle.promoted",
                    lifecycle_id="OLD",
                    ts="2026-06-13T18:00:05+00:00",
                    trust="observed",
                    actor="system",
                    enclosure="/c.md",
                    repo_id="repo-a",
                ),
            ],
            [
                _started(lifecycle_id="NEW", ts=T0),
                _event(
                    "lifecycle.promoted",
                    lifecycle_id="NEW",
                    ts="2026-06-13T18:00:05+00:00",
                    trust="observed",
                    actor="system",
                    enclosure="/c.md",
                    repo_id="repo-a",
                ),
            ],
        ]
        proj = project_workspace(
            logs,
            enclosures=[_enclosure(enclosure="/c.md", lifecycleId="NEW")],
            providers=[],
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["NEW"])

    def test_fleeting_lifecycle_does_not_need_enclosure(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1", ts=T0, fleeting=True)]],
            enclosures=[],
            providers=[],
            now=FRESH,
        )
        self.assertEqual([lc.id for lc in proj.lifecycles], ["LC1"])

    def test_legacy_blank_lifecycle_enclosure_keeps_event_lifecycle(self) -> None:
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1", ts=T0),
                    _event(
                        "lifecycle.promoted",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        trust="observed",
                        actor="system",
                        enclosure="/c.md",
                        repo_id="repo-a",
                    ),
                ]
            ],
            enclosures=[_enclosure(enclosure="/c.md", lifecycleId="")],
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

    def test_inspect_result_map_parses_container_names(self) -> None:
        mapped = _inspect_result_map(
            json.dumps(
                [
                    {"Name": "/cgc-db", "State": {"Running": True}},
                    {"Name": "grepai-watch", "State": {"Running": False}},
                    {"Name": "", "State": {"Running": True}},
                    ["not-a-container"],
                ]
            )
        )

        self.assertEqual(set(mapped), {"cgc-db", "grepai-watch"})
        self.assertTrue(mapped["cgc-db"]["State"]["Running"])
        self.assertFalse(mapped["grepai-watch"]["State"]["Running"])

    def test_inspect_result_map_ignores_unusable_payloads(self) -> None:
        self.assertEqual(_inspect_result_map(None), {})
        self.assertEqual(_inspect_result_map(""), {})
        self.assertEqual(_inspect_result_map("{"), {})
        self.assertEqual(_inspect_result_map('{"Name": "/single"}'), {})

    def test_read_providers_parses_snapshot_with_age(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {
                            "id": "codegraphcontext-code",
                            "state": "ready",
                            "ok": True,
                            "watcherUp": True,
                            "indexingState": "indexed",
                        },
                        "grepai-memory": {
                            "id": "grepai-memory",
                            "state": "stopped",
                            "ok": False,
                            "watcherUp": False,
                            "indexingState": "unknown",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        nodes = {node.id: node for node in read_providers(config, now=STALE)}
        self.assertEqual(set(nodes), {"codegraphcontext-code", "grepai-memory"})
        self.assertEqual(nodes["codegraphcontext-code"].state, "ready")
        self.assertEqual(nodes["codegraphcontext-code"].snapshotStaleSeconds, 600.0)

    def test_read_providers_projects_cgc_repo_watchers(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {
                            "id": "codegraphcontext-code",
                            "state": "degraded",
                            "ok": False,
                            "indexingState": "mixed",
                            "resources": {
                                "watchers": {
                                    "repo-b": {
                                        "state": "degraded",
                                        "ok": False,
                                        "repoId": "repo-b",
                                        "watcherUp": True,
                                        "indexingState": "empty",
                                    },
                                    "agents-remember": {
                                        "state": "ready",
                                        "ok": True,
                                        "repoId": "agents-remember",
                                        "watcherUp": True,
                                        "indexingState": "indexed",
                                    },
                                }
                            },
                        },
                        "grepai-memory": {
                            "id": "grepai-memory",
                            "state": "ready",
                            "ok": True,
                            "watcherUp": True,
                            "indexingState": "indexed",
                            "resources": {
                                "watcher": {
                                    "state": "ready",
                                }
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        nodes = {node.id: node for node in read_providers(config, now=STALE)}

        self.assertEqual(
            set(nodes),
            {
                "codegraphcontext-code:agents-remember",
                "codegraphcontext-code:repo-b",
                "grepai-memory",
            },
        )
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].repoId, "agents-remember")
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].scope, "workspace")
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].role, "code")
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].state, "ready")
        self.assertTrue(nodes["codegraphcontext-code:agents-remember"].ok)
        self.assertTrue(nodes["codegraphcontext-code:agents-remember"].watcherUp)
        self.assertEqual(nodes["codegraphcontext-code:repo-b"].state, "degraded")
        self.assertEqual(nodes["codegraphcontext-code:repo-b"].indexingState, "empty")
        self.assertIsNone(nodes["grepai-memory"].repoId)
        self.assertEqual(nodes["grepai-memory"].role, "memory")

    def test_read_providers_projects_grepai_target_repos(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "grepai-memory": {
                            "id": "grepai-memory",
                            "state": "ready",
                            "ok": True,
                            "watcherUp": True,
                            "indexingState": "indexed",
                            "targetRepos": [
                                {
                                    "repoId": "agents-remember",
                                    "path": "/memory/agents-remember",
                                },
                                {
                                    "repoId": "repo-b",
                                    "path": "/memory/repo-b",
                                },
                            ],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        nodes = {node.id: node for node in read_providers(config, now=STALE)}

        self.assertEqual(set(nodes), {"grepai-memory:agents-remember", "grepai-memory:repo-b"})
        self.assertEqual(nodes["grepai-memory:agents-remember"].repoId, "agents-remember")
        self.assertEqual(nodes["grepai-memory:agents-remember"].scope, "workspace")
        self.assertEqual(nodes["grepai-memory:agents-remember"].role, "memory")
        self.assertEqual(nodes["grepai-memory:agents-remember"].state, "ready")
        self.assertTrue(nodes["grepai-memory:agents-remember"].ok)
        self.assertTrue(nodes["grepai-memory:agents-remember"].watcherUp)
        self.assertEqual(nodes["grepai-memory:repo-b"].repoId, "repo-b")
        self.assertEqual(nodes["grepai-memory:repo-b"].indexingState, "indexed")

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
                        "codegraphcontext-code": {
                            "id": "codegraphcontext-code",
                            "state": "ready",
                            "ok": True,
                        },
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
                    "isolatedProviderSettings": {
                        "providers": ["codegraphcontext-code", "grepai-memory"]
                    },
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
            {
                "codegraphcontext-code",
                "codegraphcontext-code@260612-x-ar",
                "grepai-memory@260612-x-ar",
            },
        )
        self.assertEqual(nodes["codegraphcontext-code"].scope, "workspace")
        code = nodes["codegraphcontext-code@260612-x-ar"]
        self.assertEqual(
            (code.scope, code.role, code.repoId, code.worktreeGroup),
            ("worktree", "code", "device-management", "260612-x-ar"),
        )
        self.assertIsNone(code.ok)
        self.assertEqual(code.state, "configured")
        self.assertEqual(nodes["grepai-memory@260612-x-ar"].role, "memory")

    def test_read_providers_ignores_unadmitted_worktree_stacks(self) -> None:
        config = self._config()
        runtime = (
            config.coordination_root
            / "worktrees"
            / "device-management"
            / "parked-ar"
            / "provider-runtime"
        )
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "provider-state.json").write_text(
            json.dumps(
                {
                    "schema": "ar-worktree-provider-state/v1",
                    "repoName": "device-management",
                    "worktreeGroup": str(runtime.parent),
                    "isolatedProviderSettings": {
                        "providers": ["codegraphcontext-code", "grepai-memory"]
                    },
                }
            ),
            encoding="utf-8",
        )

        nodes = read_providers(config, now=FRESH, active_worktree_groups=set())

        self.assertEqual(nodes, [])

    def test_read_providers_marks_worktree_stack_ready_from_live_containers(self) -> None:
        config = self._config()
        coord = config.coordination_root
        runtime = coord / "worktrees" / "device-management" / "260612-x-ar" / "provider-runtime"
        settings_path = runtime / "settings" / "provider-settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "contextProviders": {
                        "providers": {
                            "codegraphcontext-code": {
                                "roots": [{"repoId": "device-management"}],
                                "runtime": {
                                    "runner": {
                                        "containerNameTemplate": "cgc-<repoId>",
                                    }
                                },
                                "backend": {"containerName": "cgc-db"},
                            },
                            "grepai-memory": {
                                "runtime": {"runner": {"containerName": "grepai-watch"}},
                                "backend": {"containerName": "grepai-db"},
                                "embedder": {"backend": {"containerName": "grepai-ollama"}},
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (runtime / "provider-state.json").write_text(
            json.dumps(
                {
                    "schema": "ar-worktree-provider-state/v1",
                    "repoName": "device-management",
                    "worktreeGroup": str(runtime.parent),
                    "isolatedProviderSettings": {
                        "path": settings_path.as_posix(),
                        "providers": ["codegraphcontext-code", "grepai-memory"],
                    },
                }
            ),
            encoding="utf-8",
        )

        def inspected(name: str) -> dict[str, object]:
            return {
                "Name": f"/{name}",
                "State": {
                    "Running": True,
                    "Status": "running",
                    "StartedAt": "2026-06-27T12:00:00Z",
                },
            }

        names = {"cgc-device-management", "cgc-db", "grepai-watch", "grepai-db", "grepai-ollama"}
        with mock.patch(
            "agents_remember.observer.snapshots._inspect_containers",
            return_value={name: inspected(name) for name in names},
        ) as inspect:
            nodes = {
                node.id: node
                for node in read_providers(
                    config, now=FRESH, active_worktree_groups={"260612-x-ar"}
                )
            }

        self.assertEqual(inspect.call_args.args[0], names)
        code = nodes["codegraphcontext-code@260612-x-ar"]
        memory = nodes["grepai-memory@260612-x-ar"]
        self.assertEqual((code.state, code.ok, code.watcherUp), ("ready", True, True))
        self.assertEqual((memory.state, memory.ok, memory.watcherUp), ("ready", True, True))

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

    def test_project_and_write_prunes_completed_lifecycle_attention_acknowledgement(self) -> None:
        config = self._config()
        root = observer_root(config)
        store = EventStore(root)
        store.append(_started(lifecycle_id="LC1", ts=T0))
        store.append(
            _event(
                "lifecycle.ended",
                lifecycle_id="LC1",
                ts="2026-06-13T18:00:05+00:00",
                outcome="completed",
            )
        )
        dismissals = AttentionDismissalStore(root)
        dismissals.dismiss(
            AttentionDismissalRecord(
                itemId="awaiting-developer:LC1",
                kind="awaiting-developer",
                lifecycleId="LC1",
                dismissedAt="2026-06-13T18:00:04+00:00",
            )
        )

        project_and_write(config, now=FRESH)

        self.assertEqual(dismissals.current(), {})
        self.assertFalse(dismissals.log_path().exists())


class TokenSeriesTests(unittest.TestCase):
    def test_cumulative_series_from_tool_events(self) -> None:
        log = [
            _started(),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:05+00:00",
                trust="observed",
                tool="a",
                tokens=100,
                ok=True,
            ),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:10+00:00",
                trust="observed",
                tool="b",
                tokens=50,
                ok=True,
            ),
        ]
        self.assertEqual(
            [(s.ts, s.cumulative) for s in token_series(log)],
            [("2026-06-13T18:00:05+00:00", 100), ("2026-06-13T18:00:10+00:00", 150)],
        )

    def test_series_on_projection(self) -> None:
        log = [
            _started(),
            _event(
                "tool.completed",
                ts="2026-06-13T18:00:05+00:00",
                trust="observed",
                tool="a",
                tokens=7,
                ok=True,
            ),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual([s.cumulative for s in proj.tokenSeries], [7])

    def test_no_tool_events_is_empty(self) -> None:
        self.assertEqual(token_series([_started()]), [])


class StalenessHistogramTests(unittest.TestCase):
    def _node(self, age: float | None) -> SidecarStaleNode:
        return SidecarStaleNode(
            onboardingFile="x", repository="r", lastVerifiedDate="d", ageSeconds=age
        )

    def test_buckets_by_age(self) -> None:
        nodes = [
            self._node(3600.0),  # <7d
            self._node(10 * 86400.0),  # 7-30d
            self._node(60 * 86400.0),  # 30-90d
            self._node(200 * 86400.0),  # >90d
            self._node(None),  # unknown
        ]
        hist = staleness_histogram(nodes)
        self.assertEqual(hist, {"<7d": 1, "7-30d": 1, "30-90d": 1, ">90d": 1, "unknown": 1})


class AnalyticsAssemblyTests(unittest.TestCase):
    def _stale(self, age: float) -> SidecarStaleNode:
        return SidecarStaleNode(
            onboardingFile=f"f{age}", repository="r", lastVerifiedDate="d", ageSeconds=age
        )

    def test_stalest_leaderboard_is_bounded_and_oldest_first(self) -> None:
        nodes = [self._stale(float(i) * 86400.0) for i in range(20)]
        analytics = build_analytics(
            drift_snapshots=[],
            sidecar_staleness=nodes,
            setup_summaries=[],
            setup_progress=[],
            route_coverage=[],
            tool_reports=[],
            ledgers=[],
            stalest_limit=5,
        )
        self.assertEqual(len(analytics.stalestSidecars), 5)
        self.assertEqual(analytics.stalestSidecars[0].ageSeconds, 19 * 86400.0)

    def test_project_workspace_wires_analytics_and_histogram(self) -> None:
        sidecars = [self._stale(3600.0), self._stale(200 * 86400.0)]
        proj = project_workspace(
            [[_started()]],
            enclosures=[],
            providers=[],
            now=FRESH,
            sidecar_staleness=sidecars,
            drift_snapshots=[
                DriftSnapshotNode(
                    repository="r", branch="main", counts={"drifted": 1}, actionableCount=1
                )
            ],
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
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]], enclosures=[], providers=[], now=STALE
        )
        item = proj.analytics.attentionQueue[0]
        self.assertEqual(
            (item.kind, item.severity, item.lifecycleId), ("stale-session", "info", "LC1")
        )

    def test_dormant_fleeting_is_info(self) -> None:
        proj = project_workspace(
            [[_started(fleeting=True, lifecycle_id="LC1")]],
            enclosures=[],
            providers=[],
            now=DORMANT,
        )
        self.assertEqual(proj.analytics.attentionQueue[0].kind, "dormant-fleeting")

    def test_awaiting_developer_yields_one_info_item(self) -> None:
        # NOTIFY-AND-CONTINUE turn end (leaf-28): exactly one awaiting-developer item,
        # info severity, carrying the summary as its detail -- no double-emission.
        proj = project_workspace(
            [
                [
                    _started(lifecycle_id="LC1"),
                    _event(
                        "lifecycle.awaiting-developer",
                        lifecycle_id="LC1",
                        ts="2026-06-13T18:00:05+00:00",
                        summary="Drafted the plan; awaiting your review.",
                    ),
                ]
            ],
            enclosures=[],
            providers=[],
            now=FRESH,
        )
        queue = proj.analytics.attentionQueue
        self.assertEqual(len(queue), 1)
        item = queue[0]
        self.assertEqual(
            (item.kind, item.severity, item.lane, item.lifecycleId),
            ("awaiting-developer", "info", "lifecycle", "LC1"),
        )
        self.assertEqual(item.title, "Turn complete — your move")
        self.assertEqual(item.detail, "Drafted the plan; awaiting your review.")

    def test_drift_and_failed_setup_surface(self) -> None:
        proj = project_workspace(
            [[_started()]],
            enclosures=[],
            providers=[],
            now=FRESH,
            drift_snapshots=[
                DriftSnapshotNode(
                    repository="repo-a",
                    branch="main",
                    counts={"drifted": 2},
                    actionableCount=2,
                    checkedAt="2026-06-13T18:00:00+00:00",
                    memoryRoot="/memory/ar-repo-a",
                    reportPath="/tmp/drift-report.md",
                )
            ],
            setup_progress=[SetupProgressNode(group="g1", state="ok", failedPhases=["cgc setup"])],
        )
        self.assertEqual(
            {item.kind for item in proj.analytics.attentionQueue},
            {"actionable-drift", "failed-setup"},
        )
        drift = next(item for item in proj.analytics.attentionQueue if item.kind == "actionable-drift")
        self.assertEqual(drift.id, "actionable-drift:repo-a:main")
        self.assertEqual(drift.title, "2 actionable drift in repo-a")
        self.assertEqual(drift.signalTs, "2026-06-13T18:00:00+00:00")
        self.assertIn("/memory/ar-repo-a", drift.detail or "")
        self.assertIn("/tmp/drift-report.md", drift.detail or "")

    def test_calm_tree_has_empty_queue(self) -> None:
        proj = project_workspace([[_started()]], enclosures=[], providers=[], now=FRESH)
        self.assertEqual(proj.analytics.attentionQueue, [])


class AttentionDismissalTests(unittest.TestCase):
    """Leaf-28 S5.2: a lifecycle acknowledgement suppresses one current occurrence,
    and a newer triggering signal re-surfaces it."""

    AWAIT_TS = "2026-06-13T18:00:05+00:00"
    DISMISS_TS = "2026-06-13T18:00:10+00:00"  # >= AWAIT_TS / blocked ts / T0

    def _await_log(self) -> list[Event]:
        return [
            _started(lifecycle_id="LC1"),
            _event(
                "lifecycle.awaiting-developer",
                lifecycle_id="LC1",
                ts=self.AWAIT_TS,
                summary="Drafted the plan; awaiting your review.",
            ),
        ]

    def _queue_for(self, logs, *, now, dismissals=None, **kw):  # type: ignore[no-untyped-def]
        return project_workspace(
            logs,
            enclosures=[],
            providers=kw.get("providers", []),
            now=now,
            gates=kw.get("gates"),
            attention_dismissals=dismissals,
        ).analytics.attentionQueue

    def _dismissal(
        self,
        item_id: str,
        *,
        lifecycle_id: str | None = "LC1",
        kind: str | None = None,
        dismissed_at: str | None = None,
    ) -> dict[str, AttentionDismissalRecord]:
        return {
            item_id: AttentionDismissalRecord(
                itemId=item_id,
                kind=kind,
                lifecycleId=lifecycle_id,
                dismissedAt=dismissed_at or self.DISMISS_TS,
            )
        }

    def test_state_entered_at_is_heartbeat_immune(self) -> None:
        # The anchor the whole design rests on: a heartbeat advances lastEventTs but NOT
        # stateEnteredAt, so a dismissed blocked/awaiting item does not flap back on a beat.
        proj = project_lifecycle(
            [
                _started(lifecycle_id="LC1"),
                _event("lifecycle.blocked", lifecycle_id="LC1", ts=self.AWAIT_TS),
                _event("lifecycle.heartbeat", lifecycle_id="LC1", ts="2026-06-13T18:00:20+00:00"),
            ],
            now=FRESH,
        )
        self.assertEqual(proj.stateEnteredAt, self.AWAIT_TS)
        self.assertEqual(proj.lastEventTs, "2026-06-13T18:00:20+00:00")

    def test_awaiting_item_carries_state_entered_signal(self) -> None:
        item = self._queue_for([self._await_log()], now=FRESH)[0]
        self.assertEqual((item.kind, item.signalTs), ("awaiting-developer", self.AWAIT_TS))

    def test_dismiss_suppresses_awaiting_developer(self) -> None:
        queue = self._queue_for(
            [self._await_log()],
            now=FRESH,
            dismissals=self._dismissal("awaiting-developer:LC1", kind="awaiting-developer"),
        )
        self.assertEqual(queue, [])

    def test_dismiss_suppresses_blocked_gate(self) -> None:
        log = [
            _started(lifecycle_id="LC1"),
            _event("lifecycle.blocked", lifecycle_id="LC1", ts=self.AWAIT_TS, ask={"x": 1}),
        ]
        self.assertEqual(
            self._queue_for(
                [log],
                now=FRESH,
                dismissals=self._dismissal("blocked-gate:LC1", kind="blocked-gate"),
            ),
            [],
        )

    def test_dismiss_suppresses_stale_session(self) -> None:
        self.assertEqual(
            self._queue_for(
                [[_started(lifecycle_id="LC1")]],
                now=STALE,
                dismissals=self._dismissal("stale-session:LC1", kind="stale-session"),
            ),
            [],
        )

    def test_dismiss_suppresses_dormant_fleeting(self) -> None:
        self.assertEqual(
            self._queue_for(
                [[_started(fleeting=True, lifecycle_id="LC1")]],
                now=DORMANT,
                dismissals=self._dismissal("dormant-fleeting:LC1", kind="dormant-fleeting"),
            ),
            [],
        )

    def test_dismiss_suppresses_gate_open(self) -> None:
        gate = create_gate(kind="closeout-approval", lifecycle_id="LC1", gate_id="G1", now=T0)
        self.assertEqual(
            self._queue_for(
                [[_started(lifecycle_id="LC1")]],
                now=FRESH,
                gates=[gate],
                dismissals=self._dismissal("gate:G1", kind="gate-open"),
            ),
            [],
        )

    def test_non_lifecycle_dismissal_does_not_suppress_provider_down(self) -> None:
        # Attention acknowledgements are scoped to lifecycles; repo/provider alarms clear
        # when their source condition clears.
        queue = self._queue_for(
            [[_started(lifecycle_id="LC1")]],
            now=FRESH,
            providers=[ProviderNode(id="cgc", state="stopped", ok=False)],
            dismissals=self._dismissal(
                "provider-down:cgc", lifecycle_id=None, kind="provider-down"
            ),
        )
        self.assertEqual([i.kind for i in queue if i.kind == "provider-down"], ["provider-down"])

    def test_dismiss_suppresses_actionable_drift_until_newer_snapshot(self) -> None:
        dismissed = self._dismissal(
            "actionable-drift:repo-a:main",
            lifecycle_id=None,
            kind="actionable-drift",
            dismissed_at="2026-06-13T18:00:10+00:00",
        )
        old_snapshot = DriftSnapshotNode(
            repository="repo-a",
            branch="main",
            actionableCount=1,
            checkedAt="2026-06-13T18:00:00+00:00",
        )
        self.assertEqual(
            build_attention_queue([], [], [old_snapshot], [], dismissals=dismissed),
            [],
        )

        newer_snapshot = DriftSnapshotNode(
            repository="repo-a",
            branch="main",
            actionableCount=1,
            checkedAt="2026-06-13T18:00:11+00:00",
        )
        queue = build_attention_queue([], [], [newer_snapshot], [], dismissals=dismissed)
        self.assertEqual([item.kind for item in queue], ["actionable-drift"])

    def test_newer_turn_end_supersedes_dismissal(self) -> None:
        # A fresh turn-end re-enters awaiting (a newer stateEnteredAt) and re-surfaces the
        # item despite an older dismissal -- a dismissal acknowledges THIS occurrence only.
        re_log = [
            _started(lifecycle_id="LC1"),
            _event("lifecycle.awaiting-developer", lifecycle_id="LC1", ts=self.AWAIT_TS, summary="1"),
            _event("lifecycle.resumed", lifecycle_id="LC1", ts="2026-06-13T18:00:07+00:00"),
            _event(
                "lifecycle.awaiting-developer",
                lifecycle_id="LC1",
                ts="2026-06-13T18:00:08+00:00",
                summary="2",
            ),
        ]
        queue = self._queue_for(
            [re_log],
            now=FRESH,
            dismissals=self._dismissal(
                "awaiting-developer:LC1",
                kind="awaiting-developer",
                dismissed_at="2026-06-13T18:00:06+00:00",
            ),
        )
        kinds = [i.kind for i in queue]
        self.assertIn("awaiting-developer", kinds)  # :08 signal supersedes the :06 dismissal

class GateProjectionTests(unittest.TestCase):
    """Slice 6c: durable gates materialize onto the lifecycle + the attention queue."""

    LATER = "2026-06-13T18:05:00+00:00"

    def _open(self, *, gate_id: str = "G1", ts: str = T0):
        return create_gate(kind="closeout-approval", lifecycle_id="LC1", gate_id=gate_id, now=ts)

    def test_open_gate_materializes_onto_lifecycle(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            enclosures=[],
            providers=[],
            now=FRESH,
            gates=[self._open()],
        )
        gate = proj.lifecycles[0].gate
        assert gate is not None
        self.assertEqual((gate.id, gate.kind, gate.state), ("G1", "closeout-approval", "open"))
        self.assertEqual(gate.decisions, ["approve", "cancel", "reject", "request-revision"])

    def test_decided_gate_is_not_attached(self) -> None:
        decided = decide_gate(
            self._open(),
            decision="approve",
            by="developer",
            via="dashboard",
            note=None,
            now=self.LATER,
        )
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            enclosures=[],
            providers=[],
            now=FRESH,
            gates=[decided],
        )
        self.assertIsNone(proj.lifecycles[0].gate)

    def test_latest_open_gate_wins(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            enclosures=[],
            providers=[],
            now=FRESH,
            gates=[self._open(gate_id="A", ts=T0), self._open(gate_id="B", ts=self.LATER)],
        )
        gate = proj.lifecycles[0].gate
        assert gate is not None
        self.assertEqual(gate.id, "B")

    def test_open_gate_adds_attention_item(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            enclosures=[],
            providers=[],
            now=FRESH,
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
            enclosures=[],
            providers=[],
            now=FRESH,
            gates=[self._open()],
        )
        lane_items = [i for i in proj.analytics.attentionQueue if i.lane == "lifecycle"]
        self.assertEqual(len(lane_items), 1)
        self.assertEqual(lane_items[0].kind, "gate-open")
        self.assertEqual(
            [i for i in proj.analytics.attentionQueue if i.kind == "blocked-gate"], []
        )

    def test_bare_block_without_gate_still_yields_blocked_gate(self) -> None:
        # PARK, not delete: a bare block() with no GateRecord still raises blocked-gate.
        proj = project_workspace(
            [self._blocked_log()], enclosures=[], providers=[], now=FRESH
        )
        kinds = [i.kind for i in proj.analytics.attentionQueue]
        self.assertEqual(kinds, ["blocked-gate"])


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
        self.assertEqual(
            (node.repository, node.closeoutCount, node.lastVerifiedCodeCommit),
            ("repo-a", 2, "cccc"),
        )

    def test_missing_ledger_is_none(self) -> None:
        self.assertIsNone(read_ledger(self.mem / "nope"))

    def test_ledger_window_returns_newest_rows_and_total(self) -> None:
        # 5h coupler popover: the newest LEDGER_WINDOW rows (newest-first) + the full total for "+N more".
        ledger = create_initial_ledger("repo-a", "base-code", "base-mem")
        for i in range(LEDGER_WINDOW + 3):  # more rows than the window
            ledger = prepend_mapping(ledger, f"code{i:02d}", f"mem{i:02d}")
        write_ledger(self.mem / "memory.md", ledger)
        rows, total = _ledger_window((self.mem / "memory.md").as_posix())
        self.assertEqual(len(rows), LEDGER_WINDOW)
        self.assertEqual(total, len(ledger.rows))  # total is the full count, not the window
        self.assertIsInstance(rows[0], LedgerRefNode)
        self.assertEqual(rows[0].codeCommit, f"code{LEDGER_WINDOW + 2:02d}")  # newest-first

    def test_ledger_window_missing_or_none_is_empty(self) -> None:
        self.assertEqual(_ledger_window((self.mem / "nope.md").as_posix()), ([], 0))
        self.assertEqual(_ledger_window(None), ([], 0))

    def test_reads_windowed_rows_with_full_count(self) -> None:
        # 5h official coupler: read_ledger surfaces the newest LEDGER_WINDOW rows; closeoutCount stays total.
        ledger = create_initial_ledger("repo-a", "base-code", "base-mem")
        for i in range(LEDGER_WINDOW + 5):
            ledger = prepend_mapping(ledger, f"code{i:02d}", f"mem{i:02d}")
        write_ledger(self.mem / "memory.md", ledger)
        node = read_ledger(self.mem)
        assert node is not None
        self.assertEqual(len(node.rows), LEDGER_WINDOW)
        self.assertEqual(node.closeoutCount, len(ledger.rows))  # the full total, not the window
        self.assertEqual(node.rows[0].codeCommit, f"code{LEDGER_WINDOW + 4:02d}")  # newest-first


class LedgerCommitMetaTests(unittest.TestCase):
    """5h Tier 2: best-effort commit message + date enrichment of the popover ledger window."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def _git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _repo_with_commits(self, subjects: list[str]) -> tuple[Path, list[str]]:
        repo = (self.tmp / "repo").resolve()
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        shas: list[str] = []
        for subject in subjects:
            self._git(repo, "commit", "--allow-empty", "-m", subject)
            shas.append(self._git(repo, "rev-parse", "HEAD"))
        return repo, shas

    def test_git_commit_meta_batches_and_maps(self) -> None:
        repo, shas = self._repo_with_commits(["first subject", "second subject"])
        meta = _git_commit_meta(repo.as_posix(), shas)
        self.assertEqual(set(meta), set(shas))
        date, subject = meta[shas[1]]
        self.assertEqual(subject, "second subject")
        self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")  # committer ISO date

    def test_git_commit_meta_drops_unknown_and_tolerates_bad_input(self) -> None:
        repo, shas = self._repo_with_commits(["only one"])
        # a bogus sha is dropped (no HEAD fallback); the real one still resolves
        meta = _git_commit_meta(
            repo.as_posix(), [shas[0], "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"]
        )
        self.assertEqual(set(meta), {shas[0]})
        # best-effort: a non-repo path, empty root, or empty commit list -> {}
        self.assertEqual(_git_commit_meta((self.tmp / "nope").as_posix(), shas), {})
        self.assertEqual(_git_commit_meta("", shas), {})
        self.assertEqual(_git_commit_meta(repo.as_posix(), []), {})

    def test_ledger_window_enriches_rows_when_commits_are_local(self) -> None:
        repo, shas = self._repo_with_commits(["code change", "memory change"])
        mem = (self.tmp / "mem").resolve()
        mem.mkdir()
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), shas[0], shas[1])
        write_ledger(mem / "memory.md", ledger)
        rows, total = _ledger_window(
            (mem / "memory.md").as_posix(), code_root=repo.as_posix(), memory_root=repo.as_posix()
        )
        self.assertEqual(total, len(ledger.rows))
        self.assertEqual(rows[0].codeSubject, "code change")
        self.assertEqual(rows[0].memorySubject, "memory change")
        self.assertIsNotNone(rows[0].codeDate)
        self.assertIsNotNone(rows[0].memoryDate)

    def test_ledger_window_leaves_meta_none_when_not_local(self) -> None:
        # honest fallback: no probe roots -> rows still served with hashes, no message/date (never faked)
        mem = (self.tmp / "mem2").resolve()
        mem.mkdir()
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), "cccc", "dddd")
        write_ledger(mem / "memory.md", ledger)
        rows, _ = _ledger_window((mem / "memory.md").as_posix())
        self.assertEqual(rows[0].codeCommit, "cccc")
        self.assertIsNone(rows[0].codeSubject)
        self.assertIsNone(rows[0].codeDate)
        self.assertIsNone(rows[0].memorySubject)
        self.assertIsNone(rows[0].memoryDate)

    def test_read_ledger_enriches_official_rows_with_code_root(self) -> None:
        # memory.md lives in the (git) memory repo so its memory commits resolve; code_root carries the code side
        repo, shas = self._repo_with_commits(["official code", "official memory"])
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), shas[0], shas[1])
        write_ledger(repo / "memory.md", ledger)
        node = read_ledger(repo, code_root=repo)
        assert node is not None
        self.assertEqual(node.rows[0].codeSubject, "official code")
        self.assertEqual(node.rows[0].memorySubject, "official memory")


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
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=t",
                "commit",
                "--allow-empty",
                "-m",
                "init",
            ],
            check=True,
            capture_output=True,
        )
        coord = (self.tmp / "coord").resolve()
        onboarding = (self.tmp / "onb").resolve()
        memory = (self.tmp / "memory").resolve()
        onboarding.mkdir()
        memory.mkdir()
        context = SimpleNamespace(
            coordination_root=coord,
            onboarding_root=onboarding,
            memory_root=memory,
        )
        rows = [
            DriftRow(
                "onboarding/a.md",
                "a.py",
                "repo-x",
                "external",
                "h",
                "d",
                "up to date",
                "high",
                "none",
                "ok",
            ),
            DriftRow(
                "onboarding/b.md",
                "b.py",
                "repo-x",
                "external",
                "h",
                "d",
                "drifted",
                "medium",
                "logic",
                "changed",
            ),
        ]
        summary._write_drift_snapshot(repo, context, rows)
        expected_path = drift_snapshot_path(coord, repository="repo-x", branch="feat-x")
        self.assertTrue(expected_path.exists())
        nodes = read_drift_snapshots(coord, now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].repository, nodes[0].branch), ("repo-x", "feat-x"))
        self.assertEqual(nodes[0].counts["drifted"], 1)
        self.assertEqual(nodes[0].counts["up to date"], 1)
        self.assertEqual(nodes[0].actionableCount, 1)
        self.assertEqual(nodes[0].sourceRoot, repo.as_posix())
        self.assertEqual(nodes[0].memoryRoot, memory.as_posix())
        self.assertIsNotNone(nodes[0].checkedAt)


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
            json.dumps(
                {
                    "schema": DRIFT_SNAPSHOT_SCHEMA,
                    "repository": "repo-a",
                    "branch": "main",
                    "checkedAt": T0,
                    "counts": {"drifted": 1},
                    "actionableCount": 1,
                    "rows": [],
                }
            ),
            encoding="utf-8",
        )
        write_ledger(
            self.mem / "memory.md",
            prepend_mapping(create_initial_ledger("repo-a", "aaaa", "bbbb"), "cccc", "dddd"),
        )
        (self.mem / "onboarding" / "a.py.md").write_text(
            "| Field | Value |\n| --- | --- |\n| doc_type | `file-level-onboarding` |\n| lastVerifiedCommitDate | 2026-06-13T17:00:00+00:00 |\n",
            encoding="utf-8",
        )
        write_task_doc(
            self.coord / "tasks" / "repo-a" / "demo",
            TaskDocument.model_validate(
                {
                    "id": "D",
                    "slug": "task",
                    "title": "Demo",
                    "kind": "light",
                    "repo": "repo-a",
                    "createdAt": "2026-01-01T00:00",
                    "lifecycleId": "LC1",
                    "steps": [{"id": "S1", "title": "a", "status": "done"}],
                }
            ),
        )
        proj = project_and_write(config, now=FRESH)
        self.assertEqual(len(proj.analytics.driftSnapshots), 1)
        self.assertEqual(proj.analytics.driftSnapshots[0].counts["drifted"], 1)
        self.assertEqual(proj.analytics.ledgers[0].closeoutCount, 2)
        self.assertEqual(len(proj.analytics.stalestSidecars), 1)
        self.assertEqual(proj.metrics.stalenessHistogram["<7d"], 1)
        self.assertEqual(len(proj.analytics.taskDocuments), 1)
        self.assertEqual(proj.analytics.taskDocuments[0].lifecycleId, "LC1")
        state = json.loads(
            (observer_root(config) / "latest-state.json").read_text(encoding="utf-8")
        )
        self.assertIn("analytics", state)

    def test_repo_surface_cache_reuses_recent_repo_reads(self) -> None:
        config = self._config()
        _repo_surface_cache.clear()
        self.addCleanup(_repo_surface_cache.clear)
        first = ([], [], [])
        refreshed = ([], [], [])
        with mock.patch(
            "agents_remember.observer.projection_store._gather_repo_surfaces",
            side_effect=[first, refreshed],
        ) as gather:
            one = _gather_repo_surfaces_cached(config, FRESH)
            two = _gather_repo_surfaces_cached(
                config, datetime(2026, 6, 13, 18, 0, 40, tzinfo=UTC)
            )
            three = _gather_repo_surfaces_cached(
                config, datetime(2026, 6, 13, 18, 0, 46, tzinfo=UTC)
            )

        self.assertIs(one, first)
        self.assertIs(two, first)
        self.assertIs(three, refreshed)
        self.assertEqual(gather.call_count, 2)

    def test_project_and_write_keeps_provider_reads_on_fast_path_with_cached_surfaces(self) -> None:
        config = self._config()
        _repo_surface_cache.clear()
        self.addCleanup(_repo_surface_cache.clear)
        with (
            mock.patch(
                "agents_remember.observer.projection_store._gather_repo_surfaces",
                return_value=([], [], []),
            ) as gather,
            mock.patch(
                "agents_remember.observer.projection_store.read_providers",
                return_value=[],
            ) as providers,
        ):
            project_and_write(config, now=FRESH)
            project_and_write(config, now=datetime(2026, 6, 13, 18, 0, 40, tzinfo=UTC))

        self.assertEqual(gather.call_count, 1)
        self.assertEqual(providers.call_count, 2)

    def test_project_and_write_prunes_orphaned_worktree_drift_snapshots(self) -> None:
        config = self._config()
        official = self._write_snapshot("repo-a", "main")
        active_contract = default_contract(
            task_name="active task",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=self.coord,
            code_repo_path=(self.tmp / "ws" / "repo-a").resolve(),
            code_source_branch="feat/dashboard",
            code_work_branch="ar/active",
            code_base_commit="base",
            worktree_name="active-worktree",
        )
        active_contract.code_worktree.mkdir(parents=True)
        write_contract(active_contract.contract_path, active_contract)
        active = self._write_snapshot(active_contract.code_worktree.name, "ar/active")
        orphaned = self._write_snapshot("deleted-worktree", "ar/deleted")
        invalid = drift_snapshot_dir(self.coord) / "invalid.json"
        invalid.write_text(
            json.dumps({"schema": "other/v9", "repository": "deleted", "branch": "ar/x"}),
            encoding="utf-8",
        )

        proj = project_and_write(config, now=FRESH)

        self.assertTrue(official.exists())
        self.assertTrue(active.exists())
        self.assertFalse(orphaned.exists())
        self.assertTrue(invalid.exists())
        self.assertEqual(
            {(node.repository, node.branch) for node in proj.analytics.driftSnapshots},
            {("repo-a", "main"), (active_contract.code_worktree.name, "ar/active")},
        )

    def _write_snapshot(self, repository: str, branch: str) -> Path:
        path = drift_snapshot_path(self.coord, repository=repository, branch=branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": DRIFT_SNAPSHOT_SCHEMA,
                    "repository": repository,
                    "branch": branch,
                    "checkedAt": T0,
                    "counts": {"drifted": 1},
                    "actionableCount": 1,
                    "rows": [],
                }
            ),
            encoding="utf-8",
        )
        return path


class TaskDocumentsReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _doc(self, **over: object) -> TaskDocument:
        base: dict[str, object] = {
            "id": "D",
            "slug": "task",
            "title": "Demo",
            "kind": "light",
            "repo": "repo-a",
            "createdAt": "2026-01-01T00:00",
        }
        base.update(over)
        return TaskDocument.model_validate(base)

    def test_reads_lifecycle_keyed_progress(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "demo"
        write_task_doc(
            root,
            self._doc(
                lifecycleId="LC1",
                steps=[
                    {"id": "S1", "title": "a", "status": "done"},
                    {"id": "S2", "title": "b", "status": "inProgress"},
                ],
            ),
        )
        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(
            (nodes[0].lifecycleId, nodes[0].stepsDone, nodes[0].stepsTotal, nodes[0].currentStep),
            ("LC1", 1, 2, "S2 — b"),
        )
        self.assertEqual(nodes[0].createdAt, "2026-01-01T00:00")

    def test_projects_docs_without_lifecycle_and_skips_non_task_json(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "demo"
        write_task_doc(root, self._doc(slug="03c_x", kind="subTask"))  # no lifecycleId
        (root / "other.json").write_text('{"schema": "other/v1"}', encoding="utf-8")
        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertIsNone(nodes[0].lifecycleId)
        self.assertEqual(nodes[0].docPath, (root / "03c_x.json").as_posix())

    def test_leaf_contract_alone_is_not_a_task_document(self) -> None:
        contract = default_contract(
            task_name="demo",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=self.coord,
            code_repo_path=self.coord / "repos" / "repo-a",
            code_source_branch="ar/demo",
            code_work_branch="ar/demo-leaf",
            code_base_commit="abc123",
            worktree_name="01_leaf-work",
            lifecycle_id="LC-LEAF",
        )
        write_contract(contract.contract_path, contract)

        self.assertEqual(
            read_task_documents(self.coord, enclosures=read_enclosures(self.coord), now=FRESH),
            [],
        )

    def test_resolves_leaf_doc_lifecycle_from_matching_enclosure_leaf_id(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "demo"
        leaf_id = "17_task-reader-top-progress-and-master-content"
        contract = default_contract(
            task_name="demo",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=self.coord,
            code_repo_path=self.coord / "repos" / "repo-a",
            code_source_branch="ar/demo",
            code_work_branch="ar/demo-leaf",
            code_base_commit="abc123",
            worktree_name=leaf_id,
            leaf_id=leaf_id,
            lifecycle_id="LC-LEAF",
        )
        write_contract(contract.contract_path, contract)
        write_task_doc(
            root,
            self._doc(
                slug=leaf_id,
                kind="subTask",
                steps=[{"id": "S1", "title": "a", "status": "inProgress"}],
            ),
        )

        [node] = read_task_documents(self.coord, enclosures=read_enclosures(self.coord), now=FRESH)

        self.assertEqual(node.lifecycleId, "LC-LEAF")
        self.assertEqual(node.docPath, (root / f"{leaf_id}.json").as_posix())

    def _master(self) -> TaskDocument:
        return TaskDocument.model_validate(
            {
                "id": "series",
                "slug": "series",
                "title": "Series",
                "kind": "master",
                "repo": "repo-a",
                "createdAt": "2026-01-01T00:00",
                "subTasks": [{"number": "1", "name": "A", "status": "inProgress"}],
                "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
            }
        )

    def test_master_without_a_lifecycle_projects_as_task_document(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "series"
        write_task_doc(root, self._master())
        [node] = read_task_documents(self.coord, enclosures=[], now=FRESH)
        self.assertIsNone(node.lifecycleId)
        self.assertEqual(node.kind, "master")
        self.assertEqual(node.title, "Series")

    def test_master_stays_on_series_surface(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "series"
        write_task_doc(root, self._master())
        [task_node] = read_task_documents(self.coord, enclosures=[], now=FRESH)
        self.assertEqual(task_node.kind, "master")
        [series] = read_series_documents(self.coord, now=FRESH)
        self.assertEqual(series.seriesId, "series")
        self.assertEqual([ref.number for ref in series.subTasks], ["1"])
        self.assertEqual([section.kind for section in series.sections], ["subTasks"])

    def test_nested_masters_stay_on_series_surface(self) -> None:
        parent_dir = self.coord / "tasks" / "repo-a" / "parent"
        child_dir = self.coord / "tasks" / "repo-a" / "child"
        write_task_doc(
            parent_dir,
            TaskDocument.model_validate(
                {
                    "id": "p",
                    "slug": "parent",
                    "title": "Parent",
                    "kind": "master",
                    "repo": "repo-a",
                    "createdAt": "2026-01-01T00:00",
                    "subTasks": [
                        {
                            "number": "06",
                            "name": "Child series",
                            "file": "../child/task.md",
                            "status": "inProgress",
                        }
                    ],
                }
            ),
        )
        write_task_doc(
            child_dir,
            TaskDocument.model_validate(
                {
                    "id": "c",
                    "slug": "child",
                    "title": "Child",
                    "kind": "master",
                    "repo": "repo-a",
                    "createdAt": "2026-01-01T00:00",
                    "master": "../parent/task.md",
                    "subTasks": [{"number": "1", "name": "A", "status": "inProgress"}],
                }
            ),
        )
        task_nodes = sorted(
            read_task_documents(self.coord, enclosures=[], now=FRESH),
            key=lambda node: node.title,
        )
        self.assertEqual([node.title for node in task_nodes], ["Child", "Parent"])
        nodes = sorted(read_series_documents(self.coord, now=FRESH), key=lambda node: node.seriesId)
        self.assertEqual([node.seriesId for node in nodes], ["child", "parent"])

    def test_missing_tasks_dir_is_empty(self) -> None:
        self.assertEqual(read_task_documents(self.coord / "nope", enclosures=[], now=FRESH), [])

    def test_archived_task_documents_are_not_projected(self) -> None:
        active = self.coord / "tasks" / "repo-a" / "active"
        archived = self.coord / "tasks" / "repo-a" / "0_archive" / "archived"
        write_task_doc(active, self._doc(slug="active", status="Completed"))
        write_task_doc(archived, self._doc(slug="archived", status="Completed"))

        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)

        self.assertEqual([node.title for node in nodes], ["Demo"])
        self.assertEqual(nodes[0].status, "Completed")

    def test_build_analytics_includes_task_documents(self) -> None:
        node = TaskDocNode(
            id="1",
            lifecycleId="LC1",
            repository="repo-a",
            title="t",
            status="planning",
            kind="light",
            docPath="p",
        )
        analytics = build_analytics(
            drift_snapshots=[],
            sidecar_staleness=[],
            setup_summaries=[],
            setup_progress=[],
            route_coverage=[],
            tool_reports=[],
            ledgers=[],
            task_documents=[node],
        )
        self.assertEqual(len(analytics.taskDocuments), 1)
        self.assertEqual(analytics.taskDocuments[0].lifecycleId, "LC1")

    def test_read_series_documents_projects_master(self) -> None:
        # The master is a checklist: each subtask is one checkbox; doneCount = declared
        # Completed subtasks, totalCount = number of subtasks. The full render (sections +
        # decisions) is carried so the dashboard is the reader.
        root = self.coord / "tasks" / "repo-a" / "series-x"
        master = TaskDocument.model_validate(
            {
                "id": "series-x",
                "slug": "series-x",
                "title": "Series X",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo-a",
                "createdAt": "2026-01-01T00:00",
                "objective": "Series X objective",
                "subTasks": [
                    {"number": "01", "name": "alpha", "status": "Completed"},
                    {"number": "02", "name": "beta", "status": "Completed"},
                    {"number": "03", "name": "gamma", "status": "inProgress"},
                ],
                "sections": [{"kind": "freeform", "heading": "Objective", "body": "the series"}],
                "decisions": [{"at": "2026-01-01T00:00", "decision": "d", "rationale": "r"}],
            }
        )
        write_task_doc(root, master)
        nodes = read_series_documents(self.coord, now=FRESH)
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.seriesId, "series-x")
        self.assertEqual(node.objective, "Series X objective")
        self.assertEqual((node.doneCount, node.totalCount), (2, 3))
        self.assertEqual(
            [(s.number, s.status) for s in node.subTasks],
            [("01", "Completed"), ("02", "Completed"), ("03", "inProgress")],
        )
        self.assertEqual(node.sections[0].heading, "Objective")
        self.assertEqual(node.decisions[0].decision, "d")

    def test_read_series_documents_orders_subtasks_by_leaf_creation(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "series-z"
        write_task_doc(
            root,
            TaskDocument.model_validate(
                {
                    "id": "series-z",
                    "slug": "series-z",
                    "title": "Series Z",
                    "kind": "master",
                    "repo": "repo-a",
                    "createdAt": "2026-01-01T00:00",
                    "subTasks": [
                        {
                            "number": "99",
                            "name": "Alpha later",
                            "file": "alpha_later.md",
                            "status": "inProgress",
                        },
                        {
                            "number": "01",
                            "name": "Zulu earlier",
                            "file": "zulu_earlier.md",
                            "status": "planning",
                        },
                    ],
                }
            ),
        )
        write_task_doc(
            root,
            self._doc(
                id="alpha",
                slug="alpha_later",
                kind="subTask",
                title="Alpha later",
                createdAt="2026-01-03T00:00",
            ),
        )
        write_task_doc(
            root,
            self._doc(
                id="zulu",
                slug="zulu_earlier",
                kind="subTask",
                title="Zulu earlier",
                createdAt="2026-01-01T00:00",
            ),
        )

        [node] = read_series_documents(self.coord, now=FRESH)

        self.assertEqual(
            [(sub.name, sub.createdAt) for sub in node.subTasks],
            [
                ("Zulu earlier", "2026-01-01T00:00"),
                ("Alpha later", "2026-01-03T00:00"),
            ],
        )

    def test_read_series_documents_skips_leaf_docs(self) -> None:
        root = self.coord / "tasks" / "repo-a" / "demo"
        write_task_doc(root, self._doc(slug="03c_x", kind="subTask"))  # a leaf, not a master
        self.assertEqual(read_series_documents(self.coord, now=FRESH), [])

    def test_declared_subtask_status_is_authoritative_over_leaf_steps(self) -> None:
        # A subtask marked Completed in the master counts as done even if its own leaf doc
        # still has open steps -- series_done reads the declared status, never leaf steps.
        write_task_doc(
            self.coord / "tasks" / "repo-a" / "series-y",
            TaskDocument.model_validate(
                {
                    "id": "series-y",
                    "slug": "series-y",
                    "title": "Series Y",
                    "kind": "master",
                    "repo": "repo-a",
                    "createdAt": "2026-01-01T00:00",
                    "subTasks": [{"number": "01", "name": "alpha", "status": "Completed"}],
                }
            ),
        )
        # the slice's own leaf doc still has an open step
        write_task_doc(
            self.coord / "tasks" / "repo-a" / "slice-01",
            self._doc(
                slug="01_alpha",
                kind="subTask",
                lifecycleId="LC9",
                steps=[{"id": "S1", "title": "x", "status": "inProgress"}],
            ),
        )
        [node] = read_series_documents(self.coord, now=FRESH)
        self.assertEqual((node.doneCount, node.totalCount), (1, 1))  # declared Completed wins

    def test_read_series_documents_missing_tasks_dir_is_empty(self) -> None:
        self.assertEqual(read_series_documents(self.coord / "nope", now=FRESH), [])

    def test_build_analytics_includes_series(self) -> None:
        node = SeriesNode(
            seriesId="s",
            repository="repo-a",
            title="t",
            status="planning",
            docPath="p",
            doneCount=1,
            totalCount=2,
        )
        analytics = build_analytics(
            drift_snapshots=[],
            sidecar_staleness=[],
            setup_summaries=[],
            setup_progress=[],
            route_coverage=[],
            tool_reports=[],
            ledgers=[],
            series=[node],
        )
        self.assertEqual(len(analytics.series), 1)
        self.assertEqual(analytics.series[0].seriesId, "s")


def _action(actions: list, name: str):  # type: ignore[type-arg]
    return next(action for action in actions if action.action == name)


def _facts(
    *,
    contract: dict | None = None,  # type: ignore[type-arg]
    status: dict | None = None,  # type: ignore[type-arg]
    guidance: dict | None = None,  # type: ignore[type-arg]
    ledger_rows: list[LedgerRefNode] | None = None,
    ledger_row_count: int = 0,
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
    return EngineProcessFacts(
        contract=base_contract,
        guidance=base_guidance,
        status=status,
        ledger_rows=ledger_rows or [],
        ledger_row_count=ledger_row_count,
    )


class EngineProcessTests(unittest.TestCase):
    """The slice-5e enclosure-centered process map (``build_engine_processes``)."""

    def test_ledger_rows_pass_through_to_the_worktree_coupler(self) -> None:
        # 5h: the worktree coupler popover reads node.ledgerRows/ledgerRowCount. The reducer is a pure
        # fold, so the windowed rows ride in on EngineProcessFacts (read in the I/O layer) and pass through.
        rows = [
            LedgerRefNode(codeCommit="08e9221a", memoryCommit="d60a0511"),
            LedgerRefNode(codeCommit="600f7fa3", memoryCommit="1e667c6d"),
        ]
        node = build_engine_processes([_facts(ledger_rows=rows, ledger_row_count=11)], [], [], [])[
            0
        ]
        self.assertEqual(node.ledgerRows, rows)
        self.assertEqual(node.ledgerRowCount, 11)

    def test_ledger_rows_default_empty(self) -> None:
        node = build_engine_processes([_facts()], [], [], [])[0]
        self.assertEqual(node.ledgerRows, [])
        self.assertEqual(node.ledgerRowCount, 0)

    def test_disposed_worktrees_drop_from_engine_processes(self) -> None:
        # 05l Gap B: a cleaned-up/abandoned worktree (runtime gone) drops from the active engine-room
        # so the frontend animates the removal instead of rendering a phantom. cleanup-pending stays --
        # the de-materialise beat still needs a live node to animate.
        self.assertEqual(
            len(build_engine_processes([_facts(contract={"cleanup": "pending"})], [], [], [])), 1
        )
        self.assertEqual(
            build_engine_processes([_facts(contract={"cleanup": "completed"})], [], [], []), []
        )
        self.assertEqual(
            build_engine_processes([_facts(contract={"cleanup": "abandoned"})], [], [], []), []
        )

    def test_carryover_done_at_surfaces_on_the_node(self) -> None:
        # 05m: the dashboard reads the carryover milestone off the projected node (5k renders it).
        node = build_engine_processes(
            [
                _facts(
                    status={
                        "code_worktree_exists": True,
                        "carryoverDoneAt": "2026-06-21T09:00:00+02:00",
                    }
                )
            ],
            [],
            [],
            [],
        )[0]
        self.assertEqual(node.carryoverDoneAt, "2026-06-21T09:00:00+02:00")

    def test_carryover_done_at_defaults_to_none(self) -> None:
        node = build_engine_processes([_facts(status={"code_worktree_exists": True})], [], [], [])[
            0
        ]
        self.assertIsNone(node.carryoverDoneAt)

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
                    "completedPhases": [
                        "codegraphcontext-code seed: ok",
                        "grepai-memory clone: ok",
                    ],
                    "failedPhases": [],
                },
            }
        )
        cgc = ProviderNode(
            id="codegraphcontext-code@grp",
            state="configured",
            ok=True,
            scope="worktree",
            role="code",
            worktreeGroup="grp",
        )
        grepai = ProviderNode(
            id="grepai-memory@grp",
            state="configured",
            ok=True,
            scope="worktree",
            role="memory",
            worktreeGroup="grp",
        )
        nodes = build_engine_processes(
            [facts],
            [],
            [grepai, cgc],
            [SetupProgressNode(group="grp", state="ok", completedCount=4)],
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
            group="grp",
            state="running",
            currentPhase="grepai-memory clone",
            heartbeatAgeSeconds=2.0,
        )
        node = build_engine_processes([facts], [], [], [setup])[0]
        self.assertEqual(node.phase, "provider-setup")
        self.assertEqual(node.health, "running")
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["cgc-seed"], "running")
        self.assertEqual(states["grepai-clone"], "running")
        self.assertEqual(node.providers, [])

    def test_missing_provider_stack_projects_missing_engine_slots(self) -> None:
        node = build_engine_processes(
            [
                _facts(
                    status={
                        "code_worktree_exists": True,
                        "memory_worktree_exists": True,
                    }
                )
            ],
            [],
            [],
            [],
        )[0]
        self.assertEqual(
            [(provider.role, provider.runtimeState, provider.factState) for provider in node.providers],
            [("code", "missing", "missing"), ("memory", "missing", "missing")],
        )
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["cgc-seed"], "planned")
        self.assertEqual(states["grepai-clone"], "planned")
        self.assertTrue(any("provider runtime not observed" in fact for fact in node.missingFacts))

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
            [],
            [],
            [],
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
        facts = _facts(
            contract={"worktree_group": "/w/r/260610-grp"}, status={"code_worktree_exists": True}
        )
        prov = ProviderNode(
            id="cgc@260610-grp",
            state="configured",
            ok=True,
            scope="worktree",
            role="code",
            worktreeGroup="260610-grp",
        )
        node = build_engine_processes([facts], [], [prov], [])[0]
        self.assertEqual([p.id for p in node.providers], ["cgc@260610-grp", "missing-memory@260610-grp"])
        self.assertEqual(node.providers[1].factState, "missing")

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

    def test_landing_and_strategy_default_empty(self) -> None:
        # Additive 5h fields: no landing observation + no recorded strategy -> empty/None (no break).
        node = build_engine_processes([_facts(status={"code_worktree_exists": True})], [], [], [])[
            0
        ]
        self.assertEqual(node.landing, [])
        self.assertIsNone(node.integrationStrategy)

    def test_landing_and_strategy_mapped_from_facts(self) -> None:
        node = build_engine_processes(
            [
                _facts(
                    contract={"integration_strategy": "ff-only"},
                    status={
                        "code_worktree_exists": True,
                        "landing": [
                            {
                                "kind": "origin-feat",
                                "label": "origin/feat-x",
                                "state": "pushed",
                                "factState": "observed",
                            },
                            {
                                "kind": "pr",
                                "label": "PR #128",
                                "state": "open",
                                "factState": "observed",
                            },
                        ],
                    },
                )
            ],
            [],
            [],
            [],
        )[0]
        self.assertEqual(node.integrationStrategy, "ff-only")
        self.assertEqual([ref.kind for ref in node.landing], ["origin-feat", "pr"])
        self.assertEqual(node.landing[1].state, "open")

    def test_project_workspace_wires_engine_processes(self) -> None:
        proj = project_workspace(
            [],
            enclosures=[],
            providers=[],
            now=FRESH,
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
                task_name="demo task",
                repo_name="r",
                workflow_kind="light",
                memory_mode="disabled",
                coordination_root=root,
                code_repo_path=root / "repo",
                code_source_branch="main",
                code_work_branch="ar/x",
                code_base_commit="abc",
                worktree_name="demo-wt",
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)
            facts = read_engine_process_facts(root)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].contract["task_name"], "demo task")
            self.assertIn("phase", facts[0].guidance)

    def test_reader_skips_inactive_engine_process_groups_when_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                task_name="demo task",
                repo_name="r",
                workflow_kind="light",
                memory_mode="disabled",
                coordination_root=root,
                code_repo_path=root / "repo",
                code_source_branch="main",
                code_work_branch="ar/x",
                code_base_commit="abc",
                worktree_name="demo-wt",
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)

            self.assertEqual(
                read_engine_process_facts(root, active_worktree_groups={"other-group"}), []
            )
            facts = read_engine_process_facts(
                root, active_worktree_groups={contract.worktree_group.name}
            )

            self.assertEqual(len(facts), 1)

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
                root,
                repo_name="r",
                task_name="t",
                worktree_name="wt",
                worktree_group="/w/r/wt-ar",
                phase="memory-blocked",
                memory_mode="external",
                blocked_reason="no ledger mapping",
                completed_phases=("preflight", "code-worktree"),
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
