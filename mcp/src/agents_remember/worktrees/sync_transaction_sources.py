"""Which branches and tips one sync pulls, and the pinned refs that name a default source.

The source choice itself -- the contract's own line or each repository's default branch -- is a
strategy (:mod:`sync_source_strategy`). This module resolves the chosen branches to tips at
admission, reads and removes the refs a default-source sync pinned, and refuses a continue onto a
line that moved.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.models.worktree import SyncSide, SyncSource
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.sync_source_strategy import (
    CONTRACT_LINE,
    REPOSITORY_DEFAULT,
    SyncSourceStrategy,
    source_strategy,
)
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError, delete_pinned_ref
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncSideRecord,
    sync_side_default_source_prefix,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class SourceSelection:
    """The source branches and tips one sync admission pulls."""

    strategy: SyncSourceStrategy
    code_branch: str
    code_tip: str
    memory_branch: str
    memory_tip: str
    external: bool


def resolve_sync_sources(
    contract: WorktreeContract, sync_source: SyncSource | None = None
) -> SourceSelection:
    """The branches the chosen strategy names, with their current tips.

    An unrecorded repository default raises ``BranchAuthorityUnavailable``, which the caller
    answers.
    """

    strategy = source_strategy(sync_source)
    external = contract.memory_mode == "external" and contract.memory_repo_path is not None
    code_branch = strategy.source_branch(contract, "code")
    memory_branch = strategy.source_branch(contract, "memory") if external else ""
    return SourceSelection(
        strategy=strategy,
        code_branch=code_branch,
        code_tip=branch_commit(contract.code_repo_path, code_branch),
        memory_branch=memory_branch,
        memory_tip=(
            branch_commit(contract.memory_repo_path, memory_branch)
            if external and contract.memory_repo_path is not None
            else ""
        ),
        external=external,
    )


def source_pair(contract: WorktreeContract) -> tuple[str, str, bool]:
    selection = resolve_sync_sources(contract)
    return selection.code_tip, selection.memory_tip, selection.external


def default_source_refs(
    repository: Path, contract_path: Path, side_name: SyncSide
) -> dict[str, str]:
    """Every pinned default-source ref of one side, by the branch its name carries."""

    prefix = sync_side_default_source_prefix(contract_path, side_name)
    listed = run_git(repository, ["for-each-ref", "--format=%(objectname) %(refname)", prefix])
    if listed.returncode != 0:
        raise SyncGitProofError(f"{side_name} default-source authority refs are unreadable")
    found: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        commit, _, ref = line.partition(" ")
        if ref.startswith(prefix):
            found[ref.removeprefix(prefix)] = commit
    return found


def recovered_default_branch(
    repository: Path, contract_path: Path, side_name: SyncSide, source_head: str
) -> str | None:
    """The default branch a journal-less side pinned, or None for the contract's own line.

    A default-source sync pinned its resolved branch in a ref name; the rebuilt side carries that
    branch so a rollback-only recovery names what the sync really pulled. Only one such ref, at the
    pinned source commit, is admitted.
    """

    defaults = default_source_refs(repository, contract_path, side_name)
    if len(defaults) <= 1 and all(commit == source_head for commit in defaults.values()):
        return next(iter(defaults), None)
    prefix = sync_side_default_source_prefix(contract_path, side_name)
    found = ", ".join(f"{prefix}{branch} at {commit}" for branch, commit in defaults.items())
    raise SyncGitProofError(
        f"{side_name} default-source recovery refs are inconsistent: found {found}; expected at "
        f"most one ref under {prefix}, at the pinned source commit {source_head}. Keep only the "
        "ref naming the branch this sync pulled, delete the others with git update-ref -d "
        "<ref>, then run worktree_sync with resolution_action='cancel'."
    )


def recovered_source_strategy(
    contract: WorktreeContract, code: SyncSideRecord
) -> SyncSourceStrategy:
    """The choice a journal rebuilt from refs records: a pinned code default names it.

    Recovery offers cancel only; the rebuilt record carries the choice so that cancel deletes the
    pinned default ref too.
    """

    if default_source_refs(Path(code.repository), contract.contract_path, "code"):
        return REPOSITORY_DEFAULT
    return CONTRACT_LINE


def delete_default_source_refs(side: SyncSideRecord, contract_path: Path) -> None:
    """Delete every default-source ref one side pinned, whichever branch it names."""

    repository = Path(side.repository)
    prefix = sync_side_default_source_prefix(contract_path, side.side)
    for branch, commit in default_source_refs(repository, contract_path, side.side).items():
        delete_pinned_ref(repository, f"{prefix}{branch}", commit)


def moved_source_sides(record: SyncOperationRecord) -> str | None:
    """Refuse a continue onto a tip the official line has left behind.

    A sync journaled onto a tip the line left behind is ended with cancel, not continued
    onto it: continuing would carry the stale tip, and a stale merge stops at the commit
    check on findings later landings already repaired. A new sync onto the line's current
    tip then starts as every sync does.
    """

    for side in (record.code, record.memory):
        if side is None or side.plan in {"already-current", "skip"}:
            continue
        repository = Path(side.repository)
        try:
            tip = branch_commit(repository, side.sourceBranch)
        except (RuntimeError, subprocess.SubprocessError, OSError) as error:
            return (
                f"The {side.side} source tip cannot be read ({error}); the retained "
                "resolution cannot continue onto an unreadable line. Run worktree_sync "
                "again once the line reads, or cancel it with resolution_action='cancel'."
            )
        if tip != side.sourceCommit:
            return (
                f"The {side.side} source line moved since this sync was admitted "
                f"({side.sourceBranch} is at {tip}, the sync carries {side.sourceCommit}). "
                "End this sync with worktree_sync and resolution_action='cancel', then "
                "start a new sync onto the line's current tip; it is not continued onto "
                "the tip the line left behind."
            )
    return None
