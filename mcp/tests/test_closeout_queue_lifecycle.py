from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.queue.closeout_queue import CloseoutCandidateRecord
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.queue import closeout_queue_lifecycle as lifecycle
from agents_remember.worktrees.queue.closeout_queue import _graph_context
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_state import initial_queue_state
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_closeout_queue import LEAF_A, MASTER_A, NOW, SPRINT, QueueFixture

OPERATION_KEY = "a" * 64


class CloseoutQueueLifecycleUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))
        self.contract = self.fixture.contracts[MASTER_A]
        self.fixture.declare(MASTER_A)
        self.contract = self.fixture.contracts[MASTER_A]
        self.fixture.mutate("select", candidate=LEAF_A)
        self.topology = TaskDocumentTopology(self.fixture.coord)
        self.graph = _graph_context(self.topology, SPRINT)
        self.store = CloseoutQueueStore(self.fixture.coord, SPRINT)
        self.state = self.store.read(initial_queue_state(SPRINT, self.graph.revision, NOW))
        self.selected = self.state.candidates[LEAF_A.key]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _context(
        self,
        candidate: CloseoutCandidateRecord | None = None,
        *,
        contract: WorktreeContract | None = None,
        operation_key: str = OPERATION_KEY,
    ) -> lifecycle._LifecycleCandidateContext:
        current = candidate or self.selected
        state = self.state.model_copy(update={"candidates": {current.taskDocumentRef.key: current}})
        return lifecycle._LifecycleCandidateContext(
            self.topology,
            self.graph,
            state,
            contract or self.contract,
            operation_key,
        )

    def _write_candidate_state(self, candidate: CloseoutCandidateRecord | None) -> None:
        candidates = {} if candidate is None else {candidate.taskDocumentRef.key: candidate}
        state = self.state.model_copy(update={"candidates": candidates, "appliedRequests": []})
        self.store.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.state_path.write_text(
            state.model_dump_json(exclude_none=True),
            encoding="utf-8",
        )
        self.store.pending_path.unlink(missing_ok=True)

    def test_binding_parser_refuses_partial_malformed_and_non_leaf_contracts(self) -> None:
        self.assertIsNone(lifecycle.contract_queue_binding(replace(self.contract, kind="series")))
        self.assertIsNone(
            lifecycle._stored_queue_binding(
                replace(
                    self.contract,
                    queue_sprint_task_document="",
                    queue_candidate_task_document="",
                )
            )
        )
        with self.assertRaisesRegex(CloseoutQueueError, "partial"):
            lifecycle._stored_queue_binding(
                replace(
                    self.contract,
                    queue_sprint_task_document=SPRINT.key,
                    queue_candidate_task_document="",
                )
            )
        with self.assertRaisesRegex(CloseoutQueueError, "malformed"):
            lifecycle._stored_queue_binding(
                replace(
                    self.contract,
                    queue_sprint_task_document="no-boundary",
                    queue_candidate_task_document=LEAF_A.key,
                )
            )
        with self.assertRaisesRegex(ValueError, "no repository/path boundary"):
            lifecycle._task_ref_from_key("no-boundary")
        self.assertEqual(lifecycle._task_ref_from_key(LEAF_A.key), LEAF_A)

    def test_unbound_legacy_absence_is_narrow(self) -> None:
        missing = TaskDocumentRefError("task-document-not-found", "missing")
        invalid = TaskDocumentRefError("task-document-invalid", "bad")
        self.assertTrue(lifecycle._is_unbound_legacy_absence(None, missing))
        self.assertFalse(
            lifecycle._is_unbound_legacy_absence(lifecycle.QueueBinding(SPRINT, LEAF_A), missing)
        )
        self.assertFalse(lifecycle._is_unbound_legacy_absence(None, invalid))

    def test_contract_binding_refuses_missing_graph_leaf_parent_and_binding_drift(self) -> None:
        stored = lifecycle.QueueBinding(SPRINT, LEAF_A)
        with (
            mock.patch.object(lifecycle, "_stored_queue_binding", return_value=stored),
            mock.patch.object(
                lifecycle,
                "_live_parent_refs",
                return_value=lifecycle._LiveParents(MASTER_A, SPRINT),
            ),
            mock.patch.object(
                TaskDocumentTopology,
                "resolve",
                side_effect=TaskDocumentRefError("task-document-not-found", "missing"),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "lost its canonical sprint"),
        ):
            lifecycle.contract_queue_binding(self.contract)

        with (
            mock.patch.object(lifecycle, "_stored_queue_binding", return_value=None),
            mock.patch.object(
                lifecycle,
                "_live_parent_refs",
                return_value=lifecycle._LiveParents(MASTER_A, SPRINT),
            ),
            mock.patch.object(
                TaskDocumentTopology,
                "resolve",
                side_effect=TaskDocumentRefError("task-document-not-found", "missing"),
            ),
        ):
            self.assertIsNone(lifecycle.contract_queue_binding(self.contract))

        no_graph = replace(
            self.graph.sprint,
            document=self.graph.sprint.document.model_copy(update={"executionGraph": None}),
        )
        with (
            mock.patch.object(lifecycle, "_stored_queue_binding", return_value=stored),
            mock.patch.object(
                lifecycle,
                "_live_parent_refs",
                return_value=lifecycle._LiveParents(MASTER_A, SPRINT),
            ),
            mock.patch.object(TaskDocumentTopology, "resolve", return_value=no_graph),
            self.assertRaisesRegex(CloseoutQueueError, "lost its executionGraph"),
        ):
            lifecycle.contract_queue_binding(self.contract)

        with (
            mock.patch.object(lifecycle, "resolve_terminal_leaf_doc", return_value=None),
            self.assertRaisesRegex(CloseoutQueueError, "no canonical leaf"),
        ):
            lifecycle.contract_queue_binding(self.contract)
        with (
            mock.patch.object(
                lifecycle,
                "_live_parent_refs",
                return_value=lifecycle._LiveParents(MASTER_A, SPRINT),
            ),
            mock.patch.object(lifecycle, "_graph_context", return_value=self.graph),
            mock.patch.object(
                TaskDocumentTopology,
                "parent",
                return_value=MASTER_A.model_copy(update={"path": "other/task.json"}),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "no longer belongs"),
        ):
            lifecycle.contract_queue_binding(self.contract)
        with (
            mock.patch.object(
                lifecycle,
                "_stored_queue_binding",
                return_value=lifecycle.QueueBinding(
                    SPRINT, LEAF_A.model_copy(update={"path": "other.json"})
                ),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "immutable queue binding"),
        ):
            lifecycle.contract_queue_binding(self.contract)

    def test_queue_bound_task_publication_refuses_a_disappeared_master_parent(self) -> None:
        publication = mock.Mock()
        with (
            mock.patch.object(
                lifecycle,
                "contract_queue_binding",
                return_value=lifecycle.QueueBinding(SPRINT, LEAF_A),
            ),
            mock.patch.object(TaskDocumentTopology, "parent", return_value=None),
            self.assertRaisesRegex(CloseoutQueueError, "no owning master"),
        ):
            lifecycle.publish_queue_bound_task_facts(
                self.contract,
                publication,
                topology_stable=True,
            )
        publication.assert_not_called()

    def test_live_parent_resolution_distinguishes_legacy_absence_from_bound_damage(self) -> None:
        topology = mock.Mock()
        topology.canonical_ref.side_effect = TaskDocumentRefError(
            "task-document-not-found", "missing"
        )
        self.assertIsNone(lifecycle._live_parent_refs(topology, self.contract, None))
        with self.assertRaisesRegex(CloseoutQueueError, "lost its canonical topology"):
            lifecycle._live_parent_refs(
                topology,
                self.contract,
                lifecycle.QueueBinding(SPRINT, LEAF_A),
            )
        topology.canonical_ref.side_effect = None
        topology.canonical_ref.return_value = MASTER_A
        topology.parent.return_value = None
        with self.assertRaisesRegex(CloseoutQueueError, "no longer has a sprint parent"):
            lifecycle._live_parent_refs(
                topology,
                self.contract,
                lifecycle.QueueBinding(SPRINT, LEAF_A),
            )
        self.assertIsNone(lifecycle._live_parent_refs(topology, self.contract, None))

    def test_claim_closeout_is_idempotent_and_refuses_state_blockers_and_contract_drift(
        self,
    ) -> None:
        owner = lifecycle._operation_owner(OPERATION_KEY)
        claimed = lifecycle._claim_closeout(self._context(), self.selected)
        self.assertEqual(
            (claimed.state, claimed.inFlightOwnerFingerprint),
            ("closeout-in-flight", owner),
        )
        self.assertIs(lifecycle._claim_closeout(self._context(claimed), claimed), claimed)
        with self.assertRaisesRegex(CloseoutQueueError, "selection-required"):
            lifecycle._claim_closeout(
                self._context(self.selected.model_copy(update={"state": "declared"})),
                self.selected.model_copy(update={"state": "declared"}),
            )
        with (
            mock.patch.object(lifecycle, "_candidate_blockers", return_value=["stale"]),
            self.assertRaisesRegex(CloseoutQueueError, "not-ready"),
        ):
            lifecycle._claim_closeout(self._context(), self.selected)
        moved = replace(
            self.contract,
            contract_path=self.contract.contract_path.with_name("other.md"),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "contract-mismatch"):
            lifecycle._claim_closeout(self._context(contract=moved), self.selected)

    def test_certify_closeout_is_idempotent_and_binds_exact_commits(self) -> None:
        owner = lifecycle._operation_owner(OPERATION_KEY)
        claimed = self.selected.model_copy(
            update={"state": "closeout-in-flight", "inFlightOwnerFingerprint": owner}
        )
        certified = claimed.model_copy(
            update={
                "state": "certified",
                "inFlightOwnerFingerprint": None,
                "closeoutCodeCommit": "a" * 40,
                "closeoutMemoryContentCommit": "b" * 40,
                "closeoutLedgerCommit": "c" * 40,
            }
        )
        with mock.patch.object(
            lifecycle,
            "closeout_door_candidate_evidence",
            return_value=mock.Mock(valid=True),
        ):
            self.assertIs(
                lifecycle._certify_closeout(self._context(certified), certified), certified
            )
        with self.assertRaisesRegex(CloseoutQueueError, "owner-mismatch"):
            lifecycle._certify_closeout(self._context(), self.selected)
        closed = self.fixture.close_contract(MASTER_A)
        with (
            mock.patch.object(lifecycle, "_post_closeout_blockers", return_value=[]),
            mock.patch.object(
                lifecycle,
                "curator_evidence",
                return_value=self.selected.memoryEvidence,
            ),
            mock.patch.object(
                lifecycle,
                "closeout_door_candidate_evidence",
                return_value=mock.Mock(valid=True),
            ),
        ):
            result = lifecycle._certify_closeout(self._context(claimed, contract=closed), claimed)
        self.assertEqual(
            (
                result.state,
                result.closeoutCodeCommit,
                result.closeoutMemoryContentCommit,
                result.closeoutLedgerCommit,
            ),
            (
                "certified",
                closed.code_commit,
                closed.memory_content_commit,
                closed.ledger_commit,
            ),
        )
        with (
            mock.patch.object(lifecycle, "_post_closeout_blockers", return_value=["stale"]),
            self.assertRaisesRegex(CloseoutQueueError, "certification-blocked"),
        ):
            lifecycle._certify_closeout(self._context(claimed, contract=closed), claimed)

    def test_graph_less_sprint_without_a_stored_binding_resolves_to_none(self) -> None:
        no_graph = replace(
            self.graph.sprint,
            document=self.graph.sprint.document.model_copy(update={"executionGraph": None}),
        )
        with (
            mock.patch.object(lifecycle, "_stored_queue_binding", return_value=None),
            mock.patch.object(
                lifecycle,
                "_live_parent_refs",
                return_value=lifecycle._LiveParents(MASTER_A, SPRINT),
            ),
            mock.patch.object(TaskDocumentTopology, "resolve", return_value=no_graph),
        ):
            self.assertIsNone(lifecycle.contract_queue_binding(self.contract))

    def test_integration_commit_blockers_name_every_exact_mismatch(self) -> None:
        candidate = self.selected.model_copy(
            update={
                "state": "certified",
                "closeoutCodeCommit": "a" * 40,
                "closeoutMemoryContentCommit": "b" * 40,
                "closeoutLedgerCommit": "c" * 40,
            }
        )
        contract = replace(self.contract, code_commit="a" * 40)
        with mock.patch.object(lifecycle, "commit_tree", return_value=candidate.candidateTree):
            self.assertEqual(
                lifecycle._integration_commit_blockers(
                    candidate, contract, "a" * 40, "b" * 40, "c" * 40
                ),
                [],
            )
        with mock.patch.object(lifecycle, "commit_tree", return_value="f" * 40):
            blockers = lifecycle._integration_commit_blockers(
                candidate, contract, "d" * 40, "e" * 40, "f" * 40
            )
        self.assertEqual(
            blockers,
            [
                "integration-code-commit-not-certified",
                "integration-memory-commit-not-certified",
                "integration-ledger-commit-not-certified",
                "integration-code-tree-not-certified",
            ],
        )

    def test_operation_key_and_internal_event_are_exact_and_bounded(self) -> None:
        self.assertEqual(
            lifecycle._required_operation_key(f" {OPERATION_KEY} ", "closeout"),
            OPERATION_KEY,
        )
        for value in ("", "g" * 64, "a" * 63):
            with self.subTest(value=value), self.assertRaisesRegex(CloseoutQueueError, "64-hex"):
                lifecycle._required_operation_key(value, "closeout")
        first = lifecycle._internal_event("claim-closeout", "request", {"b": 2, "a": 1})
        second = lifecycle._internal_event("claim-closeout", "request", {"a": 1, "b": 2})
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.actor, "lifecycle-operation")
