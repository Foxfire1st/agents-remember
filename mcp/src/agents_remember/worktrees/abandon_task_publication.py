"""Publish abandonment's contract, task stage and mirrored master row together."""

from __future__ import annotations

from agents_remember.worktrees.services import TerminalGuard
from agents_remember.worktrees.task_fact_publication import publish_contract_task_facts
from agents_remember.worktrees.task_stage import plan_task_stage
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract


def publish_abandoned_task(
    contract: WorktreeContract, updated: WorktreeContract, guard: TerminalGuard
) -> tuple[dict[str, object], list[dict[str, object]]]:
    change = plan_task_stage(contract, "abandoned")

    def publish() -> None:
        change.publish()
        write_contract(contract.contract_path, updated)

    def restore() -> None:
        write_contract(contract.contract_path, contract)
        change.restore()

    publication = publish_contract_task_facts(
        contract,
        lambda: guard.complete(outcome="abandoned", publish=publish, rollback_publish=restore),
        documents=change.candidates,
        validate=change.validate,
    )
    return publication.result, [
        effect.model_dump(by_alias=True) for effect in publication.projection_effects
    ]
