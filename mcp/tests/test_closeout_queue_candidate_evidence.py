from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.queue import closeout_queue_candidate_evidence as evidence
from agents_remember.worktrees.queue.closeout_queue import _graph_context
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from test_closeout_queue import MASTER_A, MASTER_B, SPRINT, QueueFixture


class CloseoutQueueCandidateEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        self.contract = self.fixture.contracts[MASTER_A]
        self.atomic_contract = self.fixture.contracts[MASTER_B]
        self.graph = _graph_context(TaskDocumentTopology(self.fixture.coord), SPRINT)
        self.master = self.graph.masters[MASTER_B]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _series_contract(self) -> WorktreeContract:
        path = self.atomic_contract.parent_contract_path
        assert path is not None
        return load_contract(path)

    def _landing_authority(self) -> evidence.AtomicMasterLandingAuthority:
        return evidence.atomic_master_landing_authority(self.fixture.cfg, self.graph.sprint)

    def _completed_operation(
        self,
        contract: WorktreeContract,
        authority: evidence.AtomicMasterLandingAuthority,
    ) -> LifecycleOperationRecord:
        code_repository = repository_identity(contract.code_repo_path)
        memory_repository = repository_identity(contract.memory_repo_path)
        assert code_repository is not None
        assert memory_repository is not None
        integration_authority = IntegrationOperationAuthority(
            targetKind="sprint-super",
            codeRepository=code_repository.as_posix(),
            codeSourceBranch=authority.source_branch,
            codeSourceRef=f"refs/heads/{authority.source_branch}",
            codeSourceCommit=contract.code_base_commit,
            codeCandidateCommit=contract.integrated_code_commit,
            memoryRepository=memory_repository.as_posix(),
            memorySourceBranch=authority.source_branch,
            memorySourceRef=f"refs/heads/{authority.source_branch}",
            memorySourceCommit=contract.memory_base_commit,
            memoryContentCommit=contract.integrated_memory_content_commit,
            ledgerCommit=contract.integrated_ledger_commit,
        )
        return LifecycleOperationRecord(
            taskId=contract.task_id,
            taskName=contract.task_name,
            contractPath=contract.contract_path.as_posix(),
            operationKind="integrate",
            candidateState="a" * 64,
            fingerprint="b" * 64,
            operationKey="c" * 64,
            integrationAuthority=integration_authority,
            input=IntegrateOperationInput(
                configPath="/config.json",
                contractPath=contract.contract_path.as_posix(),
            ),
            status="completed",
            phase="completed",
            queuedAt="2026-08-15T00:00:00+00:00",
            startedAt="2026-08-15T00:00:01+00:00",
            finishedAt="2026-08-15T00:00:02+00:00",
            reportPath=(contract.worktree_group / "reports" / "integrate.log").as_posix(),
            result={
                "ok": True,
                "state": "integrated",
                "operation": "worktree_integrate",
                "integrated_code_commit": contract.integrated_code_commit,
                "integrated_memory_content_commit": contract.integrated_memory_content_commit,
                "integrated_ledger_commit": contract.integrated_ledger_commit,
            },
            irreversibleBoundaryEntered=True,
            recoveryCommits=LifecycleOperationRecoveryCommits(
                codeCommit=contract.integrated_code_commit,
                memoryContentCommit=contract.integrated_memory_content_commit,
                ledgerCommit=contract.integrated_ledger_commit,
            ),
        )

    def test_route_review_fact_requires_full_record_when_summary_requires_it(self) -> None:
        with (
            mock.patch.object(
                evidence,
                "require_current_route_review",
                return_value={"required": True, "status": "pass"},
            ),
            mock.patch.object(evidence, "resolve_terminal_leaf_doc", return_value=None),
            self.assertRaisesRegex(CloseoutQueueError, "has no canonical full record"),
        ):
            evidence.route_review_fact(self.contract)

        summary = {"required": False, "status": "not-required"}
        with (
            mock.patch.object(evidence, "require_current_route_review", return_value=summary),
            mock.patch.object(evidence, "resolve_terminal_leaf_doc", return_value=None),
        ):
            fact = evidence.route_review_fact(self.contract)
        self.assertFalse(fact.required)
        self.assertEqual(fact.evidence, [])

    def test_route_review_blockers_translate_invalid_and_detect_drift(self) -> None:
        expected = evidence.route_review_fact(self.contract)
        self.assertEqual(evidence.route_review_blockers(self.contract, expected), [])
        with mock.patch.object(
            evidence,
            "route_review_fact",
            side_effect=CloseoutQueueError("invalid", "broken"),
        ):
            self.assertEqual(
                evidence.route_review_blockers(self.contract, expected)[0].split(":")[0],
                "route-review-invalid",
            )
        changed = expected.model_copy(update={"status": "changed"})
        self.assertEqual(
            evidence.route_review_blockers(self.contract, changed),
            ["route-review-stale"],
        )

    def test_source_base_checks_translate_lineage_and_exact_head_movement(self) -> None:
        with (
            mock.patch.object(
                evidence,
                "require_current_source_lineage",
                side_effect=RuntimeError("stale lineage"),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "source-lineage-stale"),
        ):
            evidence.require_source_bases_current(self.contract)
        with (
            mock.patch.object(evidence, "require_current_source_lineage"),
            mock.patch.object(evidence, "branch_commit", return_value="f" * 40),
            self.assertRaisesRegex(CloseoutQueueError, "code-source-moved"),
        ):
            evidence.require_source_bases_current(self.contract)

        heads = [self.contract.code_base_commit, "f" * 40]
        with (
            mock.patch.object(evidence, "require_current_source_lineage"),
            mock.patch.object(evidence, "branch_commit", side_effect=heads),
            self.assertRaisesRegex(CloseoutQueueError, "memory-source-moved"),
        ):
            evidence.require_source_bases_current(self.contract)
        incomplete = replace(self.contract, memory_repo_path=None)
        with (
            mock.patch.object(evidence, "require_current_source_lineage"),
            mock.patch.object(
                evidence, "branch_commit", return_value=self.contract.code_base_commit
            ),
            self.assertRaisesRegex(CloseoutQueueError, "memory-source-missing"),
        ):
            evidence.require_source_bases_current(incomplete)

    def test_ledger_mapping_requires_path_row_and_returns_exact_commit(self) -> None:
        self.assertIsNone(evidence.ledger_mapping(replace(self.contract, memory_mode="disabled")))
        with self.assertRaisesRegex(CloseoutQueueError, "ledger-missing"):
            evidence.ledger_mapping(replace(self.contract, ledger_path=None))
        with (
            mock.patch.object(evidence, "find_mapping", return_value=None),
            self.assertRaisesRegex(CloseoutQueueError, "ledger-incompatible"),
        ):
            evidence.ledger_mapping(self.contract)
        with mock.patch.object(
            evidence,
            "find_mapping",
            return_value=SimpleNamespace(memory_commit="c" * 40),
        ):
            self.assertEqual(evidence.ledger_mapping(self.contract), "c" * 40)

    def test_memory_tree_commit_tree_and_owner_fingerprint_are_exact(self) -> None:
        self.assertIsNone(
            evidence.memory_candidate_tree(replace(self.contract, memory_worktree=None))
        )
        with mock.patch.object(evidence, "worktree_candidate_tree", return_value="a" * 40):
            self.assertEqual(evidence.memory_candidate_tree(self.contract), "a" * 40)
        failed = SimpleNamespace(returncode=1, stderr="missing", stdout="")
        with (
            mock.patch.object(evidence, "run_git", return_value=failed),
            self.assertRaisesRegex(CloseoutQueueError, "commit-missing"),
        ):
            evidence.commit_tree(self.contract.code_repo_path, "bad")
        passed = SimpleNamespace(returncode=0, stderr="", stdout="b" * 40 + "\n")
        with mock.patch.object(evidence, "run_git", return_value=passed):
            self.assertEqual(
                evidence.commit_tree(self.contract.code_repo_path, "good"),
                "b" * 40,
            )
        self.assertEqual(len(evidence.operation_owner_fingerprint("operation")), 64)

    def test_atomic_contract_predicates_require_exact_final_series_landing(self) -> None:
        series = self._series_contract()
        authority = self._landing_authority()
        exact = replace(
            series,
            integration_status="completed",
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit="a" * 40,
            integrated_code_commit="a" * 40,
            memory_content_commit="b" * 40,
            ledger_commit="c" * 40,
            integrated_memory_content_commit="b" * 40,
            integrated_ledger_commit="c" * 40,
        )
        self.assertTrue(evidence._atomic_contract_matches_master(exact, self.master, authority))
        self.assertTrue(evidence._atomic_finalization_is_exact(exact))
        self.assertTrue(evidence._atomic_landing_changes_content(exact))
        self.assertFalse(
            evidence._atomic_contract_matches_master(
                replace(exact, integration_status="not-started"), self.master, authority
            )
        )
        self.assertFalse(
            evidence._atomic_contract_matches_master(
                replace(exact, kind="leaf"), self.master, authority
            )
        )
        self.assertFalse(
            evidence._atomic_contract_matches_master(
                replace(exact, task_root=exact.task_root.parent), self.master, authority
            )
        )
        for update in (
            {"coordination_root": exact.coordination_root.parent},
            {"repo_name": "foreign"},
            {"contract_path": exact.contract_path.parent / "foreign.md"},
            {"worktree_group": exact.worktree_group.parent},
            {"code_source_branch": "foreign-super"},
            {"code_work_branch": "ar/foreign"},
            {"parent_task_name": "foreign-sprint"},
            {"memory_source_branch": "foreign-super"},
            {"memory_work_branch": "ar/foreign"},
            {"memory_mode": "disabled", "memory_repo_path": None},
        ):
            with self.subTest(authority_update=update):
                self.assertFalse(
                    evidence._atomic_contract_matches_master(
                        replace(exact, **update), self.master, authority
                    )
                )
        self.assertFalse(
            evidence._atomic_finalization_is_exact(replace(exact, integrated_code_commit="d" * 40))
        )
        self.assertFalse(
            evidence._atomic_finalization_is_exact(replace(exact, human_review_status="pending"))
        )
        for update in (
            {"approved_for_commit": False},
            {"closeout_status": "not-started"},
            {"code_commit": ""},
            {"memory_content_commit": ""},
            {"integrated_memory_content_commit": "d" * 40},
            {"integrated_ledger_commit": "d" * 40},
        ):
            with self.subTest(update=update):
                self.assertFalse(evidence._atomic_finalization_is_exact(replace(exact, **update)))
        self.assertTrue(
            evidence._atomic_finalization_is_exact(replace(exact, memory_mode="disabled"))
        )
        self.assertFalse(
            evidence._atomic_landing_changes_content(
                replace(
                    exact,
                    integrated_code_commit=exact.code_base_commit,
                    integrated_ledger_commit=exact.memory_base_commit,
                )
            )
        )
        self.assertTrue(
            evidence._atomic_landing_changes_content(
                replace(
                    exact,
                    integrated_code_commit=exact.code_base_commit,
                    integrated_ledger_commit="f" * 40,
                )
            )
        )
        self.assertFalse(
            evidence._atomic_landing_changes_content(
                replace(
                    exact,
                    memory_mode="disabled",
                    integrated_code_commit=exact.code_base_commit,
                )
            )
        )

    def test_atomic_code_and_memory_tips_are_exact(self) -> None:
        series = self._series_contract()
        self.assertFalse(evidence._atomic_code_landed(series))
        landed = replace(series, integrated_code_commit="a" * 40)
        with mock.patch.object(evidence, "branch_commit", return_value="a" * 40):
            self.assertTrue(evidence._atomic_code_landed(landed))
        with mock.patch.object(evidence, "branch_commit", return_value="b" * 40):
            self.assertFalse(evidence._atomic_code_landed(landed))

        self.assertTrue(evidence._atomic_memory_landed(replace(series, memory_mode="disabled")))
        with self.assertRaisesRegex(ValueError, "no memory repository"):
            evidence._atomic_memory_landed(replace(series, memory_repo_path=None))
        memory_landed = replace(
            series,
            integrated_code_commit="a" * 40,
            integrated_memory_content_commit="b" * 40,
            integrated_ledger_commit="c" * 40,
        )
        mapping = SimpleNamespace(memory_commit="b" * 40)
        with (
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(evidence, "find_mapping", return_value=mapping),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "branch_commit", return_value="c" * 40),
        ):
            self.assertTrue(evidence._atomic_memory_landed(memory_landed))
        with (
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(evidence, "find_mapping", return_value=None),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "branch_commit", return_value="c" * 40),
        ):
            self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        with (
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(evidence, "find_mapping", return_value=mapping),
            mock.patch.object(evidence, "is_ancestor", return_value=False),
            mock.patch.object(evidence, "branch_commit", return_value="c" * 40),
        ):
            self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        with (
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(evidence, "find_mapping", return_value=mapping),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "branch_commit", return_value="d" * 40),
        ):
            self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        with (
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(
                evidence,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit="e" * 40),
            ),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "branch_commit", return_value="c" * 40),
        ):
            self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        for field in ("integrated_memory_content_commit", "integrated_ledger_commit"):
            with self.subTest(field=field):
                self.assertFalse(
                    evidence._atomic_memory_landed(replace(memory_landed, **{field: ""}))
                )

    def test_atomic_landing_requires_exact_completed_plane_operation(self) -> None:
        series = self._series_contract()
        authority = self._landing_authority()
        completed = replace(
            series,
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit="a" * 40,
            memory_content_commit="b" * 40,
            ledger_commit="c" * 40,
            integration_status="completed",
            integrated_code_commit="a" * 40,
            integrated_memory_content_commit="b" * 40,
            integrated_ledger_commit="c" * 40,
        )
        self.assertFalse(evidence._atomic_operation_landed(completed, authority))
        with (
            mock.patch.object(evidence, "load_contract", return_value=completed),
            mock.patch.object(evidence, "branch_commit", side_effect=["a" * 40, "c" * 40]),
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(
                evidence,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit="b" * 40),
            ),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            self.assertRaisesRegex(CloseoutQueueError, "does not prove"),
        ):
            evidence.require_atomic_master_landed(self.master, authority)
        record = self._completed_operation(completed, authority)
        store = LifecycleOperationStore(
            operation_record_path(completed.worktree_group, "integrate")
        )
        store.create(record)
        self.assertTrue(evidence._atomic_operation_landed(completed, authority))
        with (
            mock.patch.object(evidence, "load_contract", return_value=completed),
            mock.patch.object(evidence, "branch_commit", side_effect=["a" * 40, "c" * 40]),
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(
                evidence,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit="b" * 40),
            ),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
        ):
            evidence.require_atomic_master_landed(self.master, authority)

        for tips in (("d" * 40, "c" * 40), ("a" * 40, "d" * 40)):
            with (
                self.subTest(tips=tips),
                mock.patch.object(evidence, "load_contract", return_value=completed),
                mock.patch.object(evidence, "branch_commit", side_effect=tips),
                mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
                mock.patch.object(
                    evidence,
                    "find_mapping",
                    return_value=SimpleNamespace(memory_commit="b" * 40),
                ),
                mock.patch.object(evidence, "is_ancestor", return_value=True),
                self.assertRaisesRegex(CloseoutQueueError, "does not prove"),
            ):
                evidence.require_atomic_master_landed(self.master, authority)

        foreign = replace(completed, code_repo_path=self.fixture.memory)
        with (
            mock.patch.object(evidence, "load_contract", return_value=foreign),
            mock.patch.object(evidence, "branch_commit", side_effect=["a" * 40, "c" * 40]),
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(
                evidence,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit="b" * 40),
            ),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            self.assertRaisesRegex(CloseoutQueueError, "does not prove"),
        ):
            evidence.require_atomic_master_landed(self.master, authority)

        assert record.integrationAuthority is not None
        assert record.recoveryCommits is not None
        assert record.result is not None
        recovered = record.model_copy(
            update={"result": {**record.result, "state": "already-integrated"}}
        )
        with mock.patch.object(LifecycleOperationStore, "read", return_value=recovered):
            self.assertTrue(evidence._atomic_operation_landed(completed, authority))
        variants = (
            record.model_copy(update={"status": "running", "phase": "source-merge"}),
            record.model_copy(update={"irreversibleBoundaryEntered": False}),
            record.model_copy(update={"result": {"ok": False}}),
            record.model_copy(
                update={
                    "integrationAuthority": record.integrationAuthority.model_copy(
                        update={"codeRepository": "/foreign/.git"}
                    )
                }
            ),
            record.model_copy(
                update={
                    "recoveryCommits": record.recoveryCommits.model_copy(
                        update={"codeCommit": "d" * 40}
                    )
                }
            ),
        )
        for changed in variants:
            with (
                self.subTest(changed=changed),
                mock.patch.object(LifecycleOperationStore, "read", return_value=changed),
            ):
                self.assertFalse(evidence._atomic_operation_landed(completed, authority))

    def test_atomic_landing_proof_translates_landed_probe_failures(self) -> None:
        series = self._series_contract()
        authority = self._landing_authority()
        completed = replace(
            series,
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit="a" * 40,
            memory_content_commit="b" * 40,
            ledger_commit="c" * 40,
            integration_status="completed",
            integrated_code_commit="a" * 40,
            integrated_memory_content_commit="b" * 40,
            integrated_ledger_commit="c" * 40,
        )
        with (
            mock.patch.object(evidence, "load_contract", return_value=completed),
            mock.patch.object(evidence, "branch_commit", side_effect=["a" * 40, "c" * 40]),
            mock.patch.object(evidence, "load_named_ref_ledger", return_value=[]),
            mock.patch.object(
                evidence,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit="b" * 40),
            ),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(
                evidence,
                "_atomic_code_landed",
                side_effect=RuntimeError("no exact landed git state"),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "no valid exact landing"),
        ):
            evidence.require_atomic_master_landed(self.master, authority)

    def test_public_atomic_landing_proof_translates_invalid_and_false_predicates(self) -> None:
        authority = self._landing_authority()
        with (
            mock.patch.object(evidence, "load_contract", side_effect=OSError("missing")),
            self.assertRaisesRegex(CloseoutQueueError, "no valid exact landing"),
        ):
            evidence.require_atomic_master_landed(self.master, authority)
        series = self._series_contract()
        predicates = (
            "_atomic_contract_matches_master",
            "_atomic_finalization_is_exact",
            "_atomic_landing_changes_content",
            "_atomic_code_landed",
            "_atomic_memory_landed",
            "_atomic_operation_landed",
        )

        def patches(false_name: str | None = None) -> dict[str, mock.Mock]:
            return {name: mock.Mock(return_value=name != false_name) for name in predicates}

        with (
            mock.patch.object(evidence, "load_contract", return_value=series),
            mock.patch.multiple(evidence, **patches()),
        ):
            evidence.require_atomic_master_landed(self.master, authority)
        for false_name in predicates:
            with (
                self.subTest(false_name=false_name),
                mock.patch.object(evidence, "load_contract", return_value=series),
                mock.patch.multiple(evidence, **patches(false_name)),
                self.assertRaisesRegex(CloseoutQueueError, "does not prove"),
            ):
                evidence.require_atomic_master_landed(self.master, authority)

    def test_task_evidence_is_confined_existing_and_readable(self) -> None:
        task_root = self.contract.task_root
        local = task_root / "evidence.md"
        local.write_text("proof", encoding="utf-8")
        self.assertEqual(evidence._task_evidence(task_root, "evidence.md").path, "evidence.md")
        for value in (local.as_posix(), "../outside", "missing.md"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(CloseoutQueueError, "task-relative file"),
            ):
                evidence._task_evidence(task_root, value)
        with (
            mock.patch.object(Path, "read_bytes", side_effect=OSError("unreadable")),
            self.assertRaisesRegex(CloseoutQueueError, "cannot be read"),
        ):
            evidence._task_evidence(task_root, "evidence.md")
