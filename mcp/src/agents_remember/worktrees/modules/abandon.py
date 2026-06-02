"""Abandon a worktree-backed task without integrating it.

Abandon is the discard sibling of cleanup. cleanup runs only after a completed
integration; abandon exists for worktrees whose work will NOT land (premature
trials, dead-end experiments, the l-01-session-job-lifecycle skill read-only/abandon exit). It reclaims the
isolated provider stack (containers, networks, provider-runtime tree), removes
the code and memory worktrees, deletes the task branches, and removes the
worktree group dir.

Safety: without `force`, abandon refuses to delete a branch that still has
commits not on its source branch (reporting them) and refuses to remove a dirty
worktree. With `force` it discards them (`git worktree remove --force`,
`git branch -D`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    delete_branch_force,
    delete_branch_if_merged,
    remove_empty_dir,
    remove_registered_worktree,
)
from agents_remember.worktrees.modules.git import branch_exists, run_git
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.integrate import integration_branch
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.provider_teardown import (
    remove_tree,
    teardown_worktree_providers,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)


def abandon_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("abandon requires --approved (use dry_run to preview)")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)

    providers = teardown_worktree_providers(contract, dry_run=args.dry_run)
    removed_worktrees = _abandon_worktrees(contract, dry_run=args.dry_run, force=args.force)
    branches = _abandon_branches(contract, dry_run=args.dry_run, force=args.force)
    directories = _abandon_directories(contract, dry_run=args.dry_run, force=args.force)

    blockers = _abandon_blockers(removed_worktrees, branches)
    reclaimed = not blockers
    updated = replace(contract, cleanup="abandoned") if reclaimed else contract
    if reclaimed and not args.dry_run:
        write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": _abandon_state(args.dry_run, reclaimed),
            **status_payload(updated),
            "summary": _abandon_summary(args.dry_run, reclaimed),
            "providers": providers,
            "removed_worktrees": removed_worktrees,
            "branches": branches,
            "directories": directories,
            "blockers": blockers,
        },
    )


def _abandon_worktrees(
    contract: WorktreeContract, *, dry_run: bool, force: bool
) -> dict[str, dict[str, object]]:
    worktrees = {
        "code": remove_registered_worktree(
            contract.code_repo_path, contract.code_worktree, dry_run, force=force
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_worktree is not None
    ):
        worktrees["memory"] = remove_registered_worktree(
            contract.memory_repo_path, contract.memory_worktree, dry_run, force=force
        )
    return worktrees


def _abandon_branches(
    contract: WorktreeContract, *, dry_run: bool, force: bool
) -> dict[str, dict[str, object]]:
    branches = {
        "code": _abandon_branch(
            contract.code_repo_path,
            contract.code_work_branch,
            contract.code_source_branch,
            dry_run=dry_run,
            force=force,
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        branches["memory"] = _abandon_branch(
            contract.memory_repo_path,
            contract.memory_work_branch,
            contract.memory_source_branch,
            dry_run=dry_run,
            force=force,
        )
        integration_work_branch = integration_branch(contract)
        if branch_exists(contract.memory_repo_path, integration_work_branch):
            branches["memory_integration"] = _abandon_branch(
                contract.memory_repo_path,
                integration_work_branch,
                contract.memory_source_branch,
                dry_run=dry_run,
                force=force,
            )
    return branches


def _abandon_branch(
    repo: Path, branch: str, base_branch: str, *, dry_run: bool, force: bool
) -> dict[str, object]:
    if not branch_exists(repo, branch):
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if force:
        return delete_branch_force(repo, branch, dry_run)
    unmerged = _unmerged_commits(repo, base_branch, branch)
    if unmerged:
        return {
            "branch": branch,
            "deleted": False,
            "reason": "unmerged",
            "unmergedCommits": unmerged,
            "hint": "re-run abandon with force=true to discard these commits",
        }
    return delete_branch_if_merged(repo, branch, dry_run)


def _unmerged_commits(repo: Path, base_branch: str, branch: str) -> list[str]:
    if not base_branch or not branch_exists(repo, base_branch):
        return []
    result = run_git(repo, ["log", "--oneline", f"{base_branch}..{branch}"])
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _abandon_directories(
    contract: WorktreeContract, *, dry_run: bool, force: bool
) -> dict[str, dict[str, object]]:
    group = contract.worktree_group
    directories = {
        "worktree_group": (
            remove_tree(group, dry_run=dry_run) if force else remove_empty_dir(group, dry_run)
        ),
    }
    if group.parent.exists():
        directories["repo_worktree_group"] = remove_empty_dir(group.parent, dry_run)
    return directories


def _abandon_blockers(
    removed_worktrees: dict[str, dict[str, object]],
    branches: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for key, item in removed_worktrees.items():
        if _is_blocker(item, done_key="removed", pending_key="would_remove"):
            blockers.append({"worktree": key, "reason": item.get("reason")})
    for key, item in branches.items():
        if _is_blocker(item, done_key="deleted", pending_key="would_delete"):
            blocker: dict[str, object] = {"branch": key, "reason": item.get("reason")}
            if item.get("unmergedCommits"):
                blocker["unmergedCommits"] = item.get("unmergedCommits")
            blockers.append(blocker)
    return blockers


def _is_blocker(item: dict[str, object], *, done_key: str, pending_key: str) -> bool:
    if item.get(done_key) or item.get(pending_key):
        return False
    return item.get("reason") not in {"already-absent", None}


def _abandon_state(dry_run: bool, reclaimed: bool) -> str:
    if dry_run:
        return "would-abandon"
    return "abandoned" if reclaimed else "abandon-blocked"


def _abandon_summary(dry_run: bool, reclaimed: bool) -> str:
    if dry_run:
        return "Abandon preview: the listed provider resources, worktrees, branches, and dirs would be removed."
    if reclaimed:
        return "Worktree abandoned: provider stack reclaimed, worktrees and branches removed, group dir cleared."
    return (
        "Abandon blocked: some worktrees/branches were kept (dirty or unmerged). "
        "Re-run with force=true to discard them."
    )
