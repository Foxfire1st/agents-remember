"""Publish abandonment's native outcome, task stage and mirrored master row together."""

from __future__ import annotations

from agents_remember.worktrees.attention_terminal_notices import (
    begin_abandoned_notice,
    confirm_notice,
    discard_notice,
)
from agents_remember.worktrees.integration.terminal_abandon_commit import terminal_abandon_commit
from agents_remember.worktrees.services import TerminalGuard
from agents_remember.worktrees.task_fact_publication import publish_contract_task_facts
from agents_remember.worktrees.task_stage import plan_task_stage
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract


def publish_abandoned_task(
    contract: WorktreeContract, updated: WorktreeContract, guard: TerminalGuard
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Commit the native outcome before confirming its best-effort notification.

    The native outcome receipt is the guard's last commit operation. Before it succeeds,
    failures restore the contract, task and receipt; afterwards the native fact cannot roll
    back. Notification failures never govern that transaction, and an interruption after its
    commit leaves the pending notice available for recovery.
    """

    change = plan_task_stage(contract, "abandoned")
    with terminal_abandon_commit(contract, updated) as commit:
        occurrence = begin_abandoned_notice(updated)

        def publish() -> None:
            change.publish()
            write_contract(contract.contract_path, updated)

        def restore() -> None:
            commit.restore()
            write_contract(contract.contract_path, contract)
            change.restore()

        try:
            publication = publish_contract_task_facts(
                contract,
                lambda: guard.complete(
                    outcome="abandoned", publish=publish, rollback_publish=restore, commit=commit
                ),
                documents=change.candidates,
                validate=change.validate,
            )
        except BaseException:
            if not commit.committed:
                discard_notice(updated, occurrence)
            raise
    confirm_notice(updated, occurrence)
    return publication.result, [
        effect.model_dump(by_alias=True) for effect in publication.projection_effects
    ]
