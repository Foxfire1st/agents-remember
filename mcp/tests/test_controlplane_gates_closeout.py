from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from agents_remember.controlplane.enforcement import evaluate_closeout_gate
from agents_remember.controlplane.records import (
    GateAnchor,
    GateRecord,
    GateVerdict,
    apply_gate,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.observer.paths import observer_logs_root
from agents_remember.worktrees.modules import closeout as closeout_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from test_controlplane_gates import (
    BY_DEVELOPER,
    BY_MANAGER,
    BY_MODEL,
    BY_OWNING_MANAGER,
    MANAGER_CLOSEOUT_POLICY,
    MANAGER_CLOSEOUT_WITH_VERDICT_POLICY,
    OWNER_LIFECYCLE,
    T1,
    T2,
    Decider,
)


class ApplyGateTests(unittest.TestCase):
    def test_apply_marks_consumed_and_preserves_attribution(self) -> None:
        gate = create_gate(
            "closeout-approval", gate_id="01H", now=T1, anchor=GateAnchor(lifecycle_id="L1")
        )
        approved = decide_gate(
            gate,
            GateVerdict(decision="approve", by="developer", via="dashboard", note="ok"),
            now=T2,
        )
        applied = apply_gate(approved, now="2026-06-18T10:10:00+00:00")
        self.assertEqual(applied.state, "applied")
        self.assertEqual(applied.ts, "2026-06-18T10:10:00+00:00")
        self.assertEqual(applied.decidedBy, "developer")  # attribution carried forward
        self.assertEqual(approved.state, "approved")  # source snapshot untouched


def _gates(*records: GateRecord) -> dict[str, GateRecord]:
    return {record.id: record for record in records}


def _closeout_gate(
    gate_id: str,
    state: str,
    *,
    decision: Decider = BY_DEVELOPER,
    ts: str = T1,
) -> GateRecord:
    gate = create_gate(
        "closeout-approval",
        gate_id=gate_id,
        now=ts,
        anchor=GateAnchor(lifecycle_id=OWNER_LIFECYCLE),
    )
    if state == "open":
        return gate
    if state == "applied":
        return apply_gate(gate, now=ts)
    verb = {
        "approved": "approve",
        "rejected": "reject",
        "revision-requested": "request-revision",
        "cancelled": "cancel",
    }[state]
    return decide_gate(
        gate,
        decision.verdict(verb),
        now=ts,
        evidence_refs=decision.evidence_refs,
    )


class EvaluateCloseoutGateTests(unittest.TestCase):
    def test_gateless_permits(self) -> None:
        guard = evaluate_closeout_gate({})
        self.assertTrue(guard.permitted)
        self.assertIsNone(guard.gate_id)

    def test_non_closeout_kinds_are_ignored(self) -> None:
        other = create_gate(
            "agent-question", gate_id="Q", now=T1, anchor=GateAnchor(lifecycle_id="L1")
        )
        self.assertTrue(evaluate_closeout_gate(_gates(other)).permitted)

    def test_open_blocks(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "open")))
        self.assertFalse(guard.permitted)
        self.assertEqual(guard.gate_id, "A")

    def test_developer_approved_permits(self) -> None:
        guard = evaluate_closeout_gate(
            _gates(_closeout_gate("A", "approved", decision=BY_DEVELOPER))
        )
        self.assertTrue(guard.permitted)
        self.assertEqual(guard.gate_id, "A")

    def test_model_approved_blocks(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "approved", decision=BY_MODEL)))
        self.assertFalse(guard.permitted)
        self.assertIn("not the developer or a configured orchestration role", guard.reason)

    def test_manager_approved_requires_opt_in_policy(self) -> None:
        gate = _closeout_gate("A", "approved", decision=BY_MANAGER)

        guard = evaluate_closeout_gate(_gates(gate))

        self.assertFalse(guard.permitted)
        self.assertIn("not delegated", guard.reason)

    def test_manager_approved_permits_when_policy_delegates(self) -> None:
        gate = _closeout_gate("A", "approved", decision=BY_MANAGER)

        guard = evaluate_closeout_gate(_gates(gate), policy=MANAGER_CLOSEOUT_POLICY)

        self.assertTrue(guard.permitted)
        self.assertEqual(guard.gate_id, "A")

    def test_manager_owner_self_approval_blocks(self) -> None:
        gate = _closeout_gate("A", "approved", decision=BY_OWNING_MANAGER)

        guard = evaluate_closeout_gate(_gates(gate), policy=MANAGER_CLOSEOUT_POLICY)

        self.assertFalse(guard.permitted)
        self.assertIn("owning lifecycle", guard.reason)

    def test_manager_policy_can_require_reviewer_verdict(self) -> None:
        gate = _closeout_gate("A", "approved", decision=BY_MANAGER)

        missing = evaluate_closeout_gate(_gates(gate), policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY)

        self.assertFalse(missing.permitted)
        self.assertIn("requires reviewer verdict evidence", missing.reason)

        with_verdict = _closeout_gate(
            "B",
            "approved",
            decision=replace(
                BY_MANAGER,
                evidence_refs=[
                    {
                        "kind": "reviewer-verdict",
                        "ref": "notes/reports/verdict.md",
                        "verdict": "pass",
                    }
                ],
            ),
        )

        permitted = evaluate_closeout_gate(
            _gates(with_verdict), policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY
        )

        self.assertTrue(permitted.permitted)

    def test_rejected_blocks_with_note(self) -> None:
        guard = evaluate_closeout_gate(
            _gates(
                _closeout_gate("A", "rejected", decision=replace(BY_DEVELOPER, note="needs work"))
            )
        )
        self.assertFalse(guard.permitted)
        self.assertIn("needs work", guard.reason)

    def test_applied_blocks(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "applied")))
        self.assertFalse(guard.permitted)

    def test_latest_gate_governs(self) -> None:
        old = _closeout_gate("A", "approved", decision=BY_DEVELOPER, ts=T1)
        new = _closeout_gate("B", "open", ts=T2)
        guard = evaluate_closeout_gate(_gates(old, new))
        self.assertFalse(guard.permitted)  # newest (open) governs
        self.assertEqual(guard.gate_id, "B")


class CloseoutEnforcementHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.coord = Path(tmp.name)
        self.store = GateStore(observer_logs_root(self.coord))

    def _contract(self, lifecycle_id: str = "L1") -> SimpleNamespace:
        return SimpleNamespace(lifecycle_id=lifecycle_id, coordination_root=self.coord)

    def _args(self, policy=MANAGER_CLOSEOUT_POLICY) -> WorktreeArgs:
        return WorktreeArgs(gate_policy=policy)

    def _seed(self, state: str, *, by: str = "developer") -> str:
        gate = create_gate(
            "closeout-approval", gate_id="01H", now=T1, anchor=GateAnchor(lifecycle_id="L1")
        )
        self.store.append(gate)
        if state != "open":
            decision = {"approved": "approve", "rejected": "reject"}[state]
            self.store.append(
                decide_gate(
                    gate, GateVerdict(decision=decision, by=by, via="dashboard", note=None), now=T2
                )
            )
        return gate.id

    def test_gateless_lifecycle_returns_none(self) -> None:
        self.assertIsNone(
            closeout_mod._claim_closeout_gate(
                self._contract(lifecycle_id=""),
                self._args(),
            )
        )

    def test_open_gate_blocks_closeout(self) -> None:
        self._seed("open")
        with self.assertRaises(RuntimeError):
            closeout_mod._claim_closeout_gate(self._contract(), self._args())
        # Both rungs refuse it, and the early read must refuse it too or an open gate would
        # be discovered only after a full strict code-quality run over a staged worktree.
        with self.assertRaises(RuntimeError):
            closeout_mod._refuse_unsatisfied_closeout_gate(self._contract(), self._args())

    def test_model_approved_blocks_closeout(self) -> None:
        self._seed("approved", by="model")
        with self.assertRaises(RuntimeError):
            closeout_mod._claim_closeout_gate(self._contract(), self._args())
        with self.assertRaises(RuntimeError):
            closeout_mod._refuse_unsatisfied_closeout_gate(self._contract(), self._args())

    def test_developer_approved_permits_and_marks_applied(self) -> None:
        gate_id = self._seed("approved", by="developer")
        # The claim is the consume: permitting and marking applied are one step, so there is
        # no arrangement of these two lines that leaves the approval spendable in between.
        guard = closeout_mod._claim_closeout_gate(self._contract(), self._args())
        self.assertIsNotNone(guard)
        assert guard is not None
        self.assertTrue(guard.permitted)
        self.assertEqual(self.store.current("L1")[gate_id].state, "applied")

    def test_payload_shapes(self) -> None:
        self.assertEqual(
            closeout_mod._closeout_gate_payload(None),
            {"enforced": False, "reason": "gateless lifecycle; chat commit approval governs"},
        )
        self._seed("approved", by="developer")
        guard = closeout_mod._closeout_gate_guard(self._contract(), self._args())
        payload = closeout_mod._closeout_gate_payload(guard)
        self.assertTrue(payload["enforced"])
        self.assertTrue(payload["permitted"])
        self.assertEqual(payload["gateId"], "01H")
