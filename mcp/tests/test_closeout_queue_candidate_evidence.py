from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import closeout_queue_candidate_evidence as evidence
from agents_remember.worktrees.closeout_queue import _graph_context
from agents_remember.worktrees.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from test_closeout_queue import MASTER_A, SPRINT, QueueFixture


class CloseoutQueueCandidateEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))
        self.contract = self.fixture.contracts[MASTER_A]
        self.graph = _graph_context(TaskDocumentTopology(self.fixture.coord), SPRINT)
        self.master = self.graph.masters[MASTER_A]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _series_contract(self) -> WorktreeContract:
        path = self.contract.parent_contract_path
        assert path is not None
        return load_contract(path)

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
            mock.patch.object(evidence, "head_commit", return_value="f" * 40),
            self.assertRaisesRegex(CloseoutQueueError, "code-source-moved"),
        ):
            evidence.require_source_bases_current(self.contract)

        heads = [self.contract.code_base_commit, "f" * 40]
        with (
            mock.patch.object(evidence, "require_current_source_lineage"),
            mock.patch.object(evidence, "head_commit", side_effect=heads),
            self.assertRaisesRegex(CloseoutQueueError, "memory-source-moved"),
        ):
            evidence.require_source_bases_current(self.contract)
        incomplete = replace(self.contract, memory_repo_path=None)
        with (
            mock.patch.object(evidence, "require_current_source_lineage"),
            mock.patch.object(evidence, "head_commit", return_value=self.contract.code_base_commit),
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
        self.assertTrue(evidence._atomic_contract_matches_master(exact, self.master))
        self.assertTrue(evidence._atomic_finalization_is_exact(exact))
        self.assertTrue(evidence._atomic_landing_changes_content(exact))
        self.assertFalse(
            evidence._atomic_contract_matches_master(
                replace(exact, integration_status="not-started"), self.master
            )
        )
        self.assertFalse(
            evidence._atomic_contract_matches_master(replace(exact, kind="leaf"), self.master)
        )
        self.assertFalse(
            evidence._atomic_contract_matches_master(
                replace(exact, task_root=exact.task_root.parent), self.master
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

    def test_atomic_code_and_memory_ancestry_are_both_required(self) -> None:
        series = self._series_contract()
        with mock.patch.object(evidence, "is_ancestor", return_value=True):
            self.assertFalse(evidence._atomic_code_landed(series))
        landed = replace(series, integrated_code_commit="a" * 40)
        with (
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "head_commit", return_value="b" * 40),
        ):
            self.assertTrue(evidence._atomic_code_landed(landed))
        with mock.patch.object(evidence, "is_ancestor", return_value=False):
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
            mock.patch.object(evidence, "load_ledger", return_value=[]),
            mock.patch.object(evidence, "find_mapping", return_value=mapping),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "head_commit", return_value="d" * 40),
        ):
            self.assertTrue(evidence._atomic_memory_landed(memory_landed))
        with (
            mock.patch.object(evidence, "load_ledger", return_value=[]),
            mock.patch.object(evidence, "find_mapping", return_value=None),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "head_commit", return_value="d" * 40),
        ):
            self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        for ancestry in ((False,), (True, False)):
            with (
                self.subTest(ancestry=ancestry),
                mock.patch.object(evidence, "load_ledger", return_value=[]),
                mock.patch.object(evidence, "find_mapping", return_value=mapping),
                mock.patch.object(evidence, "is_ancestor", side_effect=ancestry),
                mock.patch.object(evidence, "head_commit", return_value="d" * 40),
            ):
                self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        with (
            mock.patch.object(evidence, "load_ledger", return_value=[]),
            mock.patch.object(
                evidence,
                "find_mapping",
                return_value=SimpleNamespace(memory_commit="e" * 40),
            ),
            mock.patch.object(evidence, "is_ancestor", return_value=True),
            mock.patch.object(evidence, "head_commit", return_value="d" * 40),
        ):
            self.assertFalse(evidence._atomic_memory_landed(memory_landed))
        self.assertFalse(
            evidence._atomic_memory_landed(
                replace(memory_landed, integrated_memory_content_commit="")
            )
        )

    def test_public_atomic_landing_proof_translates_invalid_and_false_predicates(self) -> None:
        with (
            mock.patch.object(evidence, "load_contract", side_effect=OSError("missing")),
            self.assertRaisesRegex(CloseoutQueueError, "no valid exact landing"),
        ):
            evidence.require_atomic_master_landed(self.master)
        series = self._series_contract()
        predicates = (
            "_atomic_contract_matches_master",
            "_atomic_finalization_is_exact",
            "_atomic_landing_changes_content",
            "_atomic_code_landed",
            "_atomic_memory_landed",
        )

        def patches(false_name: str | None = None) -> dict[str, mock.Mock]:
            return {name: mock.Mock(return_value=name != false_name) for name in predicates}

        with (
            mock.patch.object(evidence, "load_contract", return_value=series),
            mock.patch.multiple(evidence, **patches()),
        ):
            evidence.require_atomic_master_landed(self.master)
        for false_name in predicates:
            with (
                self.subTest(false_name=false_name),
                mock.patch.object(evidence, "load_contract", return_value=series),
                mock.patch.multiple(evidence, **patches(false_name)),
                self.assertRaisesRegex(CloseoutQueueError, "does not prove"),
            ):
                evidence.require_atomic_master_landed(self.master)

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


if __name__ == "__main__":
    unittest.main()
