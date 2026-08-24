"""Lease-serialized transfer from claimed source authority to integration journal."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import IntegrationPublicationIntent
from agents_remember.worktrees.integration.integration_publication_fence import (
    IntegrationDoorAuthorityConflict,
    classify_integration_door_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_lease import (
    contract_lifecycle_lease,
)
from agents_remember.worktrees.integration.organizational_completion_integration import (
    transfer_integration_claim,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


def transfer_and_publish_integration_claim(
    contract: WorktreeContract,
    args: WorktreeArgs,
    intent: IntegrationPublicationIntent,
    *,
    commits: tuple[str, str, str],
) -> IntegrationPublicationIntent:
    """Persist journal intent and prove its exact door/journal source authority."""

    with contract_lifecycle_lease(contract):
        current = load_contract(contract.contract_path)
        authority = classify_integration_door_authority(current, intent)
        if not authority.valid:
            raise IntegrationDoorAuthorityConflict(authority)
        report_operation_progress(
            args,
            "source-merge",
            current_command="persist exact integration publication intent",
            irreversible_boundary=True,
            recovery_commits={
                "codeCommit": commits[0],
                "memoryContentCommit": commits[1],
                "ledgerCommit": commits[2],
            },
            integration_publication=intent.model_dump(mode="json"),
        )
        proven = transfer_integration_claim(current, intent, commits=commits)
        if proven != intent:
            report_operation_progress(
                args,
                "source-merge",
                current_command="prove claimed source journal transferred to integration",
                irreversible_boundary=True,
                integration_publication=proven.model_dump(mode="json"),
            )
        return proven
