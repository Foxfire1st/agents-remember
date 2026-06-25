"""Tests for the gate control-plane substrate (slice 6a)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.controlplane.enforcement import evaluate_closeout_gate
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateRecord, apply_gate, create_gate, decide_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.mcp.tools import gates
from agents_remember.observer.paths import observer_logs_root
from agents_remember.worktrees.modules import closeout as closeout_mod

T1 = "2026-06-18T10:00:00+00:00"
T2 = "2026-06-18T10:05:00+00:00"


class GateRecordTests(unittest.TestCase):
    def test_create_and_decide_are_pure_snapshots(self) -> None:
        gate = create_gate(
            kind="closeout-approval", lifecycle_id="L1", gate_id="01H", now=T1,
            packet={"paths": 3},
        )
        self.assertEqual(gate.state, "open")
        decided = decide_gate(
            gate, decision="approve", by="developer", via="dashboard",
            note="lgtm", now=T2,
        )
        self.assertEqual(decided.id, gate.id)  # same gate id
        self.assertEqual(decided.ts, T2)  # new snapshot time
        self.assertEqual(decided.state, "approved")
        self.assertEqual(decided.decidedBy, "developer")
        self.assertEqual(decided.decidedVia, "dashboard")
        self.assertEqual(gate.state, "open")  # original snapshot untouched

    def test_wire_roundtrip_uses_schema_alias(self) -> None:
        gate = create_gate(kind="agent-question", lifecycle_id="L1", gate_id="01H", now=T1)
        line = gate.model_dump_json(by_alias=True, exclude_none=True)
        self.assertIn('"schema":"ar-gate-record/v1"', line)
        self.assertEqual(GateRecord.model_validate_json(line), gate)


class GateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_append_keeps_history_and_current_folds_last_wins(self) -> None:
        store = GateStore(self.root)
        gate = create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="01H", now=T1)
        store.append(gate)
        store.append(
            decide_gate(gate, decision="approve", by="developer", via="dashboard",
                        note=None, now=T2)
        )
        self.assertEqual(len(store.read("L1")), 2)  # history preserved
        self.assertEqual(store.current("L1")["01H"].state, "approved")  # last-wins

    def test_missing_log_reads_empty(self) -> None:
        self.assertEqual(GateStore(self.root).read("nope"), [])

    def test_log_path_routing(self) -> None:
        store = GateStore(self.root)
        self.assertEqual(store.log_path(None), self.root / "workspace" / "gates.jsonl")
        self.assertEqual(
            store.log_path("L1"), self.root / "lifecycles" / "L1" / "gates.jsonl"
        )


class GateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = GateStore(self.root)
        self.inbox = OperatorInboxStore(self.root)
        patcher = mock.patch.object(gates, "_store", return_value=self.store)
        self.addCleanup(patcher.stop)
        patcher.start()
        inbox_patcher = mock.patch.object(gates, "_inbox_store", return_value=self.inbox)
        self.addCleanup(inbox_patcher.stop)
        inbox_patcher.start()

    def _create(self, kind: str = "closeout-approval") -> str:
        created = gates.gate_create_payload(
            None,  # type: ignore[arg-type]  # _store is patched; config is unused
            kind=kind,
            lifecycle_id="L1",
        )
        self.assertEqual(created["state"], "open")
        return created["gateId"]

    def test_create_without_lifecycle_uses_active_ambient(self) -> None:
        with mock.patch.object(
            gates,
            "ambient",
            return_value=SimpleNamespace(current=SimpleNamespace(id="L-ACTIVE")),
        ):
            created = gates.gate_create_payload(
                None,  # type: ignore[arg-type]
                kind="agent-question",
                lifecycle_id=None,
            )

        self.assertEqual(created["lifecycleId"], "L-ACTIVE")
        self.assertEqual(len(self.store.current("L-ACTIVE")), 1)
        self.assertEqual(self.store.current(None), {})

    def test_create_without_lifecycle_requires_active_ambient(self) -> None:
        with mock.patch.object(gates, "ambient", return_value=None):
            with self.assertRaisesRegex(Exception, "active lifecycle"):
                gates.gate_create_payload(
                    None,  # type: ignore[arg-type]
                    kind="agent-question",
                    lifecycle_id=None,
                )

    def test_create_then_decide_records_attribution(self) -> None:
        gate_id = self._create()
        decided = gates.gate_decide_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", decision="approve",
            decided_by="model", decided_via="cli",
        )
        self.assertEqual(decided["state"], "approved")
        self.assertEqual(decided["decidedBy"], "model")
        self.assertEqual(decided["decidedVia"], "cli")

    def test_create_expires_previous_open_lifecycle_gate(self) -> None:
        first = self._create("agent-question")
        second = self._create("closeout-approval")
        current = self.store.current("L1")
        self.assertEqual(current[first].state, "expired")
        self.assertEqual(current[second].state, "open")

        result = gates.gate_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id=first,
            lifecycle_id="L1",
            sleep=lambda _s: None,
        )
        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "expired")

    def test_decide_unknown_decision_raises(self) -> None:
        gate_id = self._create("alarm-ack")
        with self.assertRaises(ValueError):
            gates.gate_decide_payload(
                None,  # type: ignore[arg-type]
                gate_id=gate_id, lifecycle_id="L1", decision="bogus",
                decided_by="model", decided_via="cli",
            )

    def test_decide_missing_gate_raises(self) -> None:
        with self.assertRaises(KeyError):
            gates.gate_decide_payload(
                None,  # type: ignore[arg-type]
                gate_id="nope", lifecycle_id="L1", decision="approve",
                decided_by="model", decided_via="cli",
            )

    def test_wait_returns_when_decided(self) -> None:
        gate_id = self._create("agent-question")
        gates.gate_decide_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", decision="approve",
            decided_by="developer", decided_via="dashboard",
        )
        result = gates.gate_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", sleep=lambda _s: None,
        )
        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "approved")

    def test_wait_returns_decision_note(self) -> None:
        gate_id = self._create("agent-question")
        gates.gate_decide_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", decision="reject",
            decided_by="developer", decided_via="dashboard", note="Needs another pass.",
        )
        result = gates.gate_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", sleep=lambda _s: None,
        )
        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["decisionNote"], "Needs another pass.")

    def test_cancel_deletes_gate_and_pending_inbox_entries(self) -> None:
        gate_id = self._create("agent-question")
        self.inbox.append(
            create_operator_inbox_entry(
                entry_id="I1",
                now=T2,
                lifecycle_id="L1",
                agent_id=None,
                gate_id=gate_id,
                ask="Continue?",
                response="Never mind.",
                created_by="developer",
                created_via="dashboard",
            )
        )

        decided = gates.gate_decide_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", decision="cancel",
            decided_by="developer", decided_via="dashboard",
        )

        self.assertEqual(decided["state"], "cancelled")
        self.assertNotIn(gate_id, self.store.current("L1"))
        self.assertEqual(self.inbox.read(), [])

    def test_wait_times_out_while_open(self) -> None:
        gate_id = self._create("agent-question")
        clock = iter([0.0, 0.0, 99.0])  # deadline calc, first check, past-deadline check
        result = gates.gate_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", timeout_seconds=10.0,
            sleep=lambda _s: None, monotonic=lambda: next(clock),
        )
        self.assertTrue(result["timedOut"])
        self.assertEqual(result["state"], "open")

    def test_response_wait_returns_matching_inbox_entry_without_consuming(self) -> None:
        gate_id = self._create("agent-question")
        self.inbox.append(
            create_operator_inbox_entry(
                entry_id="I1",
                now=T2,
                lifecycle_id="L1",
                agent_id=None,
                gate_id=gate_id,
                ask="Continue?",
                response="Use a clearer commit message first.",
                created_by="developer",
                created_via="dashboard",
            )
        )

        result = gates.gate_response_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            timeout_seconds=10.0,
            sleep=lambda _s: None,
        )

        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "open")
        self.assertEqual(result["entryCount"], 1)
        self.assertEqual(result["entries"][0]["response"], "Use a clearer commit message first.")
        self.assertEqual(len(self.inbox.list_pending(lifecycle_id="L1", agent_id=None)), 1)

    def test_response_wait_returns_gate_decision_and_note(self) -> None:
        gate_id = self._create("agent-question")
        gates.gate_decide_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id, lifecycle_id="L1", decision="reject",
            decided_by="developer", decided_via="dashboard", note="Needs another pass.",
        )

        result = gates.gate_response_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            timeout_seconds=10.0,
            sleep=lambda _s: None,
        )

        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["decisionNote"], "Needs another pass.")
        self.assertEqual(result["entryCount"], 0)
        self.assertNotIn(gate_id, self.store.current("L1"))

    def test_response_wait_deleted_gate_returns_cancelled(self) -> None:
        result = gates.gate_response_wait_payload(
            None,  # type: ignore[arg-type]
            gate_id="deleted",
            lifecycle_id="L1",
            timeout_seconds=10.0,
            sleep=lambda _s: None,
        )

        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "cancelled")

    def test_list_returns_folded_gates(self) -> None:
        gate_id = self._create()
        listed = gates.gate_list_payload(None, lifecycle_id="L1")  # type: ignore[arg-type]
        self.assertEqual(len(listed["gates"]), 1)
        self.assertEqual(listed["gates"][0]["id"], gate_id)
        self.assertEqual(listed["gates"][0]["schema"], "ar-gate-record/v1")

    def test_decide_for_lifecycle_decides_newest_open(self) -> None:
        self.store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="A", now=T1)
        )
        self.store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="B", now=T2)
        )
        result = gates.gate_decide_for_lifecycle(
            None,  # type: ignore[arg-type]
            lifecycle_id="L1", decision="approve",
            decided_by="developer", decided_via="dashboard",
        )
        self.assertEqual(result["gateId"], "B")  # newest open gate governs
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["decidedBy"], "developer")
        self.assertEqual(self.store.current("L1")["B"].state, "approved")

    def test_decide_for_lifecycle_can_target_current_gate_and_note(self) -> None:
        self.store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="A", now=T1)
        )
        result = gates.gate_decide_for_lifecycle(
            None,  # type: ignore[arg-type]
            lifecycle_id="L1",
            decision="reject",
            decided_by="developer",
            decided_via="dashboard",
            expected_gate_id="A",
            note="Needs another pass.",
        )
        self.assertEqual(result["gateId"], "A")
        decided = self.store.current("L1")["A"]
        self.assertEqual(decided.state, "rejected")
        self.assertEqual(decided.decisionNote, "Needs another pass.")

    def test_decide_for_lifecycle_rejects_stale_expected_gate(self) -> None:
        self.store.append(
            create_gate(kind="agent-question", lifecycle_id="L1", gate_id="A", now=T1)
        )
        self.store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="B", now=T2)
        )
        with self.assertRaisesRegex(KeyError, "current open gate"):
            gates.gate_decide_for_lifecycle(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1",
                decision="approve",
                decided_by="developer",
                decided_via="dashboard",
                expected_gate_id="A",
            )

    def test_decide_for_lifecycle_no_open_gate_raises(self) -> None:
        with self.assertRaises(KeyError):
            gates.gate_decide_for_lifecycle(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1", decision="approve",
                decided_by="developer", decided_via="dashboard",
            )

    def test_decide_for_lifecycle_unknown_decision_raises(self) -> None:
        self.store.append(
            create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="A", now=T1)
        )
        with self.assertRaises(ValueError):
            gates.gate_decide_for_lifecycle(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1", decision="bogus",
                decided_by="developer", decided_via="dashboard",
            )


class ApplyGateTests(unittest.TestCase):
    def test_apply_marks_consumed_and_preserves_attribution(self) -> None:
        gate = create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="01H", now=T1)
        approved = decide_gate(
            gate, decision="approve", by="developer", via="dashboard", note="ok", now=T2
        )
        applied = apply_gate(approved, now="2026-06-18T10:10:00+00:00")
        self.assertEqual(applied.state, "applied")
        self.assertEqual(applied.ts, "2026-06-18T10:10:00+00:00")
        self.assertEqual(applied.decidedBy, "developer")  # attribution carried forward
        self.assertEqual(approved.state, "approved")  # source snapshot untouched


def _gates(*records: GateRecord) -> dict[str, GateRecord]:
    return {record.id: record for record in records}


def _closeout_gate(
    gate_id: str, state: str, *, by: str = "developer", note: str | None = None, ts: str = T1
) -> GateRecord:
    gate = create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id=gate_id, now=ts)
    if state == "open":
        return gate
    if state == "applied":
        return apply_gate(gate, now=ts)
    decision = {
        "approved": "approve",
        "rejected": "reject",
        "revision-requested": "request-revision",
        "cancelled": "cancel",
    }[state]
    return decide_gate(gate, decision=decision, by=by, via="dashboard", note=note, now=ts)


class EvaluateCloseoutGateTests(unittest.TestCase):
    def test_gateless_permits(self) -> None:
        guard = evaluate_closeout_gate({})
        self.assertTrue(guard.permitted)
        self.assertIsNone(guard.gate_id)

    def test_non_closeout_kinds_are_ignored(self) -> None:
        other = create_gate(kind="agent-question", lifecycle_id="L1", gate_id="Q", now=T1)
        self.assertTrue(evaluate_closeout_gate(_gates(other)).permitted)

    def test_open_blocks(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "open")))
        self.assertFalse(guard.permitted)
        self.assertEqual(guard.gate_id, "A")

    def test_developer_approved_permits(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "approved", by="developer")))
        self.assertTrue(guard.permitted)
        self.assertEqual(guard.gate_id, "A")

    def test_model_approved_blocks(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "approved", by="model")))
        self.assertFalse(guard.permitted)
        self.assertIn("not the developer", guard.reason)

    def test_rejected_blocks_with_note(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "rejected", note="needs work")))
        self.assertFalse(guard.permitted)
        self.assertIn("needs work", guard.reason)

    def test_applied_blocks(self) -> None:
        guard = evaluate_closeout_gate(_gates(_closeout_gate("A", "applied")))
        self.assertFalse(guard.permitted)

    def test_latest_gate_governs(self) -> None:
        old = _closeout_gate("A", "approved", by="developer", ts=T1)
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

    def _seed(self, state: str, *, by: str = "developer") -> str:
        gate = create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="01H", now=T1)
        self.store.append(gate)
        if state != "open":
            decision = {"approved": "approve", "rejected": "reject"}[state]
            self.store.append(
                decide_gate(gate, decision=decision, by=by, via="dashboard", note=None, now=T2)
            )
        return gate.id

    def test_gateless_lifecycle_returns_none(self) -> None:
        self.assertIsNone(closeout_mod._enforce_closeout_gate(self._contract(lifecycle_id="")))

    def test_open_gate_blocks_closeout(self) -> None:
        self._seed("open")
        with self.assertRaises(RuntimeError):
            closeout_mod._enforce_closeout_gate(self._contract())

    def test_model_approved_blocks_closeout(self) -> None:
        self._seed("approved", by="model")
        with self.assertRaises(RuntimeError):
            closeout_mod._enforce_closeout_gate(self._contract())

    def test_developer_approved_permits_and_marks_applied(self) -> None:
        gate_id = self._seed("approved", by="developer")
        guard = closeout_mod._enforce_closeout_gate(self._contract())
        self.assertIsNotNone(guard)
        assert guard is not None
        self.assertTrue(guard.permitted)
        closeout_mod._mark_closeout_gate_applied(self._contract(), gate_id)
        self.assertEqual(self.store.current("L1")[gate_id].state, "applied")

    def test_payload_shapes(self) -> None:
        self.assertEqual(
            closeout_mod._closeout_gate_payload(None),
            {"enforced": False, "reason": "gateless lifecycle; chat commit approval governs"},
        )
        self._seed("approved", by="developer")
        guard = closeout_mod._closeout_gate_guard(self._contract())
        payload = closeout_mod._closeout_gate_payload(guard)
        self.assertTrue(payload["enforced"])
        self.assertTrue(payload["permitted"])
        self.assertEqual(payload["gateId"], "01H")
