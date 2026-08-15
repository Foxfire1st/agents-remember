"""Resolve the sprint queue that governs one task-document publication."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, json_path_for
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
class _ScopeContext:
    topology: TaskDocumentTopology
    repo_id: str
    task_root: Path
    repository_root: Path
    existing_path: Path


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
    parent_path = (context.task_root / "task.json").resolve(strict=False)
    if not parent_path.is_file():
        return None
    master_ref = context.topology.canonical_ref(context.repo_id, parent_path)
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
    parent_path = (context.task_root / "task.json").resolve(strict=False)
    if not parent_path.is_file():
        return None
    master_ref = context.topology.canonical_ref(context.repo_id, parent_path)
    return _unchanged_master_scope(context.topology, master_ref)


def governing_queue_scope(
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> QueuePublicationScope | None:
    """Return the sole governing sprint queue, or None for a genuinely ungoverned task."""

    if candidate.kind == "light":
        return None
    topology = TaskDocumentTopology(coordination_root)
    repository_root = (coordination_root / "tasks" / repo_id).resolve(strict=False)
    existing_path = json_path_for(task_root, original or candidate).resolve(strict=False)
    context = _ScopeContext(topology, repo_id, task_root, repository_root, existing_path)
    try:
        if existing_path.is_file():
            return _existing_scope(context, original, candidate)
        return _new_scope(context, original, candidate)
    except (TaskDocumentRefError, ValueError) as exc:
        raise QueueScopeError(f"cannot resolve governing sprint queue: {exc}") from exc
