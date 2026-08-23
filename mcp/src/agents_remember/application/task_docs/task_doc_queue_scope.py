"""Resolve the sprint queue that governs one task-document publication."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocSourceSnapshot, TaskDocument, json_path_for
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)


class QueueScopeError(ValueError):
    """A task document cannot resolve one unambiguous governing sprint queue."""


@dataclass(frozen=True)
class QueuePublicationScope:
    sprint_ref: TaskDocumentRef
    owning_master: TaskDocumentRef | None


@dataclass(frozen=True)
class PreparedQueuePublicationScope:
    """Prepared queue identity without promoting projection reads into task CAS."""

    scope: QueuePublicationScope | None
    source_snapshots: tuple[TaskDocSourceSnapshot, ...]


@dataclass(frozen=True)
class QueueScopePreparation:
    """Accepted task candidate and source generation used to derive queue scope."""

    coordination_root: Path
    repo_id: str
    task_root: Path
    original: TaskDocument | None
    candidate: TaskDocument
    source_snapshots: tuple[TaskDocSourceSnapshot, ...]


@dataclass(frozen=True)
class _ScopeContext:
    topology: TaskDocumentTopology
    repo_id: str
    task_root: Path
    repository_root: Path
    existing_path: Path
    existing: bool


def _single_scope(
    affected: Sequence[ResolvedTaskDocument], owning_master: TaskDocumentRef
) -> QueuePublicationScope | None:
    if len(affected) > 1:
        raise QueueScopeError(
            "task document edit affects multiple sprint queues; resolve its parent topology first"
        )
    return QueuePublicationScope(affected[0].ref, owning_master) if affected else None


def _master_edit_scope(
    topology: TaskDocumentTopology,
    master_ref: TaskDocumentRef,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> QueuePublicationScope | None:
    affected = topology.execution_sprints_affected_by_master(
        master_ref,
        original=original,
        candidate=candidate,
    )
    return _single_scope(affected, master_ref)


def _unchanged_master_scope(
    topology: TaskDocumentTopology, master_ref: TaskDocumentRef
) -> QueuePublicationScope | None:
    master = topology.resolve(master_ref).document
    return _master_edit_scope(topology, master_ref, master, master)


def _existing_scope(
    context: _ScopeContext,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> QueuePublicationScope | None:
    ref = context.topology.canonical_ref(context.repo_id, context.existing_path)
    existing = context.topology.resolve(ref)
    if existing.document.kind == "master":
        if existing.document.orchestrates or candidate.orchestrates:
            return QueuePublicationScope(ref, None)
        return _master_edit_scope(context.topology, ref, original, candidate)
    master_ref = _existing_ref(
        context.topology,
        context.repo_id,
        context.task_root / "task.json",
    )
    if master_ref is None:
        return None
    scope = _unchanged_master_scope(context.topology, master_ref)
    if scope is not None and context.topology.parent(ref) != master_ref:
        raise QueueScopeError("governed leaf does not resolve to its exact owning master")
    return scope


def _new_scope(
    context: _ScopeContext,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> QueuePublicationScope | None:
    if candidate.kind == "master":
        candidate_ref = TaskDocumentRef(
            repository=context.repo_id,
            path=context.existing_path.relative_to(context.repository_root).as_posix(),
        )
        return _master_edit_scope(context.topology, candidate_ref, original, candidate)
    master_ref = _existing_ref(
        context.topology,
        context.repo_id,
        context.task_root / "task.json",
    )
    if master_ref is None:
        return None
    return _unchanged_master_scope(context.topology, master_ref)


def _existing_ref(
    topology: TaskDocumentTopology,
    repo_id: str,
    path: Path,
) -> TaskDocumentRef | None:
    """Resolve one source through the topology's accepted snapshot set, including absence."""

    try:
        return topology.canonical_ref(repo_id, path.resolve(strict=False))
    except TaskDocumentRefError as exc:
        if exc.status == "task-document-not-found":
            return None
        raise


def prepare_governing_queue_scope(
    request: QueueScopePreparation,
) -> PreparedQueuePublicationScope:
    """Prepare queue scope entirely from one captured topology generation."""

    topology = TaskDocumentTopology(
        request.coordination_root,
        accepted_sources=request.source_snapshots,
    )
    repository_root = (request.coordination_root / "tasks" / request.repo_id).resolve(strict=False)
    existing_path = json_path_for(
        request.task_root,
        request.original or request.candidate,
    ).resolve(strict=False)
    context = _ScopeContext(
        topology,
        request.repo_id,
        request.task_root,
        repository_root,
        existing_path,
        request.original is not None,
    )
    try:
        scope = _resolve_governing_queue_scope(
            context,
            request.original,
            request.candidate,
        )
    except (TaskDocumentRefError, ValueError) as exc:
        raise QueueScopeError(f"cannot resolve governing sprint queue: {exc}") from exc
    return PreparedQueuePublicationScope(scope, request.source_snapshots)


def _resolve_governing_queue_scope(
    context: _ScopeContext,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> QueuePublicationScope | None:
    if candidate.kind == "light":
        return None
    if context.existing:
        return _existing_scope(context, original, candidate)
    return _new_scope(context, original, candidate)
