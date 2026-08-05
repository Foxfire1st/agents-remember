"""One terminal-readiness contract for task documents and their public writers."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict

from .document import DocStatus, StepStatus, SubTaskRef, TaskDocument

CompletionUnitStatus = StepStatus | DocStatus
MasterRowIdentity = tuple[str, str]


class CompletionBlocker(BaseModel):
    """One exact declared work unit that prevents terminal completion."""

    model_config = ConfigDict(extra="forbid")

    id: str
    parentId: str | None = None
    title: str
    status: CompletionUnitStatus


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


def completed_master_rows_to_validate(
    candidate: TaskDocument,
    *,
    original: TaskDocument | None,
    targeted_number: str | None,
) -> list[SubTaskRef]:
    """Rows whose terminal claim is new, explicitly targeted, or in a terminal master."""
    completed = [ref for ref in candidate.subTasks if ref.status == "Completed"]
    if candidate.status == "Completed":
        return completed
    if targeted_number is not None:
        return [ref for ref in completed if ref.number == targeted_number]

    original_counts = Counter(
        (ref.number, ref.file)
        for ref in (original.subTasks if original is not None else [])
        if ref.status == "Completed"
    )
    candidate_counts: Counter[MasterRowIdentity] = Counter()
    changed: list[SubTaskRef] = []
    for ref in completed:
        identity = (ref.number, ref.file)
        candidate_counts[identity] += 1
        if candidate_counts[identity] > original_counts[identity]:
            changed.append(ref)
    return changed
