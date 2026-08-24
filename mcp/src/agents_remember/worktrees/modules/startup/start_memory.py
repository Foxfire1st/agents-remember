"""External-memory admission and worktree preparation for worktree start."""

from __future__ import annotations

import os
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import LedgerError, MemoryLedger, find_mapping
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_exists,
    ensure_worktree,
    head_commit,
)
from agents_remember.worktrees.named_ref_memory import load_named_ref_ledger
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _memory_source_state(
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> dict[str, object] | None:
    """Settle the memory side before its ledger is ever read."""

    if contract.memory_mode == "internal":
        return {"state": "internal", "reason": "memory lives in the code worktree"}
    if contract.memory_mode == "disabled":
        return {"state": "disabled"}
    assert contract.memory_repo_path is not None
    if not contract.memory_repo_path.exists():
        return _missing_memory_repo_state(args)
    return None


def prepare_memory_for_start(
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> dict[str, object]:
    source_state = _memory_source_state(contract, args)
    if source_state is not None:
        return source_state
    memory_source_branch = _ensure_memory_source_branch(contract)
    ledger = _load_memory_ledger(contract, args)
    if isinstance(ledger, dict):
        return ledger
    if find_mapping(ledger, contract.code_base_commit) is None:
        disabled = _disabled_memory_choice(args)
        return disabled or _missing_mapping_state(contract, ledger)
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    memory_branch_state = ensure_worktree(contract, side="memory", dry_run=args.dry_run)
    mtime_sync = _sync_worktree_memory_mtimes(contract, args.dry_run)
    return {
        "state": "compatible",
        "worktree": memory_branch_state,
        "memorySourceBranch": memory_source_branch,
        "mtimeSync": mtime_sync,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }


def _ensure_memory_source_branch(contract: WorktreeContract) -> dict[str, object]:
    """Require the exact task-derived memory source; start never creates protected refs."""

    assert contract.memory_repo_path is not None
    if branch_exists(contract.memory_repo_path, contract.memory_source_branch):
        return {"state": "existing", "branch": contract.memory_source_branch}
    raise RuntimeError(
        "task-derived memory source branch is missing; create or advance protected source "
        "refs only through their repository landing plane"
    )


def _sync_worktree_memory_mtimes(
    contract: WorktreeContract,
    dry_run: bool,
) -> dict[str, object]:
    """Mirror safe source-repository mtimes onto the freshly checked-out worktree."""

    if dry_run:
        return {"state": "skipped", "reason": "dry-run"}
    if contract.memory_repo_path is None or contract.memory_worktree is None:
        return {"state": "skipped", "reason": "no external memory worktree"}
    source = contract.memory_repo_path
    target = contract.memory_worktree
    divergent = _memory_divergence_paths(source, target)
    synced = 0
    missing = 0
    left_fresh = 0
    for path in target.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        relative = path.relative_to(target).as_posix()
        if divergent is not None and relative in divergent:
            left_fresh += 1
            continue
        source_file = source / relative
        try:
            source_stat = source_file.stat()
        except OSError:
            missing += 1
            continue
        os.utime(path, (source_stat.st_atime, source_stat.st_mtime))
        synced += 1
    result: dict[str, object] = {
        "state": "synced",
        "filesSynced": synced,
        "filesMissingInSource": missing,
        "divergentLeftFresh": left_fresh,
    }
    if divergent is None:
        result["divergenceState"] = "uncomputable; synced all (pre-L2 behavior)"
    return result


def _memory_divergence_paths(source: Path, target: Path) -> set[str] | None:
    """Return paths whose committed content differs between target and source HEAD."""

    try:
        source_head = head_commit(source)
        target_head = head_commit(target)
    except Exception:
        return None
    if source_head == target_head:
        return set()
    diff = run_git(source, ["diff", "--name-only", source_head, target_head])
    if diff.returncode != 0:
        return None
    return {line.strip() for line in diff.stdout.splitlines() if line.strip()}


def _disabled_memory_choice(args: WorktreeArgs) -> dict[str, object] | None:
    if args.memory_choice == "disabled-memory":
        return {"state": "disabled", "reason": "human selected disabled memory"}
    return None


def _missing_memory_repo_state(args: WorktreeArgs) -> dict[str, object]:
    disabled = _disabled_memory_choice(args)
    if disabled:
        return disabled
    return {
        "state": "blocked",
        "reason": "external memory repo is missing; run c-00-initialize-memory-repo before starting an external-memory worktree",
        "choices": ["initialize-memory-repo", "disabled-memory"],
    }


def _load_memory_ledger(
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> MemoryLedger | dict[str, object]:
    assert contract.memory_repo_path is not None
    try:
        return load_named_ref_ledger(
            contract.memory_repo_path,
            contract.memory_source_branch,
        )
    except LedgerError as error:
        disabled = _disabled_memory_choice(args)
        if disabled:
            return disabled
        return {
            "state": "blocked",
            "reason": "the configured memory ledger is unreadable",
            "failure": public_failure_evidence(
                stage="worktree-start-ledger-read",
                side="ledger",
                name=(contract.ledger_path.name if contract.ledger_path else "memory.md"),
                error_type=type(error).__name__,
                observed={"state": "unreadable"},
            ),
            "choices": ["initialize-memory-repo", "disabled-memory"],
        }


def _missing_mapping_state(
    contract: WorktreeContract,
    ledger: MemoryLedger,
) -> dict[str, object]:
    return {
        "state": "blocked",
        "reason": "no exact ledger mapping for selected code base commit",
        "codeBaseCommit": contract.code_base_commit,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "choices": ["disabled-memory"],
        "recovery": (
            "repair the exact code-to-memory mapping in an ordinary task-owned conflict leaf, "
            "then land it through the normal closeout and integration plane"
        ),
    }


__all__ = [
    "_ensure_memory_source_branch",
    "_sync_worktree_memory_mtimes",
    "prepare_memory_for_start",
]
