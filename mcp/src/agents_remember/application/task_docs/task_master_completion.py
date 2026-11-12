"""Completing an organizational master through the task tool.

An atomic master's completion is proved by its closeout, which walks the landing chain. An
organizational master has no closeout and no landing chain: the edit that sets its status to
``Completed`` is the place where its completion is decided, so the rule that a label never
removes a landed leaf is kept here.

An abandoned row is a finished record of a leaf that will not run. It blocks nothing and is
asked for no enclosure, unless the enclosure it does have records a completed integration:
that row contradicts itself and is refused by name. A row in any other unfinished state is
refused by the ordinary completion blockers, not here.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.abandoned_row_landing import require_abandoned_rows_unlanded
from agents_remember.worktrees.scheduling_mode import effective_execution_nature

from .task_doc_route_review import TaskDocError


def require_master_completion(
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> None:
    """Refuse the edit that completes an organizational master over a landed abandoned row.

    Only the transition is checked, so a later edit of an already completed master is never
    refused for a row it does not touch.
    """

    if (
        candidate.kind != "master"
        or candidate.is_sprint
        or candidate.status != "Completed"
        or (original is not None and original.status == "Completed")
    ):
        return
    if not any(row.status == "abandoned" for row in candidate.subTasks):
        return
    if not _executes_organizationally(coordination_root, repo_id, task_root, original, candidate):
        return
    require_abandoned_rows_unlanded(
        task_root, candidate, then="set the master's status again", error=TaskDocError
    )


def _executes_organizationally(
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> bool:
    """Whether the candidate master has no closeout that would decide its completion.

    The declared nature rules, except under a sprint without an execution graph, where every
    commanded master executes atomically. The sprint is looked up only for a master that
    declares itself organizational, and an unreadable document elsewhere in the task tree is
    skipped rather than allowed to refuse this edit.
    """

    if candidate.executionNature != "organizational":
        return False
    tasks_root = (coordination_root / "tasks" / repo_id).resolve(strict=False)
    master_path = (task_root / "task.json").resolve(strict=False)
    if not master_path.is_relative_to(tasks_root):
        return True
    topology = TaskDocumentTopology(coordination_root)
    sprints = topology.projection_sprints_affected_by_master(
        TaskDocumentRef(repository=repo_id, path=master_path.relative_to(tasks_root).as_posix()),
        original=original,
        candidate=candidate,
    )
    try:
        nature = effective_execution_nature(
            candidate, sprints[0].document if len(sprints) == 1 else None
        )
    except TaskDocumentRefError:
        return False
    return nature == "organizational"
