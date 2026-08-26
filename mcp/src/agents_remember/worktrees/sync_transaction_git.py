"""Exact Git mutations and proofs for resumable worktree sync."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import LedgerError, LedgerRow, parse_ledger_text
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    head_commit,
    is_ancestor,
    repository_identity,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


class SyncGitProofError(RuntimeError):
    """Live Git state cannot be attributed exactly to the journaled sync."""


def read_ref(repository: Path, ref: str) -> str | None:
    valid = run_git(repository, ["check-ref-format", ref])
    if valid.returncode != 0:
        raise SyncGitProofError(valid.stderr.strip() or f"invalid sync authority ref {ref!r}")
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return None
    raise SyncGitProofError(result.stderr.strip() or f"could not inspect sync authority ref {ref}")


def create_pinned_ref(repository: Path, ref: str, commit: str) -> None:
    current = read_ref(repository, ref)
    if current is not None:
        if current != commit:
            raise SyncGitProofError(f"sync authority ref {ref} pins another commit")
        return
    zeros = "0" * len(commit)
    result = run_git(repository, ["update-ref", ref, commit, zeros])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or f"could not create {ref}")


def delete_pinned_ref(repository: Path, ref: str, expected: str) -> None:
    current = read_ref(repository, ref)
    if current is None:
        return
    if current != expected:
        raise SyncGitProofError(f"sync authority ref {ref} changed before cleanup")
    result = run_git(repository, ["update-ref", "-d", ref, expected])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or f"could not delete {ref}")


def ensure_temporary_worktree(side: SyncSideRecord) -> None:
    if not side.temporary:
        require_side_checkout(side)
        return
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if worktree.exists():
        require_side_checkout(side)
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(repository, ["worktree", "add", str(worktree), side.workBranch])
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or f"could not create temporary {side.side} sync worktree"
        )
    require_side_checkout(side)


def require_side_checkout(side: SyncSideRecord) -> None:
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if repository_identity(worktree) != repository_identity(repository):
        raise SyncGitProofError(f"{side.side} sync worktree changed repository identity")
    if current_branch(worktree) != side.workBranch:
        raise SyncGitProofError(f"{side.side} sync worktree changed its journaled branch")


def remove_temporary_worktree(side: SyncSideRecord) -> None:
    if not side.temporary:
        return
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if not worktree.exists():
        return
    require_side_checkout(side)
    if git_status(worktree):
        raise SyncGitProofError(f"temporary {side.side} sync worktree is not clean")
    result = run_git(repository, ["worktree", "remove", str(worktree)])
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or f"could not remove temporary {side.side} sync worktree"
        )


def git_status(worktree: Path) -> str:
    result = run_git(worktree, ["status", "--porcelain"])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    return result.stdout.strip()


def merge_head(worktree: Path) -> str | None:
    result = run_git(worktree, ["rev-parse", "--verify", "MERGE_HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def unmerged_paths(worktree: Path) -> tuple[str, ...]:
    result = run_git(worktree, ["diff", "--name-only", "--diff-filter=U"])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not inspect merge conflicts")
    return tuple(path for path in result.stdout.splitlines() if path)


def side_merge_completed(side: SyncSideRecord) -> bool:
    worktree = Path(side.worktree)
    if merge_head(worktree) is not None:
        return False
    current = head_commit(worktree)
    if not exact_created_head(side, current):
        return False
    validate_completed_side(side, current)
    return True


def start_side_merge(side: SyncSideRecord) -> tuple[str, tuple[str, ...], str]:
    """Attempt the pinned merge, retaining a genuine conflict in place."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    if merge_head(worktree) is not None:
        conflicts = unmerged_paths(worktree)
        if side.side == "memory" and side.plan == "merge" and not conflicts:
            return _finish_staged_memory_merge(side)
        return "resolution-required", conflicts, ""
    if side_merge_completed(side):
        return "completed", (), head_commit(worktree)
    if git_status(worktree):
        raise SyncGitProofError(f"{side.side} sync requires a clean worktree before merging")
    merge_args = (
        ["merge", "--no-commit", "--no-edit", side.sourceCommit]
        if side.side == "memory" and side.plan == "merge"
        else ["merge", "--no-edit", side.sourceCommit]
    )
    result = run_git(worktree, merge_args)
    if result.returncode == 0:
        if side.side == "memory" and side.plan == "merge":
            if merge_head(worktree) != side.sourceCommit:
                raise SyncGitProofError(
                    "divergent memory merge did not retain its pinned MERGE_HEAD"
                )
            return _finish_staged_memory_merge(side)
        result_head = head_commit(worktree)
        if not exact_created_head(side, result_head):
            raise SyncGitProofError(f"{side.side} merge did not create the exact admitted head")
        validate_completed_side(side, result_head)
        return "completed", (), result_head
    current_merge = merge_head(worktree)
    conflicts = unmerged_paths(worktree)
    if current_merge == side.sourceCommit and conflicts:
        return "resolution-required", conflicts, (result.stderr or result.stdout).strip()
    raise SyncGitProofError(
        (result.stderr or result.stdout).strip() or f"{side.side} source merge failed"
    )


def _finish_staged_memory_merge(
    side: SyncSideRecord,
) -> tuple[str, tuple[str, ...], str]:
    """Validate an automatic ledger merge before creating its commit."""

    worktree = Path(side.worktree)
    try:
        _validate_parent_ledgers(side, ":memory.md")
    except SyncGitProofError as error:
        return "resolution-required", ("memory.md",), str(error)
    committed = run_git(worktree, ["commit", "--no-edit"])
    if committed.returncode != 0:
        raise SyncGitProofError(
            committed.stderr.strip()
            or committed.stdout.strip()
            or "automatic memory merge commit failed"
        )
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError("memory merge commit does not have the pinned parents")
    validate_completed_side(side, result_head)
    return "completed", (), result_head


def continue_side_merge(side: SyncSideRecord) -> str:
    """Validate a staged agent resolution and commit the exact retained merge."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    current_merge = merge_head(worktree)
    if current_merge is None:
        if not exact_created_head(side, head_commit(worktree)):
            raise SyncGitProofError(
                f"{side.side} merge state disappeared without an operation-owned commit"
            )
        result_head = head_commit(worktree)
        validate_completed_side(side, result_head)
        return result_head
    validate_staged_resolution(side)
    committed = run_git(worktree, ["commit", "--no-edit"])
    if committed.returncode != 0:
        raise SyncGitProofError(
            committed.stderr.strip() or committed.stdout.strip() or "merge commit failed"
        )
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError(f"{side.side} merge commit does not have the pinned parents")
    validate_completed_side(side, result_head)
    return result_head


def validate_staged_resolution(side: SyncSideRecord) -> None:
    """Read-only proof that the exact retained merge is ready for its commit."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    if merge_head(worktree) != side.sourceCommit:
        raise SyncGitProofError(f"{side.side} MERGE_HEAD is not the pinned sync source")
    conflicts = unmerged_paths(worktree)
    if conflicts:
        raise SyncGitProofError(
            f"{side.side} resolution still has unmerged paths: {', '.join(conflicts[:30])}"
        )
    unstaged = run_git(worktree, ["diff", "--quiet"])
    if unstaged.returncode != 0:
        raise SyncGitProofError(f"{side.side} resolution has unstaged changes")
    checked = run_git(worktree, ["diff", "--cached", "--check"])
    if checked.returncode != 0:
        raise SyncGitProofError(
            checked.stdout.strip() or checked.stderr.strip() or "staged resolution is invalid"
        )
    if side.side == "memory" and side.plan == "merge":
        _validate_parent_ledgers(side, ":memory.md")


def rollback_side(side: SyncSideRecord) -> None:
    """Restore one side only when live history proves the exact sync-owned delta."""

    ensure_temporary_worktree(side)
    worktree = Path(side.worktree)
    current = head_commit(worktree)
    current_merge = merge_head(worktree)
    if current_merge is not None:
        if current != side.preSyncHead or current_merge != side.sourceCommit:
            raise SyncGitProofError(f"{side.side} active merge is outside sync authority")
        aborted = run_git(worktree, ["merge", "--abort"])
        if aborted.returncode != 0 or head_commit(worktree) != side.preSyncHead:
            raise SyncGitProofError(f"{side.side} exact merge abort did not restore its head")
    elif current != side.preSyncHead:
        if not exact_created_head(side, current):
            raise SyncGitProofError(
                f"{side.side} has later or unrelated commits; automatic rollback is unsafe"
            )
        if git_status(worktree):
            raise SyncGitProofError(f"{side.side} has post-sync work; automatic rollback is unsafe")
        reset = run_git(worktree, ["reset", "--hard", side.preSyncHead])
        if reset.returncode != 0:
            raise SyncGitProofError(reset.stderr.strip() or f"could not restore {side.side} head")
    if head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(f"{side.side} rollback did not reach its pinned pre-sync head")
    if git_status(worktree):
        raise SyncGitProofError(
            f"{side.side} branch is restored but post-sync work remains for manual repair"
        )


def exact_created_head(side: SyncSideRecord, current: str) -> bool:
    """Prove the only two heads the admitted merge can create."""
    if current == side.sourceCommit and is_ancestor(
        Path(side.repository), side.preSyncHead, side.sourceCommit
    ):
        return True
    parents = run_git(Path(side.repository), ["rev-list", "--parents", "-n", "1", current])
    cells = parents.stdout.split()
    return parents.returncode == 0 and cells[1:] == [side.preSyncHead, side.sourceCommit]


def validate_completed_side(side: SyncSideRecord, current: str) -> None:
    """Apply side-specific proof to an exact operation-created result head."""

    if side.side == "memory" and side.plan == "merge":
        _validate_parent_ledgers(side, f"{current}:memory.md")


def validate_current_memory_side(side: SyncSideRecord) -> None:
    """Prove an already-descendant memory branch retained its source ledger authority."""

    source_rows = _ledger_rows(Path(side.repository), f"{side.sourceCommit}:memory.md")
    _validate_required_ledger_rows(
        source_rows,
        _ledger_rows(Path(side.repository), f"{side.preSyncHead}:memory.md"),
    )


def _validate_parent_ledgers(side: SyncSideRecord, resolved_spec: str) -> None:
    repository = Path(side.repository)
    parent_rows = [
        *_ledger_rows(repository, f"{side.preSyncHead}:memory.md"),
        *_ledger_rows(repository, f"{side.sourceCommit}:memory.md"),
    ]
    _validate_required_ledger_rows(
        parent_rows,
        _ledger_rows(Path(side.worktree), resolved_spec),
    )


def _validate_required_ledger_rows(
    required_rows: list[LedgerRow],
    resolved_rows: list[LedgerRow],
) -> None:
    resolved = set(resolved_rows)
    missing = sorted(
        set(required_rows) - resolved,
        key=lambda row: (row.code_commit, row.memory_commit),
    )
    if missing:
        sample = ", ".join(f"{row.code_commit}->{row.memory_commit}" for row in missing[:10])
        raise SyncGitProofError(f"resolved memory ledger dropped parent mapping(s): {sample}")


def _ledger_rows(repository: Path, spec: str) -> list[LedgerRow]:
    shown = run_git(repository, ["show", spec])
    if shown.returncode != 0:
        raise SyncGitProofError(shown.stderr.strip() or f"could not read ledger at {spec}")
    try:
        return parse_ledger_text(shown.stdout).rows
    except LedgerError as error:
        raise SyncGitProofError(f"memory ledger at {spec} is invalid: {error}") from error


def side_branch_head(side: SyncSideRecord) -> str:
    return branch_commit(Path(side.repository), side.workBranch)
