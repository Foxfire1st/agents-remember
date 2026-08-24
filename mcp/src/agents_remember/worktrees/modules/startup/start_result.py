"""Construct the terminal response of one worktree-start attempt."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    contract_payload,
    next_guidance,
    recovery_guidance,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.scheduling_mode import stale_series_artifact_fact
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class StartedWorktreeState:
    code: str
    memory: dict[str, object]
    providers: dict[str, object]


def started_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    state: StartedWorktreeState,
    *,
    projection_effects: list[dict[str, object]] | None = None,
) -> WorktreeCommandResult:
    """Distinguish a non-mutating preview from a completed start on the wire."""
    if args.dry_run:
        return _start_preview_result(
            contract,
            args,
            state.code,
            state.memory,
            state.providers,
        )
    summary = "Worktree task started; continue the wrapped workflow before closeout."
    if state.providers.get("state") == "starting":
        summary = (
            "Worktree task started; provider setup is running in the background — "
            "poll worktree_status until its providers block reaches a terminal state."
        )
    return WorktreeCommandResult(
        0,
        {
            "state": "started",
            "summary": summary,
            **next_guidance(
                "continue_work",
                tool="worktree_status",
                args=contract_next_args(contract),
            ),
            **_start_result_facts(contract, state.code, state.memory, state.providers),
            "projectionEffects": projection_effects or [],
        },
    )


def _start_preview_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    code_state: str,
    memory_state: dict[str, object],
    provider_state: dict[str, object],
) -> WorktreeCommandResult:
    summary = (
        "Worktree start preview passed; apply worktree_start with the same task "
        "identity to create the branches, worktrees, contract, and fresh lifecycle binding."
    )
    apply_args: dict[str, object] = {
        "repo_id": contract.repo_name,
        "task_name": contract.task_name,
        "worktree_name": contract.code_worktree.name,
        "leaf_id": contract.leaf_id,
        "workflow_kind": contract.workflow_kind,
        "work_branch": contract.code_work_branch,
        "memory_mode": contract.memory_mode,
        "skip_provider_setup": args.skip_provider_setup,
        "dry_run": False,
    }
    parent_is_planned = (
        bool(contract.parent_task_name)
        and contract.parent_contract_path is not None
        and not contract.parent_contract_path.exists()
    )
    if parent_is_planned:
        if args.source_branch is not None:
            apply_args["source_branch"] = args.source_branch
    else:
        apply_args["source_branch"] = contract.code_source_branch
    if args.parent_task:
        apply_args["parent_task"] = args.parent_task
    guidance = recovery_guidance("apply_worktree_start", tool="worktree_start", args=apply_args)
    return WorktreeCommandResult(
        0,
        {
            "state": "would-start",
            "summary": summary,
            **guidance,
            "nextStep": {"summary": summary, **guidance},
            **_start_result_facts(contract, code_state, memory_state, provider_state),
        },
    )


def _start_result_facts(
    contract: WorktreeContract,
    code_state: str,
    memory_state: dict[str, object],
    provider_state: dict[str, object],
) -> dict[str, object]:
    facts: dict[str, object] = {
        "code_worktree": code_state,
        "memory": memory_state,
        "providers": provider_state,
        "enclosure_path": contract.contract_path.as_posix(),
        "contract_path": contract.contract_path.as_posix(),
        "leaf_id": contract.leaf_id,
        "lifecycle_id": contract.lifecycle_id,
        "task_artifact": contract.task_artifact.as_posix(),
        "contract": contract_payload(contract),
    }
    stale = stale_series_artifact_fact(contract.task_root)
    if stale is not None:
        # L13-R5b: a terminal series artifact under an organizational master was
        # ignored; report the fact instead of refusing the start.
        facts["staleSeriesArtifact"] = stale
    return facts
