from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.application import lifecycle_operation_worker
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration import (
    organizational_completion_integration as completion_integration,
)
from agents_remember.worktrees.integration import organizational_completion_repair as repair
from agents_remember.worktrees.integration.lifecycle_operation_identity import (
    operation_state_fingerprint,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    _operation_owner,
    contract_queue_binding,
)
from test_closeout_queue import NOW


class OrganizationalCompletionRepairBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = fixture_mod.OrganizationalCompletionIntegrationTests(
            "test_nonfinal_leaf_reuses_targeted_closeout_without_full_gate"
        )
        self.owner.setUp()
        self.fixture = self.owner.fixture

    def tearDown(self) -> None:
        try:
            self.owner.doCleanups()
        finally:
            self.owner.tearDown()

    def _repair_fixture(self) -> SimpleNamespace:
        contract = self.owner._certified_contract(final=True)
        store, _runtime, record = self.owner._integration_runtime(contract)
        authority = record.integrationAuthority
        assert authority is not None
        failure = {
            **fixture_mod.quality_mod.organizational_quality_failure_payload(contract, "test"),
            "ok": False,
            "operation": "worktree_integrate",
        }
        failed = record.model_copy(update={"result": failure})
        evidence = repair.organizational_completion_repair_evidence(contract, failed)
        failed = failed.model_copy(update={"organizationalRepair": evidence})
        binding = contract_queue_binding(contract)
        assert binding is not None
        expected = repair._repair_commits(contract, authority)
        reset = repair._quality_repair_contract(contract, expected_commits=expected)
        topology = repair.TaskDocumentTopology(contract.coordination_root)
        owner = _operation_owner(record.operationKey)
        context = repair._RepairContext(
            contract, reset, binding, owner, topology, expected, failed, evidence
        )
        return SimpleNamespace(
            contract=contract,
            store=store,
            record=record,
            authority=authority,
            failed=failed,
            evidence=evidence,
            binding=binding,
            reset=reset,
            topology=topology,
            owner=owner,
            context=context,
        )

    def _cancelled(self, facts, *, result, evidence):
        updates = {
            "status": "cancelled",
            "phase": "cancelled",
            "cancelRequested": True,
            "finishedAt": repair.now_iso(),
            "workerPid": None,
            "result": result,
            "organizationalRepair": evidence,
        }
        facts.store.update(lambda current: current.model_copy(update=updates))

    # ---- record_organizational_completion_repair :71 ----
    def test_record_repair_refuses_mismatched_operation_identity(self) -> None:
        contract = self.owner._certified_contract(final=True)
        self.owner._integration_runtime(contract)
        with self.assertRaisesRegex(RuntimeError, "lost its lifecycle operation identity"):
            repair.record_organizational_completion_repair(
                contract,
                operation_key="wrong-key",
                failure={"x": 1},
                progress=lambda _c, _p: None,
            )

    # ---- organizational_completion_repair_evidence :92 ----
    def test_repair_evidence_refuses_operation_state_mismatch(self) -> None:
        facts = self._repair_fixture()
        drifted = replace(facts.contract, code_commit="0" * 40)
        assert operation_state_fingerprint(drifted) != facts.record.candidateState
        with self.assertRaisesRegex(CloseoutQueueError, "operation-state-mismatch"):
            repair.organizational_completion_repair_evidence(drifted, facts.record)

    # ---- prepare :123, :132, :145, :155 ----
    def test_prepare_refuses_non_gate_failed_result(self) -> None:
        facts = self._repair_fixture()
        self._cancelled(facts, result={"state": "something-else"}, evidence=None)
        with self.assertRaisesRegex(CloseoutQueueError, "operation-identity-mismatch"):
            repair.prepare_organizational_completion_repair(facts.contract)

    def test_prepare_refuses_missing_repair_evidence(self) -> None:
        facts = self._repair_fixture()
        self._cancelled(facts, result=facts.failed.result, evidence=None)
        with self.assertRaisesRegex(CloseoutQueueError, "repair-evidence-missing"):
            repair.prepare_organizational_completion_repair(facts.contract)

    def test_prepare_refuses_evidence_commit_mismatch(self) -> None:
        facts = self._repair_fixture()
        wrong = facts.evidence.model_copy(update={"codeCommit": "0" * 40})
        self._cancelled(facts, result=facts.failed.result, evidence=wrong)
        with self.assertRaisesRegex(CloseoutQueueError, "repair-evidence-mismatch"):
            repair.prepare_organizational_completion_repair(facts.contract)

    def test_prepare_refuses_binding_mismatch(self) -> None:
        facts = self._repair_fixture()
        wrong = facts.evidence.model_copy(update={"sprintTaskDocument": "other-sprint"})
        self._cancelled(facts, result=facts.failed.result, evidence=wrong)
        with self.assertRaisesRegex(CloseoutQueueError, "repair-binding-mismatch"):
            repair.prepare_organizational_completion_repair(facts.contract)

    def test_prepare_refuses_incomplete_reset_generation(self) -> None:
        facts = self._repair_fixture()
        wrong = facts.evidence.model_copy(update={"resetContractSha256": "f" * 64})
        self._cancelled(facts, result=facts.failed.result, evidence=wrong)
        with self.assertRaisesRegex(CloseoutQueueError, "repair-evidence-mismatch"):
            repair.prepare_organizational_completion_repair(facts.contract)

    # ---- _retire_candidate :267 ----
    def test_retire_refuses_candidate_identity_mismatch(self) -> None:
        facts = self._repair_fixture()
        graph = repair._graph_context(facts.topology, facts.binding.sprint_ref)
        initial = repair._initial_state(facts.binding.sprint_ref, graph.revision, NOW)
        state = repair.CloseoutQueueStore(
            facts.contract.coordination_root, facts.binding.sprint_ref
        ).read(initial)
        candidate = state.candidates[facts.binding.candidate_ref.key]
        claimed = candidate.model_copy(
            update={"state": "integration-in-flight", "inFlightOwnerFingerprint": facts.owner}
        )
        drifted = claimed.model_copy(update={"contractPath": "/elsewhere/contract.md"})
        drifted_state = state.model_copy(
            update={"candidates": {facts.binding.candidate_ref.key: drifted}}
        )
        with self.assertRaisesRegex(CloseoutQueueError, "candidate-identity-mismatch"):
            repair._retire_candidate(drifted_state, context=facts.context)

    # ---- _require_operation_identity :314, :338, :351, :356 ----
    def test_require_operation_identity_refuses_identity_mismatch(self) -> None:
        facts = self._repair_fixture()
        wrong = facts.record.model_copy(update={"operationKind": "closeout"})
        with self.assertRaisesRegex(CloseoutQueueError, "operation-identity-mismatch"):
            repair._require_operation_identity(facts.contract, wrong)

    def test_require_operation_identity_refuses_missing_memory_target(self) -> None:
        facts = self._repair_fixture()
        code_only = tuple(
            target for target in repair.integration_targets(facts.contract) if target.side == "code"
        )
        with (
            mock.patch.object(repair, "integration_targets", return_value=code_only),
            self.assertRaisesRegex(CloseoutQueueError, "memory integration authority"),
        ):
            repair._require_operation_identity(facts.contract, facts.record)

    def test_require_operation_identity_refuses_memory_authority_mismatch(self) -> None:
        facts = self._repair_fixture()
        wrong_authority = facts.authority.model_copy(update={"memorySourceCommit": "0" * 40})
        wrong_record = facts.record.model_copy(update={"integrationAuthority": wrong_authority})
        with self.assertRaisesRegex(CloseoutQueueError, "memory integration authority"):
            repair._require_operation_identity(facts.contract, wrong_record)

    def test_require_operation_identity_refuses_code_only_with_memory_authority(self) -> None:
        facts = self._repair_fixture()
        disabled = replace(facts.contract, memory_mode="disabled")
        with self.assertRaisesRegex(CloseoutQueueError, "unexpected memory integration authority"):
            repair._require_operation_identity(disabled, facts.record)

    def test_require_operation_identity_accepts_code_only(self) -> None:
        facts = self._repair_fixture()
        disabled = replace(facts.contract, memory_mode="disabled")
        blank_authority = facts.authority.model_copy(
            update={
                "memoryRepository": "",
                "memorySourceBranch": "",
                "memorySourceRef": "",
                "memorySourceCommit": "",
                "memoryContentCommit": "",
                "ledgerCommit": "",
            }
        )
        blank_record = facts.record.model_copy(update={"integrationAuthority": blank_authority})
        result = repair._require_operation_identity(disabled, blank_record)
        self.assertEqual(result.targetKind, facts.authority.targetKind)

    # ---- _require_repair_evidence :377 ----
    def test_require_repair_evidence_refuses_mismatch(self) -> None:
        facts = self._repair_fixture()
        wrong = facts.evidence.model_copy(update={"operationKey": "other-key"})
        with self.assertRaisesRegex(CloseoutQueueError, "repair-evidence-mismatch"):
            repair._require_repair_evidence(facts.record, wrong)

    # ---- _repair_binding :392, :398 ----
    def test_repair_binding_refuses_missing_binding(self) -> None:
        facts = self._repair_fixture()
        unbound = replace(facts.contract, kind="series")
        with self.assertRaisesRegex(CloseoutQueueError, "candidate-required"):
            repair._repair_binding(unbound)

    def test_repair_binding_refuses_missing_master(self) -> None:
        facts = self._repair_fixture()
        with (
            mock.patch.object(repair, "contract_queue_binding", return_value=facts.binding),
            mock.patch.object(repair.TaskDocumentTopology, "parent", return_value=None),
            self.assertRaisesRegex(CloseoutQueueError, "master-mismatch"),
        ):
            repair._repair_binding(facts.contract)

    # ---- _repair_commits :425 ----
    def test_repair_commits_refuses_contract_commit_mismatch(self) -> None:
        facts = self._repair_fixture()
        drifted = replace(facts.contract, code_commit="0" * 40)
        with self.assertRaisesRegex(
            CloseoutQueueError, "no longer matches its integration authority"
        ):
            repair._repair_commits(drifted, facts.authority)

    # ---- lifecycle_operation_worker :352 ----
    def test_release_reversible_ownership_returns_none_for_repair_failure(self) -> None:
        record = cast(LifecycleOperationRecord, SimpleNamespace())
        with mock.patch.object(
            lifecycle_operation_worker, "_organizational_repair_failure", return_value="failure"
        ):
            self.assertIsNone(
                lifecycle_operation_worker._release_reversible_queue_ownership(
                    record, restored=False
                )
            )

    # ---- operation.py validator :299, :303 ----
    def test_repair_evidence_validator_requires_integration_operation(self) -> None:
        facts = self._repair_fixture()
        with self.assertRaisesRegex(ValueError, "belongs to integration"):
            LifecycleOperationRecord.model_validate(
                {
                    **facts.failed.model_dump(mode="json"),
                    "operationKind": "closeout",
                    "integrationAuthority": None,
                    "result": None,
                }
            )

    def test_repair_evidence_validator_requires_failure_result(self) -> None:
        facts = self._repair_fixture()
        with self.assertRaisesRegex(ValueError, "requires its exact failure result"):
            LifecycleOperationRecord.model_validate(
                {**facts.failed.model_dump(mode="json"), "result": None}
            )

    # ---- integrate.py :1184 ----
    def test_run_integration_quality_gate_skips_repair_without_progress(self) -> None:
        contract = self.owner._certified_contract(final=True)
        completion = completion_integration.preview_organizational_completion(contract)
        assert completion is not None
        with mock.patch.object(
            fixture_mod.quality_mod,
            "run_strict_code_quality_gate",
            side_effect=RuntimeError("boom"),
        ):
            result, failure = fixture_mod.integrate_mod._run_integration_quality_gate(
                contract, completion=completion, args=None
            )
        self.assertEqual(result, {})
        assert failure is not None
        self.assertEqual(failure["state"], "organizational-completion-gate-failed")

    # ---- lifecycle_operations.py :251 ----
    def test_cancel_gate_failed_operation_requires_repair_evidence(self) -> None:
        facts = self._repair_fixture()
        facts.store.update(
            lambda current: current.model_copy(
                update={"result": facts.failed.result, "organizationalRepair": None}
            )
        )
        with self.assertRaisesRegex(RuntimeError, "durable repair evidence"):
            fixture_mod.cancel_operation(facts.contract.contract_path, "integrate")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
