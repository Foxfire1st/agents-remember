"""Read-only effective status for each disposable sprint closeout projection."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import (
    CloseoutCandidateNode,
    CloseoutProjectionProblemNode,
    CloseoutQueueNode,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _iter_task_document_payloads,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, TaskDocument
from agents_remember.worktrees.queue.closeout_projection import capture_projection_source


def read_closeout_queues(coordination_root: Path, *, now: datetime) -> list[CloseoutQueueNode]:
    """Project every sprint master's closeout queue from its durable artifact."""
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    queues: list[CloseoutQueueNode] = []
    for path, payload in _iter_task_document_payloads(tasks_root, now=now):
        if payload.get("kind") != "master":
            continue
        if not payload.get("orchestrates") or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        queues.append(_project_queue(coordination_root, doc, path, now=now))
    return queues


def _project_queue(
    coordination_root: Path,
    doc: TaskDocument,
    path: Path,
    *,
    now: datetime,
) -> CloseoutQueueNode:
    sprint_ref = TaskDocumentRef(
        repository=doc.repo,
        path=path.relative_to(path.parents[1]).as_posix(),
    )
    timestamp = now.replace(microsecond=0).isoformat()
    source = capture_projection_source(coordination_root, sprint_ref, timestamp=timestamp)
    state = CloseoutQueueStore(coordination_root, sprint_ref).read_effective(
        timestamp=timestamp,
        source=source.identity,
    )
    return CloseoutQueueNode(
        sprintRef=sprint_ref,
        revision=state.revision,
        serviceCondition=state.serviceCondition,
        sourceClassification=state.sourceClassification,
        sourceFingerprint=state.sourceFingerprint,
        sourceProblems=[
            CloseoutProjectionProblemNode(**problem.model_dump(mode="json"))
            for problem in state.sourceProblems
        ],
        members=[_candidate_node(member) for member in state.members],
    )


def _candidate_node(candidate) -> CloseoutCandidateNode:
    return CloseoutCandidateNode(
        generationId=candidate.generationId,
        taskDocumentRef=candidate.taskDocumentRef,
        owningMaster=candidate.owningMaster,
        classification=candidate.classification,
        priority=candidate.priority,
        order=candidate.order,
        reasons=candidate.reasons,
    )
