"""Atomic-series selecting transaction: reconciling -> source sync -> active."""

from __future__ import annotations

from dataclasses import replace

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
    publish_atomic_series_selection,
    require_atomic_series_cancellation_owner,
    require_selected_atomic_series,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_source_refresh import fetch_source_upstreams
from agents_remember.worktrees.sync_transaction import (
    sync_contract_under_authority,
    sync_input_refusal,
)
from agents_remember.worktrees.sync_transaction_authority import source_pair
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

_EXPECTED_ADMISSION_FAILURES = (
    AtomicSeriesActivationError,
    ContractError,
    OSError,
    RuntimeError,
    UnicodeError,
    ValueError,
)


def activate_atomic_series_contract(
    contract: WorktreeContract,
    *,
    activation_args: WorktreeArgs | None = None,
    dry_run: bool = False,
) -> WorktreeContract | WorktreeCommandResult:
    """Select an existing series before implementation attach/dispatch exposure."""

    if dry_run:
        return contract
    invalid = atomic_series_activation_input_refusal(contract, activation_args)
    if invalid is not None:
        return invalid
    # Fetch is evidence and remote-tracking refresh, never local source authority.
    # Keep it outside the repository integration lock; the sync transaction pins
    # exact local source tips after that lock is held.
    try:
        fetch = fetch_source_upstreams(contract)
        with integration_authority_lock(contract.coordination_root, contract.repo_name):
            current = load_contract(contract.contract_path)
            if current != contract:
                return _admission_refusal(
                    contract,
                    activation_args,
                    status="atomic-series-contract-changed",
                    detail="atomic-series contract changed while source evidence was refreshed",
                )
            return reconcile_selected_series_under_authority(
                current,
                activation_args=activation_args,
                fetch=fetch,
            )
    except _EXPECTED_ADMISSION_FAILURES as error:
        return _admission_refusal(
            contract,
            activation_args,
            status=getattr(error, "status", "atomic-series-admission-failed"),
            detail=str(error),
        )


def reconcile_selected_series_under_authority(
    contract: WorktreeContract,
    *,
    activation_args: WorktreeArgs | None,
    fetch: dict[str, object],
) -> WorktreeContract | WorktreeCommandResult:
    """Select, sync, then expose one series while repository authority is held."""

    result = sync_selected_atomic_series_under_authority(
        contract,
        activation_args=activation_args,
        fetch=fetch,
    )
    if result.returncode != 0 or result.payload.get("state") not in {
        "synced",
        "already-current",
    }:
        return result
    return load_contract(contract.contract_path)


def atomic_series_activation_input_refusal(
    contract: WorktreeContract,
    activation_args: WorktreeArgs | None,
) -> WorktreeCommandResult | None:
    """Validate selecting-operation inputs before fetch or activation mutation."""

    args = replace(activation_args or WorktreeArgs(), contract_path=contract.contract_path)
    return sync_input_refusal(args, {})


def sync_selected_atomic_series_under_authority(
    contract: WorktreeContract,
    *,
    activation_args: WorktreeArgs | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Run the public series-sync transaction without losing its response evidence.

    The caller already holds repository integration authority. A new sync selects
    the requested series as reconciling; continue/cancel may address only the
    exact already-selected reconciling contract. Only an exact current-source
    terminal state publishes active.
    """

    args = replace(activation_args or WorktreeArgs(), contract_path=contract.contract_path)
    try:
        return _sync_selected_atomic_series_under_authority(contract, args=args, fetch=fetch)
    except _EXPECTED_ADMISSION_FAILURES as error:
        return _admission_refusal(
            contract,
            args,
            status=getattr(error, "status", "atomic-series-admission-failed"),
            detail=str(error),
        )


def _sync_selected_atomic_series_under_authority(
    contract: WorktreeContract,
    *,
    args: WorktreeArgs,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    invalid = sync_input_refusal(args, fetch)
    if invalid is not None:
        return invalid
    if args.dry_run:
        return sync_contract_under_authority(contract, args, fetch=fetch)
    if args.resolution_action == "cancel":
        reconciling = require_atomic_series_cancellation_owner(contract)
    elif args.resolution_action == "continue":
        reconciling = require_selected_atomic_series(contract)
    else:
        reconciling = publish_atomic_series_selection(contract, "reconciling")
    synced = sync_contract_under_authority(contract, args, fetch=fetch)
    state = str(synced.payload.get("state", ""))
    if args.resolution_action == "cancel" and state in {
        "sync-cancelled",
        "sync-cancelled-no-authority",
        "sync-resolution-not-active",
    }:
        released = release_atomic_series_selection(contract)
        payload = dict(synced.payload)
        if state == "sync-resolution-not-active":
            payload["state"] = "sync-cancelled"
        payload["atomicSeriesActivation"] = released.source_fact()
        payload["summary"] = (
            f"{payload.get('summary', 'The sync transaction is not active.')} "
            "The exact atomic-series selection is now vacant."
        )
        return WorktreeCommandResult(0, payload)
    if synced.returncode != 0 or state not in {"synced", "already-current"}:
        return _reconciling_result(synced, reconciling.source_fact())
    current = load_contract(contract.contract_path)
    code_tip, memory_tip, external = source_pair(current)
    if current.code_base_commit != code_tip or (
        external and current.memory_base_commit != memory_tip
    ):
        payload = dict(synced.payload)
        payload["state"] = "atomic-series-source-pair-incomplete"
        payload["syncState"] = state
        payload["nextTool"] = "worktree_sync"
        next_args: dict[str, object] = {
            "contract_path": current.contract_path.as_posix(),
            "dry_run": False,
        }
        if external and current.memory_base_commit != memory_tip:
            next_args["memory_sync_choice"] = "merge-memory"
        payload["nextArgs"] = next_args
        payload["summary"] = (
            "The sync pass completed without bringing the exact external-memory base current. "
            "The atomic master remains selected and reconciling; merge the current memory line "
            "before implementation exposure."
        )
        incomplete = WorktreeCommandResult(0, payload)
        return _reconciling_result(incomplete, reconciling.source_fact())
    active = publish_atomic_series_selection(current, "active")
    payload = dict(synced.payload)
    payload["atomicSeriesActivation"] = active.source_fact()
    return WorktreeCommandResult(synced.returncode, payload)


def _admission_refusal(
    contract: WorktreeContract,
    args: WorktreeArgs | None,
    *,
    status: str,
    detail: str,
) -> WorktreeCommandResult:
    retry_args: dict[str, object] = {
        "contract_path": contract.contract_path.as_posix(),
        "dry_run": False,
    }
    if args is not None and args.memory_sync_choice is not None:
        retry_args["memory_sync_choice"] = args.memory_sync_choice
    if args is not None and args.resolution_action is not None:
        retry_args["resolution_action"] = args.resolution_action
    return WorktreeCommandResult(
        2,
        {
            "state": status,
            "status": status,
            "summary": (
                "Atomic-series admission refused without exposing implementation work. "
                "Re-read the exact contract and retry its contract-addressed sync."
            ),
            "detail": detail,
            "contract_path": contract.contract_path.as_posix(),
            "retryable": True,
            "nextTool": "worktree_sync",
            "nextArgs": retry_args,
        },
    )


def _reconciling_result(
    synced: WorktreeCommandResult,
    activation: dict[str, object],
) -> WorktreeCommandResult:
    payload = dict(synced.payload)
    payload["atomicSeriesActivation"] = activation
    payload["summary"] = (
        f"{payload.get('summary', 'Atomic-series source reconciliation did not complete.')} "
        "The requested master remains selected and reconciling; complete or cancel the "
        "contract-addressed sync, then retry this selecting operation."
    )
    # A completed individual pass is not implementation admission when its source
    # moved again.  Selection stays reconciling and the selecting surface blocks.
    return WorktreeCommandResult(2 if synced.returncode == 0 else synced.returncode, payload)
