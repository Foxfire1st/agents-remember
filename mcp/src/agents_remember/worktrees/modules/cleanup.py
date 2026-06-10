from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents_remember.worktrees.modules import provider_async
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_exists, run_git
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.integrate import integration_branch
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.provider_teardown import teardown_worktree_providers
from agents_remember.worktrees.worktree_contract import load_contract, write_contract


def remove_registered_worktree(
    repo: Path, worktree: Path, dry_run: bool, *, force: bool = False
) -> dict[str, object]:
    if not worktree.exists():
        return {"path": worktree.as_posix(), "removed": False, "reason": "already-absent"}
    if dry_run:
        return {"path": worktree.as_posix(), "removed": False, "would_remove": True}
    command = ["worktree", "remove", *(["--force"] if force else []), str(worktree)]
    result = run_git(repo, command)
    if result.returncode != 0:
        return {
            "path": worktree.as_posix(),
            "removed": False,
            "reason": result.stderr.strip() or "git worktree remove failed",
        }
    return {"path": worktree.as_posix(), "removed": True}


def delete_branch_if_merged(repo: Path, branch: str, dry_run: bool) -> dict[str, object]:
    if not branch_exists(repo, branch):
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"branch": branch, "deleted": False, "would_delete": True}
    result = run_git(repo, ["branch", "-d", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -d refused the branch",
        }
    return {"branch": branch, "deleted": True}


def delete_branch_force(repo: Path, branch: str, dry_run: bool) -> dict[str, object]:
    """Discard a branch even if unmerged (`git branch -D`). Used by abandon force."""
    if not branch_exists(repo, branch):
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"branch": branch, "deleted": False, "would_delete": True, "force": True}
    result = run_git(repo, ["branch", "-D", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -D failed",
        }
    return {"branch": branch, "deleted": True, "force": True}


def remove_empty_dir(path: Path, dry_run: bool) -> dict[str, object]:
    if not path.exists():
        return {"path": path.as_posix(), "removed": False, "reason": "already-absent"}
    if any(path.iterdir()):
        return {"path": path.as_posix(), "removed": False, "reason": "not-empty"}
    if dry_run:
        return {"path": path.as_posix(), "removed": False, "would_remove": True}
    path.rmdir()
    return {"path": path.as_posix(), "removed": True}


def _removed_worktrees(contract, dry_run: bool) -> dict[str, dict[str, object]]:
    removed_worktrees = {
        "code": remove_registered_worktree(
            contract.code_repo_path, contract.code_worktree, dry_run
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_worktree is not None
    ):
        removed_worktrees["memory"] = remove_registered_worktree(
            contract.memory_repo_path, contract.memory_worktree, dry_run
        )
    return removed_worktrees


def _deleted_branches(contract, dry_run: bool) -> dict[str, dict[str, object]]:
    branches = {
        "code": delete_branch_if_merged(
            contract.code_repo_path, contract.code_work_branch, dry_run
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        branches["memory"] = delete_branch_if_merged(
            contract.memory_repo_path, contract.memory_work_branch, dry_run
        )
        integration_work_branch = integration_branch(contract)
        if branch_exists(contract.memory_repo_path, integration_work_branch):
            branches["memory_integration"] = delete_branch_if_merged(
                contract.memory_repo_path, integration_work_branch, dry_run
            )
    return branches


def _removed_directories(contract, dry_run: bool) -> dict[str, dict[str, object]]:
    directories = {
        "worktree_group": remove_empty_dir(contract.worktree_group, dry_run),
    }
    if contract.worktree_group.parent.exists():
        directories["repo_worktree_group"] = remove_empty_dir(
            contract.worktree_group.parent, dry_run
        )
    return directories


def _cleanup_state(
    dry_run: bool,
    cleanup_completed: bool,
    removed_worktrees: dict[str, dict[str, object]],
    branches: dict[str, dict[str, object]],
) -> str:
    if dry_run:
        return "would-cleanup"
    already_clean = (
        all(
            not item.get("removed") and item.get("reason") == "already-absent"
            for item in removed_worktrees.values()
        )
        and all(
            not item.get("deleted") and item.get("reason") == "already-absent"
            for item in branches.values()
        )
        and cleanup_completed
    )
    return "already-clean" if already_clean else "cleanup-completed"


def _kept_branches(branches: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        key: value
        for key, value in branches.items()
        if not value.get("deleted") and value.get("reason") not in {"already-absent", None}
    }


def cleanup_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("cleanup requires --approved after successful integration")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    if contract.integration_status != "completed":
        raise RuntimeError("cleanup requires integration.status completed")
    if not args.dry_run and provider_async.provider_setup_running(contract):
        # Teardown must not race the live background setup thread (GitHub #53);
        # a dead thread surfaces as a stale heartbeat and does not block.
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                "state": "blocked",
                "summary": (
                    "Provider setup is still running for this worktree; wait for a "
                    "terminal providers state (worktree_status) before cleanup."
                ),
            },
        )

    # Reclaim the worktree's isolated provider stack first so the (now provider-free)
    # worktree group dir can be removed below. shutdown-all leaves backends/networks.
    providers = (
        teardown_worktree_providers(contract, dry_run=args.dry_run)
        if args.teardown_providers
        else {"state": "skipped", "reason": "teardown_providers disabled"}
    )
    removed_worktrees = _removed_worktrees(contract, args.dry_run)
    branches = _deleted_branches(contract, args.dry_run)
    directories = _removed_directories(contract, args.dry_run)
    updated = contract if args.dry_run else replace(contract, cleanup="completed")
    if not args.dry_run:
        write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": _cleanup_state(
                args.dry_run, updated.cleanup == "completed", removed_worktrees, branches
            ),
            **status_payload(updated),
            "summary": "Cleanup completed; the worktree provider stack was reclaimed, worktrees were removed and merged local task branches were deleted where Git proved they were merged.",
            "providers": providers,
            "removed_worktrees": removed_worktrees,
            "branches": branches,
            "directories": directories,
            "kept_branches": _kept_branches(branches),
        },
    )
