from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application import gate_tools as gates
from agents_remember.application import worktree_tools
from agents_remember.application.gate_tools import GateRaise, GateWait
from agents_remember.controlplane.enforcement import evaluate_gate
from agents_remember.controlplane.gate_policy import (
    approval_failure_reason,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    GateAnchor,
    GateRecord,
    GateVerdict,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicyRule,
    apply_seam_verdict_requirement,
    make_gate_policy,
    named_gate_policy,
)
from agents_remember.serving.projections.paths import observer_logs_root
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import IntegrationSources
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_controlplane_gates import HANDOVER_SEAM_POLICY, T1, T2


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_controlplane_gates_seam.py:38).
def _handover_gate(  # pragma: no cover
    state: str = "approved",
    *,
    by: str = "lc-orchestrator",
    deciding_role: str | None = "orchestrator",
    lifecycle_id: str = "lc-manager",
    evidence_refs: list[dict[str, str]] | None = None,
) -> GateRecord:
    gate = create_gate(
        "master-handover-approval",
        gate_id="G-HANDOVER",
        now=T1,
        anchor=GateAnchor(lifecycle_id=lifecycle_id),
    )
    if state == "open":
        return gate
    return decide_gate(
        gate,
        GateVerdict(
            decision="approve", by=by, via="orchestration", note=None, deciding_role=deciding_role
        ),
        now=T1,
        evidence_refs=evidence_refs,
    )


class MasterHandoverSeamTests(unittest.TestCase):
    """The master-exit seam gate kind (developer ruling 2026-07-05)."""

    def test_master_handover_is_delegable_to_orchestrator(self) -> None:
        policy = make_gate_policy(
            [GatePolicyRule(kind="master-handover-approval", delegated_role="orchestrator")]
        )
        self.assertEqual(policy.rule_for("master-handover-approval").delegated_role, "orchestrator")

    def test_named_policy_routes_handover_to_orchestrator(self) -> None:
        policy = named_gate_policy("manager-decides-leaf-gates")
        self.assertEqual(policy.rule_for("master-handover-approval").delegated_role, "orchestrator")
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
            "master-handover-approval",
            gate_id=gate_id,
            now=T1,
            anchor=GateAnchor(lifecycle_id=lifecycle_id, enclosure=enclosure),
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
                GateVerdict(
                    decision="approve",
                    by="L-ORCH",
                    via="orchestration",
                    note=None,
                    deciding_role="orchestrator",
                ),
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
        # The application/args layer (mirror of the closeout path): the CONFIGURED
        # policy reaches integrate_result's guard, not the dataclass default.
        config = SimpleNamespace(
            coordination_root=self.coord,
            orchestration=SimpleNamespace(gate_policy=HANDOVER_SEAM_POLICY),
            # The auto-land hook is orthogonal to this test's gate-policy-plumbing
            # focus -- disabled so it never fires against this fake, unattached contract.
            retirement=SimpleNamespace(auto_land_on_integration=False),
        )
        with mock.patch.object(
            worktree_tools.git_worktree_manager,
            "integrate_result",
            return_value=SimpleNamespace(payload={"state": "integrated"}, returncode=0),
        ) as integrate_result:
            worktree_tools.worktree_integrate_tool(
                config,  # type: ignore[arg-type]
                contract_path=str(self.coord / "enclosures" / "contract.md"),
                dry_run=True,
            )
        (args,), _kwargs = integrate_result.call_args
        self.assertIs(args.gate_policy, HANDOVER_SEAM_POLICY)
        self.assertIsNot(args.gate_policy, DEFAULT_GATE_POLICY)

    # --- AR4-1(b): the unmatched-open-gate spelling-check warning (pure helper) ---

    def _warning(self):
        return integrate_mod.unmatched_handover_gate_warning(
            self.store.all_current(),
            task_name=self.MASTER,
            parent_task_name=self.SERIES,
        )

    def test_unmatched_open_gate_yields_spelling_warning(self) -> None:
        # A mis-spelled enclosure is exactly this shape: an open handover gate the
        # guard cannot match. Integration proceeds gateless, but loudly.
        self._seed(enclosure="some-other-master")
        warning = self._warning()
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertEqual(
            warning["unmatched_open_gates"],
            [{"gateId": "G-HANDOVER", "enclosure": "some-other-master"}],
        )
        self.assertIn("verify the enclosure spelling", str(warning["note"]))

    def test_no_handover_gates_yields_no_warning(self) -> None:
        self.assertIsNone(self._warning())

    def test_matching_gate_suppresses_the_warning(self) -> None:
        # The address worked — another master's in-flight open gate is legitimate.
        self._seed(enclosure=self.MASTER)
        self._seed(enclosure="some-other-master", gate_id="G-FOREIGN")
        self.assertIsNone(self._warning())

    def test_decided_foreign_gate_does_not_warn(self) -> None:
        gate = self._seed(enclosure="some-other-master")
        self._approve(gate)
        self.assertIsNone(self._warning())


class IntegrateDryRunGuardTests(unittest.TestCase):
    """AR4-2: the integrate dry-run evaluates the seam guard and persists nothing.

    ``integrate_result`` is driven with the git-touching steps mocked out (the
    live-git end-to-end drive is the disclosed AR4-6 debt); the REAL parts here
    are the ``GateStore`` fold over a temp coordination root, the guard/warning
    evaluation, and the dry-run payload assembly.
    """

    MASTER = "260703-agent-orchestration-m1"
    SERIES = "260703-agent-orchestration"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.coord = Path(tmp.name)
        self.store = GateStore(observer_logs_root(self.coord))
        task_root = self.coord / "tasks" / "agents-remember" / self.MASTER
        self.contract = WorktreeContract(
            task_id="260703-AGENT-ORCHESTRATION-M1",
            task_name=self.MASTER,
            repo_name="agents-remember",
            workflow_kind="light-task",
            memory_mode="internal",
            coordination_root=self.coord,
            task_root=task_root,
            contract_path=task_root / "enclosures" / "m1" / "series-contract.md",
            task_artifact=task_root / "task.md",
            worktree_group=self.coord / "worktrees" / "agents-remember" / "m1-ar",
            code_repo_path=self.coord / "repo",
            code_source_branch="main",
            code_work_branch="ar/m1",
            code_base_commit="c0",
            code_worktree=self.coord / "worktrees" / "agents-remember" / "m1-ar" / "m1",
            leaf_id="m1",
            parent_task_name=self.SERIES,
        )

    def _seed_gate(self, *, enclosure: str, gate_id: str = "G-HANDOVER") -> GateRecord:
        gate = create_gate(
            "master-handover-approval",
            gate_id=gate_id,
            now=T1,
            anchor=GateAnchor(lifecycle_id="L-MANAGER", enclosure=enclosure),
        )
        self.store.append(gate)
        return gate

    def _dry_run(self) -> dict[str, object]:
        args = WorktreeArgs(
            contract_path=self.contract.contract_path,
            strategy="ff-only",
            dry_run=True,
            gate_policy=HANDOVER_SEAM_POLICY,
        )
        with (
            mock.patch.object(integrate_mod, "load_contract", return_value=self.contract),
            mock.patch.object(integrate_mod, "validate_integrate_contract"),
            mock.patch.object(integrate_mod, "_integration_lineage_block", return_value=None),
            mock.patch.object(
                integrate_mod,
                "_integration_replay_requirements",
                return_value=IntegrationSources(
                    current_code_source="c1",
                    current_memory_source="",
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
            ),
            mock.patch.object(integrate_mod, "write_contract") as write_contract,
        ):
            result = integrate_mod.integrate_result(args)
        write_contract.assert_not_called()  # the dry run persists no contract mutation
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["state"], "would-integrate")
        return result.payload

    def test_dry_run_reports_open_gate_would_block(self) -> None:
        gate = self._seed_gate(enclosure=self.MASTER)
        payload = self._dry_run()
        handover = payload["handover_gate"]
        assert isinstance(handover, dict)
        self.assertFalse(handover["permitted"])
        self.assertEqual(handover["gateId"], gate.id)
        self.assertIn("open", str(handover["reason"]))
        self.assertIn("handover-gate-blocked", str(payload["summary"]))
        self.assertNotIn("handover_gate_warning", payload)

    def test_dry_run_gateless_reports_permitted(self) -> None:
        payload = self._dry_run()
        handover = payload["handover_gate"]
        assert isinstance(handover, dict)
        self.assertTrue(handover["permitted"])
        self.assertIsNone(handover["gateId"])
        self.assertIn("can proceed", str(payload["summary"]))
        self.assertNotIn("handover_gate_warning", payload)

    def test_dry_run_carries_unmatched_open_gate_warning(self) -> None:
        self._seed_gate(enclosure="some-other-master", gate_id="G-FOREIGN")
        payload = self._dry_run()
        handover = payload["handover_gate"]
        assert isinstance(handover, dict)
        self.assertTrue(handover["permitted"])  # gateless stays additive
        warning = payload["handover_gate_warning"]
        assert isinstance(warning, dict)
        self.assertEqual(
            warning["unmatched_open_gates"],
            [{"gateId": "G-FOREIGN", "enclosure": "some-other-master"}],
        )


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

    MASTER = "260703-agent-orchestration-m1"

    def _raise_handover(self, lifecycle_id: str = "L-MANAGER") -> str:
        with self._ambient(lifecycle_id):
            payload = gates.lifecycle_gate_tool(
                self.config,  # type: ignore[arg-type]
                GateRaise(
                    kind="master-handover-approval", anchor=GateAnchor(enclosure=self.MASTER)
                ),
                wait=GateWait(timeout_seconds=None, block=False),
            )
        self.assertEqual(payload["wait"]["state"], "raised")
        self.assertFalse(payload["wait"]["waited"])
        return payload["gate"]["id"]

    def test_wait_false_raises_without_blocking(self) -> None:
        gate_id = self._raise_handover()
        stored = self.store.current("L-MANAGER")[gate_id]
        self.assertEqual(stored.state, "open")
        self.assertEqual(stored.kind, "master-handover-approval")
        self.assertEqual(stored.enclosure, self.MASTER)  # the guard's address rides the gate

    def test_wait_false_refused_for_undelegated_kind(self) -> None:
        # A seam kind under an all-human policy (the default nothing forces a run to
        # change): the raise must refuse loudly and mutate NOTHING (AR3-2) — no orphan
        # open gate, and the previously open sibling is not expired as a side effect.
        sibling = create_gate(
            "agent-question",
            gate_id="G-SIBLING",
            now=T1,
            anchor=GateAnchor(lifecycle_id="L-MANAGER"),
        )
        self.store.append(sibling)
        all_human = SimpleNamespace(orchestration=SimpleNamespace(gate_policy=DEFAULT_GATE_POLICY))
        with self._ambient("L-MANAGER"), self.assertRaisesRegex(ValueError, "not delegated"):
            gates.lifecycle_gate_tool(
                all_human,  # type: ignore[arg-type]
                GateRaise(kind="master-handover-approval"),
                wait=GateWait(timeout_seconds=None, block=False),
            )
        current = self.store.current("L-MANAGER")
        self.assertEqual(set(current), {"G-SIBLING"})  # no orphan handover gate
        self.assertEqual(current["G-SIBLING"].state, "open")  # sibling not expired

    def test_wait_false_refused_without_enclosure(self) -> None:
        # AR4-1: the enclosure IS the integrate guard's address — an addressless
        # (or blank) wait=false raise could only ever fail open at the enforcement
        # rung, so it refuses BEFORE any store mutation: no orphan open gate, and
        # the pre-seeded open sibling is not expired as a side effect.
        sibling = create_gate(
            "agent-question",
            gate_id="G-SIBLING",
            now=T1,
            anchor=GateAnchor(lifecycle_id="L-MANAGER"),
        )
        self.store.append(sibling)
        for enclosure in (None, "", "   "):
            with (
                self.subTest(enclosure=enclosure),
                self._ambient("L-MANAGER"),
                self.assertRaisesRegex(ValueError, "requires\\s+enclosure=<master task name>"),
            ):
                gates.lifecycle_gate_tool(
                    self.config,  # type: ignore[arg-type]
                    GateRaise(
                        kind="master-handover-approval", anchor=GateAnchor(enclosure=enclosure)
                    ),
                    wait=GateWait(timeout_seconds=None, block=False),
                )
        current = self.store.current("L-MANAGER")
        self.assertEqual(set(current), {"G-SIBLING"})  # no orphan handover gate
        self.assertEqual(current["G-SIBLING"].state, "open")  # sibling not expired

    def test_wait_false_refused_for_delegated_non_seam_kind(self) -> None:
        # AR3-5: wait=false is reserved for SEAM kinds — plan-approval is delegated
        # under the named policy but must still block (it has no enforcement consumer).
        with (
            self._ambient("L-MANAGER"),
            self.assertRaisesRegex(ValueError, "reserved for delegated seam kinds"),
        ):
            gates.lifecycle_gate_tool(
                self.config,  # type: ignore[arg-type]
                GateRaise(kind="plan-approval"),
                wait=GateWait(timeout_seconds=None, block=False),
            )
        self.assertEqual(self.store.current("L-MANAGER"), {})

    def test_cross_lifecycle_decide_by_packet_carried_gate_id(self) -> None:
        gate_id = self._raise_handover()
        with mock.patch.object(
            gates,
            "ambient",
            return_value=SimpleNamespace(current=SimpleNamespace(id="L-ORCH", state="running")),
        ):
            decided = gates.gate_decide_tool(
                self.config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id=None,
                evidence_refs=[
                    {
                        "kind": "reviewer-verdict",
                        "ref": "notes/reports/master-exit-verdict.md",
                        "verdict": "pass",
                    }
                ],
                verdict=GateVerdict(
                    decision="approve", by=None, via="orchestration", deciding_role="orchestrator"
                ),
            )
        self.assertEqual(decided["state"], "approved")
        self.assertEqual(decided["decidedBy"], "L-ORCH")
        self.assertEqual(decided["decidingRole"], "orchestrator")

    def test_cross_lifecycle_decide_requires_verdict_when_seam_bound(self) -> None:
        gate_id = self._raise_handover()
        with (
            mock.patch.object(
                gates,
                "ambient",
                return_value=SimpleNamespace(current=SimpleNamespace(id="L-ORCH", state="running")),
            ),
            self.assertRaisesRegex(ValueError, "reviewer verdict"),
        ):
            gates.gate_decide_tool(
                self.config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id=None,
                verdict=GateVerdict(
                    decision="approve", by=None, via="orchestration", deciding_role="orchestrator"
                ),
            )

    def test_cli_decide_refused_on_delegated_kind(self) -> None:
        gate_id = self._raise_handover()
        with self.assertRaisesRegex(ValueError, "delegated by the active gate policy"):
            gates.gate_decide_tool(
                self.config,  # type: ignore[arg-type]
                gate_id=gate_id,
                lifecycle_id=None,
                verdict=GateVerdict(decision="approve", by="model", via="cli"),
            )

    def test_cancel_still_allowed_for_the_raiser(self) -> None:
        gate_id = self._raise_handover()
        cancelled = gates.gate_decide_tool(
            self.config,  # type: ignore[arg-type]
            gate_id=gate_id,
            lifecycle_id=None,
            verdict=GateVerdict(decision="cancel", by="model", via="cli"),
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
