"""Tests for the gate control-plane substrate (slice 6a)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.controllers import worktree_tools
from agents_remember.controlplane.enforcement import evaluate_closeout_gate, evaluate_gate
from agents_remember.controlplane.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicyRule,
    apply_seam_verdict_requirement,
    approval_failure_reason,
    make_gate_policy,
    named_gate_policy,
)
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    GateEvidenceRef,
    GateRecord,
    apply_gate,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.mcp.tools import gates
from agents_remember.mcp.tools.lifecycle import lifecycle_start_payload
from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, reset_ambient
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.reducer import project_workspace
from agents_remember.worktrees.modules import closeout as closeout_mod
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs

T1 = "2026-06-18T10:00:00+00:00"
T2 = "2026-06-18T10:05:00+00:00"
MANAGER_CLOSEOUT_POLICY = make_gate_policy(
    [GatePolicyRule(kind="closeout-approval", delegated_role="manager")]
)
MANAGER_CLOSEOUT_WITH_VERDICT_POLICY = make_gate_policy(
    [
        GatePolicyRule(
            kind="closeout-approval",
            delegated_role="manager",
            require_reviewer_verdict=True,
        )
    ]
)


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

    def test_decision_can_attach_reviewer_verdict_evidence(self) -> None:
        gate = create_gate(kind="closeout-approval", lifecycle_id="L1", gate_id="01H", now=T1)

        decided = decide_gate(
            gate,
            decision="approve",
            by="L-manager",
            via="orchestration",
            deciding_role="manager",
            note=None,
            now=T2,
            evidence_refs=[
                GateEvidenceRef(
                    kind="reviewer-verdict",
                    ref="notes/reports/verdict.md",
                    verdict="pass",
                )
            ],
        )

        self.assertEqual(decided.decidedBy, "L-manager")
        self.assertEqual(decided.decidedVia, "orchestration")
        self.assertEqual(decided.decidingRole, "manager")
        self.assertEqual(decided.evidenceRefs[0].ref, "notes/reports/verdict.md")

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
        with (
            mock.patch.object(gates, "ambient", return_value=None),
            self.assertRaisesRegex(Exception, "active lifecycle"),
        ):
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

    def test_orchestration_decision_records_lifecycle_identity_and_evidence(self) -> None:
        gate_id = self._create()
        config = SimpleNamespace(
            orchestration=SimpleNamespace(gate_policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY)
        )

        decided = gates.gate_decide_payload(
            config,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            decision="approve",
            decided_by="L-manager",
            decided_via="orchestration",
            deciding_role="manager",
            evidence_refs=[
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/verdict.md",
                    "verdict": "pass",
                }
            ],
        )

        self.assertEqual(decided["decidedBy"], "L-manager")
        self.assertEqual(decided["decidedVia"], "orchestration")
        self.assertEqual(decided["decidingRole"], "manager")
        self.assertEqual(decided["evidenceRefs"][0]["ref"], "notes/reports/verdict.md")
        stored = self.store.current("L1")[gate_id]
        self.assertEqual(stored.decidingRole, "manager")
        self.assertEqual(stored.evidenceRefs[0].kind, "reviewer-verdict")

    def test_orchestration_decision_rejects_owner_self_approval(self) -> None:
        gate_id = self._create()
        config = SimpleNamespace(
            orchestration=SimpleNamespace(gate_policy=MANAGER_CLOSEOUT_POLICY)
        )

        with self.assertRaisesRegex(ValueError, "owning lifecycle"):
            gates.gate_decide_payload(
                config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id="L1",
                decision="approve",
                decided_by="L1",
                decided_via="orchestration",
                deciding_role="manager",
            )

        self.assertEqual(self.store.current("L1")[gate_id].state, "open")

    def test_orchestration_decision_requires_verdict_when_policy_requires_it(self) -> None:
        gate_id = self._create()
        config = SimpleNamespace(
            orchestration=SimpleNamespace(gate_policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY)
        )

        with self.assertRaisesRegex(ValueError, "requires reviewer verdict evidence"):
            gates.gate_decide_payload(
                config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id="L1",
                decision="approve",
                decided_by="L-manager",
                decided_via="orchestration",
                deciding_role="manager",
            )

        self.assertEqual(self.store.current("L1")[gate_id].state, "open")

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

    def test_lifecycle_gate_creates_gate_blocks_and_waits_until_timeout(self) -> None:
        first = self._create("agent-question")
        blocked = SimpleNamespace(id="L1", state="blocked", phase="build")
        lifecycle = SimpleNamespace(
            current=SimpleNamespace(id="L1", state="running"),
            block=mock.Mock(return_value=blocked),
        )
        clock = iter([0.0, 0.0])

        with mock.patch.object(gates, "require_ambient", return_value=lifecycle):
            result = gates.lifecycle_gate_payload(
                None,  # type: ignore[arg-type]
                kind="plan-approval",
                ask={"kind": "decision", "prompt": "Approve?", "options": ["approve", "revise"]},
                lifecycle_id="L1",
                packet={"summary": "plan"},
                timeout_seconds=0.0,
                sleep=lambda _s: None,
                monotonic=lambda: next(clock),
            )

        self.assertEqual(result["operation"], "lifecycle_gate")
        self.assertEqual(result["gate"]["kind"], "plan-approval")
        self.assertEqual(result["gate"]["state"], "open")
        self.assertEqual(result["lifecycle"]["state"], "blocked")
        self.assertEqual(result["wait"]["state"], "open")
        self.assertTrue(result["wait"]["timedOut"])
        self.assertEqual(result["wait"]["gateId"], result["gate"]["id"])
        self.assertEqual(result["ask"]["kind"], "decision")
        lifecycle.block.assert_called_once_with(
            kind="decision", prompt="Approve?", options=["approve", "revise"]
        )
        current = self.store.current("L1")
        self.assertEqual(current[first].state, "expired")
        self.assertEqual(current[result["gate"]["id"]].state, "open")

    def test_lifecycle_gate_default_returns_after_developer_decision(self) -> None:
        blocked = SimpleNamespace(id="L1", state="blocked", phase="build")
        lifecycle = SimpleNamespace(
            current=SimpleNamespace(id="L1", state="running"),
            block=mock.Mock(return_value=blocked),
        )

        def approve_on_sleep(_seconds: float) -> None:
            open_gates = [
                gate for gate in self.store.current("L1").values() if gate.state == "open"
            ]
            self.assertEqual(len(open_gates), 1)
            self.store.append(
                decide_gate(
                    open_gates[0],
                    decision="approve",
                    by="developer",
                    via="dashboard",
                    note="approved from dashboard",
                    now=T2,
                )
            )

        with mock.patch.object(gates, "require_ambient", return_value=lifecycle):
            result = gates.lifecycle_gate_payload(
                None,  # type: ignore[arg-type]
                kind="plan-approval",
                ask={"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
                lifecycle_id="L1",
                sleep=approve_on_sleep,
            )

        self.assertEqual(result["gate"]["state"], "approved")
        self.assertFalse(result["wait"]["timedOut"])
        self.assertEqual(result["wait"]["state"], "approved")
        self.assertEqual(result["wait"]["decidedBy"], "developer")
        self.assertEqual(result["wait"]["decisionNote"], "approved from dashboard")

    def test_lifecycle_gate_ignores_ungated_lifecycle_inbox_entry(self) -> None:
        self.inbox.append(
            create_operator_inbox_entry(
                entry_id="I1",
                now=T1,
                lifecycle_id="L1",
                agent_id=None,
                gate_id=None,
                ask="Previous prompt",
                response="Previous response",
                created_by="developer",
                created_via="dashboard",
            )
        )
        blocked = SimpleNamespace(id="L1", state="blocked", phase="build")
        lifecycle = SimpleNamespace(
            current=SimpleNamespace(id="L1", state="running"),
            block=mock.Mock(return_value=blocked),
        )
        sleeps: list[float] = []

        def approve_on_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            open_gates = [
                gate for gate in self.store.current("L1").values() if gate.state == "open"
            ]
            self.assertEqual(len(open_gates), 1)
            self.store.append(
                decide_gate(
                    open_gates[0],
                    decision="approve",
                    by="developer",
                    via="dashboard",
                    note=None,
                    now=T2,
                )
            )

        with mock.patch.object(gates, "require_ambient", return_value=lifecycle):
            result = gates.lifecycle_gate_payload(
                None,  # type: ignore[arg-type]
                kind="plan-approval",
                ask={"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
                lifecycle_id="L1",
                sleep=approve_on_sleep,
            )

        self.assertEqual(sleeps, [5.0])
        self.assertEqual(result["gate"]["state"], "approved")
        self.assertEqual(result["wait"]["entries"], [])

    def test_lifecycle_gate_rejects_explicit_lifecycle_mismatch(self) -> None:
        lifecycle = SimpleNamespace(current=SimpleNamespace(id="L1", state="running"))

        with (
            mock.patch.object(gates, "require_ambient", return_value=lifecycle),
            self.assertRaisesRegex(Exception, "does not match active lifecycle"),
        ):
            gates.lifecycle_gate_payload(
                None,  # type: ignore[arg-type]
                kind="plan-approval",
                lifecycle_id="other",
            )

        self.assertEqual(self.store.current("L1"), {})

    def test_lifecycle_gate_projects_blocked_ask_and_current_gate(self) -> None:
        events = EventStore(self.root)
        install_ambient(AmbientLifecycle(events, heartbeat_seconds=3600))
        self.addCleanup(reset_ambient)
        started = lifecycle_start_payload()

        result = gates.lifecycle_gate_payload(
            None,  # type: ignore[arg-type]
            kind="plan-approval",
            ask={"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
            packet={"summary": "plan"},
            evidence_refs=[
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/verdict.md",
                    "verdict": "pass",
                }
            ],
            timeout_seconds=0.0,
            sleep=lambda _s: None,
            monotonic=lambda: 0.0,
        )

        lifecycle_id = started["lifecycleId"]
        projection = project_workspace(
            [events.read(lifecycle_id)],
            enclosures=[],
            providers=[],
            now=datetime.now(UTC),
            gates=list(self.store.current(lifecycle_id).values()),
        )
        lifecycle = projection.lifecycles[0]
        self.assertEqual(lifecycle.state, "blocked")
        self.assertEqual(
            lifecycle.ask,
            {"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
        )
        assert lifecycle.gate is not None
        self.assertEqual((lifecycle.gate.id, lifecycle.gate.kind), (result["gate"]["id"], "plan-approval"))
        self.assertEqual(
            lifecycle.gate.evidenceRefs,
            [
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/verdict.md",
                    "verdict": "pass",
                }
            ],
        )

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

    def test_list_without_id_defaults_to_ambient_lifecycle(self) -> None:
        # AR3-3: a raiser polls its own gate without ever handling a lifecycle id.
        gate_id = self._create()
        with mock.patch.object(
            gates,
            "ambient",
            return_value=SimpleNamespace(current=SimpleNamespace(id="L1")),
        ):
            listed = gates.gate_list_payload(None, lifecycle_id=None)  # type: ignore[arg-type]
        self.assertEqual(listed["lifecycleId"], "L1")
        self.assertEqual([gate["id"] for gate in listed["gates"]], [gate_id])

    def test_list_without_id_or_ambient_falls_back_to_workspace(self) -> None:
        self.store.append(
            create_gate(kind="agent-question", lifecycle_id=None, gate_id="01W", now=T1)
        )
        self._create()  # a lifecycle-scoped gate that must NOT appear
        with mock.patch.object(gates, "ambient", return_value=None):
            listed = gates.gate_list_payload(None, lifecycle_id=None)  # type: ignore[arg-type]
        self.assertIsNone(listed.get("lifecycleId"))  # workspace scope (None is stripped)
        self.assertEqual([gate["id"] for gate in listed["gates"]], ["01W"])

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
    gate_id: str,
    state: str,
    *,
    by: str = "developer",
    via: str = "dashboard",
    deciding_role: str | None = None,
    note: str | None = None,
    ts: str = T1,
    evidence_refs: list[dict[str, str]] | None = None,
    lifecycle_id: str = "L1",
) -> GateRecord:
    gate = create_gate(
        kind="closeout-approval", lifecycle_id=lifecycle_id, gate_id=gate_id, now=ts
    )
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
    return decide_gate(
        gate,
        decision=decision,
        by=by,
        via=via,  # type: ignore[arg-type]
        deciding_role=deciding_role,
        note=note,
        now=ts,
        evidence_refs=evidence_refs,
    )


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
        self.assertIn("not the developer or a configured orchestration role", guard.reason)

    def test_manager_approved_requires_opt_in_policy(self) -> None:
        gate = _closeout_gate(
            "A",
            "approved",
            by="L-manager",
            via="orchestration",
            deciding_role="manager",
        )

        guard = evaluate_closeout_gate(_gates(gate))

        self.assertFalse(guard.permitted)
        self.assertIn("not delegated", guard.reason)

    def test_manager_approved_permits_when_policy_delegates(self) -> None:
        gate = _closeout_gate(
            "A",
            "approved",
            by="L-manager",
            via="orchestration",
            deciding_role="manager",
        )

        guard = evaluate_closeout_gate(_gates(gate), policy=MANAGER_CLOSEOUT_POLICY)

        self.assertTrue(guard.permitted)
        self.assertEqual(guard.gate_id, "A")

    def test_manager_owner_self_approval_blocks(self) -> None:
        gate = _closeout_gate(
            "A",
            "approved",
            by="L1",
            via="orchestration",
            deciding_role="manager",
        )

        guard = evaluate_closeout_gate(_gates(gate), policy=MANAGER_CLOSEOUT_POLICY)

        self.assertFalse(guard.permitted)
        self.assertIn("owning lifecycle", guard.reason)

    def test_manager_policy_can_require_reviewer_verdict(self) -> None:
        gate = _closeout_gate(
            "A",
            "approved",
            by="L-manager",
            via="orchestration",
            deciding_role="manager",
        )

        missing = evaluate_closeout_gate(
            _gates(gate), policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY
        )

        self.assertFalse(missing.permitted)
        self.assertIn("requires reviewer verdict evidence", missing.reason)

        with_verdict = _closeout_gate(
            "B",
            "approved",
            by="L-manager",
            via="orchestration",
            deciding_role="manager",
            evidence_refs=[
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/verdict.md",
                    "verdict": "pass",
                }
            ],
        )

        permitted = evaluate_closeout_gate(
            _gates(with_verdict), policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY
        )

        self.assertTrue(permitted.permitted)

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

    def _args(self, policy=MANAGER_CLOSEOUT_POLICY) -> WorktreeArgs:
        return WorktreeArgs(gate_policy=policy)

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
        self.assertIsNone(
            closeout_mod._enforce_closeout_gate(
                self._contract(lifecycle_id=""),
                self._args(),
            )
        )

    def test_open_gate_blocks_closeout(self) -> None:
        self._seed("open")
        with self.assertRaises(RuntimeError):
            closeout_mod._enforce_closeout_gate(self._contract(), self._args())

    def test_model_approved_blocks_closeout(self) -> None:
        self._seed("approved", by="model")
        with self.assertRaises(RuntimeError):
            closeout_mod._enforce_closeout_gate(self._contract(), self._args())

    def test_developer_approved_permits_and_marks_applied(self) -> None:
        gate_id = self._seed("approved", by="developer")
        guard = closeout_mod._enforce_closeout_gate(self._contract(), self._args())
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
        guard = closeout_mod._closeout_gate_guard(self._contract(), self._args())
        payload = closeout_mod._closeout_gate_payload(guard)
        self.assertTrue(payload["enforced"])
        self.assertTrue(payload["permitted"])
        self.assertEqual(payload["gateId"], "01H")


def _handover_gate(
    state: str = "approved",
    *,
    by: str = "lc-orchestrator",
    deciding_role: str | None = "orchestrator",
    lifecycle_id: str = "lc-manager",
    evidence_refs: list[dict[str, str]] | None = None,
) -> GateRecord:
    gate = create_gate(
        kind="master-handover-approval", lifecycle_id=lifecycle_id, gate_id="G-HANDOVER", now=T1
    )
    if state == "open":
        return gate
    return decide_gate(
        gate,
        decision="approve",
        by=by,
        via="orchestration",
        deciding_role=deciding_role,
        note=None,
        now=T1,
        evidence_refs=evidence_refs,
    )


class MasterHandoverSeamTests(unittest.TestCase):
    """The master-exit seam gate kind (developer ruling 2026-07-05)."""

    def test_master_handover_is_delegable_to_orchestrator(self) -> None:
        policy = make_gate_policy(
            [GatePolicyRule(kind="master-handover-approval", delegated_role="orchestrator")]
        )
        self.assertEqual(
            policy.rule_for("master-handover-approval").delegated_role, "orchestrator"
        )

    def test_named_policy_routes_handover_to_orchestrator(self) -> None:
        policy = named_gate_policy("manager-decides-leaf-gates")
        self.assertEqual(
            policy.rule_for("master-handover-approval").delegated_role, "orchestrator"
        )
        self.assertEqual(policy.rule_for("plan-approval").delegated_role, "manager")

    def test_human_pinned_kinds_stay_pinned(self) -> None:
        with self.assertRaises(ValueError):
            make_gate_policy(
                [GatePolicyRule(kind="integration-approval", delegated_role="orchestrator")]
            )

    def test_seam_requirement_binds_delegated_seam_rules_only(self) -> None:
        policy = apply_seam_verdict_requirement(named_gate_policy("manager-decides-leaf-gates"))
        self.assertTrue(policy.rule_for("master-handover-approval").require_reviewer_verdict)
        self.assertFalse(policy.rule_for("plan-approval").require_reviewer_verdict)
        bound = apply_seam_verdict_requirement(DEFAULT_GATE_POLICY)
        self.assertFalse(bound.rule_for("master-handover-approval").require_reviewer_verdict)

    def test_delegated_handover_requires_verdict_evidence(self) -> None:
        policy = apply_seam_verdict_requirement(named_gate_policy("manager-decides-leaf-gates"))
        without = _handover_gate()
        reason = approval_failure_reason(without, policy)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("reviewer verdict", reason)
        with_evidence = _handover_gate(
            evidence_refs=[
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/master-exit-verdict.md",
                    "verdict": "pass",
                }
            ]
        )
        self.assertIsNone(approval_failure_reason(with_evidence, policy))

    def test_owner_still_never_self_approves_handover(self) -> None:
        policy = named_gate_policy("manager-decides-leaf-gates")
        gate = _handover_gate(by="lc-manager")
        reason = approval_failure_reason(gate, policy)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("owning lifecycle", reason)


HANDOVER_SEAM_POLICY = apply_seam_verdict_requirement(
    named_gate_policy("manager-decides-leaf-gates")
)


class HandoverEnforcementHelperTests(unittest.TestCase):
    """AR3-1: the integrate-side seam consumer — cross-lifecycle fold, master-addressed.

    The handover gate lives on the MANAGER's lifecycle while the integrating
    contract anchors the orchestrator's, so the guard folds every gate log
    (``GateStore.all_current``) and matches by the gate's ``enclosure`` against
    the contract's master/series name — never ``contract.lifecycle_id``.
    """

    MASTER = "260703-agent-orchestration-m1"
    SERIES = "260703-agent-orchestration"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.coord = Path(tmp.name)
        self.store = GateStore(observer_logs_root(self.coord))

    def _guard(self, policy=HANDOVER_SEAM_POLICY):
        return integrate_mod.handover_gate_guard(
            self.store.all_current(),
            task_name=self.MASTER,
            parent_task_name=self.SERIES,
            policy=policy,
        )

    def _seed(
        self,
        *,
        enclosure: str | None,
        gate_id: str = "G-HANDOVER",
        lifecycle_id: str = "L-MANAGER",
    ) -> GateRecord:
        gate = create_gate(
            kind="master-handover-approval",
            lifecycle_id=lifecycle_id,
            gate_id=gate_id,
            now=T1,
            enclosure=enclosure,
        )
        self.store.append(gate)
        return gate

    def _approve(self, gate: GateRecord, *, with_verdict: bool = True) -> None:
        evidence = (
            [
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/master-exit-verdict.md",
                    "verdict": "pass",
                }
            ]
            if with_verdict
            else None
        )
        self.store.append(
            decide_gate(
                gate,
                decision="approve",
                by="L-ORCH",
                via="orchestration",
                deciding_role="orchestrator",
                note=None,
                now=T2,
                evidence_refs=evidence,
            )
        )

    def test_open_gate_on_foreign_lifecycle_blocks(self) -> None:
        gate = self._seed(enclosure=self.MASTER)
        guard = self._guard()
        self.assertFalse(guard.permitted)
        self.assertEqual(guard.gate_id, gate.id)

    def test_policy_valid_approval_permits(self) -> None:
        gate = self._seed(enclosure=self.MASTER)
        self._approve(gate)
        guard = self._guard()
        self.assertTrue(guard.permitted)
        self.assertEqual(guard.gate_id, gate.id)

    def test_configured_policy_governs_not_the_default(self) -> None:
        # The exact regression AR3-1(a) named: the channel's policy-valid delegated
        # approval must NOT be re-judged under the all-human dataclass default.
        gate = self._seed(enclosure=self.MASTER)
        self._approve(gate)
        default_guard = self._guard(policy=DEFAULT_GATE_POLICY)
        self.assertFalse(default_guard.permitted)
        self.assertIn("not delegated", default_guard.reason)
        self.assertTrue(self._guard(policy=HANDOVER_SEAM_POLICY).permitted)

    def test_gateless_permits(self) -> None:
        guard = self._guard()
        self.assertTrue(guard.permitted)
        self.assertIn("existing approval channel governs", guard.reason)

    def test_gate_addressed_to_another_master_does_not_govern(self) -> None:
        self._seed(enclosure="some-other-master")
        self._seed(enclosure=None, gate_id="G-UNADDRESSED")
        self.assertTrue(self._guard().permitted)

    def test_parent_task_name_addresses_the_series(self) -> None:
        self._seed(enclosure=self.SERIES)
        self.assertFalse(self._guard().permitted)

    def test_worktree_integrate_tool_passes_configured_policy(self) -> None:
        # The controller/args layer (mirror of the closeout path): the CONFIGURED
        # policy reaches integrate_result's guard, not the dataclass default.
        config = SimpleNamespace(
            coordination_root=self.coord,
            orchestration=SimpleNamespace(gate_policy=HANDOVER_SEAM_POLICY),
        )
        with mock.patch.object(
            worktree_tools.git_worktree_manager,
            "integrate_result",
            return_value=SimpleNamespace(payload={"state": "integrated"}, returncode=0),
        ) as integrate_result:
            worktree_tools.worktree_integrate_tool(
                config,  # type: ignore[arg-type]
                contract_path=str(self.coord / "enclosures" / "contract.md"),
            )
        (args,), _kwargs = integrate_result.call_args
        self.assertIs(args.gate_policy, HANDOVER_SEAM_POLICY)
        self.assertIsNot(args.gate_policy, DEFAULT_GATE_POLICY)


class SeamChannelTests(unittest.TestCase):
    """Cycle-5 seam channel: non-blocking raise + cross-lifecycle decide (AR2-1/AR2-2)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = GateStore(self.root)
        self.inbox = OperatorInboxStore(self.root)
        for name, value in (("_store", self.store), ("_inbox_store", self.inbox)):
            patcher = mock.patch.object(gates, name, return_value=value)
            self.addCleanup(patcher.stop)
            patcher.start()
        self.config = SimpleNamespace(
            orchestration=SimpleNamespace(
                gate_policy=apply_seam_verdict_requirement(
                    named_gate_policy("manager-decides-leaf-gates")
                )
            )
        )

    def _ambient(self, lifecycle_id: str):
        return mock.patch.object(
            gates,
            "require_ambient",
            return_value=SimpleNamespace(
                current=SimpleNamespace(id=lifecycle_id, state="running"),
                block=lambda **_: SimpleNamespace(id=lifecycle_id),
            ),
        )

    def _raise_handover(self, lifecycle_id: str = "L-MANAGER") -> str:
        with self._ambient(lifecycle_id):
            payload = gates.lifecycle_gate_payload(
                self.config,  # type: ignore[arg-type]
                kind="master-handover-approval",
                wait=False,
            )
        self.assertEqual(payload["wait"]["state"], "raised")
        self.assertFalse(payload["wait"]["waited"])
        return payload["gate"]["id"]

    def test_wait_false_raises_without_blocking(self) -> None:
        gate_id = self._raise_handover()
        stored = self.store.current("L-MANAGER")[gate_id]
        self.assertEqual(stored.state, "open")
        self.assertEqual(stored.kind, "master-handover-approval")

    def test_wait_false_refused_for_undelegated_kind(self) -> None:
        # A seam kind under an all-human policy (the default nothing forces a run to
        # change): the raise must refuse loudly and mutate NOTHING (AR3-2) — no orphan
        # open gate, and the previously open sibling is not expired as a side effect.
        sibling = create_gate(
            kind="agent-question", lifecycle_id="L-MANAGER", gate_id="G-SIBLING", now=T1
        )
        self.store.append(sibling)
        all_human = SimpleNamespace(
            orchestration=SimpleNamespace(gate_policy=DEFAULT_GATE_POLICY)
        )
        with self._ambient("L-MANAGER"), self.assertRaisesRegex(ValueError, "not delegated"):
            gates.lifecycle_gate_payload(
                all_human,  # type: ignore[arg-type]
                kind="master-handover-approval",
                wait=False,
            )
        current = self.store.current("L-MANAGER")
        self.assertEqual(set(current), {"G-SIBLING"})  # no orphan handover gate
        self.assertEqual(current["G-SIBLING"].state, "open")  # sibling not expired

    def test_wait_false_refused_for_delegated_non_seam_kind(self) -> None:
        # AR3-5: wait=false is reserved for SEAM kinds — plan-approval is delegated
        # under the named policy but must still block (it has no enforcement consumer).
        with self._ambient("L-MANAGER"), self.assertRaisesRegex(
            ValueError, "reserved for delegated seam kinds"
        ):
            gates.lifecycle_gate_payload(
                self.config,  # type: ignore[arg-type]
                kind="plan-approval",
                wait=False,
            )
        self.assertEqual(self.store.current("L-MANAGER"), {})

    def test_cross_lifecycle_decide_by_packet_carried_gate_id(self) -> None:
        gate_id = self._raise_handover()
        with mock.patch.object(
            gates,
            "ambient",
            return_value=SimpleNamespace(
                current=SimpleNamespace(id="L-ORCH", state="running")
            ),
        ):
            decided = gates.gate_decide_payload(
                self.config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id=None,
                decision="approve",
                decided_by=None,
                decided_via="orchestration",
                deciding_role="orchestrator",
                evidence_refs=[
                    {
                        "kind": "reviewer-verdict",
                        "ref": "notes/reports/master-exit-verdict.md",
                        "verdict": "pass",
                    }
                ],
            )
        self.assertEqual(decided["state"], "approved")
        self.assertEqual(decided["decidedBy"], "L-ORCH")
        self.assertEqual(decided["decidingRole"], "orchestrator")

    def test_cross_lifecycle_decide_requires_verdict_when_seam_bound(self) -> None:
        gate_id = self._raise_handover()
        with mock.patch.object(
            gates,
            "ambient",
            return_value=SimpleNamespace(
                current=SimpleNamespace(id="L-ORCH", state="running")
            ),
        ), self.assertRaisesRegex(ValueError, "reviewer verdict"):
            gates.gate_decide_payload(
                self.config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id=None,
                decision="approve",
                decided_by=None,
                decided_via="orchestration",
                deciding_role="orchestrator",
            )

    def test_cli_decide_refused_on_delegated_kind(self) -> None:
        gate_id = self._raise_handover()
        with self.assertRaisesRegex(ValueError, "delegated by the active gate policy"):
            gates.gate_decide_payload(
                self.config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id=None,
                decision="approve",
                decided_by="model",
                decided_via="cli",
            )

    def test_cancel_still_allowed_for_the_raiser(self) -> None:
        gate_id = self._raise_handover()
        cancelled = gates.gate_decide_payload(
            self.config,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id=None,
            decision="cancel",
            decided_by="model",
            decided_via="cli",
        )
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertNotIn(gate_id, self.store.current("L-MANAGER"))

    def test_evaluate_gate_enforces_the_handover_kind(self) -> None:
        gate_id = self._raise_handover()
        open_guard = evaluate_gate(
            self.store.current("L-MANAGER"),
            kind="master-handover-approval",
            policy=self.config.orchestration.gate_policy,
        )
        self.assertFalse(open_guard.permitted)
        self.assertEqual(open_guard.gate_id, gate_id)
        gateless_guard = evaluate_gate(
            {}, kind="master-handover-approval", policy=self.config.orchestration.gate_policy
        )
        self.assertTrue(gateless_guard.permitted)
