"""Typed state carried from integration preflight into protected publication."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.enforcement import GateGuard
from agents_remember.models.lifecycles.operation import IntegrationPublicationIntent
from agents_remember.worktrees.integration.integration_operation_authority import (
    require_plane_integration_operation,
)
from agents_remember.worktrees.integration.integration_operation_decision import (
    classify_integration_operation,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.integration.organizational_completion import (
    OrganizationalCompletionPublicationError,
    classify_organizational_master_completion,
    publish_organizational_master_completion,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class IntegratePreview:
    """The evaluated seam guard and the planned altitude-routed quality gate."""

    guard: GateGuard
    handover_warning: dict[str, object] | None
    quality_gate: dict[str, object]


@dataclass(frozen=True)
class IntegrationPublication:
    """Every preflight fact the irreversible publication must re-verify."""

    contract: WorktreeContract
    args: WorktreeArgs
    locked_args: WorktreeArgs
    sources: IntegrationSources
    commits: IntegratedCommits
    intent: IntegrationPublicationIntent
    quality_gate: dict[str, object]
    handover_warning: dict[str, object] | None


def protected_integration_decision(
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> WorktreeCommandResult | None:
    """Reclassify all live evidence before any protected publication."""

    record = require_plane_integration_operation(contract, args)
    decision = classify_integration_operation(contract, record).decision
    return WorktreeCommandResult(2, decision) if decision is not None else None


def publish_journaled_organizational_completion(
    result: WorktreeCommandResult | None,
    intent: IntegrationPublicationIntent,
) -> WorktreeCommandResult | None:
    """Publish or classify the exact journal-owned task completion bytes."""

    organizational = intent.organizationalCompletion
    if result is None or result.returncode != 0 or organizational is None:
        return result
    try:
        publish_organizational_master_completion(organizational)
    except OrganizationalCompletionPublicationError as error:
        classification = classify_organizational_master_completion(organizational)
        if not classification.mechanically_convergent:
            return WorktreeCommandResult(2, classification.decision_payload())
        return WorktreeCommandResult(
            2,
            {
                "state": "organizational-completion-publication-interrupted",
                "reason": error.detail,
                "summary": error.detail,
                "nextAction": "recover",
                "expected": error.expected,
                "observed": error.observed,
            },
        )
    return result
