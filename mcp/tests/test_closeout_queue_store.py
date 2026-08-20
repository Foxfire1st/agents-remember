from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.closeout_queue_records import (
    CloseoutQueuePendingTransaction,
)
from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    CloseoutQueueStoreError,
    QueueTransaction,
    queue_store_paths,
)
from agents_remember.models.queue.closeout_queue import CloseoutQueueState
from agents_remember.models.task_document_ref import TaskDocumentRef

NOW = "2026-08-15T00:00:00+00:00"
SPRINT = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
OTHER_SPRINT = TaskDocumentRef(repository="repo-a", path="other/task.json")
HEX64 = "a" * 64


def _state(
    *,
    sprint: TaskDocumentRef = SPRINT,
    revision: int = 0,
    closed: bool = False,
) -> CloseoutQueueState:
    return CloseoutQueueState(
        sprintTaskDocumentRef=sprint,
        revision=revision,
        graphRevision=HEX64,
        closed=closed,
        updatedAt=NOW,
    )


def _status_pending(
    state: CloseoutQueueState,
    *,
    completed: bool,
) -> CloseoutQueuePendingTransaction:
    fingerprint = hashlib.sha256(state.model_dump_json(exclude_none=True).encode()).hexdigest()
    return CloseoutQueuePendingTransaction(
        transactionKind="sprint-status",
        requestId="status",
        requestFingerprint=fingerprint,
        action="reclaim-sprint",
        recordedAt=NOW,
        actor="task-doc",
        previousRevision=state.revision - 1,
        sprintCompleted=completed,
        state=state,
    )


class CloseoutQueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CloseoutQueueStore(self.root, SPRINT)
        self.store.task_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.task_path.write_text('{"status":"inProgress"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_state(self, state: CloseoutQueueState) -> None:
        self.store.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.state_path.write_text(state.model_dump_json(exclude_none=True), encoding="utf-8")

    def _write_pending(self, pending: CloseoutQueuePendingTransaction) -> None:
        self.store.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.pending_path.write_text(
            pending.model_dump_json(exclude_none=True), encoding="utf-8"
        )

    def test_paths_are_confined_and_existence_includes_pending(self) -> None:
        escaped = TaskDocumentRef.model_construct(repository="repo-a", path="../../task.json")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "escapes"):
            queue_store_paths(self.root, escaped)
        self.assertFalse(self.store.exists())
        self.store.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.pending_path.write_text("", encoding="utf-8")
        self.assertTrue(self.store.exists())

    def test_read_refuses_wrong_initial_and_wrong_persisted_sprint(self) -> None:
        with self.assertRaisesRegex(CloseoutQueueStoreError, "initial.*different sprint"):
            self.store.read(_state(sprint=OTHER_SPRINT))
        self._write_state(_state(sprint=OTHER_SPRINT))
        with self.assertRaisesRegex(CloseoutQueueStoreError, "state belongs.*different"):
            self.store.read(_state())

    def test_state_and_pending_io_fail_closed(self) -> None:
        self.store.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.state_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "invalid closeout queue state"):
            self.store.read(_state())
        with (
            mock.patch.object(Path, "read_text", side_effect=OSError("state unreadable")),
            self.assertRaisesRegex(CloseoutQueueStoreError, "invalid closeout queue state"),
        ):
            self.store.read(_state())

        self.store.state_path.unlink()
        self.store.pending_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "invalid pending"):
            self.store.read(_state())
        self.store.pending_path.write_text(" \n", encoding="utf-8")
        self.assertEqual(self.store.read(_state()).revision, 0)
        with (
            mock.patch.object(Path, "read_text", side_effect=OSError("pending unreadable")),
            self.assertRaisesRegex(CloseoutQueueStoreError, "cannot read"),
        ):
            self.store.read(_state())

    def test_task_publication_bootstrap_refuses_malformed_survival_state(self) -> None:
        self.store.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.state_path.write_text("{}\n", encoding="utf-8")
        publication = mock.Mock()
        with self.assertRaisesRegex(CloseoutQueueStoreError, "invalid closeout queue state"):
            self.store.publish_task_facts_update(
                publication,
                owning_master=OTHER_SPRINT,
                topology_stable=True,
            )
        publication.assert_not_called()

    def test_task_publication_recovers_initial_status_wal_without_survival_state(self) -> None:
        pending = _status_pending(_state(revision=1, closed=True), completed=True)
        self._write_pending(pending)
        publication = mock.Mock(return_value="published")
        self.assertEqual(
            self.store.publish_task_facts_update(
                publication,
                owning_master=OTHER_SPRINT,
                topology_stable=True,
            ),
            "published",
        )
        publication.assert_called_once_with()
        self.assertFalse(self.store.pending_path.exists())
        self.assertEqual(self.store.read(_state()).revision, 0)

    def test_pending_must_follow_current_revision_and_exact_state(self) -> None:
        initial = _state()
        updated = _state(revision=1)
        pending = _status_pending(updated.model_copy(update={"closed": True}), completed=True)
        other_pending = _status_pending(
            _state(sprint=OTHER_SPRINT, revision=1, closed=True),
            completed=True,
        )
        self._write_pending(other_pending)
        with self.assertRaisesRegex(CloseoutQueueStoreError, "different sprint"):
            self.store.read(initial)

        self._write_state(_state(revision=1, closed=True))
        conflicting = _status_pending(
            pending.state.model_copy(update={"updatedAt": "later"}),
            completed=True,
        )
        self._write_pending(conflicting)
        self.store.task_path.write_text('{"status":"Completed"}\n', encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "conflicts"):
            self.store.read(initial)

        self._write_state(_state(revision=3))
        self._write_pending(pending)
        with self.assertRaisesRegex(CloseoutQueueStoreError, "does not follow"):
            self.store.read(initial)

    def test_sprint_status_pending_is_discarded_when_task_publication_did_not_land(self) -> None:
        pending = _status_pending(_state(revision=1, closed=True), completed=True)
        self._write_pending(pending)
        recovered = self.store.read(_state())
        self.assertEqual(recovered.revision, 0)
        self.assertFalse(self.store.pending_path.exists())

    def test_pending_status_recovers_before_and_after_state_publication(self) -> None:
        self.store.task_path.write_text('{"status":"Completed"}\n', encoding="utf-8")
        target = _state(revision=1, closed=True)
        pending = _status_pending(target, completed=True)
        self._write_pending(pending)
        self.assertEqual(self.store.read(_state()), target)
        self.assertFalse(self.store.pending_path.exists())

        self._write_state(target)
        self._write_pending(pending)
        self.assertEqual(self.store.read(_state()), target)
        self.assertFalse(self.store.pending_path.exists())

    def test_noninitial_pending_cannot_reconstruct_a_lost_survival_record(self) -> None:
        pending = _status_pending(_state(revision=2, closed=True), completed=True)
        self._write_pending(pending)
        with self.assertRaisesRegex(CloseoutQueueStoreError, "lost its canonical state"):
            self.store.publish_sprint_update(
                lambda: None,
                completed=True,
                recorded_at=NOW,
                validate_completion=lambda: None,
            )

    def test_sprint_task_read_failure_blocks_status_recovery(self) -> None:
        pending = _status_pending(_state(revision=1, closed=True), completed=True)
        self._write_pending(pending)
        self.store.task_path.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "cannot recover"):
            self.store.read(_state())
        self.store.task_path.unlink()
        with self.assertRaisesRegex(CloseoutQueueStoreError, "cannot recover"):
            self.store.read(_state())

    def test_retry_receipt_is_persisted_for_noop_and_reuse_is_exact(self) -> None:
        event = QueueTransaction(
            action="withdraw",
            request_id="same",
            fingerprint="b" * 64,
            recorded_at=NOW,
            actor="manager",
        )
        first = self.store.transact(initial=_state(), event=event, transform=lambda state: state)
        self.assertEqual((first.revision, len(first.appliedRequests)), (1, 1))
        retried = self.store.transact(
            initial=_state(),
            event=event,
            transform=lambda _state: self.fail("retry re-applied"),
        )
        self.assertEqual(retried, first)
        with self.assertRaisesRegex(CloseoutQueueStoreError, "different payload"):
            self.store.transact(
                initial=_state(),
                event=QueueTransaction(
                    action="withdraw",
                    request_id="same",
                    fingerprint="c" * 64,
                    recorded_at=NOW,
                    actor="manager",
                ),
                transform=lambda state: state,
            )

    def test_closed_sprint_refuses_governed_task_facts(self) -> None:
        self._write_state(_state(closed=True))
        with self.assertRaisesRegex(CloseoutQueueStoreError, "must reopen"):
            self.store.publish_task_facts_update(
                lambda: None,
                owning_master=OTHER_SPRINT,
                topology_stable=True,
            )
