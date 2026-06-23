from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from agents_remember.worktrees.modules import provider_async
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_exists, is_ancestor, run_git
from agents_remember.worktrees.modules.guidance import carryover_done, status_payload
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


def delete_branch_if_merged_into(
    repo: Path, branch: str, target_ref: str, dry_run: bool
) -> dict[str, object]:
    if not branch_exists(repo, branch):
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if not is_ancestor(repo, branch, target_ref):
        return {
            "branch": branch,
            "deleted": False,
            "reason": "not-merged-into-source",
            "target": target_ref,
        }
    if dry_run:
        return {
            "branch": branch,
            "deleted": False,
            "would_delete": True,
            "target": target_ref,
        }
    result = run_git(repo, ["branch", "-D", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -D failed",
            "target": target_ref,
        }
    return {"branch": branch, "deleted": True, "target": target_ref}


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


def _repo_default_branch(repo: Path) -> str:
    """The repo's default branch (e.g. ``main``) from the local ``origin/HEAD`` symref; ``"main"`` on
    failure. Used only to refuse ever deleting the default branch during work-branch cleanup."""
    res = run_git(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().split("/", 1)[-1]
    return "main"


def delete_remote_branch_if_present(repo: Path, branch: str, dry_run: bool) -> dict[str, object]:
    """Delete ``origin/<branch>`` if it still exists -- a PR branch survives a non-deleting merge (05m)."""
    if not branch:
        return {"remote_deleted": False, "reason": "empty"}
    probe = run_git(repo, ["ls-remote", "--heads", "origin", branch])
    if probe.returncode != 0:
        return {"remote_deleted": False, "reason": "remote-unreachable"}
    if not probe.stdout.strip():
        return {"remote_deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"remote_deleted": False, "would_delete": True}
    res = run_git(repo, ["push", "origin", "--delete", branch])
    if res.returncode != 0:
        return {"remote_deleted": False, "reason": res.stderr.strip() or "git push --delete failed"}
    return {"remote_deleted": True}


def _retire_work_branch(
    repo: Path,
    branch: str,
    source_branch: str,
    default_branch: str,
    dry_run: bool,
    *,
    remote: bool,
) -> dict[str, object]:
    out: dict[str, object] = {"branch": branch}
    if not branch or branch == default_branch:
        out.update({"deleted": False, "reason": "default-or-empty"})
        return out
    if not dry_run and run_git(repo, ["branch", "--show-current"]).stdout.strip() == branch:
        run_git(repo, ["checkout", default_branch])
    out.update(delete_branch_if_merged_into(repo, branch, source_branch, dry_run))
    if remote and (out.get("deleted") or out.get("would_delete")):
        out["remote"] = delete_remote_branch_if_present(repo, branch, dry_run)
    return out


def remove_empty_dir(
    path: Path, dry_run: bool, planned_removed: set[Path] | None = None
) -> dict[str, object]:
    if not path.exists():
        return {"path": path.as_posix(), "removed": False, "reason": "already-absent"}
    if dry_run and planned_removed is not None:
        remaining = {child.resolve() for child in path.iterdir()} - planned_removed
        if remaining:
            return {"path": path.as_posix(), "removed": False, "reason": "not-empty"}
        return {"path": path.as_posix(), "removed": False, "would_remove": True}
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
    # Cleanup operates on the just-finalized child edge only: remove the task work branches
    # after they are proven reachable from their parent/source branches. Parent/source branches
    # are the next node up the task tree and are finalized/cleaned by their own lifecycle edge.
    code_default = _repo_default_branch(contract.code_repo_path)
    branches: dict[str, dict[str, object]] = {
        "code": _retire_work_branch(
            contract.code_repo_path,
            contract.code_work_branch,
            contract.code_source_branch,
            code_default,
            dry_run,
            remote=True,
        )
    }
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        mem_default = _repo_default_branch(contract.memory_repo_path)
        branches["memory"] = _retire_work_branch(
            contract.memory_repo_path,
            contract.memory_work_branch,
            contract.memory_source_branch,
            mem_default,
            dry_run,
            remote=False,
        )
        integration_work_branch = integration_branch(contract)
        if branch_exists(contract.memory_repo_path, integration_work_branch):
            branches["memory_integration"] = _retire_work_branch(
                contract.memory_repo_path,
                integration_work_branch,
                contract.memory_source_branch,
                mem_default,
                dry_run,
                remote=False,
            )
    return branches


def _scheduled_removal_paths(
    providers: Mapping[str, object], removed_worktrees: dict[str, dict[str, object]]
) -> set[Path]:
    planned: set[Path] = set()
    for item in removed_worktrees.values():
        if item.get("removed") or item.get("would_remove"):
            planned.add(Path(str(item["path"])).resolve())
    provider_runtime = providers.get("providerRuntime")
    if isinstance(provider_runtime, dict) and (
        provider_runtime.get("removed") or provider_runtime.get("would_remove")
    ):
        planned.add(Path(str(provider_runtime["path"])).resolve())
    return planned


def _removed_directories(
    contract, dry_run: bool, planned_removed: set[Path] | None = None
) -> dict[str, dict[str, object]]:
    directories = {
        "worktree_group": remove_empty_dir(contract.worktree_group, dry_run, planned_removed),
    }
    if contract.worktree_group.parent.exists():
        directories["repo_worktree_group"] = remove_empty_dir(
            contract.worktree_group.parent, dry_run, planned_removed
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
            not item.get("deleted") and item.get("reason") in {"already-absent", "default-or-empty"}
            for item in branches.values()
        )
        and cleanup_completed
    )
    return "already-clean" if already_clean else "cleanup-completed"


def _cleanup_summary(state: str) -> str:
    if state == "would-cleanup":
        return (
            "Cleanup would reclaim the worktree provider stack, remove worktrees, "
            "and delete merged local task branches where Git proves they are merged."
        )
    if state == "already-clean":
        return (
            "Cleanup already completed; no worktrees or merged local task branches "
            "remained to remove."
        )
    return (
        "Cleanup completed; the worktree provider stack was reclaimed, worktrees "
        "were removed and merged local task branches were deleted where Git proved "
        "they were merged."
    )


def _kept_branches(branches: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        key: value
        for key, value in branches.items()
        if not value.get("deleted")
        and value.get("reason") not in {"already-absent", "default-or-empty", None}
    }


def cleanup_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("cleanup requires --approved after successful integration")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    if contract.integration_status != "completed":
        raise RuntimeError("cleanup requires integration.status completed")
    # 05m: carryover must have run first -- it reads the parked memory branch this step deletes.
    # The signal is the official ledger (carryover_done), not a contract stamp; internal/disabled
    # memory has nothing to carry and passes vacuously.
    carried, _carried_at = carryover_done(contract)
    if not carried:
        raise RuntimeError(
            "cleanup requires carryover completed (run memory_carryover_apply first); cleaning up "
            "now would discard the parked memory branch"
        )
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
    planned_removed = (
        _scheduled_removal_paths(providers, removed_worktrees) if args.dry_run else None
    )
    directories = _removed_directories(contract, args.dry_run, planned_removed)
    updated = contract if args.dry_run else replace(contract, cleanup="completed")
    if not args.dry_run:
        write_contract(contract.contract_path, updated)
    state = _cleanup_state(
        args.dry_run, updated.cleanup == "completed", removed_worktrees, branches
    )
    return WorktreeCommandResult(
        0,
        {
            "state": state,
            **status_payload(updated),
            "summary": _cleanup_summary(state),
            "providers": providers,
            "removed_worktrees": removed_worktrees,
            "branches": branches,
            "directories": directories,
            "kept_branches": _kept_branches(branches),
        },
    )
