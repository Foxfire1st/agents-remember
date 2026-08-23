"""Abandon a worktree-backed task without integrating it.

Abandon is the discard sibling of cleanup. cleanup runs only after a completed
integration; abandon exists for worktrees whose work will NOT land (premature
trials, dead-end experiments, the l-01-agent-lifecycles orchestrator read-only/abandon exit). It reclaims the
isolated provider stack (containers, networks, provider-runtime tree), removes
the code and memory worktrees, deletes the task branches, and removes the
worktree group dir.

Safety: without `force`, abandon refuses to delete a branch that still has
commits not on its source branch (reporting them) and refuses to remove a dirty
worktree. With `force` it discards them (`git worktree remove --force`,
`git branch -D`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.errors import CitationCacheError
from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_terminal_worktree,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_lease import (
    contract_lifecycle_lease,
    require_lifecycle_operation_compatible,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    ENCLOSURE_REPORTS_DIRECTORY,
    _terminal_mutation_authority,
    _TerminalMutationAuthority,
    delete_branch_force,
    delete_branch_if_merged,
    local_branch_presence,
    remove_empty_dir,
    remove_registered_worktree,
)
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalPreflight,
    legacy_series_reports_is_child_enclosure,
    terminal_preflight,
    terminal_result_blockers,
)
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    AtomicSeriesTerminalPermit,
    publish_atomic_series_terminal_under_authority,
    require_atomic_series_terminal_release,
)
from agents_remember.worktrees.services import TerminalGuard, worktree_services
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)

TerminalItems: TypeAlias = dict[str, dict[str, object]]
AbandonOutputs: TypeAlias = tuple[
    dict[str, object],
    TerminalItems,
    TerminalItems,
    TerminalItems,
]


@dataclass(frozen=True)
class _AbandonBranchTarget:
    repository: Path
    branch: str
    source_branch: str


def abandon_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("abandon requires --approved (use dry_run to preview)")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    archive_refusal = terminal_archive_required_result(
        contract,
        operation="worktree_abandon",
        dry_run=args.dry_run,
    )
    if archive_refusal.returncode != 0:
        return archive_refusal
    if contract.kind == "series":
        require_atomic_series_terminal_release(contract)
    require_terminal_worktree(contract, operation="worktree_abandon")
    if (
        not args.dry_run
        and not args.force
        and worktree_services().provider_lifecycle.setup_running(contract)
    ):
        # Teardown must not race the live background setup thread (GitHub #53);
        # a dead thread surfaces as a stale heartbeat and does not block.
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                "state": "blocked",
                "summary": (
                    "Provider setup is still running for this worktree; wait for a "
                    "terminal providers state (worktree_status) or pass force=true."
                ),
            },
        )

    preflight = terminal_preflight(contract, mode="abandon", force=args.force)
    if preflight.blockers and not args.dry_run:
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                "state": "abandon-blocked",
                "summary": "Abandon terminal preflight refused before any mutation.",
                "blockers": list(preflight.blockers),
            },
        )

    return _abandon_reserved(args, contract, preflight)


def _abandon_reserved(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
) -> WorktreeCommandResult:
    assert args.contract_path is not None

    try:
        guard_context = worktree_services().citation_guard.guard(
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
                "state": "abandon-blocked",
                "summary": "Abandon could not reserve exact terminal citation cache authority.",
                "citation_source_index": {
                    "removed": False,
                    "reason": reason,
                    "detail": str(error),
                },
                "blockers": [],
            },
        )
    try:
        return _abandon_with_guard(args, contract, preflight, guard)
    finally:
        guard_context.__exit__(None, None, None)


def _abandon_with_guard(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    guard: TerminalGuard,
) -> WorktreeCommandResult:
    try:
        with contract_lifecycle_lease(contract):
            require_lifecycle_operation_compatible(
                contract,
                operation_kind=None,
                publish_worker_exits=not args.dry_run,
            )

            def publish(
                series_permit: AtomicSeriesTerminalPermit | None = None,
            ) -> WorktreeCommandResult:
                current = load_contract(contract.contract_path)
                if current != contract:
                    raise RuntimeError("abandon contract changed before terminal mutation")
                outputs = _abandon_terminal_outputs(
                    args,
                    current,
                    preflight,
                    series_permit=series_permit,
                )
                return _abandon_outputs_result(args, current, preflight, guard, outputs)

            if contract.kind == "series":
                return publish_atomic_series_terminal_under_authority(
                    contract,
                    "worktree_abandon",
                    publish,
                )
            with integration_authority_lock(contract.coordination_root, contract.repo_name):
                return publish()
    except Exception as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "abandon-blocked",
                **status_payload(contract),
                "summary": "Abandon terminal helper failed; cache and contract stayed live.",
                "citation_source_index": _preserved_cache(
                    guard.preview(), "terminal-helper-failed"
                ),
                "blockers": [{"terminal": "helper", "reason": str(error)}],
            },
        )


def _abandon_outputs_result(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    guard: TerminalGuard,
    outputs: AbandonOutputs,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, directories = outputs
    blockers = terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches=branches,
        directories=directories,
    )
    if blockers and not args.dry_run:
        return WorktreeCommandResult(
            2,
            {
                "state": "abandon-blocked",
                **status_payload(contract),
                "summary": "Abandon terminal mutation failed; cache and contract stayed live.",
                "providers": providers,
                "citation_source_index": _preserved_cache(
                    guard.preview(), "terminal-operation-failed"
                ),
                "removed_worktrees": removed_worktrees,
                "branches": branches,
                "directories": directories,
                "blockers": blockers,
            },
        )
    if args.dry_run:
        return WorktreeCommandResult(
            0,
            {
                "state": "would-abandon",
                **status_payload(contract),
                "summary": _abandon_summary(True, not preflight.blockers),
                "providers": providers,
                "citation_source_index": guard.preview(),
                "removed_worktrees": removed_worktrees,
                "branches": branches,
                "directories": directories,
                "blockers": list(preflight.blockers),
            },
        )
    return _publish_abandon(contract, guard, outputs)


def _publish_abandon(
    contract: WorktreeContract,
    guard: TerminalGuard,
    outputs: AbandonOutputs,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, directories = outputs
    updated = amend_contract(contract, ContractCells(cleanup="abandoned"))
    try:
        citation_cache = guard.complete(
            outcome="abandoned",
            publish=lambda: write_contract(contract.contract_path, updated),
            rollback_publish=lambda: write_contract(contract.contract_path, contract),
        )
    except Exception as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "abandon-blocked",
                **status_payload(contract),
                "summary": "Abandon contract/cache publication failed and was rolled back.",
                "citation_source_index": _preserved_cache(
                    guard.preview(), "terminal-publication-failed"
                ),
                "blockers": [{"publication": "contract/cache", "reason": str(error)}],
            },
        )
    return WorktreeCommandResult(
        0,
        {
            "state": "abandoned",
            **status_payload(updated),
            "summary": _abandon_summary(False, True),
            "providers": providers,
            "citation_source_index": citation_cache,
            "removed_worktrees": removed_worktrees,
            "branches": branches,
            "directories": directories,
            "blockers": [],
        },
    )


def _abandon_terminal_outputs(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    *,
    series_permit: AtomicSeriesTerminalPermit | None = None,
) -> AbandonOutputs:
    authority = _terminal_mutation_authority(
        contract,
        operation="worktree_abandon",
        series_permit=series_permit,
    )
    providers: dict[str, object] = worktree_services().provider_lifecycle.teardown(
        contract, dry_run=args.dry_run
    )
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees={},
        branches={},
        directories={},
    ):
        return providers, {}, {}, {}
    removed_worktrees = (
        _abandon_worktrees(
            contract,
            dry_run=True,
            force=args.force,
            authority=authority,
        )
        if args.dry_run
        else _abandon_worktrees(
            contract,
            dry_run=False,
            force=args.force,
            authority=authority,
        )
    )
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches={},
        directories={},
    ):
        return providers, removed_worktrees, {}, {}
    branches = (
        preflight.branches
        if args.dry_run
        else _abandon_branches(
            contract,
            dry_run=False,
            force=args.force,
            authority=authority,
        )
    )
    if not args.dry_run and terminal_result_blockers(
        providers=providers,
        worktrees=removed_worktrees,
        branches=branches,
        directories={},
    ):
        return providers, removed_worktrees, branches, {}
    directories = _abandon_directories(
        contract,
        dry_run=args.dry_run,
        force=args.force,
    )
    return providers, removed_worktrees, branches, directories


def _abandon_worktrees(
    contract: WorktreeContract,
    *,
    dry_run: bool,
    force: bool,
    authority: _TerminalMutationAuthority,
) -> dict[str, dict[str, object]]:
    if contract.kind == "series":
        return {}
    worktrees = {
        "code": remove_registered_worktree(
            contract.code_repo_path,
            contract.code_worktree,
            dry_run,
            force=force,
            authority=authority,
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_worktree is not None
    ):
        worktrees["memory"] = remove_registered_worktree(
            contract.memory_repo_path,
            contract.memory_worktree,
            dry_run,
            force=force,
            authority=authority,
        )
    return worktrees


def _abandon_branches(
    contract: WorktreeContract,
    *,
    dry_run: bool,
    force: bool,
    authority: _TerminalMutationAuthority,
) -> dict[str, dict[str, object]]:
    branches = {
        "code": _abandon_branch(
            _AbandonBranchTarget(
                contract.code_repo_path,
                contract.code_work_branch,
                contract.code_source_branch,
            ),
            dry_run=dry_run,
            force=force,
            authority=authority,
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        branches["memory"] = _abandon_branch(
            _AbandonBranchTarget(
                contract.memory_repo_path,
                contract.memory_work_branch,
                contract.memory_source_branch,
            ),
            dry_run=dry_run,
            force=force,
            authority=authority,
        )
    return branches


def _abandon_branch(
    target: _AbandonBranchTarget,
    *,
    dry_run: bool,
    force: bool,
    authority: _TerminalMutationAuthority,
) -> dict[str, object]:
    repo = target.repository
    branch = target.branch
    base_branch = target.source_branch
    source_refusal = _branch_presence_refusal(
        repo,
        base_branch,
        reported_branch=branch,
        absent_reason=f"base branch is missing: {base_branch}",
    )
    if source_refusal is not None:
        return source_refusal
    branch_refusal = _branch_presence_refusal(
        repo,
        branch,
        reported_branch=branch,
        absent_reason="already-absent",
    )
    if branch_refusal is not None:
        return branch_refusal
    if force:
        return delete_branch_force(repo, branch, dry_run, authority=authority)
    try:
        unmerged = _unmerged_commits(repo, base_branch, branch)
    except RuntimeError as error:
        return {"branch": branch, "deleted": False, "reason": str(error)}
    if unmerged:
        return {
            "branch": branch,
            "deleted": False,
            "reason": "unmerged",
            "unmergedCommits": unmerged,
            "hint": "re-run abandon with force=true to discard these commits",
        }
    return delete_branch_if_merged(repo, branch, dry_run, authority=authority)


def _branch_presence_refusal(
    repo: Path,
    queried_branch: str,
    *,
    reported_branch: str,
    absent_reason: str,
) -> dict[str, object] | None:
    presence = local_branch_presence(repo, queried_branch)
    if presence.state == "present":
        return None
    return {
        "branch": reported_branch,
        "deleted": False,
        "reason": presence.reason if presence.state == "error" else absent_reason,
    }


def _unmerged_commits(repo: Path, base_branch: str, branch: str) -> list[str]:
    if not base_branch:
        raise RuntimeError("base branch is empty")
    base = run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{base_branch}"])
    if base.returncode != 0:
        reason = base.stderr.strip() or f"base branch is missing: {base_branch}"
        raise RuntimeError(reason)
    result = run_git(
        repo,
        ["log", "--oneline", f"refs/heads/{base_branch}..refs/heads/{branch}"],
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git log query failed")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _preserved_cache(cache: dict[str, object], reason: str) -> dict[str, object]:
    return {
        **cache,
        "removed": False,
        "preserved": True,
        "reason": reason,
    }


def _abandon_directories(
    contract: WorktreeContract, *, dry_run: bool, force: bool
) -> dict[str, dict[str, object]]:
    group = contract.worktree_group
    if contract.kind == "series":
        reports = group / ENCLOSURE_REPORTS_DIRECTORY
        return {
            "reports": (
                {
                    "path": reports.as_posix(),
                    "removed": False,
                    "preserved": True,
                    "reason": "child-enclosure",
                }
                if legacy_series_reports_is_child_enclosure(contract)
                else worktree_services().provider_lifecycle.remove_tree(
                    reports,
                    dry_run=dry_run,
                )
            )
        }
    if force:
        directories = {
            "worktree_group": worktree_services().provider_lifecycle.remove_tree(
                group, dry_run=dry_run
            )
        }
    else:
        reports_path = group / ENCLOSURE_REPORTS_DIRECTORY
        reports = worktree_services().provider_lifecycle.remove_tree(reports_path, dry_run=dry_run)
        planned = (
            {reports_path.resolve()}
            if reports.get("removed") or reports.get("would_remove")
            else set()
        )
        directories = {
            "reports": reports,
            "worktree_group": remove_empty_dir(group, dry_run, planned),
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
