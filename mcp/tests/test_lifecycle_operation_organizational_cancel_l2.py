"""Focused public forcing for organizational integration cancellation in L2."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from agents_remember.application.lifecycle import lifecycle_operation_worker
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
    worktree_status_tool,
)
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationPublicationIntent,
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.integration import integration_quality as quality_mod
from agents_remember.worktrees.integration import integration_ref_state
from agents_remember.worktrees.integration import organizational_completion_repair as repair_mod
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_controls as controls_mod,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    start_or_observe_operation,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    amend_contract,
    load_contract,
    write_contract,
)
from closeout_input_test_support import (
    closeout_operation_input,
    publish_closeout_finalization,
    start_closeout_operation,
)
from test_closeout_queue import LEAF_A, MASTER_A, QueueFixture
from test_worktree_support import git


class OrganizationalCancellationL2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _mark_master_work_complete(self) -> None:
        path = self.fixture.tasks / "master-a" / "task.json"
        master = read_task_doc(path)
        rows = [row.model_copy(update={"status": "Completed"}) for row in master.subTasks]
        write_task_doc(path.parent, master.model_copy(update={"subTasks": rows}))

    def _certified_contract(self):
        self._mark_master_work_complete()
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_closeout_operation(
            closeout_operation_input(
                contract,
                config_path=self.fixture.config_path,
                code="close organizational leaf",
                memory="close organizational memory",
                ledger="map organizational pair",
                approval_note="approved",
            ),
            launcher=lambda *_: None,
        )
        closeout_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        closeout_runtime = lifecycle_operation_worker.OperationRuntime(closeout_store)
        closeout = closeout_runtime.start()
        contract = load_contract(contract.contract_path)
        claim_queue_candidate_for_closeout(contract, closeout.operationKey)
        closed = self.fixture.close_contract(MASTER_A)
        publish_closeout_finalization(closeout_runtime, closed)
        certify_queue_candidate_closeout(closed, closeout.operationKey)
        closeout_runtime.finish({"state": "closed"}, ok=True)
        git(closed.code_repo_path, "checkout", closed.code_source_branch)
        assert closed.memory_repo_path is not None
        git(closed.memory_repo_path, "checkout", closed.memory_source_branch)
        return closed

    def _integration_runtime(self, contract):
        start_or_observe_operation(
            IntegrateOperationInput(
                configPath=self.fixture.config_path.as_posix(),
                contractPath=contract.contract_path.as_posix(),
                autoCompleteSeats=False,
            ),
            contract,
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        runtime = lifecycle_operation_worker.OperationRuntime(store)
        return store, runtime, runtime.start()

    def _candidate_projection(self):
        status = self.fixture.status()
        return next(
            item
            for lane in ("ready", "inFlight", "blocked")
            for item in status[lane]
            if item["taskDocumentRef"] == LEAF_A.model_dump()
        )

    def _public_cancel(self, contract):
        return worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**self._cancel_arguments(contract)),
        )

    def _cancel_arguments(self, contract):
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        record = store.read()
        assert record is not None
        current = load_contract(contract.contract_path)
        row = next(
            item for item in legal_operation_controls(current, record) if item["action"] == "cancel"
        )
        return row["arguments"]

    def _queue_bytes(self):
        store = CloseoutQueueStore(self.fixture.coord, MASTER_A)
        return {
            path.name: path.read_bytes() if path.is_file() else None
            for path in (store.state_path, store.pending_path)
        }

    def _task_bytes(self):
        return {
            path.relative_to(self.fixture.tasks).as_posix(): path.read_bytes()
            for path in self.fixture.tasks.rglob("*")
            if path.is_file() and (path.name == "task.json" or path.suffix == ".md")
        }

    def _integration_status(self, contract):
        with mock.patch(
            "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
            return_value=WorktreeCommandResult(
                0,
                {
                    "contract_path": contract.contract_path.as_posix(),
                    "task_name": contract.task_name,
                },
            ),
        ):
            status = worktree_status_tool(
                self.fixture.cfg,
                TaskRef(
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                ),
            )
        return next(row for row in status["lifecycleOperations"] if row["kind"] == "integrate")

    @staticmethod
    def _ref_tips(contract):
        refs = {
            "code": git(
                contract.code_repo_path,
                "rev-parse",
                contract.code_source_branch,
            )
        }
        assert contract.memory_repo_path is not None
        refs["memory"] = git(
            contract.memory_repo_path,
            "rev-parse",
            contract.memory_source_branch,
        )
        return refs

    def _fail_final_gate(self, contract):
        store, runtime, record = self._integration_runtime(contract)
        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            side_effect=RuntimeError("full Dagger failure"),
        ):
            lifecycle_operation_worker.execute_operation(record, runtime)
        failed = store.read()
        assert failed is not None
        self.assertEqual(failed.status, "input-required")
        self.assertIsNotNone(failed.organizationalRepair)
        return store, failed

    def test_quality_failure_has_one_persisted_and_projected_decision_surface(self) -> None:
        contract = self._certified_contract()
        _store, failed = self._fail_final_gate(contract)
        assert failed.result is not None
        self.assertEqual(
            failed.result["decisionSurface"],
            quality_mod.INTEGRATION_QUALITY_DECISION_SURFACE,
        )
        projected = self._integration_status(contract)
        self.assertEqual(
            projected["result"]["decisionSurface"],
            quality_mod.INTEGRATION_QUALITY_DECISION_SURFACE,
        )
        self.assertTrue(projected["result"]["developerDecisionRequired"])

    def _assert_existing_repair_ref_conflict(self, *, memory: bool) -> None:
        contract = self._certified_contract()
        store, failed = self._fail_final_gate(contract)
        authority = failed.integrationAuthority
        assert authority is not None
        arguments = self._cancel_arguments(contract)
        if memory:
            assert contract.memory_repo_path is not None
            repository = contract.memory_repo_path
            branch = authority.memorySourceBranch
            target = authority.ledgerCommit
        else:
            repository = contract.code_repo_path
            branch = authority.codeSourceBranch
            target = authority.codeCandidateCommit
        git(repository, "update-ref", f"refs/heads/{branch}", target)
        journal_before = store.path.read_bytes()
        contract_before = contract.contract_path.read_bytes()
        queue_before = self._queue_bytes()
        tasks_before = self._task_bytes()
        refs_before = self._ref_tips(contract)

        projected = self._integration_status(contract)
        self.assertEqual(projected["legalControls"], [])
        decision = projected["result"]
        self.assertEqual(decision["state"], "integration-ref-conflict")
        self.assertEqual(decision["nextAction"], "developer-decision")
        refused = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**arguments),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(
            {
                "status": decision["state"],
                "detail": decision["decisionSurface"],
                "developerDecisionRequired": decision["developerDecisionRequired"],
                "nextAction": decision["nextAction"],
                "expected": decision["expected"],
                "observed": decision["observed"],
            },
            {
                key: refused[key]
                for key in (
                    "status",
                    "detail",
                    "developerDecisionRequired",
                    "nextAction",
                    "expected",
                    "observed",
                )
            },
        )
        self.assertEqual(store.path.read_bytes(), journal_before)
        self.assertEqual(contract.contract_path.read_bytes(), contract_before)
        self.assertEqual(self._queue_bytes(), queue_before)
        self.assertEqual(self._task_bytes(), tasks_before)
        self.assertEqual(self._ref_tips(contract), refs_before)

    def test_failed_final_gate_moves_no_ref_and_cancel_reopens_same_leaf(self) -> None:
        contract = self._certified_contract()
        store, failed = self._fail_final_gate(contract)
        queue_before = self._queue_bytes()
        tasks_before = self._task_bytes()
        refs_before = self._ref_tips(contract)
        door_before = contract.closeout_door
        assert door_before is not None
        self.assertEqual(self._candidate_projection()["candidateState"], "integration-in-flight")
        cancelled = self._public_cancel(contract)
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["lifecycleOperation"]["status"], "cancelled")
        reset = load_contract(contract.contract_path)
        self.assertEqual((reset.closeout_status, reset.code_commit), ("not-started", ""))
        assert reset.closeout_door is not None
        self.assertEqual(reset.closeout_door.generationId, door_before.generationId)
        self.assertEqual(reset.closeout_door.disposition, "cancelled")
        self.assertIsNone(reset.closeout_door.operationKind)
        self.assertEqual(reset.closeout_door.operationFingerprint, "")
        self.assertEqual(reset.closeout_door.claimedOperationKey, "")
        repaired = store.read()
        assert repaired is not None and repaired.organizationalRepair is not None
        assert failed.organizationalRepair is not None
        self.assertEqual(
            repaired.organizationalRepair.acceptedContractSha256,
            failed.organizationalRepair.acceptedContractSha256,
        )
        self.assertEqual(self._queue_bytes(), queue_before)
        self.assertEqual(self._task_bytes(), tasks_before)
        self.assertEqual(self._ref_tips(contract), refs_before)

    def test_existing_code_ref_move_blocks_repair_before_cancellation(self) -> None:
        self._assert_existing_repair_ref_conflict(memory=False)

    def test_existing_memory_ref_move_blocks_repair_before_cancellation(self) -> None:
        self._assert_existing_repair_ref_conflict(memory=True)

    def test_missing_code_ref_blocks_repair_through_one_public_classifier(self) -> None:
        self._assert_unreadable_repair_ref(side="code", failure="ref-missing")

    def test_unreadable_memory_ref_blocks_repair_through_one_public_classifier(self) -> None:
        self._assert_unreadable_repair_ref(side="memory", failure="ref-unreadable")

    def _assert_unreadable_repair_ref(self, *, side: str, failure: str) -> None:
        contract = self._certified_contract()
        store, failed = self._fail_final_gate(contract)
        authority = failed.integrationAuthority
        assert authority is not None and contract.memory_repo_path is not None
        arguments = self._cancel_arguments(contract)
        repository = Path(
            authority.codeRepository if side == "code" else authority.memoryRepository
        )
        reference = authority.codeSourceRef if side == "code" else authority.memorySourceRef
        real_run_git = integration_ref_state.run_git
        returncode = 1 if failure == "ref-missing" else 2

        def unreadable_ref(repo: Path, args: list[str]):
            if repo.resolve() == repository.resolve() and args == [
                "show-ref",
                "--verify",
                reference,
            ]:
                return CompletedProcess(args, returncode, stdout="", stderr="not public")
            return real_run_git(repo, args)

        before = {
            "journal": store.path.read_bytes(),
            "contract": contract.contract_path.read_bytes(),
            "queue": self._queue_bytes(),
            "tasks": self._task_bytes(),
            "refs": self._ref_tips(contract),
        }
        with mock.patch.object(
            integration_ref_state,
            "run_git",
            side_effect=unreadable_ref,
        ):
            projected = self._integration_status(contract)
            refused = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**arguments),
            )
        self.assertEqual(projected["legalControls"], [])
        decision = projected["result"]
        self.assertEqual(decision["state"], "integration-ref-conflict")
        self.assertEqual(decision["nextAction"], "developer-decision")
        self.assertEqual(
            decision["observed"][f"{side}Ref"]["errorType"],
            failure,
        )
        self.assertNotIn("not public", repr(decision))
        self.assertFalse(refused["ok"])
        self.assertEqual(
            {
                "status": decision["state"],
                "detail": decision["decisionSurface"],
                "developerDecisionRequired": decision["developerDecisionRequired"],
                "nextAction": decision["nextAction"],
                "expected": decision["expected"],
                "observed": decision["observed"],
            },
            {
                key: refused[key]
                for key in (
                    "status",
                    "detail",
                    "developerDecisionRequired",
                    "nextAction",
                    "expected",
                    "observed",
                )
            },
        )
        self.assertEqual(
            {
                "journal": store.path.read_bytes(),
                "contract": contract.contract_path.read_bytes(),
                "queue": self._queue_bytes(),
                "tasks": self._task_bytes(),
                "refs": self._ref_tips(contract),
            },
            before,
        )

    def test_ref_race_after_cancel_preflight_leaves_only_cancellation_facts(self) -> None:
        contract = self._certified_contract()
        store, failed = self._fail_final_gate(contract)
        authority = failed.integrationAuthority
        assert authority is not None
        arguments = self._cancel_arguments(contract)
        queue_before = self._queue_bytes()
        tasks_before = self._task_bytes()
        contract_before = contract.contract_path.read_bytes()
        refs_before = self._ref_tips(contract)
        record_before = failed.model_dump(mode="json")
        real_prepare = repair_mod.prepare_organizational_completion_repair

        def move_ref_then_revalidate(current):
            git(
                contract.code_repo_path,
                "update-ref",
                f"refs/heads/{authority.codeSourceBranch}",
                authority.codeCandidateCommit,
                authority.codeSourceCommit,
            )
            return real_prepare(current)

        with mock.patch.object(
            controls_mod,
            "prepare_organizational_completion_repair",
            side_effect=move_ref_then_revalidate,
        ):
            refused = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**arguments),
            )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "integration-ref-conflict")
        self.assertEqual(refused["nextAction"], "developer-decision")
        after = store.read()
        assert after is not None
        record_after = after.model_dump(mode="json")
        changed = {
            key
            for key in record_before.keys() | record_after.keys()
            if record_before.get(key) != record_after.get(key)
        }
        self.assertLessEqual(
            changed,
            {
                "status",
                "phase",
                "finishedAt",
                "cancelRequested",
                "currentCommand",
                "generationDisposition",
                "cancellationEvidence",
            },
        )
        self.assertTrue(after.cancelRequested)
        self.assertEqual(after.status, "cancelled")
        self.assertEqual(contract.contract_path.read_bytes(), contract_before)
        self.assertEqual(self._queue_bytes(), queue_before)
        self.assertEqual(self._task_bytes(), tasks_before)
        refs_after = self._ref_tips(contract)
        self.assertEqual(refs_after["memory"], refs_before["memory"])
        self.assertEqual(refs_after["code"], authority.codeCandidateCommit)

    def test_quality_repair_cannot_coexist_with_claim_publication_or_output(self) -> None:
        contract = self._certified_contract()
        _store, failed = self._fail_final_gate(contract)
        authority = failed.integrationAuthority
        assert authority is not None
        payload = failed.model_dump(mode="json")
        payload.update(
            {
                "integrationPublication": IntegrationPublicationIntent(
                    operationKey=failed.operationKey,
                    generation=failed.generation,
                    preparedAt=failed.queuedAt,
                    claimState="not-applicable",
                ).model_dump(mode="json"),
                "recoveryCommits": LifecycleOperationRecoveryCommits(
                    codeCommit=authority.codeCandidateCommit,
                    memoryContentCommit=authority.memoryContentCommit,
                    ledgerCommit=authority.ledgerCommit,
                ).model_dump(mode="json"),
            }
        )
        with self.assertRaisesRegex(ValueError, "exact preclaim mode"):
            LifecycleOperationRecord.model_validate(payload)

    def test_quality_repair_retries_when_reset_write_was_interrupted(self) -> None:
        contract = self._certified_contract()
        self._fail_final_gate(contract)
        queue_before = self._queue_bytes()
        tasks_before = self._task_bytes()
        refs_before = self._ref_tips(contract)
        arguments = self._cancel_arguments(contract)
        private_sentinel = "PRIVATE-RESET-WRITE-/secret/path"
        with mock.patch.object(
            repair_mod,
            "write_contract",
            side_effect=OSError(private_sentinel),
        ):
            interrupted = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**arguments),
            )
        self.assertFalse(interrupted["ok"])
        self.assertEqual(
            interrupted["status"],
            "organizational-completion-contract-publication-interrupted",
        )
        self.assertEqual(interrupted["nextTool"], "worktree_operation_control")
        projected = self._integration_status(contract)
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        self.assertNotIn(private_sentinel, repr([interrupted, projected, store.read()]))
        resumed = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**interrupted["nextArgs"]),
        )
        self.assertTrue(resumed["ok"])
        self.assertEqual(self._queue_bytes(), queue_before)
        self.assertEqual(self._task_bytes(), tasks_before)
        self.assertEqual(self._ref_tips(contract), refs_before)

    def test_quality_repair_same_cancel_proves_post_write_reset(self) -> None:
        contract = self._certified_contract()
        self._fail_final_gate(contract)
        queue_before = self._queue_bytes()
        tasks_before = self._task_bytes()
        refs_before = self._ref_tips(contract)
        arguments = self._cancel_arguments(contract)
        real_write = repair_mod.write_contract

        def write_then_interrupt(path, candidate):
            real_write(path, candidate)
            raise OSError("response lost after exact reset write")

        with mock.patch.object(
            repair_mod,
            "write_contract",
            side_effect=write_then_interrupt,
        ):
            first = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**arguments),
            )
        self.assertTrue(first["ok"])
        retried = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**arguments),
        )
        self.assertTrue(retried["ok"])
        self.assertEqual(self._queue_bytes(), queue_before)
        self.assertEqual(self._task_bytes(), tasks_before)
        self.assertEqual(self._ref_tips(contract), refs_before)

    def test_quality_repair_refuses_a_partial_reset_generation(self) -> None:
        contract = self._certified_contract()
        store, _failed = self._fail_final_gate(contract)
        arguments = self._cancel_arguments(contract)
        partial = amend_contract(
            replace(
                contract,
                approved_for_commit=False,
                commit_approval_note="stale approval",
                code_commit="",
                memory_content_commit="",
                ledger_commit="",
                integration_strategy="ff-only",
                integrated_code_commit="f" * 40,
                memory_state="stale",
            ),
            ContractCells(
                closeout_status="not-started",
                integration_status="not-started",
            ),
        )
        write_contract(partial.contract_path, partial)
        queue_before = self._queue_bytes()
        tasks_before = self._task_bytes()
        refs_before = self._ref_tips(contract)
        contract_before = partial.contract_path.read_bytes()
        journal_before = store.path.read_bytes()
        projected = self._integration_status(partial)
        self.assertEqual(projected["legalControls"], [])
        decision = projected["result"]
        self.assertEqual(decision["state"], "organizational-completion-contract-conflict")
        self.assertEqual(decision["nextAction"], "developer-decision")
        self.assertTrue(decision["developerDecisionRequired"])
        for stale_key in ("nextTool", "nextArgs", "arguments", "apply", "applyStep"):
            self.assertNotIn(stale_key, decision)
        self.assertNotIn("cancel", projected["guidance"].lower())
        refused = worktree_operation_control_tool(
            self.fixture.cfg,
            OperationControlRequest(**arguments),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "organizational-completion-contract-conflict")
        self.assertEqual(refused["nextAction"], "developer-decision")
        self.assertTrue(refused["developerDecisionRequired"])
        self.assertIn("acceptedContractSha256", refused["expected"])
        self.assertEqual(refused["observed"]["closeoutStatus"], "not-started")
        self.assertEqual(
            {
                "status": decision["state"],
                "detail": decision["decisionSurface"],
                "developerDecisionRequired": decision["developerDecisionRequired"],
                "nextAction": decision["nextAction"],
                "expected": decision["expected"],
                "observed": decision["observed"],
            },
            {
                key: refused[key]
                for key in (
                    "status",
                    "detail",
                    "developerDecisionRequired",
                    "nextAction",
                    "expected",
                    "observed",
                )
            },
        )
        self.assertEqual(store.path.read_bytes(), journal_before)
        self.assertEqual(partial.contract_path.read_bytes(), contract_before)
        self.assertEqual(self._queue_bytes(), queue_before)
        self.assertEqual(self._task_bytes(), tasks_before)
        self.assertEqual(self._ref_tips(contract), refs_before)


if __name__ == "__main__":
    unittest.main()
