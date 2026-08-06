from __future__ import annotations

import unittest

from agents_remember.controlplane.attention_dismissals import AttentionDismissalRecord
from agents_remember.controlplane.records import GateAnchor, create_gate
from agents_remember.observer.events import Event
from agents_remember.observer.projection import (
    DriftSnapshotNode,
    ProviderNode,
    SetupProgressNode,
    SidecarStaleNode,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    build_analytics,
    build_attention_queue,
    project_lifecycle,
    project_workspace,
    staleness_histogram,
)
from test_observer_projection import DORMANT, FRESH, STALE, T0, _event, _started


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
            AnalyticalInputs(
                drift_snapshots=[],
                sidecar_staleness=nodes,
                setup_summaries=[],
                setup_progress=[],
                route_coverage=[],
                tool_reports=[],
                ledgers=[],
                stalest_limit=5,
            ),
        )
        self.assertEqual(len(analytics.stalestSidecars), 5)
        self.assertEqual(analytics.stalestSidecars[0].ageSeconds, 19 * 86400.0)

    def test_project_workspace_wires_analytics_and_histogram(self) -> None:
        sidecars = [self._stale(3600.0), self._stale(200 * 86400.0)]
        proj = project_workspace(
            [[_started()]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(
                drift_snapshots=[
                    DriftSnapshotNode(
                        repository="r", branch="main", counts={"drifted": 1}, actionableCount=1
                    )
                ],
                sidecar_staleness=sidecars,
            ),
        )
        self.assertEqual(proj.metrics.stalenessHistogram["<7d"], 1)
        self.assertEqual(proj.metrics.stalenessHistogram[">90d"], 1)
        self.assertEqual(len(proj.analytics.driftSnapshots), 1)
        self.assertEqual(len(proj.analytics.stalestSidecars), 2)

    def test_3a_callers_get_empty_analytics(self) -> None:
        proj = project_workspace(
            [[_started()]], structure=WorkspaceStructure(enclosures=[], providers=[]), now=FRESH
        )
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
            structure=WorkspaceStructure(
                enclosures=[], providers=[ProviderNode(id="cgc", state="stopped", ok=False)]
            ),
            now=FRESH,
        )
        queue = proj.analytics.attentionQueue
        self.assertEqual(queue[0].kind, "provider-down")  # alarm sorts above warn
        blocked = next(item for item in queue if item.kind == "blocked-gate")
        self.assertEqual((blocked.lifecycleId, blocked.detail), ("LC2", "Approve the plan?"))

    def test_stale_session_is_info(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=STALE,
        )
        item = proj.analytics.attentionQueue[0]
        self.assertEqual(
            (item.kind, item.severity, item.lifecycleId), ("stale-session", "info", "LC1")
        )

    def test_dormant_fleeting_is_info(self) -> None:
        proj = project_workspace(
            [[_started(fleeting=True, lifecycle_id="LC1")]],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=DORMANT,
        )
        self.assertEqual(proj.analytics.attentionQueue[0].kind, "dormant-fleeting")

    def test_awaiting_developer_yields_one_info_item(self) -> None:
        # NOTIFY-AND-CONTINUE turn end: exactly one awaiting-developer item,
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
            structure=WorkspaceStructure(enclosures=[], providers=[]),
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
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(
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
                setup_progress=[
                    SetupProgressNode(group="g1", state="ok", failedPhases=["cgc setup"])
                ],
            ),
        )
        self.assertEqual(
            {item.kind for item in proj.analytics.attentionQueue},
            {"actionable-drift", "failed-setup"},
        )
        drift = next(
            item for item in proj.analytics.attentionQueue if item.kind == "actionable-drift"
        )
        self.assertEqual(drift.id, "actionable-drift:repo-a:main")
        self.assertEqual(drift.title, "2 actionable drift in repo-a")
        self.assertEqual(drift.signalTs, "2026-06-13T18:00:00+00:00")
        self.assertIn("/memory/ar-repo-a", drift.detail or "")
        self.assertIn("/tmp/drift-report.md", drift.detail or "")

    def test_calm_tree_has_empty_queue(self) -> None:
        proj = project_workspace(
            [[_started()]], structure=WorkspaceStructure(enclosures=[], providers=[]), now=FRESH
        )
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
            structure=WorkspaceStructure(enclosures=[], providers=kw.get("providers", [])),
            now=now,
            given=AnalyticalInputs(
                gates=kw.get("gates") or [], attention_dismissals=dismissals or {}
            ),
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
        gate = create_gate(
            "closeout-approval", gate_id="G1", now=T0, anchor=GateAnchor(lifecycle_id="LC1")
        )
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
            build_attention_queue(
                [],
                [],
                AnalyticalInputs(
                    drift_snapshots=[old_snapshot],
                    setup_progress=[],
                    attention_dismissals=dismissed,
                ),
            ),
            [],
        )

        newer_snapshot = DriftSnapshotNode(
            repository="repo-a",
            branch="main",
            actionableCount=1,
            checkedAt="2026-06-13T18:00:11+00:00",
        )
        queue = build_attention_queue(
            [],
            [],
            AnalyticalInputs(
                drift_snapshots=[newer_snapshot], setup_progress=[], attention_dismissals=dismissed
            ),
        )
        self.assertEqual([item.kind for item in queue], ["actionable-drift"])

    def test_newer_turn_end_supersedes_dismissal(self) -> None:
        # A fresh turn-end re-enters awaiting (a newer stateEnteredAt) and re-surfaces the
        # item despite an older dismissal -- a dismissal acknowledges THIS occurrence only.
        re_log = [
            _started(lifecycle_id="LC1"),
            _event(
                "lifecycle.awaiting-developer", lifecycle_id="LC1", ts=self.AWAIT_TS, summary="1"
            ),
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
