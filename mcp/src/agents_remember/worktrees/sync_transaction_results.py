"""Typed response projections for the resumable sync transaction."""

from __future__ import annotations

from agents_remember.models.worktree import SyncKnowledgeConflict
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.guidance import contract_next_args, recovery_guidance
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import command_result, side_payload
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    content_conflicts,
    validate_staged_resolution,
)
from agents_remember.worktrees.sync_transaction_recovery import (
    completed_sync_result,
    manual_repair_result,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncQuarantineRecord,
    SyncSideRecord,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def memory_choice_required(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "memory-sync-choice-required",
            "summary": "Memory has local commits and the official line moved. Choose "
            "merge-memory or skip-memory before any code mutation.",
            **recovery_guidance(
                "choose_memory_sync_recovery",
                tool="worktree_sync",
                args=contract_next_args(contract),
                required_args=["memory_sync_choice"],
            ),
            "code": side_payload(code),
            "memory": side_payload(memory),
            "fetch": fetch,
        },
    )


def sync_preview(
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "would-sync",
            "summary": "Preview only; exact sources were read but no refs, journals, or "
            "branches moved.",
            "code": side_payload(code),
            "memory": side_payload(memory),
            "fetch": fetch,
        },
    )


def resolution_required(
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    side = record.code if record.phase == "code-resolution-required" else record.memory
    assert side is not None
    parked = side.wipState == "restore-conflict"
    resolution: dict[str, object] = {
        "side": side.side,
        "owner": "agent",
        "worktree": side.worktree,
        "files": list(side.conflictFiles),
    }
    if parked:
        resolution["wipRestore"] = True
    knowledge = side.knowledgeConflict
    if knowledge is not None:
        resolution["knowledge"] = knowledge.model_dump(mode="json", exclude_none=True)
    next_operation, next_args, summary = _resolution_guidance(record, side, parked, knowledge)
    return WorktreeCommandResult(
        2,
        {
            "state": "sync-resolution-required",
            "status": "agent-action-required",
            "resolutionOwner": "agent",
            "summary": summary,
            "resolution": resolution,
            "nextOperation": next_operation,
            "nextTool": "worktree_sync",
            "nextArgs": next_args,
            "cancelArgs": {
                "contract_path": record.contractPath,
                "resolution_action": "cancel",
                "dry_run": False,
            },
            "fetch": fetch,
        },
    )


def _resolution_guidance(
    record: SyncOperationRecord,
    side: SyncSideRecord,
    parked: bool,
    knowledge: SyncKnowledgeConflict | None,
) -> tuple[str, dict[str, object], str]:
    """The next move, its exact arguments, and the sentence that says what to do.

    Three shapes, in the order they take precedence. A retained knowledge conflict that admits an
    authored decision is the only one whose next move is the reconcile operation: repeating the
    generic continuation against it is exactly the loop this replaced, so the advertised move names
    the record the engine refused and the decisions that conflict admits, with the decision left as
    a placeholder because choosing it is the agent's act and not this projection's. A parked
    candidate reapply keeps the shipped continuation. Everything else is the shipped continuation.
    """

    continuation: dict[str, object] = {
        "contract_path": record.contractPath,
        "resolution_action": "continue",
        "dry_run": False,
    }
    if knowledge is not None and knowledge.decisions:
        return (
            "reconcile_knowledge_resolution",
            _reconcile_args(record, knowledge),
            f"The retained {side.side} knowledge merge needs one authored decision: reconcile the "
            f"conflict {_conflict_subject(knowledge)} the engine reported, then the merge continues.",
        )
    if parked:
        return (
            "continue_sync_resolution",
            continuation,
            f"Resolve the parked {side.side} candidate reapply in its worktree, then continue; the "
            "parked WIP stays in the stash until it is settled.",
        )
    if knowledge is not None:
        return (
            "continue_sync_resolution",
            continuation,
            f"The retained {side.side} merge holds a conflict no authored decision settles "
            f"({_conflict_subject(knowledge)}): {_unsettled_instruction(knowledge)}",
        )
    return (
        "continue_sync_resolution",
        continuation,
        f"Resolve and stage the retained {side.side} merge, then continue.",
    )


def _unsettled_instruction(knowledge: SyncKnowledgeConflict) -> str:
    """What the caller has to do about a conflict no authored decision settles.

    Two conflicts reach here and they need different work, so the sentence names which one this is.
    A referential refusal whose retraction is unavailable is the orientation where the *arriving*
    side removed a row the retained side still cites: the missing row is not something any arriving
    insertion can account for, so the caller restores or removes the reference by hand in the
    worktree dataset, stages it, and continues -- or cancels, which is the continuation the response
    carries beside this one.
    """

    conflict = knowledge.conflict
    if conflict is not None and conflict.precondition == "no_arriving_insertion":
        return (
            "the arriving side removed a row the retained side still references, and no arriving "
            "insertion can be retracted to settle it, so restore the removed row or retract the "
            "reference in the worktree, stage it, then continue"
        )
    return "resolve it in the worktree, stage it, then continue"


def _reconcile_args(
    record: SyncOperationRecord, knowledge: SyncKnowledgeConflict
) -> dict[str, object]:
    """The exact call that authors one decision, with the decision left to the agent.

    The record and the decision list come from the journaled diagnosis rather than from anything
    re-derived here, so the call the agent is handed names the same row the engine refused.
    """

    chosen: dict[str, object] = {"decision": "<" + "|".join(knowledge.decisions) + ">"}
    conflict = knowledge.conflict
    if conflict is not None and conflict.table and conflict.record_id:
        chosen["table"] = conflict.table
        chosen["record_id"] = conflict.record_id
    return {
        "contract_path": record.contractPath,
        "resolution_action": "reconcile",
        "knowledge_resolution": chosen,
        "dry_run": False,
    }


def _conflict_subject(knowledge: SyncKnowledgeConflict) -> str:
    """Name the refused row the way the diagnosis named it, or name the reason there is none."""

    conflict = knowledge.conflict
    if conflict is not None and conflict.table is not None:
        return f"{conflict.code} on {conflict.table} {conflict.record_id}"
    if conflict is not None:
        return f"{conflict.code} on {knowledge.path}"
    if knowledge.refusal is not None:
        return f"{knowledge.refusal.code} on {knowledge.path}"
    return f"{knowledge.path} ({knowledge.detail})"


def reconcile_preview(side: SyncSideRecord, fetch: dict[str, object]) -> WorktreeCommandResult:
    """Read-only preview of authoring a decision for the retained knowledge conflict.

    The preview mutates nothing and does not predict the merge: it reports the conflict the journal
    holds, the decisions it admits, and that the authored decision would be applied and the retained
    merge validated afterwards. Whether the decision settles it is the merge's answer, not this
    projection's.
    """

    knowledge = side.knowledgeConflict
    assert knowledge is not None
    return WorktreeCommandResult(
        0,
        {
            "state": "would-reconcile-knowledge-conflict",
            "summary": (
                "Preview only; the authored decision would be applied to the retained conflict "
                f"{_conflict_subject(knowledge)} and the retained merge validated afterwards."
            ),
            "resolution": {
                "side": side.side,
                "owner": "agent",
                "worktree": side.worktree,
                "files": list(side.conflictFiles),
                "knowledge": knowledge.model_dump(mode="json", exclude_none=True),
            },
            "fetch": fetch,
        },
    )


def parked_wip_validation_preview(
    side: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Read-only preview of settling a parked-candidate reapply the agent resolved."""

    conflicts = content_conflicts(side)
    ready = not conflicts
    return WorktreeCommandResult(
        0 if ready else 2,
        {
            "state": "would-settle-parked-wip" if ready else "sync-resolution-incomplete",
            "summary": (
                "The parked candidate reapply is resolved; continuing retires its stash entry "
                "and leaves the candidate uncommitted for closeout."
                if ready
                else f"The parked {side.side} candidate reapply still has unmerged paths."
            ),
            "resolution": {
                "side": side.side,
                "owner": "agent",
                "worktree": side.worktree,
                "files": list(conflicts),
                "wipRestore": True,
            },
            "fetch": fetch,
        },
    )


def resolution_validation_preview(
    side: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    conflicts = content_conflicts(side)
    try:
        validate_staged_resolution(side)
        ready = True
        reason = "The staged resolution is ready to validate and commit."
    except SyncGitProofError as error:
        ready = False
        reason = str(error)
    resolution: dict[str, object] = {
        "side": side.side,
        "owner": "agent",
        "files": list(conflicts),
    }
    # The engine's explanation travels with every projection of the retained conflict, so a dry run
    # answers "what is still unresolved" with the row that is unresolved rather than only the path.
    if side.knowledgeConflict is not None:
        resolution["knowledge"] = side.knowledgeConflict.model_dump(mode="json", exclude_none=True)
    return WorktreeCommandResult(
        0 if ready else 2,
        {
            "state": "would-continue-sync-resolution" if ready else "sync-resolution-incomplete",
            "summary": reason,
            "resolution": resolution,
            "fetch": fetch,
        },
    )


def active_preview(
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "sync-active",
            "summary": "A journaled sync is active; apply the same call to resume it.",
            "phase": record.phase,
            "nextTool": "worktree_sync",
            "nextArgs": {"contract_path": record.contractPath, "dry_run": False},
            "fetch": fetch,
        },
    )


def cancel_preview(
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "would-cancel-sync",
            "summary": "Preview only; cancellation would restore the pinned pre-sync heads.",
            "phase": record.phase,
            "cancelArgs": {
                "contract_path": record.contractPath,
                "resolution_action": "cancel",
                "dry_run": False,
            },
            "fetch": fetch,
        },
    )


def terminal_resolution_replay(
    contract: WorktreeContract,
    args: WorktreeArgs,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if args.memory_sync_choice is not None and args.memory_sync_choice != record.memorySyncChoice:
        return manual_repair_result(
            "sync-input-mismatch",
            "memory_sync_choice cannot change for the terminal sync generation.",
            record,
            fetch,
        )
    if record.phase == "cancelled":
        return command_result(
            0 if args.resolution_action == "cancel" else 2,
            "sync-cancelled",
            "The exact sync generation was already cancelled and its heads are restored.",
            fetch,
            phase=record.phase,
        )
    if args.resolution_action == "continue":
        return completed_sync_result(contract, record, fetch)
    return command_result(
        2,
        "sync-already-completed",
        "The exact sync generation completed and cannot now be cancelled. Re-run with "
        "resolution_action='continue' to recover its terminal result.",
        fetch,
        phase=record.phase,
        nextArgs={
            "contract_path": record.contractPath,
            "resolution_action": "continue",
            "dry_run": False,
        },
    )


def quarantine_replay(
    args: WorktreeArgs,
    record: SyncQuarantineRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return command_result(
        0 if args.resolution_action == "cancel" else 2,
        "sync-cancelled-no-authority",
        "The corrupt sync journal was already quarantined. No branch heads were claimed "
        "restored because deterministic rollback authority was absent.",
        fetch,
        phase="quarantined",
        evidencePath=record.evidencePath,
    )
