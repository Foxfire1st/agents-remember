"""One terminal-readiness contract for task documents and their public writers."""

from __future__ import annotations

from collections import Counter

from agents_remember.models.task_document import (
    RESOLVED_MASTER_ROW_STATUSES,
    CompletionBlocker,
)

from .document import TaskDocument

MasterRowIdentity = tuple[str, str]


def master_is_terminal(doc: TaskDocument) -> bool:
    """Whether a commanded master has reached a terminal decision.

    Two terminal routes, and they are deliberately not the same test:

    - ``Completed`` -- its work landed and finished, so every declared row is resolved.
    - ``abandoned`` -- its work was deliberately not taken. Abandonment does **not** resolve the
      rows: on an atomic master whose remaining leaves were never started those rows stay
      ``planning`` on purpose, which is why this cannot collapse into
      ``status == "Completed" and not completion_blockers(doc)``.

    Read this instead of re-deriving it. ``status != "Completed"`` was a complete test only while
    the vocabulary was closed; it now also matches a master that was deliberately dropped.
    """

    if doc.status == "abandoned":
        return True
    return doc.status == "Completed" and not completion_blockers(doc)


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
            if ref.status not in RESOLVED_MASTER_ROW_STATUSES
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
        (ref.number, ref.file)
        for ref in original.subTasks
        if ref.status not in RESOLVED_MASTER_ROW_STATUSES
    )
    available = Counter((ref.number, ref.file) for ref in candidate.subTasks)
    return sorted((unresolved - available).elements())
