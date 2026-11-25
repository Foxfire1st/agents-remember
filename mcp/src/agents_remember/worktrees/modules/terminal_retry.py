"""Finish the folder removal of an exact completed terminal generation."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace

from agents_remember.controlplane.durable_store import UnsafeLockFilesystemError
from agents_remember.errors import AuthorityError
from agents_remember.models.lifecycles.enclosure import TerminalWorktreeCleanupArguments
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    TerminalCleanupContractAuthority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_location_reservation import (
    lifecycle_location_reservation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_contract_authority_if_present,
)
from agents_remember.worktrees.modules.terminal_agents import (
    archive_terminal_agents,
    terminal_agent_blockers,
)
from agents_remember.worktrees.modules.terminal_group import (
    GroupPreparation,
    group_directory_blockers,
    legacy_series_group,
    prepare_group_removal,
    preview_group_removal,
    remove_terminal_group,
    teardown_terminal_providers,
)
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalMode,
    TerminalResult,
    _worktree_preflight,
    terminal_result_blockers,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


def _require_generation(expected: TerminalCleanupContractAuthority) -> WorktreeContract:
    current = load_contract(expected.current_contract.contract_path)
    if (
        current != expected.current_contract
        or terminal_contract_authority_if_present(current) != expected
    ):
        raise AuthorityError(
            "The task changed generation after this terminal retry selected it. "
            "Read worktree_status for the current task before acting again."
        )
    return current


def _worktree_blockers(contract: WorktreeContract, mode: TerminalMode) -> list[dict[str, object]]:
    # This completed-generation retry owns directory residue. The ordinary Git
    # guard still classifies dirty or unreadable checkouts, and a clean checkout
    # also stays out of recursive deletion.
    worktrees, blockers = _worktree_preflight(contract, force=False, mode=mode)
    blockers.extend(
        {
            "worktree": side,
            "reason": (
                f"Git checkout {entry['path']} still exists after terminal completion. "
                "Inspect its owner with worktree_status before removing it."
            ),
        }
        for side, entry in worktrees.items()
        if entry.get("would_remove")
    )
    return blockers


def _preview_retry(
    contract: WorktreeContract, mode: TerminalMode, *, teardown_providers: bool
) -> GroupPreparation:
    directories = preview_group_removal(contract, teardown_providers=teardown_providers)
    blockers = group_directory_blockers(directories, dry_run=True)
    if directories.get("worktree_group", {}).get("would_remove"):
        blockers.extend(_worktree_blockers(contract, mode))
    return GroupPreparation(directories, {}, blockers)


def retry_terminal_group(
    expected: TerminalCleanupContractAuthority, *, dry_run: bool
) -> GroupPreparation:
    contract = expected.current_contract
    reservation = (
        nullcontext()
        if dry_run
        else lifecycle_location_reservation(contract.coordination_root, contract.contract_path)
    )
    try:
        contract = _require_generation(expected)
        if legacy_series_group(contract):
            with reservation:
                directories = outside_scratch.removal_entry_for(
                    _require_generation(expected), dry_run=dry_run
                )
            return GroupPreparation(
                directories, {}, group_directory_blockers(directories, dry_run=dry_run)
            )
        return _retry_modern_group(expected, dry_run=dry_run, reservation=reservation)
    except (
        AuthorityError,
        ContractError,
        LifecycleOperationLocationError,
        UnsafeLockFilesystemError,
    ) as error:
        return GroupPreparation({}, {}, [{"terminal": "generation", "reason": str(error)}])


def _retry_modern_group(
    expected: TerminalCleanupContractAuthority,
    *,
    dry_run: bool,
    reservation: AbstractContextManager[None],
) -> GroupPreparation:
    accepted = expected.archive.cleanupArguments
    mode: TerminalMode = (
        "cleanup" if isinstance(accepted, TerminalWorktreeCleanupArguments) else "abandon"
    )
    teardown_providers = (
        accepted.teardown_providers
        if isinstance(accepted, TerminalWorktreeCleanupArguments)
        else True
    )
    contract = expected.current_contract
    prepared = _preview_retry(contract, mode, teardown_providers=teardown_providers)
    if prepared.blockers:
        return prepared
    if prepared.directories.get("worktree_group", {}).get("would_remove"):
        # The archive verifies the observed generation after capturing receipt
        # identities and before acquiring any receipt lock or calling the host.
        def check_census() -> None:
            _require_generation(expected)

        archive = archive_terminal_agents(contract, dry_run=dry_run, check_census=check_census)
        prepared = replace(
            prepared, agent_archive=archive, blockers=terminal_agent_blockers(archive)
        )
    if prepared.blockers or dry_run:
        return prepared
    with reservation:
        contract = _require_generation(expected)
        return _apply_retry(
            contract, mode, prepared.agent_archive, teardown_providers=teardown_providers
        )


def _apply_retry(
    contract: WorktreeContract,
    mode: TerminalMode,
    archive: dict[str, object],
    *,
    teardown_providers: bool,
) -> GroupPreparation:
    fresh = _preview_retry(contract, mode, teardown_providers=teardown_providers)
    if fresh.blockers:
        return replace(fresh, agent_archive=archive)
    move_out = prepare_group_removal(contract)
    blockers = group_directory_blockers(move_out, dry_run=False)
    if blockers:
        return GroupPreparation(move_out, archive, blockers)
    if teardown_providers and fresh.directories.get("worktree_group", {}).get("would_remove"):
        providers = teardown_terminal_providers(contract, dry_run=False)
        blockers = terminal_result_blockers(
            TerminalResult(providers=providers, worktrees={}, branches={}, directories={})
        )
        if blockers:
            return GroupPreparation({}, archive, blockers)
    directories = remove_terminal_group(contract)
    return GroupPreparation(
        directories, archive, group_directory_blockers(directories, dry_run=False)
    )
