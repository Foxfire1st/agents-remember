from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.closeout_queue import ActiveAtomicBlocker
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import closeout_queue as queue
from agents_remember.worktrees.closeout_queue_errors import CloseoutQueueError
from test_closeout_queue import LEAF_A, MASTER_A, MASTER_B, NOW, SPRINT, QueueFixture


class CloseoutQueueBlockerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))
        self.fixture.declare(MASTER_A)
        self.contract = self.fixture.contracts[MASTER_A]
        self.topology = TaskDocumentTopology(self.fixture.coord)
        self.graph = queue._graph_context(self.topology, SPRINT)
        state = CloseoutQueueStore(self.fixture.coord, SPRINT).read(
            queue._initial_state(SPRINT, self.graph.revision, NOW)
        )
        self.candidate = state.candidates[LEAF_A.key]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_candidate_blockers_include_graph_owner_operation_and_revalidation_failures(
        self,
    ) -> None:
        changed_graph = replace(self.graph, revision="f" * 64, masters={})
        with mock.patch.object(
            queue, "_pre_closeout_blockers", side_effect=CloseoutQueueError("bad", "facts")
        ):
            blockers = queue._candidate_blockers(self.topology, changed_graph, self.candidate)
        self.assertIn("graph-revision-stale", blockers)
        self.assertIn("owning-master-no-longer-commanded", blockers)
        self.assertTrue(any(item.startswith("candidate-revalidation-failed") for item in blockers))

        in_flight = self.candidate.model_copy(
            update={
                "state": "closeout-in-flight",
                "inFlightOwnerFingerprint": "a" * 64,
            }
        )
        with (
            mock.patch.object(queue, "_owned_lifecycle_operation", return_value=None),
            mock.patch.object(queue, "_pre_closeout_blockers", return_value=[]),
        ):
            self.assertIn(
                "lifecycle-operation-owner-unavailable",
                queue._candidate_blockers(self.topology, self.graph, in_flight),
            )
        with (
            mock.patch.object(
                queue,
                "_owned_lifecycle_operation",
                return_value=SimpleNamespace(status="failed"),
            ),
            mock.patch.object(queue, "_pre_closeout_blockers", return_value=[]),
        ):
            self.assertIn(
                "lifecycle-operation-owner-terminal",
                queue._candidate_blockers(self.topology, self.graph, in_flight),
            )

    def test_candidate_blockers_choose_post_closeout_and_refresh_curator_evidence(self) -> None:
        in_flight = self.candidate.model_copy(
            update={
                "state": "closeout-in-flight",
                "inFlightOwnerFingerprint": "a" * 64,
            }
        )
        completed = replace(self.contract, closeout_status="completed")
        with (
            mock.patch.object(queue, "load_contract", return_value=completed),
            mock.patch.object(
                queue,
                "_owned_lifecycle_operation",
                return_value=SimpleNamespace(status="running"),
            ),
            mock.patch.object(queue, "curator_evidence", return_value=[mock.sentinel.fact]),
            mock.patch.object(queue, "_post_closeout_blockers", return_value=["post"]) as post,
        ):
            self.assertIn("post", queue._candidate_blockers(self.topology, self.graph, in_flight))
        self.assertEqual(post.call_args.kwargs["expected_memory_evidence"], [mock.sentinel.fact])

        with (
            mock.patch.object(queue, "load_contract", return_value=completed),
            mock.patch.object(
                queue,
                "_owned_lifecycle_operation",
                return_value=SimpleNamespace(status="running"),
            ),
            mock.patch.object(
                queue, "curator_evidence", side_effect=CloseoutQueueError("bad", "curator")
            ),
            mock.patch.object(queue, "_post_closeout_blockers", return_value=[]) as post,
        ):
            queue._candidate_blockers(self.topology, self.graph, in_flight)
        self.assertIsNone(post.call_args.kwargs["expected_memory_evidence"])

    def test_pre_closeout_blockers_name_lifecycle_tree_memory_and_source_changes(self) -> None:
        contract = replace(
            self.contract,
            closeout_status="completed",
            integration_status="completed",
        )
        with (
            mock.patch.object(queue, "_common_candidate_blockers", return_value=["common"]),
            mock.patch.object(queue, "code_candidate_tree", return_value="f" * 40),
            mock.patch.object(queue, "memory_candidate_tree", return_value="e" * 40),
            mock.patch.object(queue, "_source_and_ledger_blockers", return_value=["source"]),
        ):
            blockers = queue._pre_closeout_blockers(
                self.topology, self.graph, self.candidate, contract
            )
        self.assertEqual(
            blockers,
            [
                "common",
                "closeout-already-started",
                "integration-already-started",
                "candidate-tree-stale",
                "memory-candidate-tree-stale",
                "source",
            ],
        )

    def test_post_closeout_blockers_return_early_or_collect_every_exact_owner(self) -> None:
        with mock.patch.object(queue, "_common_candidate_blockers", return_value=["common"]):
            self.assertEqual(
                queue._post_closeout_blockers(
                    self.topology, self.graph, self.candidate, self.contract
                ),
                ["common", "closeout-not-certified"],
            )
        completed = replace(
            self.contract,
            closeout_status="completed",
            integration_status="in-progress",
        )
        with (
            mock.patch.object(queue, "_common_candidate_blockers", return_value=[]),
            mock.patch.object(queue, "_closed_tree_blockers", return_value=["tree"]),
            mock.patch.object(queue, "_certified_commit_blockers", return_value=["commit"]),
            mock.patch.object(queue, "_source_and_ledger_blockers", return_value=["source"]),
            mock.patch.object(queue, "_closed_ledger_blockers", return_value=["ledger"]),
        ):
            self.assertEqual(
                queue._post_closeout_blockers(self.topology, self.graph, self.candidate, completed),
                ["integration-state-invalid", "tree", "commit", "source", "ledger"],
            )

    def test_closed_tree_and_certified_commit_blockers_are_exact(self) -> None:
        with mock.patch.object(queue, "commit_tree", return_value="f" * 40):
            self.assertEqual(
                queue._closed_tree_blockers(self.candidate, self.contract),
                ["closeout-code-tree-mismatch"],
            )
        code_closed = replace(
            self.contract,
            code_commit="a" * 40,
            memory_content_commit="b" * 40,
        )
        with mock.patch.object(queue, "commit_tree", return_value=self.candidate.candidateTree):
            self.assertEqual(queue._closed_tree_blockers(self.candidate, code_closed), [])
            self.assertEqual(
                queue._closed_tree_blockers(
                    self.candidate,
                    replace(code_closed, memory_content_commit="", memory_worktree=None),
                ),
                ["closeout-memory-commit-missing"],
            )
        certified = self.candidate.model_copy(
            update={
                "state": "certified",
                "closeoutCodeCommit": "a" * 40,
                "closeoutMemoryContentCommit": "b" * 40,
                "closeoutLedgerCommit": "c" * 40,
            }
        )
        changed = replace(
            self.contract,
            code_commit="d" * 40,
            memory_content_commit="e" * 40,
            ledger_commit="f" * 40,
        )
        self.assertEqual(
            queue._certified_commit_blockers(certified, changed),
            [
                "closeout-code-commit-changed",
                "closeout-memory-commit-changed",
                "closeout-ledger-commit-changed",
            ],
        )

    def test_closed_ledger_requires_path_mapping_and_reachable_memory_commit(self) -> None:
        self.assertEqual(
            queue._closed_ledger_blockers(replace(self.contract, memory_mode="disabled")),
            [],
        )
        self.assertEqual(
            queue._closed_ledger_blockers(replace(self.contract, ledger_path=None)),
            ["closeout-ledger-missing"],
        )
        closed = replace(
            self.contract,
            code_commit="a" * 40,
            memory_content_commit="b" * 40,
            ledger_commit="c" * 40,
        )
        with mock.patch.object(queue, "find_mapping", return_value=None):
            self.assertEqual(
                queue._closed_ledger_blockers(closed),
                ["closeout-ledger-mapping-mismatch"],
            )
        mapping = SimpleNamespace(memory_commit="b" * 40)
        with (
            mock.patch.object(queue, "find_mapping", return_value=mapping),
            mock.patch.object(queue, "is_ancestor", return_value=False),
        ):
            self.assertEqual(
                queue._closed_ledger_blockers(closed),
                ["closeout-memory-commit-unreachable"],
            )
        with (
            mock.patch.object(queue, "find_mapping", return_value=mapping),
            mock.patch.object(queue, "is_ancestor", return_value=True),
        ):
            self.assertEqual(queue._closed_ledger_blockers(closed), [])

    def test_common_candidate_blockers_compare_every_bound_identity_and_evidence(self) -> None:
        foreign_leaf = LEAF_A.model_copy(update={"path": "other/leaf.json"})
        changed = replace(
            self.contract,
            code_base_commit="f" * 40,
            memory_mode="disabled",
            memory_base_commit="e" * 40,
        )
        with (
            mock.patch.object(
                queue, "_leaf_identity", return_value=(foreign_leaf, mock.sentinel.leaf)
            ),
            mock.patch.object(queue, "completion_blockers", return_value=["open"]),
            mock.patch.object(queue, "route_review_blockers", return_value=["review"]),
            mock.patch.object(queue, "curator_evidence_blockers", return_value=["memory"]),
            mock.patch.object(queue, "_grade_blockers", return_value=["grade"]),
        ):
            blockers = queue._common_candidate_blockers(
                self.topology, self.graph, self.candidate, changed
            )
        self.assertEqual(
            blockers,
            [
                "leaf-task-document-changed",
                "leaf-task-incomplete",
                "code-base-changed",
                "memory-mode-changed",
                "memory-base-changed",
                "review",
                "memory",
                "grade",
            ],
        )
        with (
            mock.patch.object(
                queue,
                "_leaf_identity",
                return_value=(self.candidate.taskDocumentRef, mock.sentinel.leaf),
            ),
            mock.patch.object(self.topology, "parent", return_value=MASTER_B),
            mock.patch.object(queue, "completion_blockers", return_value=[]),
            mock.patch.object(queue, "route_review_blockers", return_value=[]),
            mock.patch.object(queue, "curator_evidence_blockers", return_value=[]),
            mock.patch.object(queue, "_grade_blockers", return_value=[]),
        ):
            self.assertIn(
                "owning-master-changed",
                queue._common_candidate_blockers(
                    self.topology, self.graph, self.candidate, self.contract
                ),
            )

    def test_source_and_ledger_blockers_cover_modes_heads_and_mapping(self) -> None:
        with (
            mock.patch.object(
                queue, "source_lineage_for_contract", return_value=mock.sentinel.fact
            ),
            mock.patch.object(queue, "lineage_refusal", return_value=("lineage", "detail")),
            mock.patch.object(queue, "branch_commit", return_value="f" * 40),
        ):
            self.assertEqual(
                queue._source_and_ledger_blockers(
                    self.candidate,
                    replace(self.contract, memory_mode="disabled"),
                ),
                ["lineage", "code-source-moved"],
            )
        missing = replace(self.contract, memory_repo_path=None)
        with (
            mock.patch.object(queue, "lineage_refusal", return_value=None),
            mock.patch.object(
                queue,
                "branch_commit",
                return_value=self.candidate.codeBaseCommit,
            ),
        ):
            self.assertEqual(
                queue._source_and_ledger_blockers(self.candidate, missing),
                ["memory-source-missing"],
            )
        heads = [self.candidate.codeBaseCommit, "f" * 40]
        with (
            mock.patch.object(queue, "lineage_refusal", return_value=None),
            mock.patch.object(queue, "branch_commit", side_effect=heads),
            mock.patch.object(queue, "ledger_mapping", return_value="d" * 40),
        ):
            blockers = queue._source_and_ledger_blockers(self.candidate, self.contract)
        self.assertEqual(
            blockers,
            ["memory-source-moved", "ledger-base-mapping-changed"],
        )

    def test_grade_blockers_detect_invalid_judgment_and_each_drift_class(self) -> None:
        grade = self.candidate.grade
        assert grade is not None
        with mock.patch.object(queue, "_grade", side_effect=CloseoutQueueError("bad", "grade")):
            self.assertTrue(
                queue._grade_blockers(self.graph, self.candidate)[0].startswith(
                    "grade-evidence-invalid"
                )
            )
        with mock.patch.object(
            queue,
            "_grade",
            return_value=(
                grade.model_copy(update={"priority": "low"}),
                "f" * 64,
                [],
            ),
        ):
            self.assertEqual(
                queue._grade_blockers(self.graph, self.candidate),
                ["grade-judgment-stale", "grade-evidence-stale"],
            )

    def test_waiting_reasons_cover_lane_blocker_atomic_and_admission_facts(self) -> None:
        no_grade = self.candidate.model_copy(
            update={
                "grade": None,
                "admission": self.candidate.admission.model_copy(
                    update={
                        "resourceReady": False,
                        "resourceReason": "busy",
                        "admissionReady": False,
                        "admissionReason": "held",
                    }
                ),
            }
        )
        reasons = queue._waiting_reasons(
            self.graph,
            no_grade,
            LEAF_A.model_copy(update={"path": "other.json"}),
            ActiveAtomicBlocker(
                master=MASTER_B,
                graphRevision=self.graph.revision,
                acquiredBy="orchestrator",
                acquiredAt=NOW,
                rationale="isolate",
            ),
        )
        self.assertIn("explicit-grade-required", reasons)
        self.assertTrue(any(item.startswith("integration-lane-owned-by") for item in reasons))
        self.assertTrue(any(item.startswith("atomic-blocker-held-by") for item in reasons))
        self.assertIn("resource-unavailable: busy", reasons)
        self.assertIn("admission-blocked: held", reasons)
        stale = ActiveAtomicBlocker(
            master=MASTER_A,
            graphRevision="f" * 64,
            acquiredBy="orchestrator",
            acquiredAt=NOW,
            rationale="isolate",
        )
        self.assertIn(
            "atomic-blocker-graph-revision-stale",
            queue._waiting_reasons(self.graph, self.candidate, None, stale),
        )
        atomic_master = replace(
            self.graph.masters[MASTER_A],
            document=self.graph.masters[MASTER_A].document.model_copy(
                update={"executionNature": "atomic"}
            ),
        )
        atomic_graph = replace(
            self.graph,
            masters={**self.graph.masters, MASTER_A: atomic_master},
        )
        self.assertIn(
            "atomic-blocker-required",
            queue._waiting_reasons(atomic_graph, self.candidate, None, None),
        )
        predecessor_graph = replace(
            self.graph,
            incomplete_predecessors={
                **self.graph.incomplete_predecessors,
                MASTER_A: (MASTER_B,),
            },
        )
        self.assertIn(
            f"predecessor-incomplete: {MASTER_B.key}",
            queue._waiting_reasons(predecessor_graph, self.candidate, None, None),
        )


if __name__ == "__main__":
    unittest.main()
