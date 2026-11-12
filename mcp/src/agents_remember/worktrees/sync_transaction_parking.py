"""Exact ownership and adoption of WIP parked before the admission journal exists."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import (
    command_result,
    side_locations,
    side_record,
    sync_contract_kind,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
    operation_stamp,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

# Display only: restoration proves the complete stash inventory.
WIP_PATH_SAMPLE_LIMIT = 128


def orphaned_cancel_record(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    predecessor: SyncOperationRecord | None,
    fetch: dict[str, object],
) -> SyncOperationRecord | WorktreeCommandResult | None:
    """Recover only parked work from a death before the first admission write.

    No source has been carried or pinned yet. Unparked sides stay untouched;
    matching stash parents prove the pre-sync heads of the parked sides.
    """
    named = (
        {
            side.wipStash
            for side in (predecessor.code, predecessor.memory)
            if side is not None and side.wipStash
        }
        if predecessor is not None
        else set()
    )
    sides: list[SyncSideRecord] = []
    for name in ("code", "memory"):
        if name == "memory" and contract.memory_mode != "external":
            continue
        repository, _, _, branch = side_locations(contract, name)
        side = side_record(contract, name, branch_commit(repository, branch))
        adopted = _adopt_orphaned_park(contract, side, fetch, named)
        if isinstance(adopted, WorktreeCommandResult):
            return adopted
        sides.append(adopted.model_copy(update={"plan": "fast-forward"}) if adopted else side)
    if not any(side.wipStash for side in sides):
        return None
    stamp = operation_stamp()
    record = SyncOperationRecord(
        generation=predecessor.generation + 1 if predecessor else 1,
        contractPath=contract.contract_path.resolve(strict=False).as_posix(),
        taskId=contract.task_id,
        contractKind=sync_contract_kind(contract),
        codeBaseFrom=contract.code_base_commit,
        memoryBaseFrom=contract.memory_base_commit,
        phase="running-code",
        memorySyncChoice=args.memory_sync_choice,
        code=sides[0],
        memory=sides[1] if len(sides) > 1 else None,
        createdAt=stamp,
        updatedAt=stamp,
    )
    if not args.dry_run:
        store.write(record)
    return record


def _orphaned_park_stashes(
    contract: WorktreeContract, side: SyncSideRecord, named: set[str]
) -> list[tuple[str, str]]:
    """Stash entries holding this side's park message that no record names.

    Answers ``(ref, commit)`` pairs, newest first. Entries the predecessor record
    still names (stranded by an earlier failed generation, disclosed in its own
    answer) are the operator's, not orphans.
    """

    worktree = Path(side.worktree)
    expected = _wip_stash_message(contract, side)
    listed = run_git(worktree, ["stash", "list", "--format=%gd %H %s"])
    if listed.returncode != 0:
        return []
    orphans: list[tuple[str, str]] = []
    for line in listed.stdout.splitlines():
        cells = line.split(" ", 2)
        if len(cells) != 3:
            continue
        ref, commit, subject = cells
        # The stash subject prefixes the branch ("On ar/sync-thing: <message>");
        # the park message itself is the exact contract address.
        if expected not in subject:
            continue
        if commit in named or ref in named:
            continue
        orphans.append((ref, commit))
    return orphans


def _adopt_orphaned_park(
    contract: WorktreeContract,
    side: SyncSideRecord,
    fetch: dict[str, object],
    named: set[str],
) -> SyncSideRecord | WorktreeCommandResult | None:
    """Adopt one orphan stash into this admission, refuse ambiguity, else nothing.

    Adoption needs exactly one matching orphan whose stash parent is the pinned
    head on a clean tree; anything else refuses naming every match, so no two
    owners' bytes are ever confused and new dirt is never parked over an orphan.
    """

    orphans = _orphaned_park_stashes(contract, side, named)
    if not orphans:
        return None
    worktree = Path(side.worktree)
    if len(orphans) > 1:
        return _refuse_orphaned_park(side, orphans, fetch)
    ref, _commit = orphans[0]
    parent = run_git(worktree, ["rev-parse", "--verify", f"{ref}^1"])
    if parent.returncode != 0 or parent.stdout.strip() != side.preSyncHead:
        return _refuse_orphaned_park(side, orphans, fetch)
    shown = run_git(worktree, ["stash", "show", "--name-only", ref])
    if shown.returncode != 0:
        return _refuse_orphaned_park(side, orphans, fetch)
    paths = [line for line in shown.stdout.splitlines() if line.strip()]
    return side.model_copy(
        update={
            "wipState": "parked",
            "wipStash": _stash_commit(worktree, ref),
            "wipPaths": tuple(paths[:WIP_PATH_SAMPLE_LIMIT]),
            "wipPathCount": len(paths),
        }
    )


def _stash_commit(worktree: Path, ref: str) -> str:
    """The commit the stash entry names (the shape the journal keeps)."""
    resolved = run_git(worktree, ["rev-parse", "--verify", ref])
    return resolved.stdout.strip() if resolved.returncode == 0 else ref


def _refuse_orphaned_park(
    side: SyncSideRecord,
    orphans: list[tuple[str, str]],
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Refuse an admission that cannot account for parked owner bytes."""

    worktree = Path(side.worktree)
    named = ", ".join(f"{ref} ({commit[:12]})" for ref, commit in orphans)
    return command_result(
        2,
        "sync-orphaned-parked-wip",
        f"The {side.side} sync worktree cannot be admitted: a stash entry from an "
        f"interrupted sync holds parked WIP no record names ({named} in "
        f"{worktree.as_posix()}). Nothing was changed; no ref moved. Inspect it with "
        f"`git stash show -p {orphans[0][0]}` in that worktree; if it holds this sync's "
        f"candidate, apply it with `git stash apply {orphans[0][0]}` then drop it with "
        f"`git stash drop {orphans[0][0]}` (or drop it where its bytes are already in "
        "the tree), then run worktree_sync again; the admission names it.",
        fetch,
    )


def _wip_stash_message(contract: WorktreeContract, side: SyncSideRecord) -> str:
    """A stash message a human can find: the operation, the side, and the exact contract."""

    return (
        f"agents-remember worktree_sync parked {side.side} WIP for {contract.task_id} "
        f"({contract.contract_path.as_posix()})"
    )
