"""Projection-only closeout queue status and idempotent rebuild."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.scheduling_mode import commanded_sprint_masters

from .closeout_projection import (
    capture_projection_source,
    now_iso,
)
from .closeout_projection_publication import (
    rebuild_action,
    refresh_closeout_projection,
)
from .closeout_queue_errors import CloseoutQueueError


@dataclass(frozen=True)
class QueueActor:
    role: str
    task_document_ref: TaskDocumentRef


def closeout_queue_tool(
    config: McpRuntimeConfig,
    request: CloseoutQueueRequest,
    *,
    actor: QueueActor,
    now: str | None = None,
) -> dict[str, Any]:
    """Inspect or rebuild one disposable projection from canonical sources only."""

    sprint_ref = request.sprint_task_document_ref
    _authorize_projection_access(config, sprint_ref, actor)
    timestamp = (now or now_iso()).strip()
    if not timestamp:
        raise CloseoutQueueError("closeout-projection-time-invalid", "timestamp must not be blank")
    if request.action == "rebuild":
        refresh_closeout_projection(
            config.coordination_root,
            sprint_ref,
            timestamp=timestamp,
        )
    source = capture_projection_source(
        config.coordination_root,
        sprint_ref,
        timestamp=timestamp,
    )
    state = CloseoutQueueStore(config.coordination_root, sprint_ref).read_effective(
        timestamp=timestamp,
        source=source.identity,
    )
    first_ready = next(
        (member.generationId for member in state.members if member.classification == "ready"),
        None,
    )
    next_action = rebuild_action(sprint_ref) if state.serviceCondition == "invalid-empty" else None
    return {
        "ok": True,
        "operation": "closeout_queue",
        "action": request.action,
        "state": state.serviceCondition,
        "summary": _summary(state.serviceCondition, len(state.members), source.identity.readable),
        "sprintTaskDocumentRef": sprint_ref.model_dump(mode="json"),
        "revision": state.revision,
        "sourceClassification": state.sourceClassification,
        "sourceFingerprint": state.sourceFingerprint,
        "effectiveSourceFingerprint": source.identity.fingerprint,
        "sourceProblems": [problem.model_dump(mode="json") for problem in state.sourceProblems],
        "members": [member.model_dump(mode="json") for member in state.members],
        "firstReadyGenerationId": first_ready,
        "nextAction": next_action,
        "updatedAt": state.updatedAt,
    }


def require_first_ready_generation(
    coordination_root: Path,
    *,
    sprint_ref: TaskDocumentRef,
    generation_id: str,
) -> None:
    """Admission fence used only while the task/door CAS mutex is held."""

    timestamp = now_iso()
    source = capture_projection_source(coordination_root, sprint_ref, timestamp=timestamp)
    state = CloseoutQueueStore(coordination_root, sprint_ref).read_effective(
        timestamp=timestamp,
        source=source.identity,
    )
    if state.serviceCondition != "valid-built":
        raise CloseoutQueueError(
            "closeout-projection-invalid-empty",
            f"closeout admission requires an exact-current valid-built projection; "
            f"run {rebuild_action(sprint_ref)}",
        )
    ready = [member for member in state.members if member.classification == "ready"]
    if not ready or ready[0].generationId != generation_id:
        observed = ready[0].generationId if ready else "none"
        raise CloseoutQueueError(
            "closeout-door-not-first-ready",
            f"door generation {generation_id} is not first-ready; observed {observed}",
        )


def _authorize_projection_access(
    config: McpRuntimeConfig,
    sprint_ref: TaskDocumentRef,
    actor: QueueActor,
) -> None:
    if _is_sprint_planning_actor(actor, sprint_ref):
        return
    if actor.role != "manager":
        raise CloseoutQueueError(
            "closeout-projection-caller-refused",
            "projection access requires the sprint planning seat or a commanded manager",
        )
    manager_refs = _commanded_manager_refs(config, sprint_ref)
    if actor.task_document_ref not in manager_refs:
        raise CloseoutQueueError(
            "closeout-projection-caller-refused",
            "manager projection access requires one exact commanded master document",
        )


def _is_sprint_planning_actor(actor: QueueActor, sprint_ref: TaskDocumentRef) -> bool:
    return (actor.task_document_ref, actor.role) in {
        (sprint_ref, "architect"),
        (sprint_ref, "strategist"),
        (sprint_ref, "orchestrator"),
    }


def _commanded_manager_refs(
    config: McpRuntimeConfig,
    sprint_ref: TaskDocumentRef,
) -> set[TaskDocumentRef]:
    topology = TaskDocumentTopology(config.coordination_root)
    try:
        sprint = topology.resolve(sprint_ref)
        masters = commanded_sprint_masters(topology, sprint)
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(exc.status, str(exc)) from exc
    return {master.ref for master in masters}


def _summary(condition: str, members: int, source_readable: bool) -> str:
    if condition == "valid-built":
        return f"Exact-current closeout projection contains {members} waiting door generations."
    reason = "canonical source is unreadable" if not source_readable else "source identity changed"
    return f"Closeout projection is invalid-empty because the {reason}; rebuild before admission."


__all__ = [
    "CloseoutQueueError",
    "CloseoutQueueRequest",
    "QueueActor",
    "closeout_queue_tool",
    "require_first_ready_generation",
]
