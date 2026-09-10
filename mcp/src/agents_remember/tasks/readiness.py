"""One terminal-readiness contract for task documents and their public writers."""

from __future__ import annotations

from collections import Counter

from agents_remember.models.task_document import CompletionBlocker

from .document import TaskDocument

MasterRowIdentity = tuple[str, str]


def completion_blockers(doc: TaskDocument) -> list[CompletionBlocker]:
    """Return every unresolved declared unit; an empty document is ready vacuously."""
    if doc.kind == "master":
        return [
            CompletionBlocker(
                id=ref.number,
                parentId=doc.id,
                title=ref.name,
                status=ref.status,
            )
            for ref in doc.subTasks
            if ref.status != "Completed"
        ]

    blockers: list[CompletionBlocker] = []
    for step in doc.steps:
        if step.status != "done":
            blockers.append(
                CompletionBlocker(
                    id=step.id,
                    parentId=None,
                    title=step.title,
                    status=step.status,
                )
            )
        blockers.extend(
            CompletionBlocker(
                id=sub.id,
                parentId=step.id,
                title=sub.title,
                status=sub.status,
            )
            for sub in step.substeps
            if sub.status != "done"
        )
    return blockers


def missing_unresolved_master_rows(
    original: TaskDocument,
    candidate: TaskDocument,
) -> list[MasterRowIdentity]:
    """Unresolved ``(number, file)`` rows lost from a candidate, including duplicates."""
    unresolved = Counter(
        (ref.number, ref.file) for ref in original.subTasks if ref.status != "Completed"
    )
    available = Counter((ref.number, ref.file) for ref in candidate.subTasks)
    return sorted((unresolved - available).elements())
