from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from agents_remember.errors import CitationCacheError
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import discard_memory_cache_changes
from agents_remember.kernel.primitives.drift_snapshot import remove_drift_snapshot
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TERMINAL_CLEANUP_STATES,
)
from agents_remember.models.lifecycles.enclosure import TerminalWorktreeCleanupArguments
from agents_remember.worktrees.activation.atomic_series_activation_terminal import (
    with_terminal_atomic_series_release,
)
from agents_remember.worktrees.disabled_memory import is_disabled_contract, opt_out_refusal_result
from agents_remember.worktrees.integration.atomic_series_terminal import (
    AtomicSeriesTerminalPermit,
    require_atomic_series_terminal_permit,
    require_atomic_series_terminal_release,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    memory_repository_default_branch,
    repository_default_branch,
    require_terminal_worktree,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    TerminalCleanupContractAuthority,
    terminal_archive_required_result,
    terminal_contract_authority_if_present,
)
from agents_remember.worktrees.modules import terminal_cleanup
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.modules.guidance import carryover_done, status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.state_refusals import (
    RefusalNext,
    provider_setup_blocked,
    shaped_terminal_blockers,
    state_refusal_result,
    terminal_state_refusal,
)
from agents_remember.worktrees.modules.terminal_agents import archive_terminal_agents
from agents_remember.worktrees.modules.terminal_branch_retirement import (
    _TERMINAL_MUTATION_CAPABILITY,
    RetiringBranch,
    _kept_line_branch_proof,
    _normalized_branch,
    _repository_key,
    _require_terminal_capability,
    _required_repository_identity,
    _retire_work_branch,
    _TerminalMutationAuthority,
    delete_branch_if_merged_into,
)
from agents_remember.worktrees.modules.terminal_inventory import (
    _absent_branch_entries,
    _absent_worktree_entries,
    _mark_without_remains,
    _require_terminal_note_readback,
    terminal_without_remains_reason,
)
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalPreflight,
    TerminalResult,
    legacy_series_reports_is_child_enclosure,
    terminal_preflight,
    terminal_result_blockers,
)
from agents_remember.worktrees.role_reports import (
    RoleReportCollectionError,
    collect_group_reports,
    remove_group_role_folders,
)
from agents_remember.worktrees.services import TerminalGuard, worktree_services
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)

type TerminalItems = dict[str, dict[str, object]]
ENCLOSURE_REPORTS_DIRECTORY = "reports"
# MIK-R108 rule 3: the tool-call log lives here, outside both worktrees and
# outside Git. A closeout preview never lists it; cleanup removes it.
ENCLOSURE_LOGS_DIRECTORY = "logs"
type CleanupOutputs = tuple[
    dict[str, object],
    TerminalItems,
    TerminalItems,
    TerminalItems,
    TerminalItems,
]


def _terminal_mutation_authority(
    contract: WorktreeContract,
    *,
    operation: Literal["worktree_cleanup", "worktree_abandon"],
    series_permit: AtomicSeriesTerminalPermit | None = None,
) -> _TerminalMutationAuthority:
    if contract.kind == "series":
        require_atomic_series_terminal_permit(contract, operation, series_permit)
    require_terminal_worktree(contract, operation=operation)
    worktrees: set[tuple[Path, Path]] = set()
    branches: set[tuple[Path, str, str]] = set()
    remote_branches: set[tuple[Path, str]] = set()
    memory_worktree = None

    code_repository = _required_repository_identity(contract.code_repo_path, "code")
    if contract.kind == "leaf":
        worktrees.add((code_repository, contract.code_worktree.resolve()))
    code_branch = _normalized_branch(contract.code_work_branch)
    branches.add((code_repository, code_branch, _normalized_branch(contract.code_source_branch)))
    if operation == "worktree_cleanup":
        remote_branches.add((code_repository, code_branch))

    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        memory_repository = _required_repository_identity(contract.memory_repo_path, "memory")
        if contract.kind == "leaf" and contract.memory_worktree is not None:
            memory_worktree = contract.memory_worktree.resolve()
            worktrees.add((memory_repository, memory_worktree))
        branches.add(
            (
                memory_repository,
                _normalized_branch(contract.memory_work_branch),
                _normalized_branch(contract.memory_source_branch),
            )
        )
    elif (
        operation == "worktree_abandon"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        # The abandon's own authority reaches a carried memory leg and branch
        # whatever mode the contract carries (MIK-R50 rule 3): no conversion,
        # just no shield. Cleanup keeps the external-only shape.
        memory_repository = _required_repository_identity(contract.memory_repo_path, "memory")
        if contract.kind == "leaf" and contract.memory_worktree is not None:
            memory_worktree = contract.memory_worktree.resolve()
            worktrees.add((memory_repository, memory_worktree))
        branches.add(
            (
                memory_repository,
                _normalized_branch(contract.memory_work_branch),
                _normalized_branch(contract.memory_source_branch),
            )
        )
    return _TerminalMutationAuthority(
        operation=operation,
        worktrees=frozenset(worktrees),
        memory_worktree=memory_worktree,
        branches=frozenset(branches),
        remote_branches=frozenset(remote_branches),
        _capability=_TERMINAL_MUTATION_CAPABILITY,
    )


def _require_worktree_target(
    authority: _TerminalMutationAuthority | None, repo: Path, worktree: Path
) -> None:
    _require_terminal_capability(authority)
    assert authority is not None
    if (_repository_key(repo), worktree.resolve()) not in authority.worktrees:
        raise RuntimeError("terminal worktree removal target is outside contract authority")


def remove_registered_worktree(
    repo: Path,
    worktree: Path,
    dry_run: bool,
    *,
    force: bool = False,
    authority: _TerminalMutationAuthority | None = None,
) -> dict[str, object]:
    _require_worktree_target(authority, repo, worktree)
    if not worktree.exists():
        return {"path": worktree.as_posix(), "removed": False, "reason": "already-absent"}
    if dry_run:
        return {"path": worktree.as_posix(), "removed": False, "would_remove": True}
    assert authority is not None
    if worktree.resolve() == authority.memory_worktree:
        discard_memory_cache_changes(worktree)
    command = ["worktree", "remove", *(["--force"] if force else []), str(worktree)]
    result = run_git(repo, command)
    if result.returncode != 0:
        return {
            "path": worktree.as_posix(),
            "removed": False,
            "reason": result.stderr.strip() or "git worktree remove failed",
        }
    return {"path": worktree.as_posix(), "removed": True}


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


def _removed_worktrees(
    contract: WorktreeContract,
    dry_run: bool,
    *,
    authority: _TerminalMutationAuthority,
) -> dict[str, dict[str, object]]:
    if contract.kind == "series":
        return {}
    removed_worktrees = {
        "code": remove_registered_worktree(
            contract.code_repo_path,
            contract.code_worktree,
            dry_run,
            authority=authority,
        ),
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_worktree is not None
    ):
        removed_worktrees["memory"] = remove_registered_worktree(
            contract.memory_repo_path,
            contract.memory_worktree,
            dry_run,
            authority=authority,
        )
    return removed_worktrees


def _deleted_branches(
    contract: WorktreeContract,
    dry_run: bool,
    *,
    authority: _TerminalMutationAuthority,
) -> dict[str, dict[str, object]]:
    # Cleanup operates on the just-finalized child edge only: remove the task work branches
    # after they are proven reachable from their parent/source branches. Parent/source branches
    # are the next node up the task tree and are finalized/cleaned by their own lifecycle edge.
    code_default = repository_default_branch(contract.code_repo_path)
    targets: list[tuple[str, RetiringBranch, bool]] = [
        (
            "code",
            RetiringBranch(
                repo=contract.code_repo_path,
                branch=contract.code_work_branch,
                source_branch=contract.code_source_branch,
                default_branch=code_default,
            ),
            True,
        )
    ]
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        mem_default = memory_repository_default_branch(contract.memory_repo_path)
        targets.append(
            (
                "memory",
                RetiringBranch(
                    repo=contract.memory_repo_path,
                    branch=contract.memory_work_branch,
                    source_branch=contract.memory_source_branch,
                    default_branch=mem_default,
                ),
                False,
            )
        )
    if not dry_run:
        # Guard phase (read-only): prove every branch may go before the first deletion.
        # A sibling whose guard refuses withholds the whole stage; nothing is deleted.
        guards = {
            key: delete_branch_if_merged_into(
                target.repo,
                target.branch,
                target.source_branch,
                True,
                default_branch=target.default_branch,
                authority=authority,
            )
            for key, target, _ in targets
        }
        refused = {
            key: probe
            for key, probe in guards.items()
            if not probe.get("would_delete") and probe.get("reason") != "already-absent"
        }
        if refused:
            withheld: dict[str, dict[str, object]] = {}
            for key, target, _ in targets:
                if key in refused:
                    probe = refused[key]
                    withheld[key] = {
                        "branch": target.branch,
                        "deleted": False,
                        "reason": probe.get("reason"),
                    }
                else:
                    first = next(iter(refused.items()))
                    withheld[key] = {
                        "branch": target.branch,
                        "deleted": False,
                        "reason": "withheld-sibling-branch-refused",
                        "sibling": first[0],
                        "siblingReason": first[1].get("reason"),
                    }
            return withheld
    branches: dict[str, dict[str, object]] = {
        key: _retire_work_branch(target, dry_run, remote=remote, authority=authority)
        for key, target, remote in targets
    }
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
    reports_path = contract.worktree_group / ENCLOSURE_REPORTS_DIRECTORY
    lifecycle_path = contract.worktree_group / ".lifecycle"
    reports = (
        {
            "path": reports_path.as_posix(),
            "removed": False,
            "preserved": True,
            "reason": "child-enclosure",
        }
        if contract.kind == "series" and legacy_series_reports_is_child_enclosure(contract)
        else worktree_services().provider_lifecycle.remove_tree(reports_path, dry_run=dry_run)
    )
    lifecycle = worktree_services().provider_lifecycle.remove_tree(
        lifecycle_path,
        dry_run=dry_run,
    )
    # MIK-R108 rule 3: cleanup removes <enclosure>/logs/ with the enclosure.
    logs_path = contract.worktree_group / ENCLOSURE_LOGS_DIRECTORY
    logs = worktree_services().provider_lifecycle.remove_tree(
        logs_path,
        dry_run=dry_run,
    )
    if contract.kind == "series":
        return {"reports": reports, "lifecycle": lifecycle}
    role_folders = remove_group_role_folders(contract, dry_run=dry_run)
    planned = set(planned_removed or ())
    planned.update(
        Path(str(row["path"]))
        for row in role_folders.values()
        if row.get("would_remove") or row.get("removed")
    )
    if reports.get("removed") or reports.get("would_remove"):
        planned.add(reports_path.resolve())
    if lifecycle.get("removed") or lifecycle.get("would_remove"):
        planned.add(lifecycle_path.resolve())
    if logs.get("removed") or logs.get("would_remove"):
        planned.add(logs_path.resolve())
    directories = {
        **role_folders,
        "reports": reports,
        "lifecycle": lifecycle,
        "logs": logs,
        "worktree_group": remove_empty_dir(contract.worktree_group, dry_run, planned),
    }
    if contract.worktree_group.parent.exists():
        directories["repo_worktree_group"] = remove_empty_dir(
            contract.worktree_group.parent, dry_run, planned
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


def _finish_cleanup_without_remains(
    args: WorktreeArgs,
    contract: WorktreeContract,
    reason: str,
) -> WorktreeCommandResult:
    """Finish a remains-free cleanup: publish ``cleanup=completed`` with the reason."""

    assert args.contract_path is not None
    try:
        guard_context = worktree_services().citation_guard.guard(
            contract,
            requested_contract_path=args.contract_path,
        )
        guard = guard_context.__enter__()
    except CitationCacheError as error:
        cause = "live-lease-timeout" if "live lease" in str(error) else str(error)
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                "state": "blocked",
                "summary": "Cleanup could not reserve exact terminal citation cache authority.",
                "citation_source_index": {
                    "removed": False,
                    "reason": cause,
                    "detail": str(error),
                },
            },
        )
    try:
        providers: dict[str, object] = (
            worktree_services().provider_lifecycle.teardown(contract, dry_run=args.dry_run)
            if args.teardown_providers
            else {"state": "skipped", "reason": "teardown_providers disabled"}
        )
        removed_worktrees = _absent_worktree_entries(contract)
        branches = _absent_branch_entries(contract)
        drift_snapshots = {
            "code": remove_drift_snapshot(
                contract.coordination_root,
                repository=contract.code_worktree.name,
                branch=contract.code_work_branch,
                dry_run=args.dry_run,
            )
        }
        directories = _removed_directories(contract, args.dry_run, None)
        preflight = TerminalPreflight(
            worktrees=dict(removed_worktrees),
            branches=dict(branches),
            blockers=(),
        )
        result = _cleanup_outputs_result(
            args,
            contract,
            preflight,
            guard,
            (providers, removed_worktrees, branches, drift_snapshots, directories),
            terminal_note=reason,
        )
    finally:
        guard_context.__exit__(None, None, None)
    if args.dry_run:
        summary = (
            "Cleanup preview: no locator, worktree folder, or work branches remain; "
            "approving would record the cleanup with nothing to archive and nothing "
            "to delete."
        )
    else:
        summary = (
            "Cleanup completed: no locator, worktree folder, or work branches "
            "remained; there was nothing to archive and nothing to delete."
        )
    return _mark_without_remains(result, reason, summary=summary)


def _cleanup_without_remains_result(
    args: WorktreeArgs,
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """MIK-R82 rule 5: finish a remains-free enclosure before the carryover gate."""

    without_remains = terminal_without_remains_reason(contract, operation="worktree_cleanup")
    if without_remains is None:
        return None
    # The liveness read is side-effect free: the dry run evaluates it too, so
    # a dry run never promises a cleanup the real call would refuse.
    if worktree_services().provider_lifecycle.setup_running(contract):
        return provider_setup_blocked(
            contract,
            summary=(
                "Provider setup is still running for this worktree; wait for a "
                "terminal providers state (worktree_status) before cleanup."
            ),
        )
    return with_terminal_atomic_series_release(
        contract,
        _finish_cleanup_without_remains(args, contract, without_remains),
        dry_run=args.dry_run,
    )


def _kept_line_containing(
    repo: Path, tip: str, source_branch: str, *, default_branch: str | None
) -> tuple[str, str] | None:
    """The kept line holding ``tip``: the landing/source line first, then the default.

    Returns the line and the commit it holds the tip in. A probe, never a refusal:
    unresolvable lines simply do not match. The caller fails closed on ``None``.
    """
    # One proof for the carryover question and the branch check (MIK-R82 rule 5).
    return _kept_line_branch_proof(repo, tip, source_branch, default_branch)


def _landed_tips_on_kept_line(contract: WorktreeContract) -> dict[str, object] | None:
    """Both work tips on a kept line (MIK-R82 rule 5, amended in place).

    Where the ledger shows no carryover and the code tip and the parked memory tip are
    each reachable from the line the leaf lands on or from the default line of their
    repository, the work has landed as it is: no recovery leaf is asked for. Records
    both tips, the lines and the commits they were found in. ``None`` keeps the
    neither-line refusal exactly as it is.
    """
    if contract.memory_mode != "external" or contract.memory_repo_path is None:
        return None
    try:
        code_tip = branch_commit(contract.code_repo_path, contract.code_work_branch)
        memory_tip = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    except RuntimeError:
        return None
    try:
        code_default: str | None = repository_default_branch(contract.code_repo_path)
    except Exception:
        code_default = None
    try:
        memory_default: str | None = memory_repository_default_branch(contract.memory_repo_path)
    except Exception:
        memory_default = None
    code_found = _kept_line_containing(
        contract.code_repo_path,
        code_tip,
        contract.code_source_branch,
        default_branch=code_default,
    )
    if code_found is None:
        return None
    memory_found = _kept_line_containing(
        contract.memory_repo_path,
        memory_tip,
        contract.memory_source_branch,
        default_branch=memory_default,
    )
    if memory_found is None:
        return None
    return {
        "codeTip": code_tip,
        "codeLine": code_found[0],
        "codeFoundIn": code_found[1],
        "memoryTip": memory_tip,
        "memoryLine": memory_found[0],
        "memoryFoundIn": memory_found[1],
    }


def _carryover_or_kept_line(contract: WorktreeContract) -> dict[str, object] | None:
    """The carryover gate (MIK-R82 rule 5, amended in place).

    Returns the kept-line record where the ledger shows no carryover and both work
    tips sit on a kept line; returns ``None`` where the ledger shows a carryover.
    Raises the recovery-leaf refusal where a tip sits on neither kept line.
    """
    carried, _carried_at = carryover_done(contract)
    if carried:
        return None
    # Where both tips sit on a kept line, the work has landed as it is and no
    # recovery leaf is asked for. The record rides the result payload; a tip on
    # neither line keeps the refusal exactly as it is.
    kept_line = _landed_tips_on_kept_line(contract)
    if kept_line is None:
        raise RuntimeError(
            "cleanup requires the exact landed memory mapping; create an open carryover "
            "recovery leaf, close and integrate it, then retry cleanup before discarding the "
            "parked memory branch"
        )
    return kept_line


def _require_series_terminal_release(contract: WorktreeContract) -> None:
    """Require the series terminal release, passing leaves through untouched."""

    if contract.kind == "series":
        require_atomic_series_terminal_release(contract)


def _carryover_or_kept_line_value(
    contract: WorktreeContract,
) -> dict[str, object] | WorktreeCommandResult | None:
    """Answer a missing carryover, or the kept-line record (``None`` passes vacuously).

    05m: carryover must have run first -- it reads the parked memory branch
    this step deletes. The signal is the official ledger (carryover_done), not
    a contract stamp; a disabled-memory contract has no memory worktree to
    carry and passes vacuously.
    """
    try:
        return _carryover_or_kept_line(contract)
    except RuntimeError as error:
        refused = terminal_state_refusal(contract, error)
        if refused is None:
            raise
        return refused


def _cleanup_disabled_refusal(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """Refuse a removed-mode contract, or ``None`` when cleanup applies.

    MIK-R50 R4a: a contract that carries the removed mode is never cleaned
    up; the way out is abandon and a fresh start, not cleanup.
    """
    if not is_disabled_contract(contract):
        return None
    return opt_out_refusal_result(contract, "worktree_cleanup")


def _cleanup_terminal_branch(
    contract: WorktreeContract,
    terminal: TerminalCleanupContractAuthority,
    args: WorktreeArgs,
) -> tuple[WorktreeContract, WorktreeCommandResult | None, dict[str, object] | None]:
    """Follow the terminal archive branch, answering or handing back the contract."""
    accepted_arguments = TerminalWorktreeCleanupArguments(
        teardown_providers=args.teardown_providers
    )
    if (
        terminal.archive.cleanupOperation != "worktree_cleanup"
        or terminal.archive.cleanupArguments != accepted_arguments
    ):
        return (
            contract,
            _terminal_archive_observation(
                contract,
                teardown_providers=args.teardown_providers,
            ),
            None,
        )
    if terminal.state == "cleanup-completed":
        result = with_terminal_atomic_series_release(
            contract,
            _already_completed_cleanup(
                contract,
                teardown_providers=args.teardown_providers,
            ),
            dry_run=args.dry_run,
        )
        agents = archive_terminal_agents(contract, dry_run=args.dry_run)
        return (
            contract,
            WorktreeCommandResult(
                result.returncode,
                {**result.payload, **({"agentArchive": agents} if agents else {})},
            ),
            None,
        )
    # MIK-R129 rule 1 per L129-THE-GATE-IS-NOT-JUDGED-AGAIN-RULING: on the archive
    # branch the gate is not judged again. TerminalEnclosureArchive declares no
    # kept-line record of that judgement (and forbids extra fields), so the repeat
    # carries no record; its answer may lack landedTips but invents none.
    return terminal.archived_contract, None, None


def _cleanup_preflight_refusal(
    contract: WorktreeContract, preflight: TerminalPreflight
) -> WorktreeCommandResult | None:
    """Answer terminal-preflight blockers, dry run or real, or ``None`` when clear.

    MIK-R111 rule 1: the state and the blockers agree, in a dry run as in a
    real call. A dry run promises nothing the real call would refuse on the
    same state, so a dry run with blockers answers blocked with the blockers,
    never would-cleanup. Every blocker carries its code and one sentence
    beside the producer's keys.
    """
    if not preflight.blockers:
        return None
    return WorktreeCommandResult(
        2,
        {
            **status_payload(contract),
            "state": "blocked",
            "code": "cleanup-blocked",
            "error": "Cleanup terminal preflight refused before any mutation.",
            "summary": "Cleanup terminal preflight refused before any mutation.",
            "blockers": shaped_terminal_blockers(preflight.blockers),
        },
    )


def _cleanup_provider_refusal(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """Answer a running provider setup, dry run or real, or ``None`` when settled.

    The liveness read is side-effect free, so the dry run evaluates it too: a
    dry run never promises a cleanup the real call would refuse.
    """
    if not worktree_services().provider_lifecycle.setup_running(contract):
        return None
    # Teardown must not race the live background setup thread (GitHub #53);
    # a dead thread surfaces as a stale heartbeat and does not block.
    return provider_setup_blocked(
        contract,
        summary=(
            "Provider setup is still running for this worktree; wait for a "
            "terminal providers state (worktree_status) before cleanup."
        ),
    )


def _cleanup_integration_refusal(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """Answer a cleanup before its integration completed, or ``None`` when done.

    MIK-R111 rule 7: a cleanup before the integration completed is a
    task-state refusal, not a raise. The next step is the one call
    that follows.
    """
    if contract.integration_status == "completed":
        return None
    return state_refusal_result(
        contract,
        state="blocked",
        code="integration-incomplete",
        error="cleanup requires integration.status completed",
        next=RefusalNext(
            tool="worktree_integrate",
            args={"contract_path": contract.contract_path.as_posix()},
            summary="Integrate the closeout, then clean up again.",
        ),
    )


def _series_terminal_release_refusal(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """Answer a failed series terminal release, or ``None`` when released.

    Reachable terminal preconditions answer with their code and sentence;
    anything unrecognized stays a raise (fail closed).
    """
    try:
        _require_series_terminal_release(contract)
        require_terminal_worktree(contract, operation="worktree_cleanup")
    except RuntimeError as error:
        refused = terminal_state_refusal(contract, error)
        if refused is None:
            raise
        return refused
    return None


def cleanup_result(args: WorktreeArgs) -> WorktreeCommandResult:  # noqa: PLR0911
    if not args.approved and not args.dry_run:
        raise RuntimeError("cleanup requires --approved after successful integration")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    disabled = _cleanup_disabled_refusal(contract)
    if disabled is not None:
        return disabled
    terminal = terminal_contract_authority_if_present(contract)
    if terminal is not None:
        contract, terminal_result, kept_line = _cleanup_terminal_branch(contract, terminal, args)
        if terminal_result is not None:
            return terminal_result
    else:
        released = _series_terminal_release_refusal(contract)
        if released is not None:
            return released
        incomplete = _cleanup_integration_refusal(contract)
        if incomplete is not None:
            return incomplete
        # MIK-R82 rule 5: an enclosure with no locator and no remains finishes
        # here, before the carryover gate -- there is no parked memory branch
        # left to carry from, and nothing to archive or delete.
        without_remains = _cleanup_without_remains_result(args, contract)
        if without_remains is not None:
            return without_remains
        kept_line = _carryover_or_kept_line_value(contract)
        if isinstance(kept_line, WorktreeCommandResult):
            return kept_line
    return _cleanup_settled(args, contract, kept_line)


def _cleanup_settled(
    args: WorktreeArgs,
    contract: WorktreeContract,
    kept_line: dict[str, object] | None,
) -> WorktreeCommandResult:
    """Run the settled tail: provider guard, terminal preflight, reservation.

    The liveness read is side-effect free: the dry run evaluates it too, so
    a dry run never promises a cleanup the real call would refuse.
    """
    provider_blocked = _cleanup_provider_refusal(contract)
    if provider_blocked is not None:
        return provider_blocked

    preflight = terminal_preflight(contract, mode="cleanup", force=False)
    blocked = _cleanup_preflight_refusal(contract, preflight)
    if blocked is not None:
        return blocked

    return with_terminal_atomic_series_release(
        contract,
        _cleanup_reserved(args, contract, preflight, kept_line=kept_line),
        dry_run=args.dry_run,
    )


def _completed_contract_remains(contract: WorktreeContract) -> bool:
    """A terminal completed contract with folder or branches still present.

    MIK-R82 rule 5, completed contract with remains: the contract says the
    cleanup is completed while the worktree folder or the work branches still
    exist. Only a probe, never a refusal: unresolvable branches simply do not
    count, and the caller fails closed without remains.
    """
    if contract.cleanup not in TERMINAL_CLEANUP_STATES:
        return False
    folders = [
        path
        for path in (contract.code_worktree, contract.memory_worktree)
        if path is not None and path.exists()
    ]
    branches: list[str] = []
    try:
        branch_commit(contract.code_repo_path, contract.code_work_branch)
        branches.append(contract.code_work_branch)
    except RuntimeError:
        pass
    if contract.memory_repo_path is not None:
        try:
            branch_commit(contract.memory_repo_path, contract.memory_work_branch)
            branches.append(contract.memory_work_branch)
        except RuntimeError:
            pass
    return bool(folders or branches)


def _cleanup_reserved(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    *,
    kept_line: dict[str, object] | None = None,
) -> WorktreeCommandResult:
    assert args.contract_path is not None

    try:
        guard_context = worktree_services().citation_guard.guard(
            contract,
            requested_contract_path=args.contract_path,
            completed_contract_with_remains=_completed_contract_remains(contract),
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
        return terminal_cleanup._cleanup_with_guard(
            args, contract, preflight, guard, kept_line=kept_line
        )
    finally:
        guard_context.__exit__(None, None, None)


def _terminal_archive_observation(
    contract: WorktreeContract,
    *,
    teardown_providers: bool,
) -> WorktreeCommandResult:
    return terminal_archive_required_result(
        contract,
        operation="worktree_cleanup",
        arguments=TerminalWorktreeCleanupArguments(teardown_providers=teardown_providers),
        dry_run=False,
    )


def _already_completed_cleanup(
    contract: WorktreeContract,
    *,
    teardown_providers: bool,
) -> WorktreeCommandResult:
    terminal_archive = _terminal_archive_observation(
        contract,
        teardown_providers=teardown_providers,
    )
    if terminal_archive.returncode != 0:
        return terminal_archive
    return _with_terminal_archive(
        WorktreeCommandResult(
            0,
            {
                "state": "already-clean",
                **status_payload(contract),
                "summary": _cleanup_summary("already-clean"),
                "providers": {"state": "already-terminal"},
                "removed_worktrees": {},
                "branches": {},
                "drift_snapshots": {},
                "directories": {},
                "blockers": [],
                "kept_branches": {},
                "alreadyTerminal": True,
            },
        ),
        terminal_archive,
    )


def _with_terminal_archive(
    result: WorktreeCommandResult,
    terminal_archive: WorktreeCommandResult,
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        result.returncode,
        {**result.payload, "terminalArchive": terminal_archive.payload},
    )


def _cleanup_outputs_result(  # noqa: PLR0913 -- terminal_note rides the no-remains publish path
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    guard: TerminalGuard,
    outputs: CleanupOutputs,
    *,
    terminal_note: str | None = None,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, drift_snapshots, directories = outputs
    blockers = terminal_result_blockers(
        TerminalResult(
            providers=providers,
            worktrees=removed_worktrees,
            branches=branches,
            directories=directories,
            drift_snapshots=drift_snapshots,
            preview=args.dry_run,
        )
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
    return _publish_cleanup(contract, guard, outputs, terminal_note=terminal_note)


def _publish_cleanup(
    contract: WorktreeContract,
    guard: TerminalGuard,
    outputs: CleanupOutputs,
    *,
    terminal_note: str | None = None,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, drift_snapshots, directories = outputs
    updated = amend_contract(
        contract,
        ContractCells(cleanup="completed", terminal_note=terminal_note),
    )
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
    if terminal_note is not None:
        readback_error = _require_terminal_note_readback(contract.contract_path, terminal_note)
        if readback_error is not None:
            return WorktreeCommandResult(
                2,
                {
                    "state": "blocked",
                    **status_payload(contract),
                    "summary": "Cleanup contract/cache publication failed and was rolled back.",
                    "citation_source_index": _preserved_cache(
                        guard.preview(), "terminal-publication-failed"
                    ),
                    "blockers": [{"publication": "contract/cache", "reason": readback_error}],
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
    *,
    series_permit: AtomicSeriesTerminalPermit | None = None,
) -> CleanupOutputs:
    authority = _terminal_mutation_authority(
        contract,
        operation="worktree_cleanup",
        series_permit=series_permit,
    )
    try:
        collect_group_reports(contract, dry_run=args.dry_run)
    except RoleReportCollectionError as error:
        return {}, {}, {}, {}, {"role_reports": {"removed": False, "reason": str(error)}}
    providers: dict[str, object] = (
        worktree_services().provider_lifecycle.teardown(contract, dry_run=args.dry_run)
        if args.teardown_providers
        else {"state": "skipped", "reason": "teardown_providers disabled"}
    )
    if not args.dry_run and terminal_result_blockers(
        TerminalResult(providers=providers, worktrees={}, branches={}, directories={})
    ):
        return providers, {}, {}, {}, {}
    removed_worktrees = (
        _removed_worktrees(contract, dry_run=True, authority=authority)
        if args.dry_run
        else _removed_worktrees(contract, dry_run=False, authority=authority)
    )
    if not args.dry_run and terminal_result_blockers(
        TerminalResult(
            providers=providers, worktrees=removed_worktrees, branches={}, directories={}
        )
    ):
        return providers, removed_worktrees, {}, {}, {}
    branches = (
        preflight.branches
        if args.dry_run
        else _deleted_branches(contract, dry_run=False, authority=authority)
    )
    if not args.dry_run and terminal_result_blockers(
        TerminalResult(
            providers=providers, worktrees=removed_worktrees, branches=branches, directories={}
        )
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
        TerminalResult(
            providers=providers,
            worktrees=removed_worktrees,
            branches=branches,
            directories={},
            drift_snapshots=drift_snapshots,
        )
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
