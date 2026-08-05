from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from agents_remember.errors import CitationCacheError
from agents_remember.kernel.git_command import GIT_REMOTE_TIMEOUT_SECONDS, run_git
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
    terminal_namespace_guard,
)
from agents_remember.observer.drift_snapshots import remove_drift_snapshot
from agents_remember.worktrees.modules import provider_async
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.guidance import carryover_done, status_payload
from agents_remember.worktrees.modules.integrate import integration_branch
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.provider_teardown import teardown_worktree_providers
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalPreflight,
    terminal_preflight,
    terminal_result_blockers,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)

TerminalItems: TypeAlias = dict[str, dict[str, object]]
CleanupOutputs: TypeAlias = tuple[
    dict[str, object],
    TerminalItems,
    TerminalItems,
    TerminalItems,
    TerminalItems,
]


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
    presence = local_branch_presence(repo, branch)
    if presence.state == "error":
        return {"branch": branch, "deleted": False, "reason": presence.reason}
    if presence.state == "absent":
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
    presence = local_branch_presence(repo, branch)
    if presence.state == "error":
        return {"branch": branch, "deleted": False, "reason": presence.reason}
    if presence.state == "absent":
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
    presence = local_branch_presence(repo, branch)
    if presence.state == "error":
        return {"branch": branch, "deleted": False, "reason": presence.reason}
    if presence.state == "absent":
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


@dataclass(frozen=True)
class LocalBranchPresence:
    state: Literal["present", "absent", "error"]
    reason: str = ""


def local_branch_presence(repo: Path, branch: str) -> LocalBranchPresence:
    if not branch:
        return LocalBranchPresence("error", "branch is empty")
    result = run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    if result.returncode == 0:
        return LocalBranchPresence("present")
    if result.returncode == 1 and not result.stderr.strip():
        return LocalBranchPresence("absent")
    return LocalBranchPresence("error", result.stderr.strip() or "git ref query failed")


def _repo_default_branch(repo: Path) -> str:
    """The repo's default branch (e.g. ``main``) from the local ``origin/HEAD`` symref; ``"main"`` on
    failure. Used only to refuse ever deleting the default branch during work-branch cleanup."""
    res = run_git(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().split("/", 1)[-1]
    return "main"


def _remote_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a remote-talking git command under the remote bound; ``None`` when it stalled.

    Both callers below run inside an MCP tool call, which the client cannot cancel. They
    used to go through a runner that set no timeout at all, so an unreachable or wedged
    remote held the tool call open forever. A stall now reads exactly like the
    already-handled unreachable-remote case rather than escaping as an exception.
    """
    try:
        return run_git(repo, args, timeout=GIT_REMOTE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None


def delete_remote_branch_if_present(repo: Path, branch: str, dry_run: bool) -> dict[str, object]:
    """Delete ``origin/<branch>`` if it still exists -- a PR branch survives a non-deleting merge (05m)."""
    if not branch:
        return {"remote_deleted": False, "reason": "empty"}
    probe = _remote_git(repo, ["ls-remote", "--heads", "origin", branch])
    if probe is None:
        return {"remote_deleted": False, "reason": "remote-unreachable"}
    if probe.returncode != 0:
        return {
            "remote_deleted": False,
            "reason": probe.stderr.strip() or "remote-unreachable",
        }
    if not probe.stdout.strip():
        return {"remote_deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"remote_deleted": False, "would_delete": True}
    return _push_branch_deletion(repo, branch)


def _origin_refusal(repo: Path) -> dict[str, object] | None:
    remotes = run_git(repo, ["remote"])
    if remotes.returncode != 0:
        return {
            "remote_deleted": False,
            "reason": remotes.stderr.strip() or "git remote query failed",
        }
    if "origin" not in remotes.stdout.splitlines():
        return {"remote_deleted": False, "reason": "already-absent"}
    return None


def _push_branch_deletion(repo: Path, branch: str) -> dict[str, object]:
    res = _remote_git(repo, ["push", "origin", "--delete", branch])
    if res is None:
        return {"remote_deleted": False, "reason": "remote-unreachable"}
    if res.returncode != 0:
        return {"remote_deleted": False, "reason": res.stderr.strip() or "git push --delete failed"}
    return {"remote_deleted": True}


@dataclass(frozen=True)
class RetiringBranch:
    """One task work branch on its way out.

    The repo it lives in, the branch itself, the source branch it must be proven merged into
    before deletion, and that repo's default branch -- the one to check out when the branch
    being deleted is the one currently checked out. Retirement can never consult any of these
    without the others, and each call site derives the whole set from one contract side.
    """

    repo: Path
    branch: str
    source_branch: str
    default_branch: str


def _retire_work_branch(
    target: RetiringBranch,
    dry_run: bool,
    *,
    remote: bool,
) -> dict[str, object]:
    repo = target.repo
    branch = target.branch
    out: dict[str, object] = {"branch": branch}
    if not branch or branch == target.default_branch:
        out.update({"deleted": False, "reason": "default-or-empty"})
        return out
    if not dry_run and run_git(repo, ["branch", "--show-current"]).stdout.strip() == branch:
        run_git(repo, ["checkout", target.default_branch])
    out.update(delete_branch_if_merged_into(repo, branch, target.source_branch, dry_run))
    if remote and (
        out.get("deleted") or out.get("would_delete") or out.get("reason") == "already-absent"
    ):
        out["remote"] = _retire_remote_branch(
            repo,
            branch,
            dry_run,
        )
    return out


def _retire_remote_branch(
    repo: Path,
    branch: str,
    dry_run: bool,
) -> dict[str, object]:
    origin_refusal = _origin_refusal(repo)
    if origin_refusal is not None:
        return origin_refusal
    return delete_remote_branch_if_present(repo, branch, dry_run)


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
            RetiringBranch(
                repo=contract.code_repo_path,
                branch=contract.code_work_branch,
                source_branch=contract.code_source_branch,
                default_branch=code_default,
            ),
            dry_run,
            remote=True,
        )
    }
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        mem_default = _repo_default_branch(contract.memory_repo_path)
        branches["memory"] = _retire_work_branch(
            RetiringBranch(
                repo=contract.memory_repo_path,
                branch=contract.memory_work_branch,
                source_branch=contract.memory_source_branch,
                default_branch=mem_default,
            ),
            dry_run,
            remote=False,
        )
        integration_work_branch = integration_branch(contract)
        branches["memory_integration"] = _retire_work_branch(
            RetiringBranch(
                repo=contract.memory_repo_path,
                branch=integration_work_branch,
                source_branch=contract.memory_source_branch,
                default_branch=mem_default,
            ),
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

    preflight = terminal_preflight(contract, mode="cleanup", force=False)
    if preflight.blockers and not args.dry_run:
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                "state": "blocked",
                "summary": "Cleanup terminal preflight refused before any mutation.",
                "blockers": list(preflight.blockers),
            },
        )

    return _cleanup_reserved(args, contract, preflight)


def _cleanup_reserved(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
) -> WorktreeCommandResult:
    assert args.contract_path is not None

    try:
        guard_context = terminal_namespace_guard(
            contract,
            requested_contract_path=args.contract_path,
        )
        guard = guard_context.__enter__()
    except CitationCacheError as error:
        reason = "live-lease-timeout" if "live lease" in str(error) else str(error)
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                "state": "blocked",
                "summary": "Cleanup could not reserve exact terminal citation cache authority.",
                "citation_source_index": {
                    "removed": False,
                    "reason": reason,
                    "detail": str(error),
                },
            },
        )
    try:
        return _cleanup_with_guard(args, contract, preflight, guard)
    finally:
        guard_context.__exit__(None, None, None)


def _cleanup_with_guard(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    guard: TerminalNamespaceGuard,
) -> WorktreeCommandResult:
    # The exact leaf fence remains held through every terminal output and publication.
    try:
        outputs = _cleanup_terminal_outputs(args, contract, preflight)
    except Exception as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "summary": "Cleanup terminal helper failed; cache and contract stayed live.",
                "citation_source_index": _preserved_cache(
                    guard.preview(), "terminal-helper-failed"
                ),
                "blockers": [{"terminal": "helper", "reason": str(error)}],
            },
        )
    providers, removed_worktrees, branches, drift_snapshots, directories = outputs
    blockers = terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches=branches,
        directories=directories,
        drift_snapshots=drift_snapshots,
    )
    if blockers and not args.dry_run:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "summary": "Cleanup terminal mutation failed; cache and contract stayed live.",
                "providers": providers,
                "citation_source_index": _preserved_cache(
                    guard.preview(), "terminal-operation-failed"
                ),
                "removed_worktrees": removed_worktrees,
                "branches": branches,
                "drift_snapshots": drift_snapshots,
                "directories": directories,
                "blockers": blockers,
                "kept_branches": _kept_branches(branches),
            },
        )
    if args.dry_run:
        return WorktreeCommandResult(
            0,
            {
                "state": "would-cleanup",
                **status_payload(contract),
                "summary": _cleanup_summary("would-cleanup"),
                "providers": providers,
                "citation_source_index": guard.preview(),
                "removed_worktrees": removed_worktrees,
                "branches": branches,
                "drift_snapshots": drift_snapshots,
                "directories": directories,
                "blockers": list(preflight.blockers),
                "kept_branches": _kept_branches(branches),
            },
        )
    return _publish_cleanup(contract, guard, outputs)


def _publish_cleanup(
    contract: WorktreeContract,
    guard: TerminalNamespaceGuard,
    outputs: CleanupOutputs,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, drift_snapshots, directories = outputs
    updated = amend_contract(contract, ContractCells(cleanup="completed"))
    try:
        citation_cache = guard.complete(
            outcome="completed",
            publish=lambda: write_contract(contract.contract_path, updated),
            rollback_publish=lambda: write_contract(contract.contract_path, contract),
        )
    except Exception as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "summary": "Cleanup contract/cache publication failed and was rolled back.",
                "citation_source_index": _preserved_cache(
                    guard.preview(), "terminal-publication-failed"
                ),
                "blockers": [{"publication": "contract/cache", "reason": str(error)}],
            },
        )
    state = _cleanup_state(False, True, removed_worktrees, branches)
    return WorktreeCommandResult(
        0,
        {
            "state": state,
            **status_payload(updated),
            "summary": _cleanup_summary(state),
            "providers": providers,
            "citation_source_index": citation_cache,
            "removed_worktrees": removed_worktrees,
            "branches": branches,
            "drift_snapshots": drift_snapshots,
            "directories": directories,
            "kept_branches": _kept_branches(branches),
        },
    )


def _cleanup_terminal_outputs(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
) -> CleanupOutputs:
    providers: dict[str, object] = (
        teardown_worktree_providers(contract, dry_run=args.dry_run)
        if args.teardown_providers
        else {"state": "skipped", "reason": "teardown_providers disabled"}
    )
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees={},
        branches={},
        directories={},
    ):
        return providers, {}, {}, {}, {}
    removed_worktrees = (
        _removed_worktrees(contract, dry_run=True)
        if args.dry_run
        else _removed_worktrees(contract, dry_run=False)
    )
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches={},
        directories={},
    ):
        return providers, removed_worktrees, {}, {}, {}
    branches = preflight.branches if args.dry_run else _deleted_branches(contract, dry_run=False)
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches=branches,
        directories={},
    ):
        return providers, removed_worktrees, branches, {}, {}
    drift_snapshots = {
        "code": remove_drift_snapshot(
            contract.coordination_root,
            repository=contract.code_worktree.name,
            branch=contract.code_work_branch,
            dry_run=args.dry_run,
        )
    }
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches=branches,
        directories={},
        drift_snapshots=drift_snapshots,
    ):
        return providers, removed_worktrees, branches, drift_snapshots, {}
    planned_removed = (
        _scheduled_removal_paths(providers, removed_worktrees) if args.dry_run else None
    )
    directories = _removed_directories(contract, args.dry_run, planned_removed)
    return providers, removed_worktrees, branches, drift_snapshots, directories


def _preserved_cache(cache: dict[str, object], reason: str) -> dict[str, object]:
    return {
        **cache,
        "removed": False,
        "preserved": True,
        "reason": reason,
    }
