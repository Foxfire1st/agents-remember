from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from agents_remember.application import gate_tools as gates
from agents_remember.application.gate_tools import GateRaise, GateWait
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.records import GateAnchor, GateRequest, GateVerdict, decide_gate
from agents_remember.mcp.tools.lifecycle import lifecycle_start_payload
from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, reset_ambient
from agents_remember.observer.ambient import AmbientTiming
from agents_remember.observer.reducer import AnalyticalInputs, WorkspaceStructure, project_workspace
from test_controlplane_gates import (
    MANAGER_CLOSEOUT_POLICY,
    MANAGER_CLOSEOUT_WITH_VERDICT_POLICY,
    T1,
    T2,
    GateToolTests,
)


class GateToolTests1(GateToolTests):
    def test_create_without_lifecycle_uses_active_ambient(self) -> None:
        with mock.patch.object(
            gates,
            "ambient",
            return_value=SimpleNamespace(current=SimpleNamespace(id="L-ACTIVE")),
        ):
            created = gates.gate_create_tool(
                None,  # type: ignore[arg-type]
                kind="agent-question",
                anchor=GateAnchor(lifecycle_id=None),
            )

        self.assertEqual(created["lifecycleId"], "L-ACTIVE")
        self.assertEqual(len(self.store.current("L-ACTIVE")), 1)
        self.assertEqual(self.store.current(None), {})

    def test_create_without_lifecycle_requires_active_ambient(self) -> None:
        with (
            mock.patch.object(gates, "ambient", return_value=None),
            self.assertRaisesRegex(Exception, "active lifecycle"),
        ):
            gates.gate_create_tool(
                None,  # type: ignore[arg-type]
                kind="agent-question",
                anchor=GateAnchor(lifecycle_id=None),
            )

    def test_create_then_decide_records_attribution(self) -> None:
        gate_id = self._create()
        decided = gates.gate_decide_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            verdict=GateVerdict(decision="approve", by="model", via="cli"),
        )
        self.assertEqual(decided["state"], "approved")
        self.assertEqual(decided["decidedBy"], "model")
        self.assertEqual(decided["decidedVia"], "cli")

    def test_orchestration_decision_records_lifecycle_identity_and_evidence(self) -> None:
        gate_id = self._create()
        config = SimpleNamespace(
            orchestration=SimpleNamespace(gate_policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY)
        )

        decided = gates.gate_decide_tool(
            config,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            evidence_refs=[
                {
                    "kind": "reviewer-verdict",
                    "ref": "notes/reports/verdict.md",
                    "verdict": "pass",
                }
            ],
            verdict=GateVerdict(
                decision="approve", by="L-manager", via="orchestration", deciding_role="manager"
            ),
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
        config = SimpleNamespace(orchestration=SimpleNamespace(gate_policy=MANAGER_CLOSEOUT_POLICY))

        with self.assertRaisesRegex(ValueError, "owning lifecycle"):
            gates.gate_decide_tool(
                config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id="L1",
                verdict=GateVerdict(
                    decision="approve", by="L1", via="orchestration", deciding_role="manager"
                ),
            )

        self.assertEqual(self.store.current("L1")[gate_id].state, "open")

    def test_orchestration_decision_requires_verdict_when_policy_requires_it(self) -> None:
        gate_id = self._create()
        config = SimpleNamespace(
            orchestration=SimpleNamespace(gate_policy=MANAGER_CLOSEOUT_WITH_VERDICT_POLICY)
        )

        with self.assertRaisesRegex(ValueError, "requires reviewer verdict evidence"):
            gates.gate_decide_tool(
                config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id="L1",
                verdict=GateVerdict(
                    decision="approve", by="L-manager", via="orchestration", deciding_role="manager"
                ),
            )

        self.assertEqual(self.store.current("L1")[gate_id].state, "open")

    def test_create_expires_previous_open_lifecycle_gate(self) -> None:
        first = self._create("agent-question")
        second = self._create("closeout-approval")
        current = self.store.current("L1")
        self.assertEqual(current[first].state, "expired")
        self.assertEqual(current[second].state, "open")

        result = gates.gate_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id=first,
            lifecycle_id="L1",
            wait=GateWait(timeout_seconds=30.0, poll_seconds=1.0, sleep=lambda _s: None),
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
            result = gates.lifecycle_gate_tool(
                None,  # type: ignore[arg-type]
                GateRaise(
                    kind="plan-approval",
                    anchor=GateAnchor(lifecycle_id="L1"),
                    request=GateRequest(packet={"summary": "plan"}),
                    ask={
                        "kind": "decision",
                        "prompt": "Approve?",
                        "options": ["approve", "revise"],
                    },
                ),
                wait=GateWait(
                    timeout_seconds=0.0, sleep=lambda _s: None, monotonic=lambda: next(clock)
                ),
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
                    GateVerdict(
                        decision="approve",
                        by="developer",
                        via="dashboard",
                        note="approved from dashboard",
                    ),
                    now=T2,
                )
            )

        with mock.patch.object(gates, "require_ambient", return_value=lifecycle):
            result = gates.lifecycle_gate_tool(
                None,  # type: ignore[arg-type]
                GateRaise(
                    kind="plan-approval",
                    anchor=GateAnchor(lifecycle_id="L1"),
                    ask={"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
                ),
                wait=GateWait(timeout_seconds=None, sleep=approve_on_sleep),
            )

        self.assertEqual(result["gate"]["state"], "approved")
        self.assertFalse(result["wait"]["timedOut"])
        self.assertEqual(result["wait"]["state"], "approved")
        self.assertEqual(result["wait"]["decidedBy"], "developer")
        self.assertEqual(result["wait"]["decisionNote"], "approved from dashboard")

    def test_lifecycle_gate_ignores_ungated_lifecycle_inbox_entry(self) -> None:
        self.inbox.append(
            create_operator_inbox_entry(
                InboxMessage(ask="Previous prompt", response="Previous response", gate_id=None),
                entry_id="I1",
                now=T1,
                routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id=None)),
                poster=InboxPoster(created_by="developer", created_via="dashboard"),
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
                    GateVerdict(decision="approve", by="developer", via="dashboard", note=None),
                    now=T2,
                )
            )

        with mock.patch.object(gates, "require_ambient", return_value=lifecycle):
            result = gates.lifecycle_gate_tool(
                None,  # type: ignore[arg-type]
                GateRaise(
                    kind="plan-approval",
                    anchor=GateAnchor(lifecycle_id="L1"),
                    ask={"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
                ),
                wait=GateWait(timeout_seconds=None, sleep=approve_on_sleep),
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
            gates.lifecycle_gate_tool(
                None,  # type: ignore[arg-type]
                GateRaise(kind="plan-approval", anchor=GateAnchor(lifecycle_id="other")),
            )

        self.assertEqual(self.store.current("L1"), {})

    def test_lifecycle_gate_projects_blocked_ask_and_current_gate(self) -> None:
        events = EventStore(self.root)
        install_ambient(AmbientLifecycle(events, timing=AmbientTiming(heartbeat_seconds=3600)))
        self.addCleanup(reset_ambient)
        started = lifecycle_start_payload()

        result = gates.lifecycle_gate_tool(
            None,  # type: ignore[arg-type]
            GateRaise(
                kind="plan-approval",
                request=GateRequest(
                    packet={"summary": "plan"},
                    evidence_refs=[
                        {
                            "kind": "reviewer-verdict",
                            "ref": "notes/reports/verdict.md",
                            "verdict": "pass",
                        }
                    ],
                ),
                ask={"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
            ),
            wait=GateWait(timeout_seconds=0.0, sleep=lambda _s: None, monotonic=lambda: 0.0),
        )

        lifecycle_id = started["lifecycleId"]
        projection = project_workspace(
            [events.read(lifecycle_id)],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=datetime.now(UTC),
            given=AnalyticalInputs(gates=list(self.store.current(lifecycle_id).values())),
        )
        lifecycle = projection.lifecycles[0]
        self.assertEqual(lifecycle.state, "blocked")
        self.assertEqual(
            lifecycle.ask,
            {"kind": "decision", "prompt": "Approve?", "options": ["approve"]},
        )
        assert lifecycle.gate is not None
        self.assertEqual(
            (lifecycle.gate.id, lifecycle.gate.kind), (result["gate"]["id"], "plan-approval")
        )
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
            gates.gate_decide_tool(
                None,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id="L1",
                verdict=GateVerdict(decision="bogus", by="model", via="cli"),
            )

    def test_decide_missing_gate_raises(self) -> None:
        with self.assertRaises(KeyError):
            gates.gate_decide_tool(
                None,  # type: ignore[arg-type]
                gate_id="nope",
                lifecycle_id="L1",
                verdict=GateVerdict(decision="approve", by="model", via="cli"),
            )

    def test_wait_returns_when_decided(self) -> None:
        gate_id = self._create("agent-question")
        gates.gate_decide_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
        )
        result = gates.gate_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            wait=GateWait(timeout_seconds=30.0, poll_seconds=1.0, sleep=lambda _s: None),
        )
        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "approved")

    def test_wait_returns_decision_note(self) -> None:
        gate_id = self._create("agent-question")
        gates.gate_decide_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            verdict=GateVerdict(
                decision="reject", by="developer", via="dashboard", note="Needs another pass."
            ),
        )
        result = gates.gate_wait_tool(
            None,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id="L1",
            wait=GateWait(timeout_seconds=30.0, poll_seconds=1.0, sleep=lambda _s: None),
        )
        self.assertFalse(result["timedOut"])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["decisionNote"], "Needs another pass.")
