"""Integration-boundary payloads and the atomic-master review gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.route_review import (
    RouteReviewError,
    route_review_refusal_fields,
)
from agents_remember.worktrees.route_review_scope import require_current_master_route_review
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    write_contract,
)

if TYPE_CHECKING:
    from agents_remember.worktrees.modules.args import WorktreeArgs


def blocked_integration_payload(
    contract: WorktreeContract,
    state: str,
    reason: str,
    persist: bool = True,
    developer_decision_required: bool = True,
    **extra: object,
) -> dict[str, object]:
    """Persist and project one blocked integration decision."""

    blocked = amend_contract(contract, ContractCells(integration_status="blocked"))
    if persist:
        write_contract(blocked.contract_path, blocked)
    next_step: dict[str, object] = {"summary": reason}
    for key in ("nextOperation", "nextTool", "nextArgs", "nextRequiredArgs"):
        if key in extra:
            next_step[key] = extra[key]
    return {
        "state": state,
        **status_payload(blocked),
        "reason": reason,
        "summary": reason,
        "developerDecisionRequired": developer_decision_required,
        "nextStep": next_step,
        **extra,
    }


def master_route_review_block(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    expected_candidate_commit: str | None = None,
) -> WorktreeCommandResult | None:
    """Return structured guidance when the atomic-master review is not current."""

    # A leaf-to-master landing is the accumulation step. Its atomic child review
    # is intentionally deferred; only the canonical series contract's
    # master-to-parent movement owns this gate.
    if contract.kind != "series":
        return None
    try:
        require_current_master_route_review(
            contract,
            expected_candidate_commit=expected_candidate_commit or contract.code_commit,
        )
    except RouteReviewError as error:
        return master_route_review_refusal(contract, args, error)
    return None


def master_route_review_refusal(
    contract: WorktreeContract,
    args: WorktreeArgs,
    error: RouteReviewError,
) -> WorktreeCommandResult:
    """Project one caught review error through the shared R25 refusal authority."""

    refusal = route_review_refusal_fields(error, contract=contract, boundary="integration")
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            error.status,
            str(error),
            persist=not args.dry_run,
            developer_decision_required=refusal["nextAction"] == "developer-decision",
            reviewRefusal={"status": error.status, "detail": str(error)},
            **refusal,
        ),
    )


__all__ = [
    "blocked_integration_payload",
    "master_route_review_block",
    "master_route_review_refusal",
]
