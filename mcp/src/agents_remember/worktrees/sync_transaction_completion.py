"""The answer of a completed sync pass, built live at finalization or replayed from its journal."""

from __future__ import annotations

from agents_remember.worktrees.knowledge_crossing import crossing_summary
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.modules.guidance import contract_next_args, recovery_guidance
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_source_strategy import SyncSourceStrategy, source_strategy
from agents_remember.worktrees.sync_transaction_authority import reload_contract, side_payload
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord
from agents_remember.worktrees.worktree_contract import WorktreeContract

_SYNCED = "synced"
_MEMORY_SKIPPED = "sync-pass-completed-memory-skipped"
_MOVED_AGAIN = "sync-pass-completed-source-moved-again"
_UNFINISHED_SUMMARIES = {
    _MEMORY_SKIPPED: (
        "The code sync pass completed, but memory was explicitly skipped and the "
        "recorded pair is not current. Run merge-memory before paired activation."
    ),
    _MOVED_AGAIN: (
        "The admitted sync pass completed, but the source moved again; run the "
        "next contract-addressed sync before resuming work."
    ),
}


def completed_sync_result(
    contract: WorktreeContract,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Reconstruct the exact successful-pass result, including moved-again state."""

    current = reload_contract(contract)
    strategy = source_strategy(record.syncSource)
    code_to, memory_to = strategy.target_bases(record)
    if current.code_base_commit != code_to or current.memory_base_commit != memory_to:
        raise SyncGitProofError(
            "completed sync journal does not match the contract's finalized base pair"
        )
    state = _completed_state(current, record)
    result = WorktreeCommandResult(
        0,
        {
            "state": state,
            "summary": _UNFINISHED_SUMMARIES.get(state, strategy.synced_summary),
            "code": side_payload(record.code),
            "memory": side_payload(record.memory),
            "codeBaseCommit": code_to,
            "memoryBaseCommit": memory_to,
            **strategy.result_keys(),
            "fetch": fetch,
            **_crossing_keys(record),
            **(_lineage_guidance(current, strategy) if state != _SYNCED else {}),
        },
    )
    # One place for every completed-sync answer, replayed or live: the journal is the record of what
    # the merge reported, so a replay names the same debt the original answer did instead of dropping
    # the field.
    _name_memory_repair_debt(result, record)
    return result


def _completed_state(current: WorktreeContract, record: SyncOperationRecord) -> str:
    moved_again = _source_moved_again(current, record)
    if record.memory is not None and record.memory.plan == "skip":
        return _MEMORY_SKIPPED
    return _MOVED_AGAIN if moved_again else _SYNCED


def _source_moved_again(current: WorktreeContract, record: SyncOperationRecord) -> bool:
    """Whether a recorded source line moved past the tip this sync pulled.

    "Moved again" reads the lines this sync recorded, never a re-resolved name.
    """

    latest_code = branch_commit(current.code_repo_path, record.code.sourceBranch)
    latest_memory = (
        branch_commit(current.memory_repo_path, record.memory.sourceBranch)
        if current.memory_mode == "external"
        and current.memory_repo_path is not None
        and record.memory is not None
        else ""
    )
    return latest_code != record.code.sourceCommit or (
        record.memory is not None and latest_memory != record.memory.sourceCommit
    )


def _lineage_guidance(current: WorktreeContract, strategy: SyncSourceStrategy) -> dict[str, object]:
    return recovery_guidance(
        "sync_source_lineage",
        tool="worktree_sync",
        args=contract_next_args(current, dry_run=True, **strategy.request_args()),
    )


def _crossing_keys(record: SyncOperationRecord) -> dict[str, object]:
    crossings = successful_crossings(record)
    if not crossings:
        return {}
    return {"crossing": crossings[0] if len(crossings) == 1 else crossings}


def successful_crossings(record: SyncOperationRecord) -> list[dict[str, object]]:
    """The journaled crossing summaries of a completed sync's sides, in side order.

    The same :func:`crossing_summary` view the resolution response carries, so a
    successful sync reports its report identity and alignment counts too.
    Sides with no journaled report contribute nothing.
    """

    summaries: list[dict[str, object]] = []
    for side in (record.code, record.memory):
        if side is not None and side.crossingReport:
            summaries.append(crossing_summary(side.crossingReport))
    return summaries


def _name_memory_repair_debt(result: WorktreeCommandResult, record: SyncOperationRecord) -> None:
    """Name the debt a sync merge reported instead of refusing.

    A sync validates with two bases and publishes no leaf, so a stale reference anchor is reported
    rather than refused: the merged state can be created from two valid sides and is repaired after
    the merge exists. The debt is the route's answer, so it is named here -- rule IDs and the rule
    statement -- and a later publication of the line refuses until the Curator repairs it.
    """

    if record.memory is None or not record.memory.repairDebt:
        return
    result.payload["memoryRepairDebt"] = {
        "side": "memory",
        "rules": list(record.memory.repairDebt),
        "statement": (
            "the merged memory reports these refusing rules instead of refusing: repair them before "
            "this line publishes again, because a publication of the line refuses them"
        ),
        "resultHead": record.memory.resultHead,
    }
