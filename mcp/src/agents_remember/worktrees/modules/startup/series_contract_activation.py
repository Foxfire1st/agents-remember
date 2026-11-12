"""Activate a master series after proving its task-stage publication."""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

from agents_remember.controlplane.durable_store import exclusive_access
from agents_remember.worktrees.activation.atomic_series_activation_transaction import (
    atomic_series_activation_input_refusal,
    reconcile_selected_series_under_authority,
)
from agents_remember.worktrees.integration.integration_branch_authority import integration_surfaces
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.start_task_publication import (
    publish_start_task_facts,
    task_start_authority_refusal,
    validate_start_task_stage,
)
from agents_remember.worktrees.sync_source_refresh import fetch_source_upstreams
from agents_remember.worktrees.task_leaf_binding import TaskLeafBindingError
from agents_remember.worktrees.worktree_contract import WorktreeContract

from . import start_contract as owner

if TYPE_CHECKING:
    from .start_contract import MasterSeriesContractSpec


def activate_master_series(
    spec: MasterSeriesContractSpec,
    *,
    dry_run: bool = False,
    activation_args: WorktreeArgs | None = None,
    leaf_admission_operation: str | None = None,
) -> WorktreeContract | WorktreeCommandResult:
    """Return or create the plane-owned contract for one canonical master document.

    The orchestrator must be able to create the manager seat before that manager starts the
    first leaf.  Contract and integration-branch identity therefore belong to the structural
    plane, not to the first worker request.  First-leaf start calls this same owner so both
    entry points remain one operation rather than competing bootstrap implementations.

    Contract presence proves durable work exists; it does not own scheduling.  Once
    this operation has recovered or created the requested contract, it publishes that
    contract's own activation as reconciling (no other master's record is touched),
    syncs its pinned source pair, and publishes it active before returning
    implementation authority.
    """

    preflight = _stage_preflight(
        spec, dry_run=dry_run, operation=leaf_admission_operation or "worktree_start"
    )
    if isinstance(preflight, WorktreeCommandResult) or dry_run:
        return preflight
    invalid = atomic_series_activation_input_refusal(preflight, activation_args)
    if invalid is not None:
        return invalid
    # Fetch is best-effort evidence and may touch remote-tracking refs, so it happens
    # before repository integration authority is held.  The sync transaction derives
    # and pins the exact local source commits again inside that authority.
    fetch = fetch_source_upstreams(preflight)

    # Manager dispatch and first-leaf start are two public entries into one bootstrap.  Hold the
    # same host-local/process-local lock across the second existence check, both branch creations,
    # and contract publication.  A concurrent loser therefore adopts the winner's completed edge;
    # it can never enter rollback and delete the winner's contract.
    owner._require_commanded_atomic_master(spec)
    # Store locks never nest: finish the per-master bootstrap journal transaction
    # before reading or writing the per-contract activation store.
    with exclusive_access(
        owner._master_series_bootstrap_lock_target(spec), owner.MASTER_SERIES_BOOTSTRAP_OWNERSHIP
    ):
        recovering = owner._recover_master_series_bootstrap(spec)
        if recovering is not None:
            contract = recovering
        else:
            try:
                existing = owner._existing_master_series_contract(spec)
            except owner.MasterSeriesContractAdmissionError as error:
                return owner._master_series_admission_refusal(
                    spec,
                    error,
                    operation=leaf_admission_operation or "worktree_start",
                )
            if existing is not None:
                contract = existing
            else:
                contract = owner._new_master_series_contract(spec)
                integration_surfaces(contract)
                owner._publish_master_series_contract(spec, contract)
    result = reconcile_selected_series_under_authority(
        contract,
        activation_args=activation_args,
        fetch=fetch,
    )
    return _publish_stage(result)


def _stage_preflight(
    spec: MasterSeriesContractSpec, *, dry_run: bool, operation: str
) -> WorktreeContract | WorktreeCommandResult:
    owner._require_commanded_atomic_master(spec)
    # Observe the journal-to-contract handoff under its native mutex. Preview remains read-only.
    lock = (
        nullcontext()
        if dry_run
        else exclusive_access(
            owner._master_series_bootstrap_lock_target(spec),
            owner.MASTER_SERIES_BOOTSTRAP_OWNERSHIP,
        )
    )
    try:
        with lock:
            candidate = owner._bootstrap_preflight_contract(spec)
        validate_start_task_stage(candidate)
        return candidate
    except owner.MasterSeriesContractAdmissionError as error:
        return owner._master_series_admission_refusal(spec, error, operation=operation)
    except TaskLeafBindingError as error:
        return task_start_authority_refusal(error)


def _publish_stage(
    result: WorktreeContract | WorktreeCommandResult,
) -> WorktreeContract | WorktreeCommandResult:
    if isinstance(result, WorktreeContract):
        try:
            publish_start_task_facts(result)
        except TaskLeafBindingError as error:
            return task_start_authority_refusal(error)
    return result
