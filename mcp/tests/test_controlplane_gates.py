"""Tests for the gate control-plane substrate (slice 6a)."""

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from agents_remember.application import gate_tools as gates
from agents_remember.controlplane.gate_policy import (
    GatePolicyRule,
    apply_seam_verdict_requirement,
    make_gate_policy,
    named_gate_policy,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    DecidedVia,
    GateAnchor,
    GateEvidenceRef,
    GateRecord,
    GateRequest,
    GateVerdict,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore


@dataclass(frozen=True)
class Decider:
    """Who decides a gate in this suite, and what they attach to the decision.

    Production's :class:`GateVerdict` carries the verb as well; these cases vary the verb
    (``approved``/``rejected``/...) independently of the decider, so a named decider holds
    only the actor, the surface, the claimed role, the note and any evidence, and
    :meth:`verdict` assembles the production model around the verb under test. The closeout
    policy never reads actor/surface/role apart -- a delegated approval is the
    ``orchestration`` surface AND a ``manager`` role AND an actor that is not the owning
    lifecycle -- so naming the deciders keeps that triple from being respelled at every call.
    """

    by: str = "developer"
    via: DecidedVia = "dashboard"
    deciding_role: str | None = None
    note: str | None = None
    evidence_refs: list[dict[str, str]] | None = None

    def verdict(self, decision: str) -> GateVerdict:
        return GateVerdict(
            decision=decision,
            via=self.via,
            by=self.by,
            note=self.note,
            deciding_role=self.deciding_role,
        )


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
            "closeout-approval",
            gate_id="01H",
            now=T1,
            anchor=GateAnchor(lifecycle_id="L1"),
            request=GateRequest(packet={"paths": 3}),
        )
        self.assertEqual(gate.state, "open")
        decided = decide_gate(
            gate,
            GateVerdict(decision="approve", by="developer", via="dashboard", note="lgtm"),
            now=T2,
        )
        self.assertEqual(decided.id, gate.id)  # same gate id
        self.assertEqual(decided.ts, T2)  # new snapshot time
        self.assertEqual(decided.state, "approved")
        self.assertEqual(decided.decidedBy, "developer")
        self.assertEqual(decided.decidedVia, "dashboard")
        self.assertEqual(gate.state, "open")  # original snapshot untouched

    def test_decision_can_attach_reviewer_verdict_evidence(self) -> None:
        gate = create_gate(
            "closeout-approval", gate_id="01H", now=T1, anchor=GateAnchor(lifecycle_id="L1")
        )

        decided = decide_gate(
            gate,
            GateVerdict(
                decision="approve",
                by="L-manager",
                via="orchestration",
                note=None,
                deciding_role="manager",
            ),
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
        gate = create_gate(
            "agent-question", gate_id="01H", now=T1, anchor=GateAnchor(lifecycle_id="L1")
        )
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
        gate = create_gate(
            "closeout-approval", gate_id="01H", now=T1, anchor=GateAnchor(lifecycle_id="L1")
        )
        store.append(gate)
        store.append(
            decide_gate(
                gate,
                GateVerdict(decision="approve", by="developer", via="dashboard", note=None),
                now=T2,
            )
        )
        self.assertEqual(len(store.read("L1")), 2)  # history preserved
        self.assertEqual(store.current("L1")["01H"].state, "approved")  # last-wins

    def test_missing_log_reads_empty(self) -> None:
        self.assertEqual(GateStore(self.root).read("nope"), [])

    def test_log_path_routing(self) -> None:
        store = GateStore(self.root)
        self.assertEqual(store.log_path(None), self.root / "workspace" / "gates.jsonl")
        self.assertEqual(store.log_path("L1"), self.root / "lifecycles" / "L1" / "gates.jsonl")


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
        created = gates.gate_create_tool(
            None,  # type: ignore[arg-type]  # the store is patched; config is unused here
            kind=kind,
            anchor=GateAnchor(lifecycle_id="L1"),
        )
        self.assertEqual(created["state"], "open")
        return created["gateId"]


OWNER_LIFECYCLE = "L1"
BY_DEVELOPER = Decider()
BY_MODEL = Decider(by="model")
BY_MANAGER = Decider(by="L-manager", via="orchestration", deciding_role="manager")
BY_OWNING_MANAGER = Decider(
    by=OWNER_LIFECYCLE, via="orchestration", deciding_role="manager"
)  # the gate's own lifecycle claiming the manager role: self-approval


HANDOVER_SEAM_POLICY = apply_seam_verdict_requirement(
    named_gate_policy("manager-decides-leaf-gates")
)
