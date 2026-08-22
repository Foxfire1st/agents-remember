"""Truthful closeout-generation transition after queue conflict repair."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.models.lifecycles.operation import IntegrateOperationInput
from agents_remember.tasks import write_task_doc
from agents_remember.worktrees.integration.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle_operations import (
    cancel_operation,
    start_or_observe_operation,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    prove_git_commit,
)
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules import sync as sync_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from closeout_input_test_support import closeout_operation_input, start_closeout_operation
from test_closeout_queue import LEAF_A, MASTER_A, QueueFixture, _leaf
from test_worktree_support import git


class CloseoutQueueGenerationTransitionTests(unittest.TestCase):
    def test_conflict_reset_admits_a_truthful_new_closeout_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QueueFixture(Path(tmp), memory_mode="internal")
            fixture.declare(MASTER_A)
            fixture.mutate("select", candidate=LEAF_A)
            candidate = fixture.contracts[MASTER_A]
            closed = _close_and_certify_candidate(
                fixture,
                candidate,
                "close original candidate",
            )
            integration_input, integration_store = _open_conflicting_integration(closed)
            cancelled = cancel_operation(closed.contract_path, "integrate")
            self.assertEqual(cancelled.status, "cancelled")
            reset = load_contract(closed.contract_path)
            self.assertEqual((reset.closeout_status, reset.code_commit), ("not-started", ""))
            _assert_candidate_absent(self, fixture)

            synced = sync_mod.sync_result(WorktreeArgs(contract_path=reset.contract_path))
            self.assertEqual(synced.payload["state"], "synced")
            reset = load_contract(reset.contract_path)
            (reset.code_worktree / "resolution.txt").write_text("resolved\n", encoding="utf-8")
            write_task_doc(reset.task_root, _leaf(reset, "leaf-a"))
            fixture.contracts[MASTER_A] = reset
            fixture.declare(MASTER_A)
            fixture.mutate("select", candidate=LEAF_A)
            resolved = _close_and_certify_candidate(
                fixture,
                reset,
                "close resolved candidate",
            )
            integrated = _finish_integration(resolved, integration_input, integration_store)
            self.assertEqual(integrated.payload["state"], "integrated")
            _assert_candidate_absent(self, fixture)


def _open_conflicting_integration(
    closed: WorktreeContract,
) -> tuple[IntegrateOperationInput, LifecycleOperationStore]:
    git(closed.code_repo_path, "checkout", closed.code_source_branch)
    (closed.code_repo_path / "parallel.txt").write_text("parallel\n", encoding="utf-8")
    git(closed.code_repo_path, "add", "parallel.txt")
    git(closed.code_repo_path, "commit", "-m", "parallel source")
    operation_input = IntegrateOperationInput(
        configPath=(closed.code_repo_path.parent / "settings.json").as_posix(),
        contractPath=closed.contract_path.as_posix(),
        strategy="replay",
    )
    start_or_observe_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(closed.worktree_group, "integrate"))
    record = OperationRuntime(store).start()
    handoff = integrate_mod.integrate_result(
        WorktreeArgs(
            contract_path=closed.contract_path,
            approved=True,
            strategy="replay",
            operation_key=record.operationKey,
        )
    )
    assert handoff.payload["state"] == "integration-resolution-required"
    assert handoff.payload["conflictTransaction"] is not None
    return operation_input, store


def _finish_integration(
    resolved: WorktreeContract,
    previous: IntegrateOperationInput,
    store: LifecycleOperationStore,
):
    next_input = IntegrateOperationInput(
        configPath=previous.configPath,
        contractPath=previous.contractPath,
    )
    start_or_observe_operation(next_input, launcher=lambda *_: None)
    record = OperationRuntime(store).start()
    with mock.patch.object(
        integrate_mod,
        "_run_integration_quality_gate",
        return_value=({"passed": True}, None),
    ):
        return integrate_mod.integrate_result(
            WorktreeArgs(
                contract_path=resolved.contract_path,
                approved=True,
                operation_key=record.operationKey,
            )
        )


def _close_and_certify_candidate(
    fixture: QueueFixture,
    contract: WorktreeContract,
    message: str,
) -> WorktreeContract:
    operation_input = closeout_operation_input(contract, code=message, approval_note="approved")
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
    runtime = OperationRuntime(store)
    record = runtime.start()
    claim_queue_candidate_for_closeout(contract, record.operationKey)
    runtime.progress("approval-claim", {"approval_claimed": True})
    mutation_args = WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=operation_input.effectiveInput,
        operation_progress=runtime.progress,
    )
    intent = begin_git_mutation(
        mutation_args,
        leg="code",
        repository=contract.code_worktree,
        expected_output_tree=None,
        use_current_candidate=True,
    )
    git(contract.code_worktree, "add", "-A")
    git(contract.code_worktree, "commit", "-m", message)
    code_commit = git(contract.code_worktree, "rev-parse", "HEAD")
    prove_git_commit(
        mutation_args,
        intent,
        repository=contract.code_worktree,
        commit=code_commit,
    )
    closed = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        commit_approval_note=operation_input.approvalNote,
        closeout_status="completed",
        code_commit=code_commit,
    )
    runtime.progress(
        "contract-finalization",
        {"closeout_finalized_contract_sha256": closeout_contract_sha256(closed)},
    )
    write_contract(closed.contract_path, closed)
    certify_queue_candidate_closeout(closed, record.operationKey)
    runtime.finish({"state": "closed"}, ok=True)
    fixture.contracts[MASTER_A] = closed
    return closed


def _assert_candidate_absent(test: unittest.TestCase, fixture: QueueFixture) -> None:
    projected = fixture.status()
    for lane in ("ready", "inFlight", "blocked"):
        test.assertFalse(
            any(item["taskDocumentRef"] == LEAF_A.model_dump() for item in projected[lane])
        )
