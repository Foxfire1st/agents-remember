"""The task-document publication shared by worktree start and reattachment."""

from __future__ import annotations

from agents_remember.tasks import read_task_doc
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.task_fact_publication import (
    contract_projection_scopes,
    publish_task_fact_mutation,
)
from agents_remember.worktrees.task_leaf_binding import (
    TaskLeafBindingError,
    plan_current_leaf_enclosure_registration,
    require_current_leaf_enclosure_binding,
)
from agents_remember.worktrees.task_stage import TaskStageChange, plan_task_stage
from agents_remember.worktrees.worktree_contract import WorktreeContract


def publish_start_task_facts(
    contract: WorktreeContract, *, dry_run: bool = False, started: bool = True
) -> list[dict[str, object]]:
    """Bind a leaf and mirror its stage in one existing task-fact publication.

    Reattachment preserves stage. Start promotes planning even when the enclosure binding is
    already exact; retrying either operation is a no-op once all task facts agree.
    """
    if dry_run:
        return []
    change = TaskStageChange()

    def validate() -> None:
        nonlocal change
        source = None
        if contract.kind == "leaf" and contract.leaf_id:
            plan = plan_current_leaf_enclosure_registration(
                contract.coordination_root,
                contract.repo_name,
                contract.task_root,
                contract.leaf_id,
                contract.contract_path,
                lifecycle_id=contract.lifecycle_id or None,
                task_name=contract.task_name,
            )
            if plan.blockers:
                raise TaskLeafBindingError(
                    "leaf enclosure registration would republish terminal status with unresolved "
                    f"work units: {[blocker.model_dump() for blocker in plan.blockers]!r}",
                    status="task-enclosure-binding-terminal-blocked",
                    facts={
                        "leafId": plan.leaf_id,
                        "taskName": contract.task_name,
                        "taskDocument": plan.doc_path.as_posix(),
                        "contractPath": plan.enclosure_path,
                        "recoveryOperation": (
                            "resolve the task document's unfinished work units, then re-run "
                            "worktree_start/worktree_attach"
                        ),
                    },
                )
            source = (plan.doc_path, plan.candidate or read_task_doc(plan.doc_path))
        try:
            change = plan_task_stage(contract, "inProgress" if started else None, source=source)
        except ValueError as error:
            raise TaskLeafBindingError(str(error)) from error

    validate()

    def publish() -> None:
        change.publish()

    effects = []
    if change.documents:
        publication = publish_task_fact_mutation(
            contract.coordination_root,
            validate=validate,
            projection_scopes=lambda: contract_projection_scopes(contract, change.candidates),
            publication=publish,
        )
        effects = [effect.model_dump(by_alias=True) for effect in publication.projection_effects]
    if contract.kind == "leaf" and contract.leaf_id:
        require_current_leaf_enclosure_binding(
            contract.coordination_root,
            contract.repo_name,
            contract.task_root,
            contract.leaf_id,
            contract.contract_path,
            task_name=contract.task_name,
        )
    return effects


def task_start_authority_refusal(error: TaskLeafBindingError) -> WorktreeCommandResult:
    next_action = "re-read-task-authority"
    if error.status.startswith("task-enclosure-binding"):
        next_action = "repair-task-enclosure-binding"
    return WorktreeCommandResult(
        2,
        {
            "state": error.status,
            "status": error.status,
            "summary": error.detail,
            "detail": error.detail,
            **error.facts,
            # MIK-R111 rule 2: the recovery is a file read or a repair the
            # prose names, not a task_doc read call (removed). No nextTool.
            "nextAction": next_action,
        },
    )


def validate_start_task_stage(contract: WorktreeContract) -> None:
    """Prove task/parent stage publication before series bootstrap mutates anything."""
    try:
        plan_task_stage(contract, "inProgress")
    except ValueError as error:
        raise TaskLeafBindingError(str(error)) from error
