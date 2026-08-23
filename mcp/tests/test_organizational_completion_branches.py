from __future__ import annotations

import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.lifecycles.operation import IntegrationQualityCertification
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration import integration_claim_transfer as claim_transfer_mod
from agents_remember.worktrees.integration import organizational_completion as completion
from agents_remember.worktrees.integration import (
    organizational_completion_integration as integration,
)
from agents_remember.worktrees.queue.closeout_queue_lifecycle import contract_queue_binding
from agents_remember.worktrees.queue.closeout_queue_state import initial_queue_state
from agents_remember.worktrees.worktree_contract import write_contract
from test_closeout_queue import LEAF_A, NOW, SPRINT
from test_worktree_support import git


class OrganizationalCompletionBranchTests(unittest.TestCase):
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

    def _context(self, contract):
        binding = contract_queue_binding(contract)
        assert binding is not None
        topology = TaskDocumentTopology(contract.coordination_root)
        graph = integration._graph_context(topology, binding.sprint_ref)
        initial = initial_queue_state(binding.sprint_ref, graph.revision, NOW)
        state = CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).read(initial)
        candidate = state.candidates[binding.candidate_ref.key]
        master = graph.masters[candidate.owningMaster]
        return (
            completion.OrganizationalCompletionContext(
                topology=topology,
                sprint=graph.sprint,
                master=master,
                candidate=candidate,
                candidates=state.candidates,
            ),
            state,
            graph,
            binding,
        )

    @staticmethod
    def _expectation(first, final):
        return completion._SiblingExpectation(
            completing_contract=final,
            contract_path=first.contract_path,
            sprint_ref=SPRINT,
            child_ref=LEAF_A,
            child_id=first.leaf_id,
            source_branch=final.code_source_branch,
        )

    def test_completion_scope_and_completed_marker_guard_matrix(self) -> None:
        contract = self.owner._certified_contract(final=True)
        context, _state, _graph, _binding = self._context(contract)
        plan = completion.organizational_completion_plan(context, contract=contract)
        assert plan is not None

        completed_master = replace(
            context.master,
            document=context.master.document.model_copy(update={"status": "Completed"}),
        )
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "exact certified"):
            completion.organizational_completion_plan(
                replace(context, master=completed_master),
                contract=contract,
            )

        atomic_master = replace(
            context.master,
            document=context.master.document.model_copy(update={"executionNature": "atomic"}),
        )
        self.assertIsNone(completion._completion_scope(replace(context, master=atomic_master)))

        unsupported_master = replace(
            context.master,
            document=context.master.document.model_copy(update={"executionNature": None}),
        )
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "executionNature"):
            completion._completion_scope(replace(context, master=unsupported_master))

        with (
            mock.patch.object(context.topology, "parent", return_value=None),
            self.assertRaisesRegex(completion.OrganizationalCompletionError, "not owned"),
        ):
            completion._completion_scope(context)

        other_master = TaskDocumentRef(repository="repo-a", path="master-b/task.json")
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "different owning"):
            completion._completion_scope(
                replace(
                    context,
                    candidate=context.candidate.model_copy(update={"owningMaster": other_master}),
                )
            )

        with (
            mock.patch.object(context.topology, "children", return_value=()),
            self.assertRaisesRegex(completion.OrganizationalCompletionError, "canonical child"),
        ):
            completion._completion_scope(context)

        blocked_rows = [
            row.model_copy(update={"status": "inProgress"})
            for row in context.master.document.subTasks
        ]
        blocked_master = replace(
            context.master,
            document=context.master.document.model_copy(update={"subTasks": blocked_rows}),
        )
        self.assertIsNone(completion._completion_scope(replace(context, master=blocked_master)))

        other_ref = TaskDocumentRef(repository="repo-a", path="master-a/other.json")
        other_candidate = context.candidate.model_copy(update={"taskDocumentRef": other_ref})
        self.assertIsNone(
            completion._completion_scope(
                replace(
                    context,
                    candidates={
                        context.candidate.taskDocumentRef.key: context.candidate,
                        other_ref.key: other_candidate,
                    },
                )
            )
        )

        no_super = replace(
            context.sprint,
            document=context.sprint.document.model_copy(update={"integrationBranch": ""}),
        )
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "integrationBranch"):
            completion._completion_scope(replace(context, sprint=no_super))

    def test_sibling_loading_and_master_publication_guards(self) -> None:
        first, final = self.owner._prepare_two_leaf_final()
        context, _state, _graph, _binding = self._context(final)
        plan = completion.organizational_completion_plan(context, contract=final)
        assert plan is not None

        original_contract = first.contract_path.read_bytes()
        first.contract_path.write_text("not a contract\n", encoding="utf-8")
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "no readable"):
            completion.organizational_completion_plan(context, contract=final)
        first.contract_path.write_bytes(original_contract)

        write_contract(first.contract_path, replace(first, integration_status="not-started"))
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "not integrated"):
            completion.organizational_completion_plan(context, contract=final)
        first.contract_path.write_bytes(original_contract)

        certification = cast(
            IntegrationQualityCertification,
            SimpleNamespace(
                completionFingerprint=plan.fingerprint,
                resultSha256="1" * 64,
            ),
        )
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "certificate"):
            completion.prepare_organizational_master_completion(
                plan,
                certification=cast(
                    IntegrationQualityCertification,
                    SimpleNamespace(
                        completionFingerprint="0" * 64,
                        resultSha256="1" * 64,
                    ),
                ),
                completed_at=NOW,
            )

        changed = plan.master_document.model_copy(update={"objective": "raced edit"})
        write_task_doc(plan.master_path.parent, changed)
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "changed before"):
            completion.prepare_organizational_master_completion(
                plan,
                certification=certification,
                completed_at=NOW,
            )

        blocked_rows = [
            row.model_copy(update={"status": "inProgress"}) for row in plan.master_document.subTasks
        ]
        blocked = plan.master_document.model_copy(update={"subTasks": blocked_rows})
        blocked_plan = replace(plan, master_document=blocked)
        write_task_doc(plan.master_path.parent, blocked)
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "regained"):
            completion.prepare_organizational_master_completion(
                blocked_plan,
                certification=certification,
                completed_at=NOW,
            )

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "not durably"):
            completion.require_published_organizational_master_completion(
                plan.master_document,
                fingerprint=plan.fingerprint,
            )

    def test_candidate_identity_guard_matrix(self) -> None:
        contract = self.owner._certified_contract(final=True)
        context, _state, _graph, _binding = self._context(contract)
        candidate = context.candidate

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "certified leaf"):
            completion._require_candidate_identity(
                replace(contract, kind="series"),
                candidate,
                SPRINT,
            )

        sprint_path = self.fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(
            sprint_path.parent,
            sprint.model_copy(update={"integrationBranch": "moved-super"}),
        )
        with self.assertRaisesRegex(RuntimeError, "does not match task-derived target"):
            completion._require_candidate_identity(
                contract,
                candidate,
                SPRINT,
            )
        write_task_doc(sprint_path.parent, sprint)

        completion._require_candidate_identity(
            replace(contract, memory_mode="disabled"),
            candidate,
            SPRINT,
        )

    def test_landed_sibling_identity_target_ancestry_and_memory_guards(self) -> None:
        first, final = self.owner._prepare_two_leaf_final()
        expected = self._expectation(first, final)

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "exact landed code"):
            completion._require_sibling_contract_identity(
                replace(first, queue_sprint_task_document=""),
                expected,
            )

        sprint_path = self.fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(
            sprint_path.parent,
            sprint.model_copy(update={"integrationBranch": "moved-super"}),
        )
        with self.assertRaisesRegex(RuntimeError, "does not match task-derived target"):
            completion._require_landed_sibling(first, expected)
        write_task_doc(sprint_path.parent, sprint)

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "code commit"):
            completion._require_landed_sibling(
                replace(first, code_base_commit="0" * 40),
                expected,
            )

        disabled_first = replace(first, memory_mode="disabled")
        disabled_final = replace(final, memory_mode="disabled")
        disabled_expected = replace(expected, completing_contract=disabled_final)
        projected = completion._require_landed_sibling(disabled_first, disabled_expected)
        self.assertEqual(projected["code"], first.integrated_code_commit)

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "landed memory"):
            completion._require_sibling_memory_identity(
                replace(first, integrated_memory_content_commit=""),
                final,
                LEAF_A,
            )

        foreign = self.fixture.root / "foreign-memory-identity"
        subprocess.run(
            ["git", "init", "-b", "main", foreign.as_posix()],
            check=True,
            capture_output=True,
            text=True,
        )
        git(foreign, "config", "user.name", "Tests")
        git(foreign, "config", "user.email", "tests@example.com")
        (foreign / "memory.md").write_text("# foreign\n", encoding="utf-8")
        git(foreign, "add", "memory.md")
        git(foreign, "commit", "-m", "foreign")
        git(foreign, "remote", "add", "origin", "https://example.invalid/foreign.git")
        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "another memory"):
            completion._require_sibling_memory_identity(
                replace(first, memory_repo_path=foreign),
                final,
                LEAF_A,
            )

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "memory mapping"):
            completion._require_sibling_memory_ancestry(
                replace(first, memory_base_commit="0" * 40),
                final,
                LEAF_A,
            )

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "memory mapping"):
            completion._require_sibling_memory_mapping(first, LEAF_A, None, None)

        completion._require_landed_sibling(first, expected)

    def test_path_commit_and_marker_guard_matrix(self) -> None:
        master_root = self.fixture.tasks / "master-a"
        contract_path = master_root / "enclosures" / "leaf-a" / "series-contract.md"
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text("contract\n", encoding="utf-8")
        escaped = self.fixture.root / "resolved-escape.md"
        escaped.write_text("escaped\n", encoding="utf-8")
        real_is_symlink = Path.is_symlink

        def swap_after_symlink_check(path: Path) -> bool:
            if path == contract_path:
                observed = real_is_symlink(path)
                path.unlink()
                path.symlink_to(escaped)
                return observed
            return real_is_symlink(path)

        with (
            mock.patch.object(Path, "is_symlink", new=swap_after_symlink_check),
            self.assertRaisesRegex(completion.OrganizationalCompletionError, "escapes"),
        ):
            completion._require_confined_sibling_contract_path(
                master_root,
                contract_path,
                LEAF_A,
            )

        with self.assertRaisesRegex(completion.OrganizationalCompletionError, "cannot resolve"):
            completion._commit_tree(self.fixture.code, "0" * 40)

        wrong_type = SimpleNamespace(
            decision=completion._COMPLETION_DECISION,
            rationale=42,
        )
        self.assertIsNone(completion._completion_marker_fingerprint(wrong_type))
        wrong_digest = SimpleNamespace(
            decision=completion._COMPLETION_DECISION,
            rationale=f"{completion._COMPLETION_RATIONALE_PREFIX}{'z' * 64}",
        )
        self.assertIsNone(completion._completion_marker_fingerprint(wrong_digest))

    def test_under_authority_master_drift_returns_structured_no_ref_failure(self) -> None:
        contract = self.owner._certified_contract(final=True)
        _store, runtime, record = self.owner._integration_runtime(contract)
        assert contract.memory_repo_path is not None
        contract_before = contract.contract_path.read_bytes()
        real_transfer = claim_transfer_mod.transfer_integration_claim

        def race_master_after_journal_intent(*args, **kwargs):
            master_path = self.fixture.tasks / "master-a" / "task.json"
            master = read_task_doc(master_path)
            payload = master.model_dump(mode="json", by_alias=True)
            payload["decisions"] = [
                {
                    "at": NOW,
                    "decision": "Record a concurrent master ruling.",
                    "rationale": "This fact changes the exact completion generation.",
                },
                *payload["decisions"],
            ]
            write_task_doc(
                master_path.parent,
                type(master).model_validate(payload),
            )
            return real_transfer(*args, **kwargs)

        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                claim_transfer_mod,
                "transfer_integration_claim",
                side_effect=race_master_after_journal_intent,
            ),
        ):
            refused = fixture_mod.integrate_mod.integrate_result(
                self.owner._args(contract, runtime, record),
                contract,
            )

        assert refused.returncode == 2
        assert refused.payload["state"] == "organizational-completion-publication-conflict"
        assert refused.payload["developerDecisionRequired"] is True
        assert refused.payload["nextAction"] == "developer-decision"
        full_gate.assert_called_once()
        with self.assertRaises(StopIteration):
            self.owner._candidate_projection(LEAF_A)
        self.assertEqual(contract.contract_path.read_bytes(), contract_before)
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_base_commit,
        )
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            contract.memory_base_commit,
        )

    def _assert_completion_scope_drift(self, *, initially_final: bool) -> None:
        contract = self.owner._certified_contract(final=initially_final)
        _store, runtime, record = self.owner._integration_runtime(contract)
        assert contract.memory_repo_path is not None
        contract_before = contract.contract_path.read_bytes()
        real_transfer = claim_transfer_mod.transfer_integration_claim

        def race_finality_after_journal_intent(*args, **kwargs):
            master_path = self.fixture.tasks / "master-a" / "task.json"
            if initially_final:
                master = read_task_doc(master_path)
                rows = [row.model_copy(update={"status": "inProgress"}) for row in master.subTasks]
                write_task_doc(master_path.parent, master.model_copy(update={"subTasks": rows}))
            else:
                self.owner._mark_master_work_complete()
            return real_transfer(*args, **kwargs)

        with (
            mock.patch.object(
                fixture_mod.quality_mod,
                "run_strict_code_quality_gate",
                return_value=fixture_mod._full_gate(contract),
            ) as full_gate,
            mock.patch.object(
                claim_transfer_mod,
                "transfer_integration_claim",
                side_effect=race_finality_after_journal_intent,
            ),
        ):
            refused = fixture_mod.integrate_mod.integrate_result(
                self.owner._args(contract, runtime, record),
                contract,
            )

        assert refused.returncode == (2 if initially_final else 0)
        assert refused.payload["state"] == (
            "organizational-completion-publication-conflict" if initially_final else "integrated"
        )
        self.assertEqual(full_gate.call_count, 1 if initially_final else 0)
        with self.assertRaises(StopIteration):
            self.owner._candidate_projection(LEAF_A)
        comparison = self.assertEqual if initially_final else self.assertNotEqual
        comparison(contract.contract_path.read_bytes(), contract_before)
        self.assertEqual(
            git(contract.code_repo_path, "rev-parse", contract.code_source_branch),
            contract.code_base_commit if initially_final else contract.code_commit,
        )
        self.assertEqual(
            git(contract.memory_repo_path, "rev-parse", contract.memory_source_branch),
            contract.memory_base_commit if initially_final else contract.ledger_commit,
        )

    def test_final_preflight_refuses_locked_nonfinal_scope(self) -> None:
        self._assert_completion_scope_drift(initially_final=True)

    def test_nonfinal_preflight_refuses_locked_final_scope(self) -> None:
        self._assert_completion_scope_drift(initially_final=False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
