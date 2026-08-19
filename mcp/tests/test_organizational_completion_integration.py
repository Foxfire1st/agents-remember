from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application import lifecycle_operation_worker
from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    QueueTransaction,
)
from agents_remember.kernel.memory_ledger import ledger_to_text, parse_ledger_text
from agents_remember.memory import carryover as carryover_mod
from agents_remember.models.closeout_queue import CloseoutQueueRequest
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
)
from agents_remember.tasks import read_task_doc, render_markdown, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import integration_quality as quality_mod
from agents_remember.worktrees import organizational_completion as organizational_mod
from agents_remember.worktrees import organizational_completion_integration as completion_mod
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueError,
    QueueActor,
    closeout_queue_tool,
)
from agents_remember.worktrees.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.lifecycle_operations import (
    cancel_operation,
    start_or_observe_operation,
)
from agents_remember.worktrees.modules import clean_quality_executor, code_quality_gate
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules import sync as sync_mod
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.organizational_completion import OrganizationalCompletionError
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    amend_contract,
    load_contract,
    write_contract,
)
from test_closeout_queue import (
    JUDGMENT_HEADING,
    LEAF_A,
    LEAF_B,
    MASTER_A,
    MASTER_B,
    NOW,
    SPRINT,
    QueueFixture,
    _grade,
    _judgment_row,
    _leaf,
)
from test_worktree_support import git

FULL_GATE = {
    "required": True,
    "status": "enforced",
    "passed": True,
    "command": "./scripts/run-quality-gate --full",
    "diffBase": "base",
    "mode": "full",
    "executor": "dagger",
    "memoryPolicy": {
        "mode": "container-host-managed",
        "pytestProcesses": "auto",
        "swap": "container-host-managed",
    },
    "reason": "exact full organizational acceptance passed",
}
LEAF_A2 = LEAF_A.model_copy(update={"path": "master-a/leaf-a2.json"})


def _full_gate(contract):
    return {**FULL_GATE, "diffBase": contract.code_base_commit}


class OrganizationalCompletionIntegrationTests(unittest.TestCase):
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

    def _certified_contract(self, *, final: bool):
        if final:
            self._mark_master_work_complete()
        self.fixture.declare(MASTER_A)
        self.fixture.mutate("select", candidate=LEAF_A)
        contract = self.fixture.contracts[MASTER_A]
        start_or_observe_operation(
            CloseoutOperationInput(
                configPath=self.fixture.config_path.as_posix(),
                contractPath=contract.contract_path.as_posix(),
                codeCommitMessage="close organizational leaf",
                memoryCommitMessage="close organizational memory",
                ledgerCommitMessage="map organizational pair",
                approvalNote="approved",
            ),
            launcher=lambda *_: None,
        )
        closeout_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        closeout_runtime = lifecycle_operation_worker.OperationRuntime(closeout_store)
        closeout = closeout_runtime.start()
        claim_queue_candidate_for_closeout(contract, closeout.operationKey)
        closed = self.fixture.close_contract(MASTER_A)
        certify_queue_candidate_closeout(closed, closeout.operationKey)
        closeout_runtime.finish({"ok": True}, ok=True)
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
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        runtime = lifecycle_operation_worker.OperationRuntime(store)
        return store, runtime, runtime.start()

    def _candidate_projection(self, candidate_ref):
        status = self.fixture.status()
        return next(
            item
            for lane in ("ready", "inFlight", "blocked")
            for item in status[lane]
            if item["taskDocumentRef"] == candidate_ref.model_dump()
        )

    def _add_second_organizational_leaf(self):
        master_path = self.fixture.tasks / "master-a" / "task.json"
        master = read_task_doc(master_path)
        second_row = master.subTasks[0].model_copy(
            update={
                "number": "LEAF-A2",
                "name": "LEAF-A2",
                "file": "leaf-a2.md",
                "status": "inProgress",
            }
        )
        write_task_doc(
            master_path.parent,
            master.model_copy(update={"subTasks": [*master.subTasks, second_row]}),
        )
        sprint_path = self.fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        sections = [
            section.model_copy(
                update={"body": f"{section.body}\n{_judgment_row(LEAF_A2, 'normal')}"}
            )
            if section.heading == JUDGMENT_HEADING
            else section
            for section in sprint.sections
        ]
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"sections": sections}))
        self.fixture.priorities[LEAF_A2] = "normal"
        self.fixture.set_priority(LEAF_A2, "normal")
        code_base = git(self.fixture.code, "rev-parse", "super")
        memory_base = git(self.fixture.memory, "rev-parse", "super")
        second = self.fixture._contract("master-a", "LEAF-A2", code_base, memory_base)
        (second.code_worktree / "feature.txt").rename(second.code_worktree / "feature-a2.txt")
        write_task_doc(second.task_root, _leaf(second, "leaf-a2"))
        return second

    def _declare_second(self, contract):
        request_id = self.fixture.next_request_id("declare-second")
        closeout_queue_tool(
            self.fixture.cfg,
            CloseoutQueueRequest.model_validate(
                {
                    "action": "declare",
                    "sprint_task_document_ref": SPRINT.model_dump(),
                    "request_id": request_id,
                    "expected_revision": self.fixture.request_revision(request_id),
                    "contract_path": contract.contract_path.as_posix(),
                }
            ),
            actor=QueueActor(role="manager", task_document_ref=MASTER_A),
            now=NOW,
        )
        self.fixture.mutate(
            "set-grade",
            candidate=LEAF_A2,
            grade=_grade("normal", LEAF_A2),
            update_priority=False,
        )
        return load_contract(contract.contract_path)

    def _close_and_certify(self, contract):
        start_or_observe_operation(
            CloseoutOperationInput(
                configPath=self.fixture.config_path.as_posix(),
                contractPath=contract.contract_path.as_posix(),
                codeCommitMessage="close organizational leaf",
                memoryCommitMessage="close organizational memory",
                ledgerCommitMessage="map organizational pair",
                approvalNote="approved",
            ),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        runtime = lifecycle_operation_worker.OperationRuntime(store)
        record = runtime.start()
        claim_queue_candidate_for_closeout(contract, record.operationKey)
        self.fixture.contracts[MASTER_A] = contract
        closed = self.fixture.close_contract(MASTER_A)
        certify_queue_candidate_closeout(closed, record.operationKey)
        runtime.finish({"ok": True}, ok=True)
        git(closed.code_repo_path, "checkout", closed.code_source_branch)
        assert closed.memory_repo_path is not None
        git(closed.memory_repo_path, "checkout", closed.memory_source_branch)
        return closed

    def _prepare_two_leaf_final(self):
        second = self._add_second_organizational_leaf()
        self.fixture.declare(MASTER_A)
        first = self.fixture.contracts[MASTER_A]
        second = self._declare_second(second)
        self.fixture.mutate("select", candidate=LEAF_A)
        first = self._close_and_certify(first)
        _first_store, first_runtime, first_record = self._integration_runtime(first)
        first_result = integrate_mod.integrate_result(
            self._args(first, first_runtime, first_record)
        )
        self.assertEqual(first_result.returncode, 0)
        first_gate = first_result.payload["quality_gate"]
        assert isinstance(first_gate, dict)
        self.assertEqual(first_gate["mode"], "targeted")
        first_runtime.finish(first_result.payload, ok=True)
        first = load_contract(first.contract_path)
        blocked = next(
            item
            for item in self.fixture.status()["blocked"]
            if item["taskDocumentRef"] == LEAF_A2.model_dump()
        )
        self.assertIn("source-lineage-stale", blocked["reasons"])
        moved = {item.split(":")[0] for item in blocked["reasons"]}
        self.assertTrue({"code-source-moved", "memory-source-moved"} <= moved)
        self.assertNotIn("ledger-base-mapping-changed", moved)
        self.assertEqual(blocked["candidateState"], "declared")
        code_tip = git(second.code_repo_path, "rev-parse", second.code_source_branch)
        assert second.memory_repo_path is not None
        memory_tip = git(second.memory_repo_path, "rev-parse", second.memory_source_branch)
        contract_bytes = second.contract_path.read_bytes()
        carryover_patcher = mock.patch.object(carryover_mod, "_apply_carryover_for_request")
        carryover = carryover_patcher.start()
        self.addCleanup(carryover_patcher.stop)
        with self.assertRaisesRegex(CloseoutQueueError, "first deterministic") as selection_error:
            self.fixture.mutate("select", candidate=LEAF_A2)
        self.assertEqual(selection_error.exception.status, "closeout-candidate-not-ready")
        with self.assertRaises(CloseoutQueueError) as closeout_error:
            claim_queue_candidate_for_closeout(second, "a" * 64)
        self.assertEqual(closeout_error.exception.status, "closeout-candidate-selection-required")
        tip = git(second.code_repo_path, "rev-parse", second.code_source_branch)
        self.assertEqual(tip, code_tip)
        self.assertEqual(
            git(second.memory_repo_path, "rev-parse", second.memory_source_branch), memory_tip
        )
        self.assertEqual(second.contract_path.read_bytes(), contract_bytes)
        self.fixture.mutate("withdraw", candidate=LEAF_A2)
        second = load_contract(second.contract_path)
        synced = sync_mod.sync_result(WorktreeArgs(contract_path=second.contract_path))
        self.assertEqual(synced.returncode, 0)
        second = load_contract(second.contract_path)
        self.assertEqual(second.code_base_commit, first.integrated_code_commit)
        self.assertEqual(second.memory_base_commit, first.integrated_ledger_commit)
        write_task_doc(second.task_root, _leaf(second, "leaf-a2"))
        self._mark_master_work_complete()
        second = self._declare_second(second)
        self.fixture.mutate("select", candidate=LEAF_A2)
        second = self._close_and_certify(second)
        carryover.assert_not_called()
        return first, second

    @staticmethod
    def _args(contract, runtime, record) -> WorktreeArgs:
        return WorktreeArgs(
            contract_path=contract.contract_path,
            approved=True,
            strategy="ff-only",
            operation_key=record.operationKey,
            recovery_commits=record.recoveryCommits,
            quality_certification=record.qualityCertification,
            operation_progress=runtime.progress,
        )

    def test_nonfinal_leaf_reuses_targeted_closeout_without_full_gate(self) -> None:
        contract = self._certified_contract(final=False)
        _store, runtime, record = self._integration_runtime(contract)
        with mock.patch.object(quality_mod, "run_strict_code_quality_gate") as full_gate:
            result = integrate_mod.integrate_result(self._args(contract, runtime, record))
        self.assertEqual(result.returncode, 0)
        quality_gate = result.payload["quality_gate"]
        assert isinstance(quality_gate, dict)
        self.assertEqual(quality_gate["mode"], "targeted")
        full_gate.assert_not_called()
        master = read_task_doc(self.fixture.tasks / "master-a" / "task.json")
        self.assertEqual(master.status, "inProgress")
        self.assertFalse(_completion_markers(master))
        runtime.finish(result.payload, ok=True)

    def test_parallel_master_sibling_syncs_the_exact_code_memory_pair_and_redeclares(
        self,
    ) -> None:
        sibling = self.fixture.contracts[MASTER_B]
        (sibling.code_worktree / "feature.txt").unlink()
        (sibling.code_worktree / "leaf-b.txt").write_text("LEAF-B\n", encoding="utf-8")
        write_task_doc(sibling.task_root, _leaf(sibling, "leaf-b"))
        self.fixture.declare(MASTER_B)

        first = self._certified_contract(final=False)
        _store, runtime, record = self._integration_runtime(first)
        integrated = integrate_mod.integrate_result(self._args(first, runtime, record))
        self.assertEqual(integrated.returncode, 0)
        runtime.finish(integrated.payload, ok=True)

        self.fixture.mutate("withdraw", candidate=LEAF_B)
        sibling = load_contract(sibling.contract_path)
        synced = sync_mod.sync_result(WorktreeArgs(contract_path=sibling.contract_path))
        self.assertEqual(synced.returncode, 0)
        self.assertEqual(synced.payload["state"], "synced")
        memory_sync = synced.payload["memory"]
        assert isinstance(memory_sync, dict)
        self.assertNotEqual(memory_sync["state"], "skipped-by-choice")

        sibling = load_contract(sibling.contract_path)
        self.assertEqual(
            sibling.code_base_commit,
            git(sibling.code_repo_path, "rev-parse", sibling.code_source_branch),
        )
        assert sibling.memory_repo_path is not None
        self.assertEqual(
            sibling.memory_base_commit,
            git(sibling.memory_repo_path, "rev-parse", sibling.memory_source_branch),
        )
        write_task_doc(sibling.task_root, _leaf(sibling, "leaf-b"))
        self.fixture.contracts[MASTER_B] = sibling
        declared = self.fixture.declare(MASTER_B)
        self.assertEqual(declared["ready"][0]["taskDocumentRef"], LEAF_B.model_dump())

    def test_final_leaf_runs_one_exact_full_gate_then_completes_master_and_pair(self) -> None:
        contract = self._certified_contract(final=True)
        store, runtime, record = self._integration_runtime(contract)

        def exact_gate(target, **_kwargs):
            self.assertEqual(git(target.code_worktree, "rev-parse", "HEAD"), contract.code_commit)
            return _full_gate(contract)

        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            side_effect=exact_gate,
        ) as full_gate:
            result = integrate_mod.integrate_result(self._args(contract, runtime, record))

        self.assertEqual(result.returncode, 0)
        quality_gate = result.payload["quality_gate"]
        assert isinstance(quality_gate, dict)
        self.assertEqual(quality_gate["mode"], "full")
        self.assertTrue(quality_gate["reusedCertification"])
        full_gate.assert_called_once()
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_commit,
        )
        assert contract.memory_repo_path is not None
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            contract.ledger_commit,
        )
        master = read_task_doc(self.fixture.tasks / "master-a" / "task.json")
        self.assertEqual(master.status, "Completed")
        self.assertEqual(len(_completion_markers(master)), 1)
        durable = store.read()
        assert durable is not None
        self.assertIsNotNone(durable.qualityCertification)
        self.assertIsNotNone(durable.queueCompletion)
        assert durable.qualityCertification is not None
        with self.assertRaisesRegex(RuntimeError, "quality certification is immutable"):
            store.update(lambda current: current.model_copy(update={"qualityCertification": None}))
        closeout_store = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        )
        with self.assertRaisesRegex(RuntimeError, "only integration operations"):
            closeout_store.update(
                lambda current: current.model_copy(
                    update={"qualityCertification": durable.qualityCertification}
                )
            )
        with self.assertRaisesRegex(RuntimeError, "queue completion is immutable"):
            store.update(lambda current: current.model_copy(update={"queueCompletion": None}))
        assert durable.queueCompletion is not None
        with self.assertRaisesRegex(RuntimeError, "only integration operations"):
            closeout_store.update(
                lambda current: current.model_copy(
                    update={"queueCompletion": durable.queueCompletion}
                )
            )
        self.assertEqual(self.fixture.status()["inFlight"], [])
        runtime.finish(result.payload, ok=True)

    def test_two_leaf_master_proves_landed_sibling_and_runs_one_exact_full_gate(self) -> None:
        first, final = self._prepare_two_leaf_final()
        _store, runtime, record = self._integration_runtime(final)
        expected_completion = completion_mod.preview_organizational_completion(final)
        assert expected_completion is not None

        def exact_gate(target, **kwargs):
            self.assertEqual(git(target.code_worktree, "rev-parse", "HEAD"), final.code_commit)
            self.assertEqual(kwargs["diff_base"], final.code_base_commit)
            self.assertEqual(kwargs["plan"].mode, "full")
            self.assertEqual(kwargs["plan"].executor, "dagger")
            self.assertEqual(kwargs["invocation"], "master-integration")
            self.assertEqual(
                kwargs["attestation"]["completionFingerprint"],
                expected_completion.fingerprint,
            )
            return _full_gate(final)

        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            side_effect=exact_gate,
        ) as full_gate:
            result = integrate_mod.integrate_result(self._args(final, runtime, record))

        self.assertEqual(result.returncode, 0)
        full_gate.assert_called_once()
        self.assertEqual(
            git(final.code_repo_path, "merge-base", "--is-ancestor", first.code_commit, "super"),
            "",
        )
        assert final.memory_repo_path is not None
        self.assertEqual(
            git(
                final.memory_repo_path,
                "merge-base",
                "--is-ancestor",
                first.ledger_commit,
                "super",
            ),
            "",
        )
        self.assertEqual(
            len(_completion_markers(read_task_doc(self.fixture.tasks / "master-a/task.json"))),
            1,
        )
        runtime.finish(result.payload, ok=True)

    def test_final_leaf_refuses_a_ledger_that_drops_the_landed_sibling_mapping(self) -> None:
        first, final = self._prepare_two_leaf_final()
        assert final.memory_worktree is not None and final.ledger_path is not None
        ledger = parse_ledger_text(final.ledger_path.read_text(encoding="utf-8"))
        self.assertTrue(any(row.code_commit == first.code_commit for row in ledger.rows))
        final.ledger_path.write_text(
            ledger_to_text(
                replace(
                    ledger,
                    rows=[row for row in ledger.rows if row.code_commit != first.code_commit],
                )
            ),
            encoding="utf-8",
        )
        git(final.memory_worktree, "add", "memory.md")
        git(final.memory_worktree, "commit", "-m", "drop landed sibling mapping")
        forged = replace(final, ledger_commit=git(final.memory_worktree, "rev-parse", "HEAD"))

        topology = TaskDocumentTopology(self.fixture.coord)
        graph = completion_mod._graph_context(topology, SPRINT)
        initial = completion_mod._initial_state(SPRINT, graph.revision, NOW)
        state = CloseoutQueueStore(self.fixture.coord, SPRINT).read(initial)
        candidate = state.candidates[LEAF_A2.key]
        master = graph.masters[candidate.owningMaster]
        with self.assertRaisesRegex(
            OrganizationalCompletionError,
            "mapping is not preserved in the proposed final ledger",
        ):
            organizational_mod.organizational_completion_plan(
                organizational_mod.OrganizationalCompletionContext(
                    topology=topology,
                    sprint=graph.sprint,
                    master=master,
                    candidate=candidate,
                    candidates=state.candidates,
                ),
                contract=forged,
            )

    def test_final_leaf_refuses_a_copied_foreign_sibling_contract(self) -> None:
        first, final = self._prepare_two_leaf_final()
        foreign_code = self.fixture.root / "foreign-code"
        foreign_memory = self.fixture.root / "foreign-memory"
        subprocess.run(
            ["git", "clone", self.fixture.code.as_posix(), foreign_code.as_posix()],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "clone", self.fixture.memory.as_posix(), foreign_memory.as_posix()],
            check=True,
            capture_output=True,
            text=True,
        )
        write_contract(
            first.contract_path,
            replace(
                first,
                code_repo_path=foreign_code,
                memory_repo_path=foreign_memory,
            ),
        )
        code_before = git(final.code_repo_path, "rev-parse", final.code_source_branch)
        assert final.memory_repo_path is not None
        memory_before = git(final.memory_repo_path, "rev-parse", final.memory_source_branch)
        _store, runtime, record = self._integration_runtime(final)
        with (
            mock.patch.object(quality_mod, "run_strict_code_quality_gate") as full_gate,
            self.assertRaisesRegex(OrganizationalCompletionError, "another code repository"),
        ):
            integrate_mod.integrate_result(self._args(final, runtime, record))
        full_gate.assert_not_called()
        self.assertEqual(
            git(final.code_repo_path, "rev-parse", final.code_source_branch), code_before
        )
        self.assertEqual(
            git(final.memory_repo_path, "rev-parse", final.memory_source_branch), memory_before
        )

    def test_final_leaf_refuses_a_missing_landed_sibling_contract(self) -> None:
        first, final = self._prepare_two_leaf_final()
        first.contract_path.unlink()
        code_before = git(final.code_repo_path, "rev-parse", final.code_source_branch)
        assert final.memory_repo_path is not None
        memory_before = git(final.memory_repo_path, "rev-parse", final.memory_source_branch)
        _store, runtime, record = self._integration_runtime(final)
        with self.assertRaisesRegex(OrganizationalCompletionError, "no readable landing contract"):
            integrate_mod.integrate_result(self._args(final, runtime, record))
        self.assertEqual(
            git(final.code_repo_path, "rev-parse", final.code_source_branch), code_before
        )
        self.assertEqual(
            git(final.memory_repo_path, "rev-parse", final.memory_source_branch), memory_before
        )

    def test_final_leaf_refuses_a_symlinked_sibling_contract_escape(self) -> None:
        first, final = self._prepare_two_leaf_final()
        escaped = self.fixture.root / "escaped-sibling-contract.md"
        escaped.write_bytes(first.contract_path.read_bytes())
        first.contract_path.unlink()
        first.contract_path.symlink_to(escaped)
        code_before = git(final.code_repo_path, "rev-parse", final.code_source_branch)
        assert final.memory_repo_path is not None
        memory_before = git(final.memory_repo_path, "rev-parse", final.memory_source_branch)
        _store, runtime, record = self._integration_runtime(final)
        with (
            mock.patch.object(quality_mod, "run_strict_code_quality_gate") as full_gate,
            self.assertRaisesRegex(OrganizationalCompletionError, "escapes through a symlink"),
        ):
            integrate_mod.integrate_result(self._args(final, runtime, record))
        full_gate.assert_not_called()
        self.assertEqual(
            git(final.code_repo_path, "rev-parse", final.code_source_branch), code_before
        )
        self.assertEqual(
            git(final.memory_repo_path, "rev-parse", final.memory_source_branch), memory_before
        )

    def test_final_leaf_refuses_duplicate_landed_sibling_code_mappings(self) -> None:
        first, final = self._prepare_two_leaf_final()
        assert first.memory_repo_path is not None
        source_branch = git(first.memory_repo_path, "symbolic-ref", "--short", "HEAD")
        git(first.memory_repo_path, "checkout", "--detach", first.integrated_ledger_commit)
        ledger_path = first.memory_repo_path / "memory.md"
        ledger = parse_ledger_text(ledger_path.read_text(encoding="utf-8"))
        ledger_path.write_text(
            ledger_to_text(replace(ledger, rows=[ledger.rows[0], *ledger.rows])),
            encoding="utf-8",
        )
        git(first.memory_repo_path, "add", "memory.md")
        git(first.memory_repo_path, "commit", "-m", "forge duplicate sibling mapping")
        duplicate_commit = git(first.memory_repo_path, "rev-parse", "HEAD")
        git(first.memory_repo_path, "checkout", source_branch)
        write_contract(
            first.contract_path,
            replace(
                first,
                ledger_commit=duplicate_commit,
                integrated_ledger_commit=duplicate_commit,
            ),
        )

        code_before = git(final.code_repo_path, "rev-parse", final.code_source_branch)
        assert final.memory_repo_path is not None
        memory_before = git(final.memory_repo_path, "rev-parse", final.memory_source_branch)
        _store, runtime, record = self._integration_runtime(final)
        with (
            mock.patch.object(quality_mod, "run_strict_code_quality_gate") as full_gate,
            self.assertRaisesRegex(OrganizationalCompletionError, "duplicate code mappings"),
        ):
            integrate_mod.integrate_result(self._args(final, runtime, record))
        full_gate.assert_not_called()
        self.assertEqual(
            git(final.code_repo_path, "rev-parse", final.code_source_branch), code_before
        )
        self.assertEqual(
            git(final.memory_repo_path, "rev-parse", final.memory_source_branch), memory_before
        )

    def test_failed_final_gate_moves_no_ref_and_cancel_reopens_same_leaf(self) -> None:
        contract = self._certified_contract(final=True)
        store, runtime, record = self._integration_runtime(contract)
        code_before = git(contract.code_repo_path, "rev-parse", contract.code_source_branch)
        assert contract.memory_repo_path is not None
        memory_before = git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch)
        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            side_effect=RuntimeError("full Dagger failure"),
        ):
            lifecycle_operation_worker.execute_operation(record, runtime)

        failed = store.read()
        assert failed is not None
        self.assertEqual(failed.status, "input-required")
        assert failed.result is not None and failed.organizationalRepair is not None
        self.assertEqual(failed.result["state"], "organizational-completion-gate-failed")
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch), code_before
        )
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            memory_before,
        )
        self.assertEqual(
            self._candidate_projection(LEAF_A)["candidateState"],
            "integration-in-flight",
        )
        cancelled = cancel_operation(contract.contract_path, "integrate")
        self.assertEqual(cancelled.status, "cancelled")
        reset = load_contract(contract.contract_path)
        self.assertEqual((reset.closeout_status, reset.code_commit), ("not-started", ""))
        self.assertEqual(self.fixture.status()["inFlight"], [])
        (reset.code_worktree / "repair.txt").write_text("repair\n", encoding="utf-8")
        write_task_doc(reset.task_root, _leaf(reset, "leaf-a"))
        self.fixture.contracts[MASTER_A] = reset
        declared = self.fixture.declare(MASTER_A)
        self.assertEqual(declared["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())

    def test_quality_repair_retries_after_contract_reset_before_queue_commit(self) -> None:
        contract = self._certified_contract(final=True)
        _store, runtime, record = self._integration_runtime(contract)
        code_before = git(contract.code_repo_path, "rev-parse", contract.code_source_branch)
        assert contract.memory_repo_path is not None
        memory_before = git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch)
        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            side_effect=RuntimeError("full Dagger failure"),
        ):
            lifecycle_operation_worker.execute_operation(record, runtime)
        with (
            mock.patch.object(
                CloseoutQueueStore,
                "_commit_transaction",
                side_effect=RuntimeError("crash after repair contract publication"),
            ),
            self.assertRaisesRegex(RuntimeError, "crash after repair contract publication"),
        ):
            cancel_operation(contract.contract_path, "integrate")
        reset = load_contract(contract.contract_path)
        self.assertEqual((reset.closeout_status, reset.code_commit), ("not-started", ""))
        self.assertEqual(
            self._candidate_projection(LEAF_A)["candidateState"],
            "integration-in-flight",
        )
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch), code_before
        )
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            memory_before,
        )

        cancelled = cancel_operation(contract.contract_path, "integrate")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.fixture.status()["inFlight"], [])
        self.assertEqual(
            cancel_operation(contract.contract_path, "integrate").status,
            "cancelled",
        )

    def test_quality_repair_refuses_a_partial_reset_generation(self) -> None:
        contract = self._certified_contract(final=True)
        _store, runtime, record = self._integration_runtime(contract)
        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            side_effect=RuntimeError("full Dagger failure"),
        ):
            lifecycle_operation_worker.execute_operation(record, runtime)

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
        code_tip = git(contract.code_repo_path, "rev-parse", contract.code_source_branch)
        assert contract.memory_repo_path is not None
        memory_tip = git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch)
        with self.assertRaisesRegex(CloseoutQueueError, "operation-state-mismatch"):
            cancel_operation(contract.contract_path, "integrate")
        self.assertEqual(
            self._candidate_projection(LEAF_A)["candidateState"],
            "integration-in-flight",
        )
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch), code_tip
        )
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            memory_tip,
        )

    def test_pre_cas_retry_reuses_durable_full_gate_certification(self) -> None:
        contract = self._certified_contract(final=True)
        store, runtime, record = self._integration_runtime(contract)
        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                return_value=_full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                integrate_mod,
                "publish_queue_candidate_integration_result_under_authority",
                side_effect=RuntimeError("crash after quality certification"),
            ),
            self.assertRaisesRegex(RuntimeError, "crash after quality certification"),
        ):
            integrate_mod.integrate_result(self._args(contract, runtime, record))
        after_crash = store.read()
        assert after_crash is not None and after_crash.qualityCertification is not None
        self.assertIsNone(after_crash.recoveryCommits)
        completion = completion_mod.preview_organizational_completion(contract)
        assert completion is not None
        for changes in (
            {"completionFingerprint": "0" * 64},
            {"codeCommit": "0" * 40},
            {"candidateTree": "0" * 40},
        ):
            attestation = dict(after_crash.qualityCertification.attestation)
            attestation.update(changes)
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(RuntimeError, "targets another candidate"),
            ):
                quality_mod.run_integration_quality_gate(
                    contract,
                    completion=completion,
                    certification=after_crash.qualityCertification.model_copy(
                        update={**changes, "attestation": attestation}
                    ),
                )
        invalid_result = after_crash.qualityCertification.model_dump(mode="json")
        invalid_result["result"] = {"passed": False, "mode": "full"}
        with self.assertRaises(ValueError):
            type(after_crash.qualityCertification).model_validate(invalid_result)

        for field, value in (
            ("required", False),
            ("status", "skipped"),
            ("executor", "host"),
            ("diffBase", "0" * 40),
        ):
            with self.subTest(result_field=field):
                result_payload = dict(after_crash.qualityCertification.result)
                result_payload[field] = value
                result_sha = hashlib.sha256(
                    json.dumps(
                        result_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                forged = after_crash.qualityCertification.model_copy(
                    update={"result": result_payload, "resultSha256": result_sha}
                )
                with self.assertRaisesRegex(RuntimeError, "not an exact Dagger result"):
                    quality_mod.run_integration_quality_gate(
                        contract,
                        completion=completion,
                        certification=forged,
                    )
        with self.assertRaisesRegex(RuntimeError, "current Dagger quality plan"):
            quality_mod.run_integration_quality_gate(
                contract,
                completion=completion,
                certification=after_crash.qualityCertification,
                memory_cap_bytes=4096,
            )
        with (
            mock.patch.object(
                quality_mod,
                "quality_gate_settings",
                return_value=mock.Mock(executor="host", memory_cap_bytes=None),
            ),
            self.assertRaisesRegex(RuntimeError, "current Dagger quality plan"),
        ):
            quality_mod.run_integration_quality_gate(
                contract,
                completion=completion,
                certification=after_crash.qualityCertification,
            )

        result = integrate_mod.integrate_result(self._args(contract, runtime, after_crash))
        self.assertEqual(result.returncode, 0)
        full_gate.assert_called_once()
        self.assertEqual(
            len(_completion_markers(read_task_doc(self.fixture.tasks / "master-a" / "task.json"))),
            1,
        )
        runtime.finish(result.payload, ok=True)

    def test_post_certification_master_decision_drift_requires_a_fresh_gate(self) -> None:
        contract = self._certified_contract(final=True)
        store, runtime, record = self._integration_runtime(contract)
        code_before = git(contract.code_repo_path, "rev-parse", contract.code_source_branch)
        assert contract.memory_repo_path is not None
        memory_before = git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch)
        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                return_value=_full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                integrate_mod,
                "publish_queue_candidate_integration_result_under_authority",
                side_effect=RuntimeError("crash after quality certification"),
            ),
            self.assertRaisesRegex(RuntimeError, "crash after quality certification"),
        ):
            integrate_mod.integrate_result(self._args(contract, runtime, record))
        after_crash = store.read()
        assert after_crash is not None and after_crash.qualityCertification is not None
        master_path = self.fixture.tasks / "master-a" / "task.json"
        master = read_task_doc(master_path)
        payload = master.model_dump(mode="json", by_alias=True)
        payload["decisions"] = [
            {
                "at": NOW,
                "decision": "Retain a new master-level ruling before landing.",
                "rationale": "This semantic change was not part of the certified generation.",
            },
            *payload["decisions"],
        ]
        write_task_doc(master_path.parent, type(master).model_validate(payload))
        refused = integrate_mod.integrate_result(self._args(contract, runtime, after_crash))
        assert refused.returncode == 2
        assert refused.payload["state"] == "organizational-completion-gate-failed"
        reason = refused.payload["reason"]
        assert isinstance(reason, str)
        self.assertIn("targets another candidate", reason)
        full_gate.assert_called_once()
        assert self._candidate_projection(LEAF_A)["candidateState"] == "integration-in-flight"
        code_after = git(contract.code_repo_path, "rev-parse", contract.code_source_branch)
        memory_after = git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch)
        self.assertEqual((code_after, memory_after), (code_before, memory_before))

    def test_post_dagger_report_crash_recovers_without_a_second_full_gate(self) -> None:
        contract = self._certified_contract(final=True)
        completion = completion_mod.preview_organizational_completion(contract)
        assert completion is not None
        reports = contract.worktree_group / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stale_markdown = reports / "test-results.md"
        stale_markdown.write_text("# failed prior run\n", encoding="utf-8")

        def published_gate(target, **kwargs):
            export = self.fixture.root / "exact-full-gate-export"
            export.mkdir()
            (export / "clean-quality-results.json").write_text(
                json.dumps({"status": "passed", "exitCode": 0}) + "\n",
                encoding="utf-8",
            )
            clean_quality_executor._publish_reports(
                export,
                target.worktree_group / "reports",
                attestation=kwargs["attestation"],
            )
            return _full_gate(contract)

        captured = []
        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                side_effect=published_gate,
            ) as full_gate,
            self.assertRaisesRegex(RuntimeError, "crash before operation progress"),
        ):
            quality_mod.run_integration_quality_gate(
                contract,
                completion=completion,
                certification_sink=lambda _certificate: (_ for _ in ()).throw(
                    RuntimeError("crash before operation progress")
                ),
            )

        recovered = quality_mod.run_integration_quality_gate(
            contract,
            completion=completion,
            certification_sink=captured.append,
        )
        full_gate.assert_called_once()
        self.assertTrue(recovered.result["recoveredPublishedReport"])
        recovered_report = recovered.result["reportPath"]
        assert isinstance(recovered_report, str)
        self.assertIn(".quality-report-generations", recovered_report)
        self.assertEqual(stale_markdown.read_text(encoding="utf-8"), "# failed prior run\n")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], recovered.certification)

        manifest_path = reports / clean_quality_executor.REPORT_SET_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_attestation = dict(manifest["attestation"])
        manifest["attestation"]["codeCommit"] = "0" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        plan = code_quality_gate.QualityGatePlan(mode="full", executor="dagger")
        target = code_quality_gate.QualityGateTarget(
            code_worktree=contract.code_worktree,
            worktree_group=contract.worktree_group,
        )
        self.assertIsNone(
            code_quality_gate.recover_strict_code_quality_gate(
                target,
                diff_base=contract.code_base_commit,
                plan=plan,
                attestation=expected_attestation,
            )
        )
        manifest["attestation"] = expected_attestation
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        selected = clean_quality_executor.published_report_path(
            reports,
            "clean-quality-results.json",
        )
        selected.unlink()
        with self.assertRaisesRegex(RuntimeError, "published Dagger report is incomplete"):
            code_quality_gate.recover_strict_code_quality_gate(
                target,
                diff_base=contract.code_base_commit,
                plan=plan,
                attestation=expected_attestation,
            )

    def test_post_contract_crash_recovers_master_without_rerunning_gate(self) -> None:
        _first, contract = self._prepare_two_leaf_final()
        store, runtime, record = self._integration_runtime(contract)
        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                return_value=_full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                completion_mod,
                "publish_organizational_master_completion",
                side_effect=RuntimeError("crash before logical completion"),
            ),
            self.assertRaisesRegex(RuntimeError, "crash before logical completion"),
        ):
            integrate_mod.integrate_result(self._args(contract, runtime, record))

        landed = load_contract(contract.contract_path)
        self.assertEqual(landed.integration_status, "completed")
        self.assertFalse(
            _completion_markers(read_task_doc(self.fixture.tasks / "master-a" / "task.json"))
        )
        after_crash = store.read()
        assert after_crash is not None
        self.assertIsNotNone(after_crash.recoveryCommits)
        self.assertIsNotNone(after_crash.qualityCertification)

        result = integrate_mod.integrate_result(self._args(landed, runtime, after_crash))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["state"], "already-integrated")
        full_gate.assert_called_once()
        self.assertEqual(
            len(_completion_markers(read_task_doc(self.fixture.tasks / "master-a" / "task.json"))),
            1,
        )
        self.assertEqual(self.fixture.status()["inFlight"], [])
        runtime.finish(result.payload, ok=True)

    def test_post_master_json_crash_republishes_markdown_without_rerunning_gate(self) -> None:
        contract = self._certified_contract(final=True)
        store, runtime, record = self._integration_runtime(contract)
        master_json = self.fixture.tasks / "master-a" / "task.json"
        master_markdown = self.fixture.tasks / "master-a" / "task.md"
        markdown_before = master_markdown.read_bytes()

        def publish_json_then_crash(task_root, document):
            master_json.write_text(
                document.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n",
                encoding="utf-8",
            )
            raise RuntimeError("crash after master JSON publication")

        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                return_value=_full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                organizational_mod,
                "write_task_doc",
                side_effect=publish_json_then_crash,
            ),
            self.assertRaisesRegex(RuntimeError, "crash after master JSON publication"),
        ):
            integrate_mod.integrate_result(self._args(contract, runtime, record))

        completed_json = read_task_doc(master_json)
        self.assertEqual(completed_json.status, "Completed")
        self.assertEqual(master_markdown.read_bytes(), markdown_before)
        after_crash = store.read()
        assert after_crash is not None
        result = integrate_mod.integrate_result(
            self._args(load_contract(contract.contract_path), runtime, after_crash)
        )
        self.assertEqual(result.returncode, 0)
        full_gate.assert_called_once()
        completed = read_task_doc(master_json)
        self.assertEqual(master_markdown.read_text(encoding="utf-8"), render_markdown(completed))
        runtime.finish(result.payload, ok=True)

    def test_post_queue_completion_crash_retries_without_redeclaring_candidate(self) -> None:
        contract = self._certified_contract(final=True)
        store, runtime, record = self._integration_runtime(contract)
        with mock.patch.object(
            quality_mod,
            "run_strict_code_quality_gate",
            return_value=_full_gate(contract),
        ) as full_gate:
            first = integrate_mod.integrate_result(self._args(contract, runtime, record))
            self.assertEqual(first.returncode, 0)
            self.assertEqual(self.fixture.status()["inFlight"], [])
            after_crash = store.read()
            assert after_crash is not None
            self.assertEqual(after_crash.status, "running")
            self.assertIsNotNone(after_crash.queueCompletion)
            original_inspect = CloseoutQueueStore.inspect
            real_record = store.read()
            self.assertEqual(real_record, after_crash)
            assert after_crash.queueCompletion is not None
            reloaded = load_contract(contract.contract_path)
            frozen_args = replace(
                self._args(reloaded, runtime, after_crash), operation_progress=None
            )
            invalid_wal_records = {
                "missing": after_crash.model_copy(update={"queueCompletion": None}),
                "mismatched": after_crash.model_copy(
                    update={
                        "queueCompletion": after_crash.queueCompletion.model_copy(
                            update={"fingerprint": "0" * 64}
                        )
                    }
                ),
            }
            for case, invalid_record in invalid_wal_records.items():
                completion_store = mock.Mock()
                completion_store.read.return_value = invalid_record
                with (
                    self.subTest(case=case),
                    mock.patch.object(
                        completion_mod,
                        "LifecycleOperationStore",
                        return_value=completion_store,
                    ),
                    self.assertRaisesRegex(CloseoutQueueError, "durable removal intent"),
                ):
                    integrate_mod.integrate_result(frozen_args)
                completion_store.read.assert_called()
                self.assertEqual(store.read(), real_record)

            def inspect_with_mismatched_completion_receipt(queue_store, initial, reader):
                def mismatched(state):
                    receipts = [
                        receipt.model_copy(update={"fingerprint": "0" * 64})
                        if receipt.requestId.startswith("integration-complete:")
                        else receipt
                        for receipt in state.appliedRequests
                    ]
                    return reader(state.model_copy(update={"appliedRequests": receipts}))

                return original_inspect(queue_store, initial, mismatched)

            with (
                mock.patch.object(
                    CloseoutQueueStore,
                    "inspect",
                    new=inspect_with_mismatched_completion_receipt,
                ),
                self.assertRaisesRegex(CloseoutQueueError, "receipt does not match"),
            ):
                integrate_mod.integrate_result(frozen_args)
            self.assertEqual(store.read(), real_record)

            topology = TaskDocumentTopology(self.fixture.coord)
            graph = completion_mod._graph_context(topology, SPRINT)
            initial = completion_mod._initial_state(SPRINT, graph.revision, NOW)
            queue_store = CloseoutQueueStore(self.fixture.coord, SPRINT)
            for index in range(129):
                fingerprint = hashlib.sha256(f"later-queue-event:{index}".encode()).hexdigest()
                queue_store.transact(
                    initial=initial,
                    event=QueueTransaction(
                        action="reclaim-sprint",
                        request_id=f"later-queue-event:{index}",
                        fingerprint=fingerprint,
                        recorded_at=NOW,
                        actor="test-queue-owner",
                    ),
                    transform=lambda state: state,
                )
            state = queue_store.read(initial)
            assert after_crash.queueCompletion is not None
            self.assertNotIn(
                after_crash.queueCompletion.requestId,
                {receipt.requestId for receipt in state.appliedRequests},
            )
            retry = integrate_mod.integrate_result(
                self._args(load_contract(contract.contract_path), runtime, after_crash)
            )
        self.assertEqual(retry.returncode, 0)
        self.assertEqual(retry.payload["state"], "already-integrated")
        self.assertEqual(self.fixture.status()["inFlight"], [])
        full_gate.assert_called_once()
        runtime.finish(retry.payload, ok=True)


def _completion_markers(document) -> list[object]:
    return [
        decision
        for decision in document.decisions
        if decision.decision
        == "Complete organizational master at its certified final-leaf landing."
    ]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
