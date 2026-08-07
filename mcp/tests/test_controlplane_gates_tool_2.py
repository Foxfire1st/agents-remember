from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from agents_remember.application import gate_tools as gates
from agents_remember.application.gate_tools import GateWait
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.records import GateAnchor, GateVerdict, create_gate
from test_controlplane_gates import T1, T2, GateToolTests


class GateToolTests2(GateToolTests):
    def test_cancel_deletes_gate_and_pending_inbox_entries(self) -> None:
        gate_id = self._create("agent-question")
        self.inbox.append(
            create_operator_inbox_entry(
                InboxMessage(ask="Continue?", response="Never mind.", gate_id=gate_id),
                entry_id="I1",
                now=T2,
                routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id=None)),
                poster=InboxPoster(created_by="developer", created_via="dashboard"),
            )
        )

        decided = gates.gate_decide_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            verdict=GateVerdict(decision="cancel", by="developer", via="dashboard"),
        )

        self.assertEqual(decided["state"], "cancelled")
        self.assertNotIn(gate_id, self.store.current("L1"))
        self.assertEqual(self.inbox.read(), [])

    def test_wait_times_out_while_open(self) -> None:
        gate_id = self._create("agent-question")
        clock = iter([0.0, 0.0, 99.0])  # deadline calc, first check, past-deadline check
        result = gates.gate_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            wait=GateWait(
                timeout_seconds=10.0, sleep=lambda _s: None, monotonic=lambda: next(clock)
            ),
        )
        self.assertTrue(result["timedOut"])
        self.assertEqual(result["state"], "open")

    def test_response_wait_returns_matching_inbox_entry_without_consuming(self) -> None:
        gate_id = self._create("agent-question")
        self.inbox.append(
            create_operator_inbox_entry(
                InboxMessage(
                    ask="Continue?", response="Use a clearer commit message first.", gate_id=gate_id
                ),
                entry_id="I1",
                now=T2,
                routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id=None)),
                poster=InboxPoster(created_by="developer", created_via="dashboard"),
            )
        )

        result = gates.gate_response_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            wait=GateWait(timeout_seconds=10.0, sleep=lambda _s: None),
        )

        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "open")
        self.assertEqual(result["entryCount"], 1)
        self.assertEqual(result["entries"][0]["response"], "Use a clearer commit message first.")
        self.assertEqual(len(self.inbox.list_pending(lifecycle_id="L1", agent_id=None)), 1)

    def test_response_wait_returns_gate_decision_and_note(self) -> None:
        gate_id = self._create("agent-question")
        gates.gate_decide_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            verdict=GateVerdict(
                decision="reject", by="developer", via="dashboard", note="Needs another pass."
            ),
        )

        result = gates.gate_response_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            wait=GateWait(timeout_seconds=10.0, sleep=lambda _s: None),
        )

        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["decisionNote"], "Needs another pass.")
        self.assertEqual(result["entryCount"], 0)
        self.assertNotIn(gate_id, self.store.current("L1"))

    def test_response_wait_deleted_gate_returns_cancelled(self) -> None:
        result = gates.gate_response_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id="deleted",
            lifecycle_id="L1",
            wait=GateWait(timeout_seconds=10.0, sleep=lambda _s: None),
        )

        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "cancelled")

    def test_list_returns_folded_gates(self) -> None:
        gate_id = self._create()
        listed = gates.gate_list_tool(None, lifecycle_id="L1")  # type: ignore[arg-type]
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
            listed = gates.gate_list_tool(None, lifecycle_id=None)  # type: ignore[arg-type]
        self.assertEqual(listed["lifecycleId"], "L1")
        self.assertEqual([gate["id"] for gate in listed["gates"]], [gate_id])

    def test_list_without_id_or_ambient_falls_back_to_workspace(self) -> None:
        self.store.append(
            create_gate(
                "agent-question", gate_id="01W", now=T1, anchor=GateAnchor(lifecycle_id=None)
            )
        )
        self._create()  # a lifecycle-scoped gate that must NOT appear
        with mock.patch.object(gates, "ambient", return_value=None):
            listed = gates.gate_list_tool(None, lifecycle_id=None)  # type: ignore[arg-type]
        self.assertIsNone(listed.get("lifecycleId"))  # workspace scope (None is stripped)
        self.assertEqual([gate["id"] for gate in listed["gates"]], ["01W"])

    def test_decide_for_lifecycle_decides_newest_open(self) -> None:
        self.store.append(
            create_gate(
                "closeout-approval", gate_id="A", now=T1, anchor=GateAnchor(lifecycle_id="L1")
            )
        )
        self.store.append(
            create_gate(
                "closeout-approval", gate_id="B", now=T2, anchor=GateAnchor(lifecycle_id="L1")
            )
        )
        result = gates.gate_decide_for_lifecycle_tool(
            None,  # type: ignore[arg-type]
            lifecycle_id="L1",
            verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
        )
        self.assertEqual(result["gateId"], "B")  # newest open gate governs
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["decidedBy"], "developer")
        self.assertEqual(self.store.current("L1")["B"].state, "approved")

    def test_decide_for_lifecycle_can_target_current_gate_and_note(self) -> None:
        self.store.append(
            create_gate(
                "closeout-approval", gate_id="A", now=T1, anchor=GateAnchor(lifecycle_id="L1")
            )
        )
        result = gates.gate_decide_for_lifecycle_tool(
            None,  # type: ignore[arg-type]
            lifecycle_id="L1",
            expected_gate_id="A",
            verdict=GateVerdict(
                decision="reject", by="developer", via="dashboard", note="Needs another pass."
            ),
        )
        self.assertEqual(result["gateId"], "A")
        decided = self.store.current("L1")["A"]
        self.assertEqual(decided.state, "rejected")
        self.assertEqual(decided.decisionNote, "Needs another pass.")

    def test_decide_for_lifecycle_rejects_stale_expected_gate(self) -> None:
        self.store.append(
            create_gate("agent-question", gate_id="A", now=T1, anchor=GateAnchor(lifecycle_id="L1"))
        )
        self.store.append(
            create_gate(
                "closeout-approval", gate_id="B", now=T2, anchor=GateAnchor(lifecycle_id="L1")
            )
        )
        with self.assertRaisesRegex(KeyError, "current open gate"):
            gates.gate_decide_for_lifecycle_tool(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1",
                expected_gate_id="A",
                verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
            )

    def test_decide_for_lifecycle_no_open_gate_raises(self) -> None:
        with self.assertRaises(KeyError):
            gates.gate_decide_for_lifecycle_tool(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1",
                verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
            )

    def test_decide_for_lifecycle_unknown_decision_raises(self) -> None:
        self.store.append(
            create_gate(
                "closeout-approval", gate_id="A", now=T1, anchor=GateAnchor(lifecycle_id="L1")
            )
        )
        with self.assertRaises(ValueError):
            gates.gate_decide_for_lifecycle_tool(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1",
                verdict=GateVerdict(decision="bogus", by="developer", via="dashboard"),
            )
