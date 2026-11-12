"""Read-only enclosure plan records, contract projections and preview results."""

from __future__ import annotations

from dataclasses import dataclass, replace

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import ensure_worktree
from agents_remember.worktrees.modules.guidance import recovery_guidance
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class StartContractPlan:
    """A leaf and the same request's owner-validated, preview-only parent."""

    contract: WorktreeContract
    preview_parent: WorktreeContract | None = None


@dataclass(frozen=True)
class _PreparedStartEnclosure:
    contract: WorktreeContract
    code_state: str
    memory_state: dict[str, object]
    provider_plan: dict[str, object]


@dataclass(frozen=True)
class _StartEnclosurePlan:
    contract: WorktreeContract
    memory_preview: dict[str, object]
    provider_plan: dict[str, object]


def _blocked_memory_start_result(
    context, args: WorktreeArgs, code_state: str, memory_state: dict[str, object]
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Code worktree is prepared, but external memory cannot be used until the developer selects a recovery path.",
            **recovery_guidance(
                "choose_memory_recovery",
                tool="worktree_start",
                args={
                    "repo_id": context.code_repository_name,
                    "task_name": args.task_name,
                    "worktree_name": args.worktree_name,
                    "workflow_kind": args.workflow_kind,
                },
                required_args=["memory_choice"],
            ),
            "code_worktree": code_state,
            "memory": memory_state,
        },
    )


def _contract_after_memory_start(
    contract: WorktreeContract, memory_state: dict[str, object]
) -> WorktreeContract:
    # No code path rewrites a contract to the removed ``disabled`` mode or writes
    # a new contract in it (MIK-R50): a started contract always runs external
    # memory, and the memory preview only reconciles the external base commit.
    reconciled_base = memory_state.get("reconciledMemoryBaseCommit")
    if isinstance(reconciled_base, str) and reconciled_base:
        return replace(contract, memory_base_commit=reconciled_base)
    return contract


def _blocked_provider_start_result(
    context,
    args: WorktreeArgs,
    code_state: str,
    memory_state: dict[str, object],
    provider_state: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Worktree provider setup could not be prepared safely.",
            **recovery_guidance(
                "choose_provider_setup_recovery",
                tool="worktree_start",
                args={
                    "repo_id": context.code_repository_name,
                    "task_name": args.task_name,
                    "worktree_name": args.worktree_name,
                    "workflow_kind": args.workflow_kind,
                    "skip_provider_setup": True,
                },
            ),
            "code_worktree": code_state,
            "memory": memory_state,
            "providers": provider_state,
        },
    )


def _preview_start_enclosure(plan: _StartEnclosurePlan) -> _PreparedStartEnclosure:
    code_state = ensure_worktree(plan.contract, side="code", dry_run=True)
    return _PreparedStartEnclosure(
        plan.contract,
        code_state,
        plan.memory_preview,
        plan.provider_plan,
    )
