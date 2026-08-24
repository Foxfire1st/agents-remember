"""Monotonic task-owned registration before typed seat/report evidence reclamation."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.task_publication_lock import task_publication_lock
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.tasks import (
    TaskDocSourceReadError,
    TaskDocSourceSnapshot,
    TaskDocument,
    TaskExecutionRegistration,
    read_task_doc_with_source,
    write_task_docs,
)
from agents_remember.worktrees.task_fact_publication import publish_task_fact_mutation

from .task_doc_publication import (
    TaskDocPublicationConflict,
    require_task_doc_sources_current,
)
from .task_doc_queue_scope import (
    TaskDocScopeChange,
    TaskDocScopeError,
    resolve_projection_scope_union,
)

TaskExecutionRegistrationStatus = Literal[
    "registered",
    "already-registered",
    "task-retired",
    "not-leaf",
    "blocked",
]
ExecutionRegistrationRole = Literal["worker", "reviewer", "curator"]


@dataclass(frozen=True)
class TaskExecutionRegistrationResult:
    status: TaskExecutionRegistrationStatus
    task_document_ref: TaskDocumentRef
    detail: str | None = None

    @property
    def durable_or_irrelevant(self) -> bool:
        return self.status in {
            "registered",
            "already-registered",
            "task-retired",
        }


@dataclass(frozen=True)
class _RegistrationSource:
    root: Path
    path: Path
    document: TaskDocument
    source: TaskDocSourceSnapshot


class _RegistrationRefusal(Exception):
    def __init__(
        self,
        status: Literal["blocked", "not-leaf"],
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.status: Literal["blocked", "not-leaf"] = status
        self.detail: str = detail


def register_task_execution_evidence(
    coordination_root: Path,
    task_document_ref: TaskDocumentRef,
    registration: TaskExecutionRegistration,
) -> TaskExecutionRegistrationResult:
    """Register one bounded first-observation marker before its source row is reclaimed."""

    try:
        root, path = _registration_address(coordination_root, task_document_ref)
        loaded = _load_registration_source(root, path)
    except _RegistrationRefusal as refusal:
        return TaskExecutionRegistrationResult(
            refusal.status,
            task_document_ref,
            refusal.detail,
        )
    if loaded is None:
        return _classify_missing_task_source(
            root,
            path,
            task_document_ref,
        )
    document = loaded.document
    source = loaded.source
    if document.kind != "subTask":
        return TaskExecutionRegistrationResult("not-leaf", task_document_ref)
    key = (registration.sourceKind, registration.role)
    if any((item.sourceKind, item.role) == key for item in document.executionRegistrations):
        return TaskExecutionRegistrationResult("already-registered", task_document_ref)
    candidate = document.model_copy(
        update={
            "executionRegistrations": [
                *document.executionRegistrations,
                registration,
            ]
        }
    )

    def projection_scopes() -> tuple[TaskDocumentRef, ...]:
        return resolve_projection_scope_union(
            root,
            task_document_ref.repository,
            (TaskDocScopeChange(task_document_ref, document, candidate),),
        )

    try:
        publish_task_fact_mutation(
            root,
            task_document_ref.repository,
            validate=lambda: require_task_doc_sources_current((source,)),
            projection_scopes=projection_scopes,
            publication=lambda: write_task_docs(path.parent, [candidate]),
        )
    except (OSError, ValueError, TaskDocScopeError, TaskDocPublicationConflict) as exc:
        return TaskExecutionRegistrationResult(
            "blocked",
            task_document_ref,
            f"execution registration publication failed: {type(exc).__name__}",
        )
    return TaskExecutionRegistrationResult("registered", task_document_ref)


def _registration_address(
    coordination_root: Path,
    task_document_ref: TaskDocumentRef,
) -> tuple[Path, Path]:
    root = coordination_root.resolve(strict=False)
    repository_root = (root / "tasks" / task_document_ref.repository).resolve(strict=False)
    path = repository_root / task_document_ref.path
    if (
        not path.is_relative_to(repository_root)
        or path.suffix != ".json"
        or path.name == "task.json"
    ):
        raise _RegistrationRefusal(
            "not-leaf",
            "execution registration addresses only an exact JSON-primary leaf task",
        )
    try:
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise _RegistrationRefusal(
            "blocked",
            f"task source address cannot be resolved exactly: {type(exc).__name__}",
        ) from exc
    if resolved_path != path:
        raise _RegistrationRefusal(
            "blocked",
            "task source address crosses a symbolic-link boundary",
        )
    return root, path


def _regular_file_mode(path: Path, label: str) -> int | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _RegistrationRefusal(
            "blocked",
            f"{label} inspection failed: {type(exc).__name__}",
        ) from exc
    if not stat.S_ISREG(mode):
        raise _RegistrationRefusal(
            "blocked",
            f"{label} is present but is not a regular file",
        )
    return mode


def _load_registration_source(root: Path, path: Path) -> _RegistrationSource | None:
    if _regular_file_mode(path, "task source") is None:
        return None
    _regular_file_mode(path.with_suffix(".md"), "rendered task source")
    try:
        document, source = read_task_doc_with_source(path)
    except (OSError, UnicodeError, ValueError, TaskDocSourceReadError) as exc:
        raise _RegistrationRefusal(
            "blocked",
            f"task source is unreadable: {type(exc).__name__}",
        ) from exc
    return _RegistrationSource(root, path, document, source)


def _classify_missing_task_source(
    coordination_root: Path,
    path: Path,
    task_document_ref: TaskDocumentRef,
) -> TaskExecutionRegistrationResult:
    """Prove under the task CAS that a missing child no longer has a live parent row."""

    with task_publication_lock(coordination_root, task_document_ref.repository):
        try:
            _require_task_source_still_missing(path)
            parent = _load_retirement_parent(path.parent / "task.json")
            detail = _retirement_detail(parent, path, task_document_ref)
        except _RegistrationRefusal as refusal:
            return TaskExecutionRegistrationResult(
                refusal.status,
                task_document_ref,
                refusal.detail,
            )
        return TaskExecutionRegistrationResult(
            "task-retired",
            task_document_ref,
            detail,
        )


def _require_task_source_still_missing(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _RegistrationRefusal(
            "blocked",
            f"missing task source cannot be rechecked: {type(exc).__name__}",
        ) from exc
    raise _RegistrationRefusal(
        "blocked",
        "task source changed while its retirement state was being proved",
    )


def _load_retirement_parent(parent_path: Path) -> TaskDocument:
    if _regular_file_mode(parent_path, "canonical parent source") is None:
        raise _RegistrationRefusal(
            "blocked",
            "missing child has no durable canonical parent removal proof",
        )
    try:
        parent, _source = read_task_doc_with_source(parent_path)
    except (OSError, UnicodeError, ValueError, TaskDocSourceReadError) as exc:
        raise _RegistrationRefusal(
            "blocked",
            f"canonical parent source is unreadable: {type(exc).__name__}",
        ) from exc
    if parent.kind != "master":
        raise _RegistrationRefusal(
            "blocked",
            "missing leaf source is not owned by a canonical master",
        )
    return parent


def _retirement_detail(
    parent: TaskDocument,
    path: Path,
    task_document_ref: TaskDocumentRef,
) -> str:
    child_markdown = path.with_suffix(".md").name
    if any(row.file == child_markdown for row in parent.subTasks):
        raise _RegistrationRefusal(
            "blocked",
            "child bytes are absent but the exact parent row remains live",
        )
    matching_audit = any(
        audit.file == child_markdown and audit.proof.taskDocumentRef == task_document_ref
        for audit in parent.discardedSubTasks
    )
    return (
        "matching parent discard audit proves the task retired"
        if matching_audit
        else "exact parent row absence proves the task retired"
    )


def _registration_role(value: object) -> ExecutionRegistrationRole | None:
    if value == "worker":
        return "worker"
    if value == "reviewer":
        return "reviewer"
    if value == "curator":
        return "curator"
    return None


def register_terminal_catalog_execution_evidence(
    coordination_root: Path,
    entries: tuple[TerminalCatalogEntry, ...],
) -> frozenset[str]:
    """Return catalog row ids safe to reclaim after task-owned registration."""

    reclaimable: set[str] = set()
    for entry in entries:
        role = _registration_role(entry.binding_role)
        if role is None:
            continue
        refs = {
            ref
            for ref in (
                entry.task_document_ref,
                entry.replacement_for_task_document_ref,
            )
            if ref is not None
        }
        if not refs:
            continue
        results = [
            register_task_execution_evidence(
                coordination_root,
                ref,
                TaskExecutionRegistration(
                    sourceKind="terminal-catalog-seat",
                    role=role,
                    sourceId=entry.id,
                    observedAt=entry.created_at,
                ),
            )
            for ref in refs
        ]
        if all(result.durable_or_irrelevant for result in results):
            reclaimable.add(entry.id)
    return frozenset(reclaimable)


def register_operator_inbox_execution_evidence(
    coordination_root: Path,
    entries: tuple[OperatorInboxEntry, ...],
) -> frozenset[str]:
    """Return turn-report ids safe to reclaim after task-owned registration."""

    reclaimable: set[str] = set()
    for entry in entries:
        role = _registration_role(entry.senderRole)
        if entry.messageKind != "turn-report" or role is None:
            continue
        refs = {
            ref for ref in (entry.subjectTaskDocumentRef, entry.taskDocumentRef) if ref is not None
        }
        if not refs:
            continue
        results = [
            register_task_execution_evidence(
                coordination_root,
                ref,
                TaskExecutionRegistration(
                    sourceKind="operator-inbox-turn-report",
                    role=role,
                    sourceId=entry.id,
                    observedAt=entry.createdAt,
                ),
            )
            for ref in refs
        ]
        if all(result.durable_or_irrelevant for result in results):
            reclaimable.add(entry.id)
    return frozenset(reclaimable)
