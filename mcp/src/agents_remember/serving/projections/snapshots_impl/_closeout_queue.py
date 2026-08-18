"""Closeout-queue projection: candidate states, grades, and the active atomic blocker.

The queue artifact is the durable source of truth (never task titles, numbering, labels, or
open terminals). This reader is strictly read-only: it reads each sprint master's queue state
file and projects the recorded candidate states, grades, waiting reasons it can state from the
artifact alone, and the active blocker. The dashboard combines this with the already-projected
``executionGraph``/``executionWaves``/``executionNature`` for the dependency picture; it never
re-runs the queue's contract/source/ledger revalidation (that is the queue tool's
declaration-time job).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agents_remember.models.closeout_queue import CloseoutQueueState
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import (
    AtomicBlockerNode,
    CloseoutCandidateNode,
    CloseoutQueueNode,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _iter_task_document_payloads,
    _read_json,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, TaskDocument


def read_closeout_queues(coordination_root: Path, *, now: datetime) -> list[CloseoutQueueNode]:
    """Project every sprint master's closeout queue from its durable artifact."""
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    queues: list[CloseoutQueueNode] = []
    for path, payload in _iter_task_document_payloads(tasks_root, now=now):
        if payload.get("kind") != "master":
            continue
        if payload.get("executionGraph") is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        queue = _project_queue(doc, path)
        if queue is not None:
            queues.append(queue)
    return queues


def _project_queue(doc: TaskDocument, path: Path) -> CloseoutQueueNode | None:
    payload = _read_json(path.parent / "artifacts" / "closeout-candidates.json")
    if payload is None:
        return None
    try:
        state = CloseoutQueueState.model_validate(payload)
    except ValueError:
        return None
    active_blocker = state.activeBlocker
    candidates = [
        _candidate_node(candidate, active_blocker) for candidate in state.candidates.values()
    ]
    candidates.sort(key=lambda item: item.taskDocumentRef.key)
    blocker = None
    if active_blocker is not None:
        blocker = AtomicBlockerNode(
            master=active_blocker.master,
            rationale=active_blocker.rationale,
            acquiredBy=active_blocker.acquiredBy,
        )
    return CloseoutQueueNode(
        sprintRef=TaskDocumentRef(
            repository=doc.repo, path=path.relative_to(path.parents[1]).as_posix()
        ),
        revision=state.revision,
        graphRevision=state.graphRevision,
        activeBlocker=blocker,
        candidates=candidates,
    )


def _candidate_node(candidate, active_blocker) -> CloseoutCandidateNode:
    reasons: list[str] = []
    if candidate.grade is None:
        reasons.append("explicit-grade-required")
    if active_blocker is not None and active_blocker.master != candidate.owningMaster:
        reasons.append(f"atomic-blocker-held-by: {active_blocker.master.key}")
    return CloseoutCandidateNode(
        taskDocumentRef=candidate.taskDocumentRef,
        owningMaster=candidate.owningMaster,
        candidateState=candidate.state,
        gradePriority=candidate.grade.priority if candidate.grade is not None else None,
        reasons=reasons,
    )
