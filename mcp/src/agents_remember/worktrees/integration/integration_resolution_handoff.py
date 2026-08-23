"""Executable task-addressed handoff for reversible integration source drift."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegrationSources,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


def integration_resolution_required(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    operation: LifecycleOperationRecord | None,
) -> WorktreeCommandResult:
    """Return the exact preview/apply cancellation control for one accepted generation."""
    if operation is None:
        summary = (
            "The selected source moved after this leaf closed. A dry-run has no admitted "
            "integration generation to cancel; admit the operation before requesting "
            "task-addressed resolution."
        )
        return WorktreeCommandResult(
            2,
            {
                "state": "integration-resolution-planning-required",
                "reason": summary,
                "summary": summary,
                "developerDecisionRequired": True,
                "code_replay_required": sources.code_replay_required,
                "memory_replay_required": sources.memory_replay_required,
                "nextOperation": "admit_integration_before_resolution",
            },
        )
    if args.operation_key and args.operation_key != operation.operationKey:
        raise RuntimeError("integration resolution operation key changed after admission")
    if args.operation_generation and args.operation_generation != operation.generation:
        raise RuntimeError("integration resolution generation changed after admission")
    generation = operation.generation
    conflict = (
        operation.integrationAuthority.conflictTransaction
        if operation.integrationAuthority is not None
        else None
    )
    summary = (
        "The selected source moved after this leaf closed. Integration will not create an "
        "untested replay commit. Cancel this pre-boundary operation, resolve the exact "
        "source delta in the recorded leaf worktree, and produce a new targeted closeout."
    )
    cancel_note = (
        "Cancel the stale pre-boundary integration so the owning leaf can absorb the "
        "recorded source delta and produce a new targeted closeout."
    )
    preview_args = {
        "contract_path": contract.contract_path.as_posix(),
        "operation_kind": "integrate",
        "action": "cancel",
        "expected_generation": generation,
        "intent_note": cancel_note,
        "dry_run": True,
    }
    apply_args = {**preview_args, "dry_run": False}
    return WorktreeCommandResult(
        2,
        {
            "state": "integration-resolution-required",
            "reason": summary,
            "summary": summary,
            "developerDecisionRequired": True,
            "conflictTransaction": (
                conflict.model_dump(mode="json") if conflict is not None else None
            ),
            "code_replay_required": sources.code_replay_required,
            "memory_replay_required": sources.memory_replay_required,
            "nextOperation": "preview_cancel_before_leaf_refresh",
            "nextTool": "worktree_operation_control",
            "nextArgs": preview_args,
            "applyStep": {
                "summary": cancel_note,
                "nextOperation": "cancel_stale_integration",
                "nextTool": "worktree_operation_control",
                "nextArgs": apply_args,
            },
            "resolutionSteps": [
                {
                    "operation": "preview_cancel_before_leaf_refresh",
                    "tool": "worktree_operation_control",
                    "args": preview_args,
                },
                {
                    "operation": "cancel_stale_integration",
                    "tool": "worktree_operation_control",
                    "args": apply_args,
                },
            ],
            "nextStep": {
                "summary": summary,
                "nextOperation": "preview_cancel_before_leaf_refresh",
                "nextTool": "worktree_operation_control",
                "nextArgs": preview_args,
            },
        },
    )
