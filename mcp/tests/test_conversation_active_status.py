"""Canonical status classification, revision discipline, and orchestration parity."""

from __future__ import annotations

import unittest

from agents_remember.serving.conversation.active.status import (
    ConversationStatusService,
    ProcessEvidence,
    TurnTerminalEvidence,
    classify_snapshot,
    seat_turn_state_for,
    snapshot_seat_turn_state,
)
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    ConversationStatus,
)
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    PendingInteraction,
)
from agents_remember.serving.hosted_control_projection import snapshot_turn_state

NOW = "2026-07-19T08:00:00+00:00"


def _identity() -> ActiveConversationRef:
    return ActiveConversationRef(
        harness_id="codex",
        vendor_conversation_id="thread-1",
        project_scope="/workspace",
        identity_digest="digest-1",
        ar_session_id="ar-1",
        bridge_epoch="epoch-1",
    )


def _snapshot(
    *,
    control: str = "ready",
    activity: str = "idle",
    raw: dict[str, object] | None = None,
    pending: PendingInteraction | None = None,
    sequence: int = 1,
) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(
            ar_session_id="ar-1", tmux_name="ar-t-1", created_at="2026-07-19T07:00:00+00:00"
        ),
        control=control,  # type: ignore[arg-type]
        activity=activity,  # type: ignore[arg-type]
        acceptance="immediate",
        vendor_session_id="thread-1",
        pending_interaction=pending,
        last_event_sequence=sequence,
        raw=raw or {},
    )


def _pending() -> PendingInteraction:
    return PendingInteraction(
        interaction_id="permission-1",
        kind="permission",
        prompt="Allow Bash?",
        created_at=NOW,
        choices=("allow", "deny"),
    )


class ClassificationTests(unittest.TestCase):
    def test_pending_interaction_is_needs_input_exact(self) -> None:
        result = classify_snapshot(_snapshot(activity="blocked", pending=_pending()), "codex")
        assert result.turn is not None
        self.assertEqual(result.turn.evidence, "pending-interaction")
        self.assertEqual(result.turn.strength, "exact")
        self.assertEqual(result.turn.interaction_id, "permission-1")

    def test_blocked_without_interaction_is_declared_wait(self) -> None:
        result = classify_snapshot(_snapshot(activity="blocked"), "codex")
        assert result.turn is not None
        self.assertEqual(result.turn.evidence, "declared-external-wait")
        self.assertEqual(result.turn.strength, "native-only")
        self.assertIsNotNone(result.turn.waiting_reason)

    def test_running_maps_active_native_turn_with_codex_turn_id(self) -> None:
        result = classify_snapshot(
            _snapshot(activity="running", raw={"activeTurnId": "turn-9"}), "codex"
        )
        assert result.turn is not None
        self.assertEqual(result.turn.evidence, "active-native-turn")
        self.assertEqual(result.turn.turn_id, "turn-9")

    def test_running_turn_id_only_for_codex(self) -> None:
        result = classify_snapshot(
            _snapshot(activity="running", raw={"activeTurnId": "turn-9"}), "claude"
        )
        assert result.turn is not None
        self.assertIsNone(result.turn.turn_id)

    def test_settling_variants_per_harness(self) -> None:
        claude_compacting = classify_snapshot(
            _snapshot(activity="settling", raw={"claudeStatus": "compacting"}), "claude"
        )
        assert claude_compacting.turn is not None
        self.assertEqual(claude_compacting.turn.evidence, "native-compaction")
        claude_retry = classify_snapshot(
            _snapshot(activity="settling", raw={"retryAttempt": 2}), "claude"
        )
        assert claude_retry.turn is not None
        self.assertEqual(claude_retry.turn.evidence, "native-retry")
        pi_compacting = classify_snapshot(
            _snapshot(activity="settling", raw={"isCompacting": True}), "pi"
        )
        assert pi_compacting.turn is not None
        self.assertEqual(pi_compacting.turn.evidence, "native-compaction")
        pi_retry = classify_snapshot(
            _snapshot(activity="settling", raw={"piEvent": {"type": "auto_retry_start"}}), "pi"
        )
        assert pi_retry.turn is not None
        self.assertEqual(pi_retry.turn.evidence, "native-retry")
        pi_retry_end = classify_snapshot(
            _snapshot(activity="settling", raw={"piEvent": {"type": "auto_retry_end"}}), "pi"
        )
        assert pi_retry_end.turn is not None
        self.assertEqual(pi_retry_end.turn.evidence, "native-end-reconciling")
        generic = classify_snapshot(_snapshot(activity="settling"), "codex")
        assert generic.turn is not None
        self.assertEqual(generic.turn.evidence, "native-end-reconciling")

    def test_idle_ready_is_settled_dispatchable(self) -> None:
        result = classify_snapshot(_snapshot(activity="idle", control="ready"), "pi")
        assert result.turn is not None
        self.assertEqual(result.turn.evidence, "settled-dispatchable")

    def test_unknown_and_starting_carry_no_turn_evidence(self) -> None:
        self.assertIsNone(classify_snapshot(_snapshot(activity="unknown"), "codex").turn)
        self.assertIsNone(
            classify_snapshot(_snapshot(activity="idle", control="starting"), "codex").turn
        )

    def test_process_mapping(self) -> None:
        self.assertEqual(
            classify_snapshot(_snapshot(control="ready"), "codex").process.state, "connected"
        )
        self.assertEqual(
            classify_snapshot(_snapshot(control="starting"), "codex").process.state, "starting"
        )
        self.assertEqual(
            classify_snapshot(_snapshot(control="disconnected"), "codex").process.state,
            "disconnected",
        )
        self.assertEqual(
            classify_snapshot(_snapshot(control="failed"), "codex").process.state, "failed"
        )


class SeatParityTests(unittest.TestCase):
    """The canonical projection must reproduce the pre-canonical mapping exactly."""

    def _legacy(self, snapshot: AdapterSnapshot) -> str:
        if snapshot.activity in {"running", "settling"}:
            return "working"
        if snapshot.activity == "blocked":
            return "awaiting-input"
        if snapshot.activity == "idle" and snapshot.control == "ready":
            return "turn-ended"
        return "stale"

    def test_parity_across_control_activity_product(self) -> None:
        for control in ("starting", "ready", "disconnected", "failed", "unsupported"):
            for activity in ("idle", "running", "blocked", "settling", "unknown"):
                snapshot = _snapshot(control=control, activity=activity)
                self.assertEqual(
                    snapshot_turn_state(snapshot),
                    self._legacy(snapshot),
                    f"{control}/{activity}",
                )
                self.assertEqual(
                    snapshot_turn_state(snapshot),
                    snapshot_seat_turn_state(snapshot),
                )

    def test_pending_interaction_prefers_needs_input_seat(self) -> None:
        snapshot = _snapshot(activity="idle", pending=_pending())
        self.assertEqual(snapshot_turn_state(snapshot), "awaiting-input")

    def test_seat_projection_rule(self) -> None:
        # Activity-led parity: live turn and wait states win over process
        # transitions, exactly as the pre-canonical mapping behaved.
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="starting"), "working"), "working"
        )
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="disconnected"), "waiting"),
            "awaiting-input",
        )
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="disconnected"), None), "stale"
        )
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="connected"), "needs-input"),
            "awaiting-input",
        )
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="connected"), "ready"), "turn-ended"
        )
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="starting"), "ready"), "stale"
        )
        self.assertEqual(
            seat_turn_state_for(ProcessEvidence(state="connected"), "interrupted"),
            "turn-ended",
        )


class StatusServiceTests(unittest.TestCase):
    def _service(self) -> ConversationStatusService:
        return ConversationStatusService(_identity(), clock=lambda: NOW)

    def test_initial_status_is_honest_waiting_unknown(self) -> None:
        status = self._service().current()
        self.assertEqual(status.revision, 1)
        self.assertEqual(status.turn.state, "waiting")
        self.assertEqual(status.evidence.strength, "unknown")
        self.assertEqual(status.process.state, "starting")

    def test_semantic_change_advances_revision(self) -> None:
        service = self._service()
        service.observe(_snapshot(activity="idle"), "codex")
        working = service.observe(_snapshot(activity="running", sequence=2), "codex")
        self.assertGreater(working.revision, 1)
        self.assertEqual(working.turn.state, "working")

    def test_identical_observation_keeps_revision(self) -> None:
        service = self._service()
        first = service.observe(_snapshot(activity="running", sequence=2), "codex")
        again = service.observe(_snapshot(activity="running", sequence=2), "codex")
        self.assertEqual(again.revision, first.revision)

    def test_terminal_outcomes(self) -> None:
        service = self._service()
        service.observe(_snapshot(activity="running", sequence=2), "codex")
        interrupted = service.observe(
            _snapshot(activity="idle", sequence=3),
            "codex",
            terminal=TurnTerminalEvidence(outcome="interrupted", turn_id="turn-9"),
        )
        self.assertEqual(interrupted.turn.state, "interrupted")
        assert interrupted.turn.terminal_outcome is not None
        self.assertEqual(interrupted.turn.terminal_outcome.state, "interrupted")
        ready = service.observe(_snapshot(activity="idle", sequence=4), "codex")
        self.assertEqual(ready.turn.state, "ready")
        # An interrupted outcome cannot masquerade as a completed ready turn.
        self.assertIsNone(ready.turn.terminal_outcome)

    def test_completed_outcome_survives_into_ready(self) -> None:
        service = self._service()
        service.observe(_snapshot(activity="running", sequence=2), "codex")
        settling = service.observe(
            _snapshot(activity="settling", sequence=3),
            "codex",
            terminal=TurnTerminalEvidence(outcome="completed", turn_id="turn-9"),
        )
        self.assertEqual(settling.turn.state, "settling")
        ready = service.observe(_snapshot(activity="idle", sequence=4), "codex")
        self.assertEqual(ready.turn.state, "ready")
        assert ready.turn.terminal_outcome is not None
        self.assertEqual(ready.turn.terminal_outcome.state, "completed")

    def test_needs_input_carries_interaction_id(self) -> None:
        status = self._service().observe(_snapshot(activity="blocked", pending=_pending()), "pi")
        self.assertEqual(status.turn.state, "needs-input")
        assert status.turn.waiting is not None
        self.assertEqual(status.turn.waiting.interaction_id, "permission-1")

    def test_lost_authority_keeps_turn_but_shows_process(self) -> None:
        service = self._service()
        service.observe(_snapshot(activity="running", sequence=2), "codex")
        lost = service.observe(
            _snapshot(control="disconnected", activity="unknown", sequence=3), "codex"
        )
        self.assertEqual(lost.process.state, "disconnected")
        self.assertEqual(lost.turn.state, "working")
        self.assertNotEqual(lost.turn.state, "ready")

    def test_unknown_evidence_never_becomes_ready(self) -> None:
        status = self._service().observe(_snapshot(activity="unknown", sequence=2), "codex")
        self.assertIsInstance(status, ConversationStatus)
        self.assertNotEqual(status.turn.state, "ready")


if __name__ == "__main__":
    unittest.main()
