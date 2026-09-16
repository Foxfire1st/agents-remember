"""Reviewer production wiring: canonical terminal truth wakes the current manager.

A structurally bound reviewer seat is a short-lived seat. Its durable verdict is the substantive
handoff and its canonical terminal turn is the completion signal, so the reviewer must not have to
remember a completion message to its manager. This module proves the production relay carries that
signal for the reviewer role, and it proves it the way the failure actually occurs: from adapter
evidence, not from catalog truth a test typed in.

The chain under test is the production chain end to end:

* the real terminal observer (``TerminalCatalogLivenessSweeper`` over the real ``TerminalCatalog``)
  reads a live bridge snapshot and a native terminal-evidence page;
* the real canonical projectors decide the seat turn state, the terminal outcome and the terminal
  evidence identity -- the test never writes ``turn_state``, ``terminal_outcome`` or
  ``terminal_evidence_id`` into a row;
* the real notifier sweep (``run_agent_notifier_sweep``) derives the finding, resolves the current
  structural owner and persists one durable inbox row;
* that durable inbox row is the manager's wake, addressed by current occupancy rather than by any
  runtime id a brief once carried.

What is faked, and where the fakes stop:

* the tmux host is a double that answers "session alive" -- the process boundary, not the relay;
* the bridge snapshot reader and the native evidence reader are doubles at the *external process*
  port; the lift itself is the production one (``NativeEvidencePage`` in,
  ``latest_native_terminal_evidence`` out), so the outcome and the evidence identity come from the
  projector registry rather than from this module.

The structural rows (a manager seat and a reviewer seat) are created directly, which is the
permitted setup boundary: what may not be pre-populated is the terminal truth under test, and
``test_leaf_reviewer_completion_reaches_the_current_manager_without_a_completion_post`` asserts it
is absent before the first observation pass and produced by that pass.

Nothing here composes the serving app or issues an HTTP request. The wake must not depend on the
terminal-session read route, and the producer of the truth this signal rides on is asserted to be
the observation pass rather than a seeded row or a read-side projection.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    SubmissionReceipt,
)
from agents_remember.models.conversations.evidence import (
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.observer.store import EventStore
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.state_signals import evaluate_state_signal_findings
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_evidence import (
    TerminalEvidenceRead,
    latest_native_terminal_evidence,
)
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    SnapshotReader,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    TerminalEvidenceReader,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks.document_refs import TaskDocumentTopology
from test_state_signal_relay import _accepted_paster, _write_task_topology

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
SWEEP_STEP = timedelta(seconds=11)
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")
REVIEWER = "reviewer-1"
MANAGER = "manager-1"
REVIEW_TURN = "review-turn-1"
REVIEW_EVIDENCE_ID = f"native:{REVIEW_TURN}"
LATER_REVIEW_TURN = "review-turn-2"
LATER_REVIEW_EVIDENCE_ID = f"native:{LATER_REVIEW_TURN}"

#: A pane whose own diagnostic reading claims the reviewer's turn is over. The relay must not wake
#: anyone from this: pane text is diagnostic detail, and it never becomes terminal evidence.
PANE_TEXT = "reviewer verdict written for the candidate\nready"
PANE_DIAGNOSTIC = "turn-ended"

#: Verdict vocabulary the notifier must never speak. The relay wakes the manager; it does not read
#: the verdict, and it cannot turn a terminal outcome into an acceptance.
VERDICT_WORDS = ("accept", "approv", "pass", "block", "reject")


def _entry(session_id: str, **overrides: object) -> TerminalCatalogEntry:
    fields: dict[str, object] = dict(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="pi",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("pi",),
        created_at="2026-07-13T00:00:00+00:00",
        last_attached_at="2026-07-13T00:00:00+00:00",
        status="running",
    )
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)  # type: ignore[arg-type]


def _busy_manager(session_id: str = MANAGER) -> TerminalCatalogEntry:
    """A master-scoped manager mid-turn: reachable, but not at a delivery boundary."""

    return _entry(
        session_id,
        task_document_ref=MASTER,
        seat_role="manager",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        control_state="ready",
        turn_state="working",
        turn_state_changed_at=(NOW - timedelta(minutes=3)).isoformat(),
    )


def _reviewer(session_id: str = REVIEWER, *, spawned_by: str = MANAGER) -> TerminalCatalogEntry:
    """The production shape of a leaf reviewer: leaf document, plane-stamped manager parent."""

    return _entry(
        session_id,
        task_document_ref=LEAF,
        seat_role="reviewer",
        spawn_role="reviewer",
        spawned_by_session=spawned_by,
        structural_parent_task_document_ref=MASTER,
        structural_parent_role="manager",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        control_state="ready",
    )


def _accepted_receipt(request_id: str) -> SubmissionReceipt:
    return SubmissionReceipt(
        request_id=request_id,
        acceptance="immediate",
        submitted_at=NOW.isoformat(),
        accepted_at=NOW.isoformat(),
    )


class _AliveHost:
    """A tmux host whose sessions are alive: the process boundary, not the relay under test.

    ``gone`` names the tmux sessions this scenario presents as no longer running, so a departed
    seat stays departed across a sweep instead of being re-probed back to life.
    """

    def __init__(self, gone: frozenset[str] = frozenset()) -> None:
        self.gone = gone
        self.probes = 0

    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name not in self.gone

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        self.probes += 1
        if tmux_name in self.gone:
            return TmuxProbeResult(exists=False, evidence="pane-gone")
        return TmuxProbeResult(exists=True, evidence="alive")


def _native_page(stop_reason: str, native_id: str = REVIEW_TURN) -> NativeEvidencePage:
    """One durable native turn entry, carrying the vendor's own end-of-turn reason."""

    frame = NativeEvidenceFrame(
        native_id=native_id,
        native_parent_id=None,
        native_type="message",
        created_at=NOW.isoformat(),
        raw={
            "id": native_id,
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "verdict written for the candidate"}],
                "stopReason": stop_reason,
            },
        },
    )
    return NativeEvidencePage(
        frames=(frame,), next_cursor=None, truncated=False, bridge_epoch="epoch-1"
    )


def _terminal_projection(stop_reason: str, native_id: str = REVIEW_TURN) -> TerminalEvidenceRead:
    """The production lift for one native page: projector-decided outcome and evidence identity."""

    return TerminalEvidenceRead(
        projection=latest_native_terminal_evidence(_native_page(stop_reason, native_id), "pi"),
        evidence_sequence=7,
    )


def _snapshot(entry: TerminalCatalogEntry, activity: str) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity=activity,  # type: ignore[arg-type]
        acceptance="immediate",
        vendor_session_id=f"vendor-{entry.id}",
        raw={},
    )


class _Bridge:
    """One controlled adapter endpoint, scripted for exactly one seat.

    ``activities`` supplies the reviewed seat's process/turn reading per pass and
    ``terminal_reads`` supplies its native evidence page per pass. An exhausted
    ``terminal_reads`` queue reports no new terminal claim, exactly as the production reader does
    once its cursor has advanced past the read window.

    Every other observed session -- the manager in this scenario, which is mid-turn -- reports a
    live turn in progress and no terminal evidence, so the script belongs to the reviewer alone
    rather than to whichever row the sweep happens to visit first.
    """

    def __init__(
        self,
        session_id: str,
        activities: list[str],
        terminal_reads: list[TerminalEvidenceRead],
    ) -> None:
        self.session_id = session_id
        self.activities = activities
        self.terminal_reads = terminal_reads
        self.snapshots = 0
        self.terminal_calls = 0

    def snapshot(self, entry: TerminalCatalogEntry) -> AdapterSnapshot:
        if entry.id != self.session_id:
            return _snapshot(entry, "running")
        activity = self.activities[min(self.snapshots, len(self.activities) - 1)]
        self.snapshots += 1
        return _snapshot(entry, activity)

    def terminal(self, entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
        if entry.id != self.session_id:
            return TerminalEvidenceRead(projection=None)
        self.terminal_calls += 1
        return self.terminal_reads.pop(0) if self.terminal_reads else TerminalEvidenceRead(None)


class ReviewerTurnOwnerWakeTests(unittest.TestCase):
    """The reviewer role's own production-wiring wake scenario."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        _write_task_topology(self.coordination_root)
        self.topology = TaskDocumentTopology(self.coordination_root)
        self.leaf_document = (
            self.coordination_root / "tasks" / "repo-a" / "260707_master" / "leaf-9.json"
        )
        observer_root = self.coordination_root / "logs" / "observer"
        self.catalog = TerminalCatalog(root / "catalog.json")
        self.inbox_store = OperatorInboxStore(observer_root)
        self.moment = NOW
        # The relay's own topology writer owns these two documents; a mismatch would silently aim
        # every assertion below at the wrong document, so it is proven rather than assumed.
        self.assertEqual(self.topology.parent(LEAF), MASTER)
        self.assertEqual(self.topology.altitude(LEAF), "leaf")
        self.assertTrue(self.leaf_document.is_file(), self.leaf_document)

    # -- fixtures ---------------------------------------------------------------------------

    def _context(self) -> AgentNotifierContext:
        root = self.coordination_root / "logs" / "observer"
        return AgentNotifierContext(
            catalog=self.catalog,
            host=cast(TerminalHost, _AliveHost()),
            paster=_accepted_paster(),
            inbox_store=self.inbox_store,
            expectation_store=ExpectationRowStore(root),
            signal_cooldown_store=AgentNotifierSignalCooldownStore(root),
            event_store=EventStore(root),
            heartbeat_store=AgentNotifierHeartbeatStore(root),
            coordination_root=self.coordination_root,
            stale_seat_seconds=60.0,
            redeliver_rate_limit_seconds=900.0,
        )

    def _sweeper(
        self, bridge: _Bridge, *, gone: frozenset[str] = frozenset()
    ) -> TerminalCatalogLivenessSweeper:
        snapshot_reader: SnapshotReader = bridge.snapshot
        terminal_reader: TerminalEvidenceReader = bridge.terminal
        return TerminalCatalogLivenessSweeper(
            self.catalog,
            _AliveHost(gone),
            now=lambda: self.moment,
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=0.0,
                ),
                pane_capturer=lambda _tmux_name: PANE_TEXT,
                snapshot_reader=snapshot_reader,
                terminal_reader=terminal_reader,
            ),
        )

    def _observe(
        self, sweeper: TerminalCatalogLivenessSweeper, passes: int
    ) -> TerminalCatalogEntry:
        for _ in range(passes):
            sweeper.refresh()
            self.moment = self.moment + SWEEP_STEP
        return self._row(REVIEWER)

    def _row(self, session_id: str) -> TerminalCatalogEntry:
        entry = self.catalog.get(session_id)
        assert entry is not None, session_id
        return entry

    def _signals(self) -> list[OperatorInboxEntry]:
        return [
            row for row in self.inbox_store.current().values() if row.messageKind == "state-signal"
        ]

    def _reviewer_wakes(self) -> list[OperatorInboxEntry]:
        return [row for row in self._signals() if row.subjectAgentId == REVIEWER]

    def _rows_for(self, agent_id: str) -> list[OperatorInboxEntry]:
        return [row for row in self.inbox_store.current().values() if row.agentId == agent_id]

    def _sweep_notifier(self) -> None:
        run_agent_notifier_sweep(self._context(), now=self.moment)

    def _assert_no_verdict_inference(self, signal: OperatorInboxEntry) -> None:
        spoken = f"{signal.ask}\n{signal.response}".lower()
        for word in VERDICT_WORDS:
            self.assertNotIn(word, spoken)

    # -- the wake ---------------------------------------------------------------------------

    def test_leaf_reviewer_completion_reaches_the_current_manager_without_a_completion_post(
        self,
    ) -> None:
        """Canonical reviewer terminal truth, produced by observation, wakes the current manager.

        The reviewer posts nothing. The catalog row starts with no terminal claim at all, and the
        only producer of the completed outcome and its evidence identity is the real observation
        pass below -- so a worker-only relay, a relay fed by a hand-written ``completed`` row, or a
        relay that stopped lifting terminal evidence would each leave the manager asleep here.
        """

        self.catalog.upsert(_busy_manager())
        self.catalog.upsert(_reviewer())
        before = self._row(REVIEWER)
        self.assertEqual(before.turn_state, None)
        self.assertEqual(before.terminal_outcome, None)
        self.assertEqual(before.terminal_evidence_id, None)

        sweeper = self._sweeper(
            _Bridge(REVIEWER, ["running", "idle"], [_terminal_projection("stop")])
        )
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: _accepted_receipt(submission.request_id),
        ) as submit:
            observed = self._observe(sweeper, 3)
            self.assertEqual(observed.turn_state, "turn-ended")
            self.assertEqual(observed.terminal_outcome, "completed")
            self.assertEqual(observed.terminal_evidence_id, REVIEW_EVIDENCE_ID)

            self._sweep_notifier()

            wakes = self._reviewer_wakes()
            self.assertEqual(len(wakes), 1, wakes)
            wake = wakes[0]
            self.assertEqual(wake.agentId, MANAGER)
            self.assertEqual(wake.recipientRole, "manager")
            self.assertEqual(wake.seatRole, "reviewer")
            self.assertEqual(wake.subjectAgentId, REVIEWER)
            self.assertEqual(wake.subjectTaskDocumentRef, LEAF)
            self.assertEqual(wake.taskDocumentRef, MASTER)
            self.assertIn("completed", wake.ask)
            self.assertIn(REVIEW_EVIDENCE_ID, wake.ask)
            self.assertIn("as reviewer", wake.response)
            self.assertIn("outcome completed", wake.response)
            self.assertIn(REVIEW_EVIDENCE_ID, wake.response)
            self.assertEqual(self._row(REVIEWER).state_signal_emitted_for, REVIEW_EVIDENCE_ID)
            self._assert_no_verdict_inference(wake)

            # The busy owner holds the durable row: no submission mid-turn, and no read-side
            # traffic exists in this scenario to produce or deliver anything.
            self.assertEqual(submit.call_count, 0)
            self.assertEqual(wake.state, "pending")
            self.assertEqual(wake.deliveryState, "queued")
            self.assertEqual(wake.adapterDeliveryState, "queued")
            self.assertIsNotNone(wake.nextAttemptAt)

            # The manager reaches a turn boundary on a later tick: the same durable row lands,
            # exactly once.
            self.moment = self.moment + SWEEP_STEP
            self.catalog.upsert(
                replace(
                    self._row(MANAGER),
                    turn_state="turn-ended",
                    turn_state_changed_at=self.moment.isoformat(),
                )
            )
            self._sweep_notifier()
            self.assertEqual(submit.call_count, 1)
            landed = self._reviewer_wakes()
            self.assertEqual(len(landed), 1, landed)
            self.assertEqual(landed[0].id, wake.id)
            self.assertEqual(landed[0].deliveryState, "delivered")
            self.assertEqual(landed[0].adapterDeliveryState, "accepted")

    def test_liveness_readiness_and_pane_text_alone_never_authorize_the_reviewer_wake(self) -> None:
        """A live, ready, pane-classified reviewer is not terminal truth, and must not wake anyone.

        The row below is proven endable through the real observer: it reaches ``turn-ended`` with a
        live bridge, a ready control state and a pane diagnostic that itself reads ``turn-ended``.
        It carries no canonical terminal outcome and no evidence identity, so nothing may be
        emitted -- eligibility is the canonical completion fact, not liveness, not control
        readiness, and not the pane's opinion. The same row then wakes the manager the moment a
        canonical terminal identity exists, so the silence above is about missing evidence and not
        about a fixture that could never emit.
        """

        self.catalog.upsert(_busy_manager())
        self.catalog.upsert(_reviewer())
        bridge = _Bridge(REVIEWER, ["running", "idle"], [])
        sweeper = self._sweeper(bridge)

        self._observe(sweeper, 3)

        row = self._row(REVIEWER)
        self.assertEqual(row.status, "running")
        self.assertEqual(row.control_state, "ready")
        self.assertEqual(row.control_activity, "idle")
        self.assertEqual(row.control_acceptance, "immediate")
        self.assertEqual((row.control_raw or {}).get("paneDiagnostic"), PANE_DIAGNOSTIC)
        self.assertEqual(row.turn_state, "turn-ended")
        self.assertEqual(row.terminal_outcome, None)
        self.assertEqual(row.terminal_evidence_id, None)

        self.assertEqual(evaluate_state_signal_findings(self.catalog, self.topology), [])
        self._sweep_notifier()
        self.assertEqual(self._reviewer_wakes(), [])
        self.assertEqual(self._row(REVIEWER).state_signal_emitted_for, None)

        # Control: the identical row becomes eligible the moment canonical evidence exists.
        bridge.terminal_reads.append(_terminal_projection("stop"))
        self._observe(sweeper, 2)
        self.assertEqual(self._row(REVIEWER).terminal_evidence_id, REVIEW_EVIDENCE_ID)
        self._sweep_notifier()
        self.assertEqual(len(self._reviewer_wakes()), 1)

    def test_interrupted_reviewer_wakes_as_interrupted_and_is_never_read_as_accepted(self) -> None:
        """An interrupted reviewer wakes the manager with ``interrupted`` and no verdict language.

        An interrupted turn is an explicit recovery state: the manager has to be able to tell it
        from a finished review. The relay preserves the outcome, attributes no developer interrupt
        that never happened, speaks no verdict vocabulary, and touches no task document.
        """

        self.catalog.upsert(_busy_manager())
        self.catalog.upsert(_reviewer())
        leaf_before = self.leaf_document.read_bytes()
        sweeper = self._sweeper(
            _Bridge(REVIEWER, ["running", "idle"], [_terminal_projection("aborted")])
        )

        self._observe(sweeper, 2)

        row = self._row(REVIEWER)
        self.assertEqual(row.turn_state, "turn-ended")
        self.assertEqual(row.terminal_outcome, "interrupted")
        self.assertEqual(row.terminal_evidence_id, REVIEW_EVIDENCE_ID)

        self._sweep_notifier()

        wakes = self._reviewer_wakes()
        self.assertEqual(len(wakes), 1, wakes)
        wake = wakes[0]
        self.assertIn("interrupted", wake.ask)
        self.assertIn("outcome interrupted", wake.response)
        self.assertIn("interrupted_by=unknown", wake.response)
        self.assertEqual(wake.seatRole, "reviewer")
        self.assertEqual(wake.subjectTaskDocumentRef, LEAF)
        self._assert_no_verdict_inference(wake)
        self.assertEqual(self.leaf_document.read_bytes(), leaf_before)

    def test_reviewer_wake_follows_the_current_manager_not_a_departed_generation(self) -> None:
        """The wake is addressed by current occupancy, never by the id the reviewer spawned under.

        The reviewer's recorded ``spawned_by_session`` names a manager generation that has exited.
        A relay that reused that brief-stamped runtime id would wake a dead seat and leave the live
        manager asleep; the durable row here is addressed only to the current manager.
        """

        self.catalog.upsert(
            _entry(
                "manager-old",
                task_document_ref=MASTER,
                seat_role="manager",
                status="exited",
                tmux_name="ar-manager-old",
            )
        )
        self.catalog.upsert(_busy_manager("manager-current"))
        self.catalog.upsert(_reviewer(spawned_by="manager-old"))
        sweeper = self._sweeper(
            _Bridge(REVIEWER, ["running", "idle"], [_terminal_projection("stop")]),
            gone=frozenset({"ar-manager-old"}),
        )

        self._observe(sweeper, 3)
        self.assertEqual(self._row(REVIEWER).terminal_outcome, "completed")
        self.assertEqual(self._row(REVIEWER).spawned_by_session, "manager-old")

        self._sweep_notifier()

        wakes = self._reviewer_wakes()
        self.assertEqual(len(wakes), 1, wakes)
        self.assertEqual(wakes[0].agentId, "manager-current")
        current_rows = self._rows_for("manager-current")
        self.assertEqual(len(current_rows), 1, current_rows)
        self.assertEqual(current_rows[0].subjectAgentId, REVIEWER)
        self.assertEqual(self._rows_for("manager-old"), [])

    def test_one_terminal_evidence_identity_yields_exactly_one_reviewer_signal(self) -> None:
        """Re-observing the same terminal evidence identity never mints a second reviewer signal.

        A crash, a restart or an ordinary repeated sweep re-reads the same canonical evidence. The
        marker on the reviewed seat is what makes the signal idempotent, so the manager wakes once
        per completed review rather than once per observation pass -- including after the first row
        has landed and can no longer absorb a repeat by coalescing onto a pending row.
        """

        self.catalog.upsert(_busy_manager())
        self.catalog.upsert(_reviewer())
        sweeper = self._sweeper(
            _Bridge(REVIEWER, ["running", "idle"], [_terminal_projection("stop")])
        )
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: _accepted_receipt(submission.request_id),
        ):
            self._observe(sweeper, 3)
            self._sweep_notifier()
            first = self._reviewer_wakes()
            self.assertEqual(len(first), 1, first)

            # The manager crosses a boundary and the durable row lands.
            self.moment = self.moment + SWEEP_STEP
            self.catalog.upsert(
                replace(
                    self._row(MANAGER),
                    turn_state="turn-ended",
                    turn_state_changed_at=self.moment.isoformat(),
                )
            )
            self._sweep_notifier()
            landed = self._reviewer_wakes()
            self.assertEqual(len(landed), 1, landed)
            self.assertEqual(landed[0].deliveryState, "delivered")

            # Every later sweep re-reads the same terminal evidence identity.
            self._observe(sweeper, 2)
            self._sweep_notifier()
            self._observe(sweeper, 1)
            self._sweep_notifier()

            repeat = self._reviewer_wakes()
            self.assertEqual(len(repeat), 1, repeat)
            self.assertEqual(repeat[0].id, first[0].id)
            self.assertEqual(repeat[0].ask, first[0].ask)
            self.assertEqual(self._row(REVIEWER).state_signal_emitted_for, REVIEW_EVIDENCE_ID)
            self.assertEqual(self._row(REVIEWER).terminal_evidence_id, REVIEW_EVIDENCE_ID)

    def test_a_failed_reviewer_turn_never_wakes_the_manager_and_stays_eligible(self) -> None:
        """``failed`` is not one of the two wakeable outcomes, even though it is real terminal truth.

        The eligible set is `completed` and `interrupted`, and that boundary is deliberate: a failed
        turn is a failure of the attempt, not a delivered review, so waking the manager on it would
        report a completion that never happened. The discriminating shape is production-reachable,
        not hypothetical -- the pi projector settles ``stopReason="error"`` into ``outcome="failed"``
        while still carrying a real evidence identity, so a row arrives here with BOTH terminal
        fields present and a non-canonical outcome. Widening the eligible set to any non-null
        outcome leaves every truthless row refused and only this one exposed, which is why the case
        is asserted on the derived row itself rather than on an empty terminal claim.

        The seat also stays *eligible*: no marker is stamped, so the same reviewer waking later with
        a canonical outcome still reaches its manager. Silence here is a refusal to report, not a
        consumed episode.
        """

        self.catalog.upsert(_busy_manager())
        self.catalog.upsert(_reviewer())
        bridge = _Bridge(
            REVIEWER,
            ["running", "idle"],
            [_terminal_projection("error"), _terminal_projection("stop", LATER_REVIEW_TURN)],
        )
        sweeper = self._sweeper(bridge)

        self._observe(sweeper, 1)

        failed = self._row(REVIEWER)
        self.assertEqual(failed.turn_state, "turn-ended")
        self.assertEqual(failed.terminal_outcome, "failed")
        self.assertEqual(failed.terminal_evidence_id, REVIEW_EVIDENCE_ID)

        self.assertEqual(evaluate_state_signal_findings(self.catalog, self.topology), [])
        self._sweep_notifier()
        self.assertEqual(self._reviewer_wakes(), [])
        self.assertEqual(self._row(REVIEWER).state_signal_emitted_for, None)

        # The refusal consumed nothing: the same seat, a later canonical turn, wakes the manager.
        self._observe(sweeper, 2)
        self.assertEqual(self._row(REVIEWER).terminal_outcome, "completed")
        self.assertEqual(self._row(REVIEWER).terminal_evidence_id, LATER_REVIEW_EVIDENCE_ID)
        self._sweep_notifier()
        wakes = self._reviewer_wakes()
        self.assertEqual(len(wakes), 1, wakes)
        self.assertEqual(wakes[0].agentId, MANAGER)
        self.assertIn(LATER_REVIEW_EVIDENCE_ID, wakes[0].ask)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
