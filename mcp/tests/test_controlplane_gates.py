"""Tests for the gate control-plane substrate (slice 6a)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.records import GateRecord, create_gate, decide_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.mcp.tools import gates

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
        self.store = GateStore(Path(tmp.name))
        patcher = mock.patch.object(gates, "_store", return_value=self.store)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _create(self, kind: str = "closeout-approval") -> str:
        created = gates.gate_create_payload(
            None,  # type: ignore[arg-type]  # _store is patched; config is unused
            kind=kind,
            lifecycle_id="L1",
        )
        self.assertEqual(created["state"], "open")
        return created["gateId"]

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

    def test_list_returns_folded_gates(self) -> None:
        gate_id = self._create()
        listed = gates.gate_list_payload(None, lifecycle_id="L1")  # type: ignore[arg-type]
        self.assertEqual(len(listed["gates"]), 1)
        self.assertEqual(listed["gates"][0]["id"], gate_id)
        self.assertEqual(listed["gates"][0]["schema"], "ar-gate-record/v1")
