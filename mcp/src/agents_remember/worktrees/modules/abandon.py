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

from agents_remember.errors import CitationCacheError
from agents_remember.kernel.git_command import run_git
from agents_remember.models.lifecycles.enclosure import TerminalWorktreeAbandonArguments
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.abandon_task_publication import publish_abandoned_task
from agents_remember.worktrees.activation.atomic_series_activation_terminal import (
    with_terminal_atomic_series_release,
)
from agents_remember.worktrees.integration.atomic_series_terminal import (
    AtomicSeriesTerminalPermit,
    require_atomic_series_terminal_release,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_terminal_worktree,
)
from agents_remember.worktrees.integration.lifecycle.outside_scratch_reconcile import (
    with_outside_scratch_reconcile,
)
from agents_remember.worktrees.integration.terminal_cleanup_completion import (
    write_completion_record,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    TerminalCleanupContractAuthority,
    terminal_archive_required_result,
    terminal_contract_authority_if_present,
)
from agents_remember.worktrees.modules import terminal_abandon
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    ENCLOSURE_LOGS_DIRECTORY,
    ENCLOSURE_REPORTS_DIRECTORY,
    _terminal_mutation_authority,
    _with_terminal_archive,
    remove_empty_dir,
    remove_registered_worktree,
)
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.state_refusals import (
    provider_setup_blocked,
    shaped_terminal_blockers,
    task_stage_refusal,
    terminal_state_refusal,
)
from agents_remember.worktrees.modules.terminal_agents import archive_terminal_agents
from agents_remember.worktrees.modules.terminal_branch_retirement import (
    _TerminalMutationAuthority,
    delete_branch_force,
    delete_branch_if_merged,
)
from agents_remember.worktrees.modules.terminal_inventory import (
    _absent_branch_entries,
    _absent_worktree_entries,
    _mark_without_remains,
    _require_terminal_note_readback,
    local_branch_presence,
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
from agents_remember.worktrees.task_stage import plan_task_stage
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
)

type TerminalItems = dict[str, dict[str, object]]
type AbandonOutputs = tuple[
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


def _abandon_terminal_branch(
    contract: WorktreeContract,
    terminal: TerminalCleanupContractAuthority,
    args: WorktreeArgs,
) -> tuple[WorktreeContract, WorktreeCommandResult | None]:
    """Follow the terminal archive branch, answering or handing back the contract."""
    accepted_arguments = TerminalWorktreeAbandonArguments(force=args.force)
    if (
        terminal.archive.cleanupOperation != "worktree_abandon"
        or terminal.archive.cleanupArguments != accepted_arguments
    ):
        return contract, _terminal_archive_observation(contract, force=args.force)
    if terminal.state == "cleanup-completed":
        result = with_terminal_atomic_series_release(
            contract,
            _already_abandoned(contract, force=args.force, dry_run=args.dry_run),
            dry_run=args.dry_run,
        )
        agents = archive_terminal_agents(contract, dry_run=args.dry_run)
        return contract, WorktreeCommandResult(
            result.returncode,
            {**result.payload, **({"agentArchive": agents} if agents else {})},
        )
    return terminal.archived_contract, None


def _abandon_series_release_refusal(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """Answer a failed series terminal release, or ``None`` when released.

    Reachable terminal preconditions answer with their code and sentence;
    anything unrecognized stays a raise (fail closed).
    """
    try:
        if contract.kind == "series":
            require_atomic_series_terminal_release(contract)
        require_terminal_worktree(contract, operation="worktree_abandon")
    except RuntimeError as error:
        refused = terminal_state_refusal(contract, error)
        if refused is None:
            raise
        return refused
    return None


def abandon_result(args: WorktreeArgs) -> WorktreeCommandResult:
    """Abandon the worktree, with the automatic outside-scratch reconcile (MIK-R207)."""

    return with_outside_scratch_reconcile(_abandon_result(args), dry_run=args.dry_run)


def _abandon_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("abandon requires --approved (use dry_run to preview)")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    terminal = terminal_contract_authority_if_present(contract)
    if terminal is not None:
        contract, terminal_result = _abandon_terminal_branch(contract, terminal, args)
        if terminal_result is not None:
            return terminal_result
    else:
        series_refused = _abandon_series_release_refusal(contract)
        if series_refused is not None:
            return series_refused
    # The liveness read is side-effect free: the dry run evaluates it too, so
    # a dry run never promises an abandon the real call would refuse. Force
    # still overrides; a dry run is not force.
    if not args.force and worktree_services().provider_lifecycle.setup_running(contract):
        # Teardown must not race the live background setup thread (GitHub #53);
        # a dead thread surfaces as a stale heartbeat and does not block.
        return provider_setup_blocked(
            contract,
            summary=(
                "Provider setup is still running for this worktree; wait for a "
                "terminal providers state (worktree_status) or pass force=true."
            ),
        )

    without_remains = terminal_without_remains_reason(contract, operation="worktree_abandon")
    if without_remains is not None:
        return with_terminal_atomic_series_release(
            contract,
            _finish_abandon_without_remains(args, contract, without_remains),
            dry_run=args.dry_run,
        )
    preflight = terminal_preflight(contract, mode="abandon", force=args.force)
    blocked = _abandon_preflight_refusal(contract, preflight)
    if blocked is not None:
        return blocked

    return with_terminal_atomic_series_release(
        contract,
        _abandon_reserved(args, contract, preflight),
        dry_run=args.dry_run,
    )


def _abandon_preflight_refusal(
    contract: WorktreeContract, preflight: TerminalPreflight
) -> WorktreeCommandResult | None:
    """Answer terminal-preflight blockers, dry run or real, or ``None`` when clear.

    A dry run never promises an abandon the real call would refuse. Every
    blocker carries its code and one sentence beside the producer's keys.
    """
    if not preflight.blockers:
        return None
    return WorktreeCommandResult(
        2,
        {
            **status_payload(contract),
            "state": "abandon-blocked",
            "code": "abandon-blocked",
            "error": "Abandon terminal preflight refused before any mutation.",
            "summary": "Abandon terminal preflight refused before any mutation.",
            "blockers": shaped_terminal_blockers(preflight.blockers),
        },
    )


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
        return terminal_abandon._abandon_with_guard(args, contract, preflight, guard)
    finally:
        guard_context.__exit__(None, None, None)


def _terminal_archive_observation(
    contract: WorktreeContract,
    *,
    force: bool,
) -> WorktreeCommandResult:
    return terminal_archive_required_result(
        contract,
        operation="worktree_abandon",
        arguments=TerminalWorktreeAbandonArguments(force=force),
        dry_run=False,
    )


def _finish_abandon_without_remains(
    args: WorktreeArgs,
    contract: WorktreeContract,
    reason: str,
) -> WorktreeCommandResult:
    """Finish a remains-free abandon: publish ``cleanup=abandoned`` with the reason."""

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
                "state": "abandon-blocked",
                "summary": "Abandon could not reserve exact terminal citation cache authority.",
                "citation_source_index": {
                    "removed": False,
                    "reason": cause,
                    "detail": str(error),
                },
                "blockers": [],
            },
        )
    try:
        try:
            plan_task_stage(contract, "abandoned")
        except ValueError as error:
            return task_stage_refusal(contract, error)
        providers: dict[str, object] = worktree_services().provider_lifecycle.teardown(
            contract, dry_run=args.dry_run
        )
        removed_worktrees = _absent_worktree_entries(contract)
        branches = _absent_branch_entries(contract)
        directories = _abandon_directories(
            contract,
            dry_run=args.dry_run,
            force=args.force,
        )
        preflight = TerminalPreflight(
            worktrees=dict(removed_worktrees),
            branches=dict(branches),
            blockers=(),
        )
        result = _abandon_outputs_result(
            args,
            contract,
            preflight,
            guard,
            (providers, removed_worktrees, branches, directories),
            terminal_note=reason,
        )
    finally:
        guard_context.__exit__(None, None, None)
    if args.dry_run:
        summary = (
            "Abandon preview: no locator, worktree folder, or work branches remain; "
            "approving would record the abandonment with nothing to archive and "
            "nothing to delete."
        )
    else:
        summary = (
            "Worktree abandoned: no locator, worktree folder, or work branches "
            "remained; there was nothing to archive and nothing to delete."
        )
    return _mark_without_remains(result, reason, summary=summary)


def _already_abandoned(
    contract: WorktreeContract,
    *,
    force: bool,
    dry_run: bool,
) -> WorktreeCommandResult:
    terminal_archive = _terminal_archive_observation(
        contract,
        force=force,
    )
    if terminal_archive.returncode != 0:
        return terminal_archive
    # MIK-R207 rule 4: a completed retry removes a registered root that is still there, and
    # never answers a success while it remains.
    outside, blockers = outside_scratch.terminal_blocker(contract, dry_run=dry_run)
    if blockers:
        return WorktreeCommandResult(
            2,
            {
                "state": "abandon-blocked",
                **status_payload(contract),
                "summary": (
                    "The worktree is already recorded as abandoned but its outside scratch root "
                    "could not be removed."
                ),
                "blockers": blockers,
                **({"outsideScratch": outside} if outside is not None else {}),
            },
        )
    return _with_terminal_archive(
        WorktreeCommandResult(
            0,
            {
                "state": "abandoned",
                **status_payload(contract),
                "summary": "Worktree was already abandoned; terminal archive proof remains valid.",
                "providers": {"state": "already-terminal"},
                "removed_worktrees": {},
                "branches": {},
                "directories": {},
                "blockers": [],
                "alreadyTerminal": True,
                **({"outsideScratch": outside} if outside is not None else {}),
            },
        ),
        terminal_archive,
    )


def _abandon_outputs_result(  # noqa: PLR0913 -- terminal_note rides the no-remains publish path
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    guard: TerminalGuard,
    outputs: AbandonOutputs,
    *,
    terminal_note: str | None = None,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, directories = outputs
    blockers = terminal_result_blockers(
        TerminalResult(
            providers=providers,
            worktrees=removed_worktrees,
            branches=branches,
            directories=directories,
            preview=args.dry_run,
        )
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
    return _publish_abandon(contract, guard, outputs, terminal_note=terminal_note)


def _publish_abandon(
    contract: WorktreeContract,
    guard: TerminalGuard,
    outputs: AbandonOutputs,
    *,
    terminal_note: str | None = None,
) -> WorktreeCommandResult:
    providers, removed_worktrees, branches, directories = outputs
    updated = amend_contract(
        contract,
        ContractCells(cleanup="abandoned", terminal_note=terminal_note),
    )
    try:
        citation_cache, projection_effects = publish_abandoned_task(contract, updated, guard)
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
    if terminal_note is not None:
        readback_error = _require_terminal_note_readback(contract.contract_path, terminal_note)
        if readback_error is not None:
            return WorktreeCommandResult(
                2,
                {
                    "state": "abandon-blocked",
                    **status_payload(contract),
                    "summary": "Abandon contract/cache publication failed and was rolled back.",
                    "citation_source_index": _preserved_cache(
                        guard.preview(), "terminal-publication-failed"
                    ),
                    "blockers": [{"publication": "contract/cache", "reason": readback_error}],
                },
            )
    # MIK-R207: the completed abandon records its own completion beside the terminal archive, so a
    # successor's sweep can prove this generation ended without the contract the group took away.
    write_completion_record(contract, updated, operation="worktree_abandon")
    return WorktreeCommandResult(
        0,
        {
            "state": "abandoned",
            **status_payload(updated),
            "summary": _abandon_summary(False, True),
            "providers": providers,
            "citation_source_index": citation_cache,
            "projectionEffects": projection_effects,
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
    try:
        collect_group_reports(contract, dry_run=args.dry_run)
    except RoleReportCollectionError as error:
        return {}, {}, {}, {"role_reports": {"removed": False, "reason": str(error)}}
    providers: dict[str, object] = worktree_services().provider_lifecycle.teardown(
        contract, dry_run=args.dry_run
    )
    if not args.dry_run and terminal_result_blockers(
        TerminalResult(providers=providers, worktrees={}, branches={}, directories={})
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
        TerminalResult(
            providers=providers, worktrees=removed_worktrees, branches={}, directories={}
        )
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
        TerminalResult(
            providers=providers, worktrees=removed_worktrees, branches=branches, directories={}
        )
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
    # The abandon's own teardown reaches a carried memory leg whatever mode
    # the contract carries (MIK-R50 rule 3): no conversion, just no shield.
    if contract.memory_repo_path is not None and contract.memory_worktree is not None:
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
    targets = {
        "code": _AbandonBranchTarget(
            contract.code_repo_path,
            contract.code_work_branch,
            contract.code_source_branch,
        )
    }
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_work_branch
    ):
        targets["memory"] = _AbandonBranchTarget(
            contract.memory_repo_path,
            contract.memory_work_branch,
            contract.memory_source_branch,
        )
    if not dry_run:
        # Guard phase (read-only): prove every branch may go before the first deletion.
        # A sibling whose guard refuses withholds the whole stage; nothing is deleted.
        refusals = {
            key: _abandon_branch_refusal(target, force=force) for key, target in targets.items()
        }
        refused = {key: entry for key, entry in refusals.items() if entry is not None}
        if refused:
            withheld: dict[str, dict[str, object]] = {}
            for key, target in targets.items():
                if key in refused:
                    entry = refused[key]
                    assert entry is not None
                    withheld[key] = entry
                else:
                    first_key, first_entry = next(iter(refused.items()))
                    assert first_entry is not None
                    withheld[key] = {
                        "branch": target.branch,
                        "deleted": False,
                        "reason": "withheld-sibling-branch-refused",
                        "sibling": first_key,
                        "siblingReason": first_entry.get("reason"),
                    }
            return withheld
    branches = {
        "code": _abandon_branch(
            targets["code"],
            dry_run=dry_run,
            force=force,
            authority=authority,
        ),
    }
    if "memory" in targets:
        branches["memory"] = _abandon_branch(
            targets["memory"],
            dry_run=dry_run,
            force=force,
            authority=authority,
        )
    elif contract.memory_repo_path is not None and contract.memory_work_branch:
        # A carried memory branch goes with the abandon, whatever the mode;
        # an absent branch is benign, an unmerged one was already refused
        # before anything moved when force is off.
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


def _abandon_branch_refusal(
    target: _AbandonBranchTarget,
    *,
    force: bool,
) -> dict[str, object] | None:
    """The read-only guard for one abandon branch target: a refusal entry, or None.

    The guard phase runs for every target before the first deletion, so a refused
    abandon changes nothing.
    """
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
        return None
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
    return None


def _abandon_branch(
    target: _AbandonBranchTarget,
    *,
    dry_run: bool,
    force: bool,
    authority: _TerminalMutationAuthority,
) -> dict[str, object]:
    repo = target.repository
    branch = target.branch
    refusal = _abandon_branch_refusal(target, force=force)
    if refusal is not None:
        return refusal
    if force:
        return delete_branch_force(repo, branch, dry_run, authority=authority)
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
    lifecycle = group / ".lifecycle"
    outside = outside_scratch.removal_entry_for(contract, dry_run=dry_run)
    if contract.kind == "series":
        reports = group / ENCLOSURE_REPORTS_DIRECTORY
        # Parity with cleanup and the leaf path (MIK-R108 rule 3): the series
        # branch removes <group>/logs/ before it returns; the answer keeps
        # its two keys.
        worktree_services().provider_lifecycle.remove_tree(
            group / ENCLOSURE_LOGS_DIRECTORY,
            dry_run=dry_run,
        )
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
            ),
            "lifecycle": worktree_services().provider_lifecycle.remove_tree(
                lifecycle,
                dry_run=dry_run,
            ),
            **outside,
        }
    role_folders = remove_group_role_folders(contract, dry_run=dry_run)
    if force:
        directories = {
            "worktree_group": worktree_services().provider_lifecycle.remove_tree(
                group, dry_run=dry_run
            ),
            **outside,
        }
    else:
        reports_path = group / ENCLOSURE_REPORTS_DIRECTORY
        reports = worktree_services().provider_lifecycle.remove_tree(reports_path, dry_run=dry_run)
        lifecycle_result = worktree_services().provider_lifecycle.remove_tree(
            lifecycle,
            dry_run=dry_run,
        )
        # Abandon removes <enclosure>/logs/ with the enclosure, as cleanup does
        # (MIK-R108 rule 3): the tool-call log is the product's own trace.
        logs_path = group / ENCLOSURE_LOGS_DIRECTORY
        logs = worktree_services().provider_lifecycle.remove_tree(
            logs_path,
            dry_run=dry_run,
        )
        planned: set[Path] = {
            Path(str(row["path"]))
            for row in role_folders.values()
            if row.get("would_remove") or row.get("removed")
        }
        if reports.get("removed") or reports.get("would_remove"):
            planned.add(reports_path.resolve())
        if lifecycle_result.get("removed") or lifecycle_result.get("would_remove"):
            planned.add(lifecycle.resolve())
        if logs.get("removed") or logs.get("would_remove"):
            planned.add(logs_path.resolve())
        directories = {
            **role_folders,
            "reports": reports,
            "lifecycle": lifecycle_result,
            "logs": logs,
            "worktree_group": remove_empty_dir(group, dry_run, planned),
            **outside,
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
