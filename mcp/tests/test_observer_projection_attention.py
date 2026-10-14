from __future__ import annotations

import unittest

from agents_remember.controlplane.attention_dismissals import AttentionDismissalRecord
from agents_remember.observer.projection import (
    DriftSnapshotNode,
    ProviderNode,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    build_attention_queue,
    project_workspace,
)
from test_observer_projection import STALE, T0, _event, _started


class AttentionQueueTests(unittest.TestCase):
    def test_provider_down_ranks_above_a_quiet_session(self) -> None:
        proj = project_workspace(
            [[_started(lifecycle_id="LC2")]],
            structure=WorkspaceStructure(
                enclosures=[], providers=[ProviderNode(id="cgc", state="stopped", ok=False)]
            ),
            now=STALE,
        )
        queue = proj.analytics.attentionQueue
        self.assertEqual(queue[0].kind, "provider-down")  # alarm sorts above info
        quiet = next(item for item in queue if item.kind == "stale-session")
        self.assertEqual((quiet.lifecycleId, quiet.signalTs), ("LC2", T0))


class AttentionDismissalTests(unittest.TestCase):
    """Leaf-28 S5.2: a lifecycle acknowledgement suppresses one current occurrence,
    and a newer triggering signal re-surfaces it."""

    DISMISS_TS = "2026-06-13T18:00:10+00:00"  # after T0

    def _queue_for(self, logs, *, now, dismissals=None, **kw):  # type: ignore[no-untyped-def]
        return project_workspace(
            logs,
            structure=WorkspaceStructure(enclosures=[], providers=kw.get("providers", [])),
            now=now,
            given=AnalyticalInputs(attention_dismissals=dismissals or {}),
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

    def test_newer_activity_supersedes_dismissal(self) -> None:
        # A dismissal acknowledges THIS occurrence only: the session that went quiet at T0
        # stays cleared, and the same session going quiet again after later activity returns.
        dismissed = self._dismissal("stale-session:LC1", kind="stale-session")
        quiet_since_start = [_started(lifecycle_id="LC1")]
        self.assertEqual(self._queue_for([quiet_since_start], now=STALE, dismissals=dismissed), [])

        quiet_after_later_activity = [
            _started(lifecycle_id="LC1"),
            _event("lifecycle.heartbeat", lifecycle_id="LC1", ts="2026-06-13T18:00:20+00:00"),
        ]
        queue = self._queue_for([quiet_after_later_activity], now=STALE, dismissals=dismissed)
        self.assertEqual([item.kind for item in queue], ["stale-session"])  # :20 is after :10
