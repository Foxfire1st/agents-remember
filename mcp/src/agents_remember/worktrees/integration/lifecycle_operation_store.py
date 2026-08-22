"""Strict, atomic enclosure-local storage for long lifecycle operations."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationRecord,
    LifecycleOperationStatus,
)
from agents_remember.worktrees.integration.closeout_recovery_projection import (
    closeout_generation_retained,
    require_closeout_finalization_evidence,
    require_closeout_recovery_projection,
)

_OWNERSHIP = StoreOwnership(
    store="lifecycle-operation",
    writers=("mcp", "lifecycle-operation"),
    compaction_owner=None,
    rationale="the MCP starts operations and its detached worker advances the same record",
)
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_ALLOWED: dict[LifecycleOperationStatus, frozenset[LifecycleOperationStatus]] = {
    "queued": frozenset({"queued", "running", "input-required", "failed", "cancelled"}),
    "running": frozenset(
        {"queued", "running", "input-required", "completed", "failed", "cancelled"}
    ),
    "input-required": frozenset({"queued", "input-required", "cancelled"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}
_MUTATION_ALLOWED = {
    "pre-mutation": frozenset({"pre-mutation", "mutation-intent"}),
    "mutation-intent": frozenset({"mutation-intent", "reconciled-unchanged", "commit-proven"}),
    "reconciled-unchanged": frozenset({"reconciled-unchanged"}),
    "commit-proven": frozenset({"commit-proven"}),
}


def _validate_recovery_commits_transition(
    current: LifecycleOperationRecord,
    updated: LifecycleOperationRecord,
) -> None:
    current_commits = current.recoveryCommits
    if current_commits is None:
        return
    if updated.recoveryCommits is None:
        raise RuntimeError("recorded lifecycle recovery commits cannot be cleared")
    for field in ("codeCommit", "memoryContentCommit", "ledgerCommit"):
        before = getattr(current_commits, field)
        after = getattr(updated.recoveryCommits, field)
        if before and after != before:
            raise RuntimeError("recorded lifecycle recovery commits can only fill empty cells")


def _validate_quality_certification_transition(
    current: LifecycleOperationRecord,
    updated: LifecycleOperationRecord,
) -> None:
    before = current.qualityCertification
    after = updated.qualityCertification
    if before is not None and after != before:
        raise RuntimeError("recorded integration quality certification is immutable")
    if after is not None and current.operationKind != "integrate":
        raise RuntimeError("only integration operations may record quality certification")


def _validate_queue_completion_transition(
    current: LifecycleOperationRecord,
    updated: LifecycleOperationRecord,
) -> None:
    before = current.queueCompletion
    after = updated.queueCompletion
    if before is not None and after != before:
        raise RuntimeError("recorded integration queue completion is immutable")
    if after is not None and current.operationKind != "integrate":
        raise RuntimeError("only integration operations may record queue completion")


def _validate_organizational_repair_transition(
    current: LifecycleOperationRecord,
    updated: LifecycleOperationRecord,
) -> None:
    before = current.organizationalRepair
    after = updated.organizationalRepair
    if before is not None and after != before:
        raise RuntimeError("recorded organizational repair evidence is immutable")


def _validate_mutation_evidence_transition(
    current: LifecycleOperationRecord,
    updated: LifecycleOperationRecord,
) -> None:
    for leg, before in current.mutationEvidence.items():
        after = updated.mutationEvidence[leg]
        if after.leg != before.leg or after.repository != before.repository:
            raise RuntimeError("closeout mutation evidence identity is immutable")
        if after.state not in _MUTATION_ALLOWED[before.state]:
            raise RuntimeError(
                f"invalid closeout mutation evidence transition {before.state} -> {after.state}"
            )
        if before.before is not None and after.before != before.before:
            raise RuntimeError("closeout pre-command Git evidence is immutable")
        if before.observed is not None and after.observed != before.observed:
            raise RuntimeError("closeout observed Git evidence is immutable once recorded")
        if (
            before.expectedOutputTree is not None
            and after.expectedOutputTree != before.expectedOutputTree
        ):
            raise RuntimeError("closeout expected output tree is immutable once recorded")


def _validate_closeout_finalization_transition(
    current: LifecycleOperationRecord,
    updated: LifecycleOperationRecord,
) -> None:
    before = current.closeoutFinalizedContractSha256
    after = updated.closeoutFinalizedContractSha256
    if before is not None and after != before:
        raise RuntimeError("closeout finalized contract SHA-256 is immutable once recorded")
    if before is None and after is not None and updated.phase != "contract-finalization":
        raise RuntimeError(
            "closeout finalized contract SHA-256 must be introduced at contract-finalization"
        )
    require_closeout_finalization_evidence(updated)


def operation_record_path(worktree_group: Path, operation_kind: LifecycleOperationKind) -> Path:
    return worktree_group / "reports" / f"{operation_kind}-operation.json"


def operation_report_path(worktree_group: Path, operation_kind: LifecycleOperationKind) -> Path:
    return worktree_group / "reports" / f"{operation_kind}-operation.log"


class LifecycleOperationStore:
    """One validated current snapshot per task and operation kind."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> LifecycleOperationRecord | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("schemaVersion") != "3.0":
                raise RuntimeError(
                    "legacy lifecycle operation record cannot be resumed through the normal "
                    "reader: migrate or retire it through the explicit lifecycle-record "
                    "migration task"
                )
            return LifecycleOperationRecord.model_validate(payload)
        except RuntimeError:
            raise
        except (json.JSONDecodeError, OSError, ValidationError) as error:
            raise RuntimeError(
                f"invalid lifecycle operation record {self.path}: {error}"
            ) from error

    def create(self, record: LifecycleOperationRecord) -> tuple[LifecycleOperationRecord, bool]:
        with exclusive_access(self.path, _OWNERSHIP):
            current = self.read()
            if current is not None:
                return current, False
            self._write(record)
            return record, True

    def update(
        self,
        transform: Callable[[LifecycleOperationRecord], LifecycleOperationRecord],
    ) -> LifecycleOperationRecord:
        with exclusive_access(self.path, _OWNERSHIP):
            current = self.read()
            if current is None:
                raise RuntimeError(f"lifecycle operation record does not exist: {self.path}")
            updated = LifecycleOperationRecord.model_validate(
                transform(current).model_dump(mode="json")
            )
            self._validate_transition(current, updated)
            self._write(updated)
            return updated

    def replace_for_recovery(
        self,
        transform: Callable[[LifecycleOperationRecord], LifecycleOperationRecord],
        *,
        expected_attempt: int,
    ) -> tuple[LifecycleOperationRecord, bool]:
        """Requeue only a stale or exactly restored operation under the same lock and identity."""
        with exclusive_access(self.path, _OWNERSHIP):
            current = self.read()
            if current is not None and current.attempt != expected_attempt:
                return current, False
            if current is None or current.status == "completed":
                raise RuntimeError("a completed lifecycle operation cannot recover in place")
            restored_failure = (
                current.status == "failed"
                and isinstance(current.result, dict)
                and current.result.get("safeToReplace") is True
            )
            past_boundary = (
                closeout_generation_retained(current)
                if current.operationKind == "closeout"
                else current.irreversibleBoundaryEntered
            )
            if current.status in {"failed", "cancelled"} and past_boundary and not restored_failure:
                raise RuntimeError("a terminal operation past its boundary cannot restart")
            updated = LifecycleOperationRecord.model_validate(
                transform(current).model_dump(mode="json")
            )
            if updated.fingerprint != current.fingerprint:
                raise RuntimeError("lifecycle operation recovery cannot change its fingerprint")
            if updated.operationKey != current.operationKey or updated.input != current.input:
                raise RuntimeError("lifecycle operation recovery cannot change durable input")
            _validate_mutation_evidence_transition(current, updated)
            require_closeout_recovery_projection(updated)
            _validate_closeout_finalization_transition(current, updated)
            self._write(updated)
            return updated, True

    def replace_terminal(self, candidate: LifecycleOperationRecord) -> LifecycleOperationRecord:
        """Publish a new sequential attempt only after the previous attempt is terminal."""
        with exclusive_access(self.path, _OWNERSHIP):
            current = self.read()
            if current is None or current.status not in _TERMINAL:
                raise RuntimeError("an active lifecycle operation cannot be replaced")
            for field in ("taskId", "taskName", "contractPath", "operationKind"):
                if getattr(candidate, field) != getattr(current, field):
                    raise RuntimeError(f"a sequential lifecycle operation cannot change {field}")
            validated = LifecycleOperationRecord.model_validate(
                candidate.model_copy(update={"attempt": current.attempt + 1}).model_dump(
                    mode="json"
                )
            )
            self._write(validated)
            return validated

    def _write(self, record: LifecycleOperationRecord) -> None:
        _OWNERSHIP.check_declared_writer()
        validated = LifecycleOperationRecord.model_validate(record.model_dump(mode="json"))
        require_closeout_recovery_projection(validated)
        require_closeout_finalization_evidence(validated)
        atomic_write_text(
            self.path,
            validated.model_dump_json(indent=2, exclude_none=True) + "\n",
        )

    @staticmethod
    def _validate_transition(
        current: LifecycleOperationRecord, updated: LifecycleOperationRecord
    ) -> None:
        immutable = (
            "taskId",
            "taskName",
            "contractPath",
            "operationKind",
            "candidateState",
            "candidateTree",
            "fingerprint",
            "integrationAuthority",
        )
        for field in immutable:
            if getattr(current, field) != getattr(updated, field):
                raise RuntimeError(f"lifecycle operation transition cannot change {field}")
        if updated.operationKey != current.operationKey or updated.input != current.input:
            raise RuntimeError("lifecycle operation transition cannot change its durable input")
        if updated.status not in _ALLOWED[current.status]:
            raise RuntimeError(
                f"invalid lifecycle operation transition {current.status} -> {updated.status}"
            )
        if current.approvalClaimed and not updated.approvalClaimed:
            raise RuntimeError("a claimed approval cannot become unclaimed")
        _validate_recovery_commits_transition(current, updated)
        _validate_quality_certification_transition(current, updated)
        _validate_queue_completion_transition(current, updated)
        _validate_organizational_repair_transition(current, updated)
        _validate_mutation_evidence_transition(current, updated)
        require_closeout_recovery_projection(updated)
        _validate_closeout_finalization_transition(current, updated)
        if current.irreversibleBoundaryEntered and not updated.irreversibleBoundaryEntered:
            raise RuntimeError("an entered irreversible boundary cannot be cleared")
        closeout_ambiguous = current.operationKind == "closeout" and any(
            item.state == "mutation-intent" for item in current.mutationEvidence.values()
        )
        if updated.status == "cancelled" and closeout_ambiguous:
            raise RuntimeError("a closeout operation cannot cancel with ambiguous Git intent")
        if updated.status == "cancelled" and current.irreversibleBoundaryEntered:
            raise RuntimeError(
                "a lifecycle operation cannot cancel after its irreversible boundary"
            )
