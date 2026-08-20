"""Mid-task base sync: pull the moved official line into a live worktree (issue #54).

A worktree's claim "my base = the official line" decays while parallel cycles
land. ``worktree_sync`` moves the recorded code/memory base pair forward
atomically: it never advances one side to a state the other side's ledger does
not map. The dominant pre-closeout case — parked memory with zero local
commits — is a pure fast-forward, leaving the parallel cycle's sidecars and
ledger rows beneath the task's future work, so closeout appends on top with no
carryover reconciliation, and end-of-series integration stays ``ff-only``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.git_freshness import fetch_remote, upstream_ref
from agents_remember.kernel.memory_ledger import LedgerError, find_mapping, parse_ledger_text
from agents_remember.worktrees.integration.integration_branch_authority import require_sync_worktree
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_commit,
    head_commit,
    is_ancestor,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    recovery_guidance,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.start import load_contract_from_args
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract


def sync_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    require_sync_worktree(contract)
    if not contract.code_worktree.exists():
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": "The code worktree does not exist; sync needs a live worktree.",
            },
        )
    fetch = _fetch_source_upstreams(contract)
    if args.dry_run:
        return _sync_under_authority(contract, args, fetch)
    with integration_authority_lock(contract.coordination_root, contract.repo_name):
        current = load_contract_from_args(args)
        if current != contract:
            raise RuntimeError("worktree_sync contract changed before branch mutation")
        require_sync_worktree(current)
        return _sync_under_authority(current, args, fetch)


def _sync_under_authority(
    contract: WorktreeContract,
    args: WorktreeArgs,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    code_tip = branch_commit(contract.code_repo_path, contract.code_source_branch)
    memory_repo = contract.memory_repo_path
    external = (
        contract.memory_mode == "external"
        and memory_repo is not None
        and contract.memory_worktree is not None
    )
    memory_tip = (
        branch_commit(memory_repo, contract.memory_source_branch)
        if external and memory_repo is not None
        else ""
    )

    stop = _stop_before_sync(
        contract, code_tip=code_tip, memory_tip=memory_tip, external=external, fetch=fetch
    )
    if stop is not None:
        return stop

    code_sync = _sync_code(contract, args.dry_run)
    if code_sync["state"] == "conflicts":
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": "Merging the moved source branch into the code work branch "
                "conflicts; resolve manually in the worktree, then re-run worktree_sync.",
                "code": code_sync,
                "fetch": fetch,
            },
        )

    memory_sync: dict[str, object] = (
        _sync_memory(contract, args) if external else {"state": "no-external-memory"}
    )
    memory_block = _memory_sync_block(contract, code_sync, memory_sync, fetch)
    if memory_block is not None:
        return memory_block

    memory_synced = memory_sync["state"] in {"fast-forwarded", "merged", "already-current"}
    if not args.dry_run:
        entry = {
            "at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            "codeBaseFrom": contract.code_base_commit,
            "codeBaseTo": code_tip,
            "memoryBaseFrom": contract.memory_base_commit,
            "memoryBaseTo": memory_tip if memory_synced else contract.memory_base_commit,
            "code": str(code_sync["state"]),
            "memory": str(memory_sync["state"]),
        }
        contract = replace(
            contract,
            code_base_commit=code_tip,
            memory_base_commit=memory_tip if memory_synced else contract.memory_base_commit,
            sync_log=(*contract.sync_log, entry),
        )
        write_contract(contract.contract_path, contract)
    return WorktreeCommandResult(
        0,
        {
            "state": "would-sync" if args.dry_run else "synced",
            "summary": (
                "Preview only; no branches were moved."
                if args.dry_run
                else "The worktree base pair now matches the official line."
            ),
            "code": code_sync,
            "memory": memory_sync,
            "codeBaseCommit": code_tip,
            "memoryBaseCommit": memory_tip if memory_synced else contract.memory_base_commit,
            "fetch": fetch,
        },
    )


def _stop_before_sync(
    contract: WorktreeContract,
    *,
    code_tip: str,
    memory_tip: str,
    external: bool,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    """The result when no branch should move: an inconsistent official pair, or nothing to do."""
    if external:
        pair_block = _consistent_pair_block(contract, code_tip)
        if pair_block is not None:
            return WorktreeCommandResult(2, {**pair_block, "fetch": fetch})
    if code_tip == contract.code_base_commit and (
        not external or memory_tip == contract.memory_base_commit
    ):
        return WorktreeCommandResult(
            0,
            {
                "state": "already-current",
                "summary": "The recorded base pair already matches the official line.",
                "fetch": fetch,
            },
        )
    return None


def _memory_sync_block(
    contract: WorktreeContract,
    code_sync: dict[str, object],
    memory_sync: dict[str, object],
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    """The blocked result when the memory side could not be advanced on its own."""
    if memory_sync["state"] == "needs-review":
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": "The memory work branch has local commits and the official memory "
                "moved; choose merge-memory (ledger conflicts abort cleanly) or skip-memory "
                "(defer to end-of-task carryover).",
                **recovery_guidance(
                    "choose_memory_sync_recovery",
                    tool="worktree_sync",
                    args=contract_next_args(contract),
                    required_args=["memory_sync_choice"],
                ),
                "code": code_sync,
                "memory": memory_sync,
                "fetch": fetch,
            },
        )
    if memory_sync["state"] == "conflicts":
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": "Merging official memory into the memory work branch conflicts "
                "(ledger or onboarding overlap); the merge was aborted. Use "
                "memory_sync_choice='skip-memory' to defer to end-of-task carryover.",
                "code": code_sync,
                "memory": memory_sync,
                "fetch": fetch,
            },
        )
    return None


def _fetch_source_upstreams(contract: WorktreeContract) -> dict[str, object]:
    """Best-effort bounded fetch of each source branch's upstream remote.

    Offline or remote-less repos degrade to reported state; sync proceeds on
    local facts (the local source branch is the integration target either way).
    """
    results: dict[str, object] = {}
    targets = [("code", contract.code_repo_path, contract.code_source_branch)]
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        targets.append(("memory", contract.memory_repo_path, contract.memory_source_branch))
    for side, repo, branch in targets:
        upstream = upstream_ref(repo, branch) if branch else None
        if upstream is None:
            results[side] = {"state": "no-upstream"}
            continue
        error = fetch_remote(repo, upstream.partition("/")[0])
        results[side] = (
            {"state": "fetched"} if error is None else {"state": "failed", "error": error}
        )
    return results


def _consistent_pair_block(contract: WorktreeContract, code_tip: str) -> dict[str, object] | None:
    """The new code tip must be ledger-mapped at the official memory tip."""
    assert contract.memory_repo_path is not None
    ledger_blob = run_git(
        contract.memory_repo_path,
        ["show", f"{contract.memory_source_branch}:memory.md"],
    )
    if ledger_blob.returncode != 0:
        return {
            "state": "blocked",
            "summary": "The official memory source branch has no readable memory.md ledger.",
            "reason": (ledger_blob.stderr or ledger_blob.stdout).strip(),
        }
    try:
        ledger = parse_ledger_text(ledger_blob.stdout)
    except LedgerError as error:
        return {
            "state": "blocked",
            "summary": "The official memory ledger could not be parsed.",
            "reason": str(error),
        }
    if find_mapping(ledger, code_tip) is None:
        return {
            "state": "blocked",
            "summary": "The official line is mid-cycle: the official memory ledger does not "
            "map the new code tip. Run memory carryover on the official side first, then "
            "re-run worktree_sync.",
            "codeTip": code_tip,
        }
    return None


def _sync_code(contract: WorktreeContract, dry_run: bool) -> dict[str, object]:
    """Merge the moved source branch into the work branch inside the code worktree.

    Merge (ff when the work branch is unchanged), never rebase: work branches may
    already be pushed. A conflicted merge is aborted so the worktree is never left
    half-merged.
    """
    worktree = contract.code_worktree
    if head_commit(worktree) == branch_commit(contract.code_repo_path, contract.code_source_branch):
        return {"state": "already-current"}
    if dry_run:
        return {"state": "would-merge", "source": contract.code_source_branch}
    result = run_git(worktree, ["merge", "--no-edit", contract.code_source_branch])
    if result.returncode == 0:
        return {"state": "merged", "head": head_commit(worktree)}
    return _aborted_merge_state(worktree, result)


def _aborted_merge_state(worktree: Path, result: CompletedProcess[str]) -> dict[str, object]:
    """Collect the conflicted paths and abort, so the worktree is never left half-merged."""
    conflicts = run_git(worktree, ["diff", "--name-only", "--diff-filter=U"]).stdout.split()
    run_git(worktree, ["merge", "--abort"])
    return {
        "state": "conflicts",
        "files": conflicts,
        "reason": (result.stderr or result.stdout).strip(),
    }


def _sync_memory(contract: WorktreeContract, args: WorktreeArgs) -> dict[str, object]:
    """Move the memory work branch onto the official memory tip.

    Pre-closeout the memory work branch has no commits beyond its base, so this
    is a pure fast-forward and the official cycle's sidecars and ledger rows end
    up beneath the task's future memory work. Local memory commits + a moved
    official line need an explicit choice; the ledger file is append-ordered, so
    merges are attempted but never auto-resolved.
    """
    assert contract.memory_worktree is not None and contract.memory_repo_path is not None
    worktree = contract.memory_worktree
    if args.memory_sync_choice == "skip-memory":
        return {"state": "skipped-by-choice"}
    tip = branch_commit(contract.memory_repo_path, contract.memory_source_branch)
    worktree_head = head_commit(worktree)
    if worktree_head == tip:
        return {"state": "already-current"}
    move = _memory_branch_move(contract, args, worktree_head=worktree_head, tip=tip)
    if move == "needs-review":
        return {"state": "needs-review", "worktreeHead": worktree_head, "officialTip": tip}
    if args.dry_run:
        return {"state": f"would-{move}", "to": tip}
    return _move_memory_branch(worktree, contract.memory_source_branch, move=move, tip=tip)


def _memory_branch_move(
    contract: WorktreeContract, args: WorktreeArgs, *, worktree_head: str, tip: str
) -> str:
    """Which move the memory work branch needs: ``fast-forward``, ``merge``, or ``needs-review``."""
    assert contract.memory_repo_path is not None
    if is_ancestor(contract.memory_repo_path, worktree_head, tip):
        return "fast-forward"
    if args.memory_sync_choice == "merge-memory":
        return "merge"
    return "needs-review"


def _move_memory_branch(
    worktree: Path, source_branch: str, *, move: str, tip: str
) -> dict[str, object]:
    """Perform the decided move. A failed ff leaves nothing to abort; a failed merge does."""
    if move == "fast-forward":
        result = run_git(worktree, ["merge", "--ff-only", source_branch])
        if result.returncode == 0:
            return {"state": "fast-forwarded", "to": tip}
        return {
            "state": "conflicts",
            "files": [],
            "reason": (result.stderr or result.stdout).strip(),
        }
    result = run_git(worktree, ["merge", "--no-edit", source_branch])
    if result.returncode == 0:
        return {"state": "merged", "head": head_commit(worktree)}
    return _aborted_merge_state(worktree, result)
