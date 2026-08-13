"""Strict, atomic enclosure-local storage for long lifecycle operations."""

from __future__ import annotations

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

_OWNERSHIP = StoreOwnership(
    store="lifecycle-operation",
    writers=("mcp",),
    compaction_owner=None,
    rationale="the MCP starts operations and its detached worker advances the same record",
)
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_ALLOWED: dict[LifecycleOperationStatus, frozenset[LifecycleOperationStatus]] = {
    "queued": frozenset({"queued", "running", "failed", "cancelled"}),
    "running": frozenset(
        {"queued", "running", "input-required", "completed", "failed", "cancelled"}
    ),
    "input-required": frozenset({"queued", "input-required", "cancelled"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}


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
            return LifecycleOperationRecord.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
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
        """Requeue only a stale nonterminal operation under the same lock and identity."""
        with exclusive_access(self.path, _OWNERSHIP):
            current = self.read()
            if current is not None and current.attempt != expected_attempt:
                return current, False
            if current is None or current.status == "completed":
                raise RuntimeError("a completed lifecycle operation cannot recover in place")
            if current.status in {"failed", "cancelled"} and current.irreversibleBoundaryEntered:
                raise RuntimeError("a terminal operation past its boundary cannot restart")
            updated = LifecycleOperationRecord.model_validate(
                transform(current).model_dump(mode="json")
            )
            if updated.fingerprint != current.fingerprint:
                raise RuntimeError("lifecycle operation recovery cannot change its fingerprint")
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
        validated = LifecycleOperationRecord.model_validate(record.model_dump(mode="json"))
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
        if current.irreversibleBoundaryEntered and not updated.irreversibleBoundaryEntered:
            raise RuntimeError("an entered irreversible boundary cannot be cleared")
        if updated.status == "cancelled" and current.irreversibleBoundaryEntered:
            raise RuntimeError(
                "a lifecycle operation cannot cancel after its irreversible boundary"
            )
