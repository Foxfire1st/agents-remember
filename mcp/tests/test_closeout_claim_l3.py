"""L3 journal-first claim transfer, retry, and projection-independence forcing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    LifecycleOperationCandidateBinding,
    lifecycle_operation_candidate,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlCommand,
    control_operation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.worktree_contract import load_contract
from closeout_input_test_support import (
    closeout_operation_input,
    start_closeout_operation,
)
from test_closeout_queue import MASTER_A, SPRINT, QueueFixture


class CloseoutClaimTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temporary.name), memory_mode="internal")
        self.fixture.declare(MASTER_A)
        self.contract = load_contract(self.fixture.contracts[MASTER_A].contract_path)
        self.operation_input = closeout_operation_input(
            self.contract,
            config_path=self.fixture.config_path,
            memory=None,
            ledger=None,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _store(self) -> LifecycleOperationStore:
        return LifecycleOperationStore(
            operation_record_path(self.contract.worktree_group, "closeout")
        )

    def test_apply_creates_root_journal_then_claims_exact_door(self) -> None:
        projection = start_closeout_operation(self.operation_input, launcher=lambda *_: None)
        record = self._store().read()
        claimed = load_contract(self.contract.contract_path).closeout_door
        assert record is not None and claimed is not None
        assert record.doorPublication is not None
        self.assertEqual(record.status, "queued")
        self.assertEqual(claimed.disposition, "claimed")
        self.assertEqual(claimed.operationFingerprint, record.fingerprint)
        self.assertEqual(claimed.claimedOperationKey, record.operationKey)
        self.assertEqual(record.doorPublication.generation, claimed)
        self.assertEqual(len(projection.projectionEffects), 1)

    def test_projection_refresh_failure_is_advisory_and_does_not_gate_launch(self) -> None:
        launcher = mock.Mock(return_value=None)
        with mock.patch.object(
            lifecycle_operations,
            "refresh_closeout_projection",
            side_effect=OSError("disposable projection unavailable"),
        ):
            projection = start_closeout_operation(self.operation_input, launcher=launcher)
        launcher.assert_called_once()
        self.assertEqual(projection.status, "queued")
        self.assertEqual(projection.projectionEffects[0].rebuild.outcome, "not-attempted")
        self.assertIsNotNone(projection.projectionEffects[0].nextAction)

    def test_queue_deletion_after_claim_does_not_erase_operation_owner(self) -> None:
        launcher = mock.Mock(return_value=None)
        first = start_closeout_operation(self.operation_input, launcher=launcher)
        first_record = self._store().read()
        assert first_record is not None
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)
        queue.state_path.unlink(missing_ok=True)
        retried = start_closeout_operation(self.operation_input, launcher=launcher)
        record = self._store().read()
        assert record is not None
        self.assertEqual(retried.generation, first.generation)
        self.assertEqual(record.operationKey, first_record.operationKey)
        self.assertEqual(launcher.call_count, 1)

    def test_second_intent_cannot_rewrite_claimed_operation_identity(self) -> None:
        start_closeout_operation(self.operation_input, launcher=lambda *_: None)
        conflicting = self.operation_input.model_copy(update={"approvalNote": "different"})
        with self.assertRaisesRegex(RuntimeError, "conflicting closeout intent"):
            start_closeout_operation(conflicting, launcher=lambda *_: None)
        record = self._store().read()
        claimed = load_contract(self.contract.contract_path).closeout_door
        assert record is not None and claimed is not None
        self.assertEqual(claimed.operationFingerprint, record.fingerprint)

    def test_door_successor_identity_creates_a_distinct_operation_generation(self) -> None:
        door = self.contract.closeout_door
        assert door is not None
        first = lifecycle_operation_candidate(
            LifecycleOperationCandidateBinding(
                operation_input=self.operation_input,
                candidate_state="1" * 64,
                candidate_tree="2" * 40,
                closeout_door_generation_id=door.generationId,
            )
        )
        successor = lifecycle_operation_candidate(
            LifecycleOperationCandidateBinding(
                operation_input=self.operation_input,
                candidate_state="1" * 64,
                candidate_tree="2" * 40,
                closeout_door_generation_id="3" * 64,
            )
        )
        self.assertNotEqual(successor.fingerprint, first.fingerprint)

    def test_cancelled_claim_history_publishes_an_executable_successor(self) -> None:
        start_closeout_operation(self.operation_input, launcher=lambda *_: None)
        first = self._store().read()
        assert first is not None and first.doorPublication is not None
        claimed = first.doorPublication.generation

        control_operation(
            LifecycleControlCommand(
                admitted_contract=load_contract(self.contract.contract_path),
                admitted_location=require_matching_lifecycle_operation_location(self.contract),
                configured_authority=self.fixture.config_path.as_posix(),
                kind="closeout",
                action="cancel",
                expected_generation=first.generation,
                intent_note="cancel the exact claimed generation and reschedule it",
            )
        )
        cancelled = self._store().read()
        waiting = load_contract(self.contract.contract_path).closeout_door
        assert cancelled is not None and waiting is not None
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(waiting.disposition, "waiting")
        self.assertEqual(waiting.predecessorGenerationId, claimed.generationId)
        self.assertEqual(cancelled.doorPublicationHistory[-1].generation, claimed)

        successor_launch = mock.Mock(return_value=None)
        start_closeout_operation(self.operation_input, launcher=successor_launch)

        successor = self._store().read()
        claimed_successor = load_contract(self.contract.contract_path).closeout_door
        assert successor is not None and claimed_successor is not None
        self.assertEqual(successor.generation, first.generation + 1)
        self.assertEqual(successor.predecessorFingerprint, first.fingerprint)
        self.assertEqual(claimed_successor.generationId, waiting.generationId)
        self.assertEqual(claimed_successor.disposition, "claimed")
        successor_launch.assert_called_once()
