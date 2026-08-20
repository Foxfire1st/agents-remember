"""Bounded, lock-protected persistence for one sprint closeout queue."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from agents_remember.controlplane.closeout_queue_records import (
    CloseoutQueuePendingTransaction,
)
from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.queue.closeout_queue import (
    AppliedQueueRequest,
    CloseoutQueueState,
    QueueEventAction,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

QUEUE_REQUEST_RECEIPT_LIMIT = 128
T = TypeVar("T")

QUEUE_OWNERSHIP = StoreOwnership(
    store="sprint-closeout-candidate-artifact",
    writers=("mcp", "lifecycle-operation"),
    compaction_owner="mcp",
    rationale=(
        "the MCP owns public mutation, bounded retry-receipt eviction, and quiescent sprint "
        "closure; the detached lifecycle-operation worker owns claim/certify/consume/recovery "
        "transitions under the same lock; the adjacent one-record WAL is "
        "recoverable publication scratch, never the survival record"
    ),
)


class CloseoutQueueStoreError(RuntimeError):
    """The durable queue state or its one-record write-ahead transaction is invalid."""


@dataclass(frozen=True)
class QueueTransaction:
    action: QueueEventAction
    request_id: str
    fingerprint: str
    recorded_at: str
    actor: str
    rationale: str = ""


def queue_store_paths(coordination_root: Path, sprint_ref: TaskDocumentRef) -> tuple[Path, Path]:
    """Locate the canonical sprint artifact and its one recoverable publication scratch file."""

    sprint_path = (coordination_root / "tasks" / sprint_ref.repository / sprint_ref.path).resolve(
        strict=False
    )
    repository_root = (coordination_root / "tasks" / sprint_ref.repository).resolve(strict=False)
    if not sprint_path.is_relative_to(repository_root):
        raise CloseoutQueueStoreError("sprint closeout queue path escapes its task repository")
    root = sprint_path.parent / "artifacts"
    return root / "closeout-candidates.json", root / ".closeout-candidates.pending"


class CloseoutQueueStore:
    """Canonical sprint artifact plus one-record WAL; cost is independent of event history."""

    def __init__(self, coordination_root: Path, sprint_ref: TaskDocumentRef) -> None:
        self.sprint_ref = sprint_ref
        self.state_path, self.pending_path = queue_store_paths(coordination_root, sprint_ref)
        self.task_path = (
            coordination_root / "tasks" / sprint_ref.repository / sprint_ref.path
        ).resolve(strict=False)

    def exists(self) -> bool:
        return self.state_path.is_file() or self.pending_path.is_file()

    def read(self, initial: CloseoutQueueState) -> CloseoutQueueState:
        return self.inspect(initial, lambda state: state)

    def inspect(
        self,
        initial: CloseoutQueueState,
        reader: Callable[[CloseoutQueueState], T],
    ) -> T:
        """Read and evaluate dependent canonical facts under the queue's shared lock."""

        QUEUE_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.state_path, QUEUE_OWNERSHIP):
            return reader(self._recover(initial))

    def transact(
        self,
        *,
        initial: CloseoutQueueState,
        event: QueueTransaction,
        transform: Callable[[CloseoutQueueState], CloseoutQueueState],
    ) -> CloseoutQueueState:
        QUEUE_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.state_path, QUEUE_OWNERSHIP):
            current = self._recover(initial)
            if self._request_was_applied(current, event):
                return current
            candidate = transform(current)
            return self._commit_transaction(current, candidate, event)

    def transact_with_publication(
        self,
        *,
        initial: CloseoutQueueState,
        event: QueueTransaction,
        transform: Callable[[CloseoutQueueState], CloseoutQueueState],
        publication: Callable[[], None],
    ) -> CloseoutQueueState:
        """Publish dependent durable facts before committing their queue transition.

        The transform is validated under the queue lock before publication. If the process
        dies after publication but before the queue WAL is written, retry sees the old queue
        state, proves the same transition again, and republishes idempotently. The inverse
        ordering would lose the only candidate that identifies a still-closed contract.
        """

        QUEUE_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.state_path, QUEUE_OWNERSHIP):
            current = self._recover(initial)
            if self._request_was_applied(current, event):
                return current
            candidate = transform(current)
            publication()
            return self._commit_transaction(current, candidate, event)

    @staticmethod
    def _request_was_applied(current: CloseoutQueueState, event: QueueTransaction) -> bool:
        receipt = next(
            (item for item in current.appliedRequests if item.requestId == event.request_id),
            None,
        )
        if receipt is None:
            return False
        if receipt.fingerprint != event.fingerprint:
            raise CloseoutQueueStoreError(
                "closeout queue request id was reused with a different payload"
            )
        return True

    def _commit_transaction(
        self,
        current: CloseoutQueueState,
        candidate: CloseoutQueueState,
        event: QueueTransaction,
    ) -> CloseoutQueueState:
        next_revision = current.revision + 1
        receipts = [
            *current.appliedRequests,
            AppliedQueueRequest(
                requestId=event.request_id,
                fingerprint=event.fingerprint,
                revision=next_revision,
            ),
        ][-QUEUE_REQUEST_RECEIPT_LIMIT:]
        updated = CloseoutQueueState.model_validate(
            {
                **candidate.model_dump(mode="json"),
                "revision": next_revision,
                "appliedRequests": receipts,
                "updatedAt": event.recorded_at,
            }
        )
        pending = CloseoutQueuePendingTransaction(
            transactionKind="queue-mutation",
            requestId=event.request_id,
            requestFingerprint=event.fingerprint,
            action=event.action,
            recordedAt=event.recorded_at,
            actor=event.actor,
            rationale=event.rationale.strip(),
            previousRevision=current.revision,
            state=updated,
        )
        atomic_write_text(self.pending_path, pending.model_dump_json(exclude_none=True) + "\n")
        self._publish(updated)
        self._clear_pending_best_effort()
        return updated

    def publish_sprint_update(
        self,
        publication: Callable[[], T],
        *,
        completed: bool,
        recorded_at: str,
        validate_completion: Callable[[], None],
    ) -> T:
        """Serialize sprint publication with queue closure or quiescent reopen."""

        QUEUE_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.state_path, QUEUE_OWNERSHIP):
            initial = self._initial_from_existing_artifact()
            current = self._recover(initial) if initial is not None else None
            if completed:
                validate_completion()
            if (
                completed
                and current is not None
                and (current.candidates or current.activeBlocker is not None)
            ):
                raise CloseoutQueueStoreError(
                    "a sprint cannot complete while closeout candidates or a blocker remain"
                )
            if (
                not completed
                and current is not None
                and (
                    current.activeBlocker is not None
                    or any(
                        candidate.state != "declared" for candidate in current.candidates.values()
                    )
                )
            ):
                raise CloseoutQueueStoreError(
                    "sprint task facts are frozen while a candidate or atomic blocker owns the landing lane"
                )
            if current is not None and current.closed != completed:
                target = current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "updatedAt": recorded_at,
                        "appliedRequests": [],
                        "closed": completed,
                    }
                )
                request_id = f"sprint-status:{current.revision}:{str(completed).lower()}"
                fingerprint = hashlib.sha256(
                    target.model_dump_json(exclude_none=True).encode()
                ).hexdigest()
                pending = CloseoutQueuePendingTransaction(
                    transactionKind="sprint-status",
                    requestId=request_id,
                    requestFingerprint=fingerprint,
                    action="reclaim-sprint",
                    recordedAt=recorded_at,
                    actor="task-doc",
                    rationale="serialize canonical sprint completion or reopen",
                    previousRevision=current.revision,
                    sprintCompleted=completed,
                    state=target,
                )
                atomic_write_text(
                    self.pending_path,
                    pending.model_dump_json(exclude_none=True) + "\n",
                )
            result = publication()
            if current is not None and current.closed != completed:
                self._recover(current)
            return result

    def publish_task_facts_update(
        self,
        publication: Callable[[], T],
        *,
        owning_master: TaskDocumentRef,
        topology_stable: bool,
    ) -> T:
        """Publish a governed master/leaf edit under the sprint queue lock."""

        QUEUE_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.state_path, QUEUE_OWNERSHIP):
            initial = self._initial_from_existing_artifact()
            current = self._recover(initial) if initial is not None else None
            if current is not None and current.closed:
                raise CloseoutQueueStoreError(
                    "a completed sprint queue must reopen before governed task facts can change"
                )
            lane_owner = (
                next(
                    (
                        candidate
                        for candidate in current.candidates.values()
                        if candidate.state != "declared"
                    ),
                    None,
                )
                if current is not None
                else None
            )
            # A task-doc publication can synchronize more than the addressed
            # document (for example leaf -> master status). Freeze the whole
            # sprint fact set during the short selected/in-flight lane so the
            # candidate's final graph/readiness proof cannot race a side write.
            lane_blocks = lane_owner is not None
            blocker_blocks = (
                current is not None
                and current.activeBlocker is not None
                and (current.activeBlocker.master != owning_master or not topology_stable)
            )
            if lane_blocks or blocker_blocks:
                raise CloseoutQueueStoreError(
                    "sprint task facts are frozen while a candidate or atomic blocker owns the landing lane"
                )
            return publication()

    def _recover(self, initial: CloseoutQueueState) -> CloseoutQueueState:
        self._require_initial(initial)
        state = self._read_state(initial)
        pending = self._read_pending()
        if pending is None:
            return state
        if pending.state.sprintTaskDocumentRef != self.sprint_ref:
            raise CloseoutQueueStoreError(
                "pending closeout queue transaction belongs to a different sprint"
            )
        if (
            pending.transactionKind == "sprint-status"
            and self._sprint_completed() != pending.sprintCompleted
        ):
            self._clear_pending_best_effort()
            return state
        if state.revision == pending.previousRevision:
            self._publish(pending.state)
            state = pending.state
        elif state.revision == pending.state.revision:
            if state != pending.state:
                raise CloseoutQueueStoreError(
                    "published queue state conflicts with its pending transaction"
                )
        else:
            raise CloseoutQueueStoreError(
                "pending closeout queue transaction does not follow the current revision"
            )
        self._clear_pending_best_effort()
        return state

    def _read_state(self, initial: CloseoutQueueState) -> CloseoutQueueState:
        if not self.state_path.is_file():
            return initial
        try:
            state = CloseoutQueueState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise CloseoutQueueStoreError(f"invalid closeout queue state: {exc}") from exc
        if state.sprintTaskDocumentRef != self.sprint_ref:
            raise CloseoutQueueStoreError(
                "closeout queue state belongs to a different sprint task document"
            )
        return state

    def _read_pending(self) -> CloseoutQueuePendingTransaction | None:
        if not self.pending_path.is_file():
            return None
        try:
            text = self.pending_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CloseoutQueueStoreError(f"cannot read closeout queue transaction: {exc}") from exc
        if not text.strip():
            return None
        try:
            return CloseoutQueuePendingTransaction.model_validate_json(text)
        except ValidationError as exc:
            raise CloseoutQueueStoreError(
                f"invalid pending closeout queue transaction: {exc}"
            ) from exc

    def _publish(self, state: CloseoutQueueState) -> None:
        atomic_write_text(
            self.state_path,
            state.model_dump_json(exclude_none=True, indent=2) + "\n",
        )

    def _clear_pending_best_effort(self) -> None:
        with suppress(OSError):
            self.pending_path.unlink(missing_ok=True)

    def _initial_from_existing_artifact(self) -> CloseoutQueueState | None:
        if self.state_path.is_file():
            try:
                return CloseoutQueueState.model_validate_json(
                    self.state_path.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError) as exc:
                raise CloseoutQueueStoreError(f"invalid closeout queue state: {exc}") from exc
        pending = self._read_pending()
        if pending is None:
            return None
        if pending.previousRevision != 0:
            raise CloseoutQueueStoreError(
                "closeout queue lost its canonical state before a non-initial pending transaction"
            )
        return pending.state.model_copy(
            update={
                "revision": 0,
                "candidates": {},
                "activeBlocker": None,
                "appliedRequests": [],
                "closed": False,
            }
        )

    def _require_initial(self, initial: CloseoutQueueState) -> None:
        if initial.sprintTaskDocumentRef != self.sprint_ref:
            raise CloseoutQueueStoreError(
                "initial closeout queue state belongs to a different sprint task document"
            )

    def _sprint_completed(self) -> bool:
        try:
            payload = json.loads(self.task_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CloseoutQueueStoreError(
                f"cannot recover sprint-status transaction from canonical task document: {exc}"
            ) from exc
        return payload.get("status") == "Completed"
