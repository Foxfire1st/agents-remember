"""Atomic-series selecting transaction: reconciling -> source sync -> active."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
    AtomicSeriesActivationObservation,
    observe_atomic_series,
    publish_atomic_series_selection,
    require_atomic_series_cancellation_owner,
    require_selected_atomic_series,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.activation.atomic_series_admission import (
    AtomicSeriesAdmissionRequest,
    atomic_series_admission_projection,
)
from agents_remember.worktrees.cutover_lock import CUTOVER_LOCK_CODE, legacy_format_refusal
from agents_remember.worktrees.knowledge_crossing import merge_base
from agents_remember.worktrees.knowledge_validation import has_layout_marker
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_commit, is_ancestor
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_source_refresh import fetch_source_upstreams
from agents_remember.worktrees.sync_transaction import (
    sync_contract_under_authority,
    sync_input_refusal,
)
from agents_remember.worktrees.sync_transaction_authority import command_result, source_pair
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


@dataclass(frozen=True)
class _AdmissionRefusalRequest:
    contract: WorktreeContract
    args: WorktreeArgs | None
    operation: str
    status: str
    detail: str
    error: BaseException | None = None


def activate_atomic_series_contract(
    contract: WorktreeContract,
    *,
    activation_args: WorktreeArgs | None = None,
    dry_run: bool = False,
    operation: str = "worktree_attach",
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
        current = load_contract(contract.contract_path)
        if current != contract:
            return _admission_refusal(
                _AdmissionRefusalRequest(
                    contract=contract,
                    args=activation_args,
                    operation=operation,
                    status="atomic-series-contract-changed",
                    detail="atomic-series contract changed while source evidence was refreshed",
                )
            )
        return reconcile_selected_series_under_authority(
            current,
            activation_args=activation_args,
            fetch=fetch,
        )
    except _EXPECTED_ADMISSION_FAILURES as error:
        return _admission_refusal(
            _AdmissionRefusalRequest(
                contract=contract,
                args=activation_args,
                operation=operation,
                status=getattr(error, "status", "atomic-series-admission-failed"),
                detail=str(error),
                error=error,
            )
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
            _AdmissionRefusalRequest(
                contract=contract,
                args=args,
                operation="worktree_sync",
                status=getattr(error, "status", "atomic-series-admission-failed"),
                detail=str(error),
                error=error,
            )
        )


def _series_legacy_refusal(
    repository: Path,
    line: str,
    fetch: dict[str, object],
    sides: str,
) -> WorktreeCommandResult:
    """The shared legacy-format refusal for a series memory sync."""
    return command_result(
        2,
        f"sync-{CUTOVER_LOCK_CODE}",
        "The memory sync is refused: its memory is unconverted and must cross the boundary first.",
        fetch,
        detail=legacy_format_refusal(
            repository,
            operation="worktree_sync",
            subject=f"the memory sync of {line} ({sides})",
        ),
    )


def _series_memory_ff_refusal(
    repository: Path,
    line: str,
    fetch: dict[str, object],
    work_tip: str,
    source_tip: str,
) -> WorktreeCommandResult | None:
    """Judge the memory fast-forward shape for a series sync.

    Returns a refusal payload, or None when the line takes the source.
    """
    if work_tip == source_tip:
        return None  # admitted as already-current
    sides = f"preSyncHead {work_tip}, sourceCommit {source_tip}"
    states = {side: has_layout_marker(repository, side) for side in (work_tip, source_tip)}
    if all(states.values()) or (not states[work_tip] and states[source_tip]):
        return None  # converted throughout, or the line takes the source
    if states[work_tip]:
        return command_result(
            2,
            f"sync-{CUTOVER_LOCK_CODE}",
            "The memory sync is refused: its source is unconverted and would take a "
            "converted line out of the text format.",
            fetch,
            detail=legacy_format_refusal(
                repository,
                operation="worktree_sync",
                subject=(
                    f"the memory sync of {line} ({sides}): its target holds converted "
                    "memory (knowledge/layout.json) and its source does not, so a "
                    "fast-forward would take the line out of the text format -- convert "
                    "the source line first"
                ),
            ),
        )
    return _series_legacy_refusal(repository, line, fetch, sides)


def _series_memory_merge_refusal(
    repository: Path,
    line: str,
    fetch: dict[str, object],
    work_tip: str,
    source_tip: str,
) -> WorktreeCommandResult | None:
    """Judge the diverged (merge) shape for a series sync.

    Returns a refusal payload, or None when the transaction's crossing or
    converted-throughout shapes proceed.
    """
    base = merge_base(repository, work_tip, source_tip)
    sides = f"preSyncHead {work_tip}, sourceCommit {source_tip}, merge base {base}"
    if any(has_layout_marker(repository, side) for side in (base, work_tip, source_tip)):
        return None  # the crossing sync or converted throughout, as today
    return _series_legacy_refusal(repository, line, fetch, sides)


def _legacy_series_memory_refusal(
    contract: WorktreeContract, args: WorktreeArgs, fetch: dict[str, object]
) -> WorktreeCommandResult | None:
    """Judge the series memory sides before the reconciling selection is published (MIK-R46 rule 1).

    The refusal precedes every durable write of the envelope. The judgment mirrors
    the transaction's exact preflight (:func:`sync_transaction._sync_side_outcome`)
    on the same branch tips a series plans from, so this check can only refuse what
    the transaction would refuse, and admits what it admits:
    - already current (equal tips, or the source an ancestor of the work tip):
      proceed, as today;
    - all unconverted sides (fast-forward pair or merge triple): refuse with the
      one ``legacy-format`` message;
    - converted target with an unconverted source: refuse (taking it would leave
      the text format);
    - any converted side the transaction admits or crosses: proceed.
    Skip-memory, missing branches and unanswered probes all proceed to the
    transaction, which judges precisely. ``None`` means proceed.
    """

    if (
        contract.memory_mode != "external"
        or contract.memory_repo_path is None
        or args.memory_sync_choice == "skip-memory"
        or not contract.memory_work_branch
        or not contract.memory_source_branch
    ):
        return None
    repository = contract.memory_repo_path
    work_branch = contract.memory_work_branch
    source_branch = contract.memory_source_branch
    try:
        work_tip = branch_commit(repository, work_branch)
        source_tip = branch_commit(repository, source_branch)
    except Exception:
        return None  # never fail closed here; the transaction judges precisely
    if work_tip == source_tip or is_ancestor(repository, source_tip, work_tip):
        return None  # already current, exactly as the transaction plans it
    line = f"{work_branch} ({repository.as_posix()})"
    try:
        if is_ancestor(repository, work_tip, source_tip):
            return _series_memory_ff_refusal(repository, line, fetch, work_tip, source_tip)
        return _series_memory_merge_refusal(repository, line, fetch, work_tip, source_tip)
    except Exception:
        return None  # never fail closed here; the transaction judges precisely


def _reconciling_series_selection(
    contract: WorktreeContract, args: WorktreeArgs, fetch: dict[str, object]
) -> AtomicSeriesActivationObservation | WorktreeCommandResult:
    """The reconciling selection, or the refusal that precedes it (MIK-R46 rule 1).

    A fresh sync judges the series memory sides before the reconciling selection
    is published -- the certain all-unconverted refusal precedes the write
    (no-write envelope). Anything else runs the transaction's exact preflight.
    In-flight continue/cancel are untouched.
    """

    if args.resolution_action == "cancel":
        return require_atomic_series_cancellation_owner(contract)
    if args.resolution_action == "continue":
        return require_selected_atomic_series(contract)
    early = _legacy_series_memory_refusal(contract, args, fetch)
    if early is not None:
        return early
    return publish_atomic_series_selection(contract, "reconciling")


def _cancelled_series_release(
    synced: WorktreeCommandResult, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult | None:
    """The released-cancel result, or ``None`` when this is not a cancel unwind."""

    state = str(synced.payload.get("state", ""))
    if args.resolution_action != "cancel" or state not in {
        "sync-cancelled",
        "sync-cancelled-no-authority",
        "sync-resolution-not-active",
    }:
        return None
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


def _incomplete_series_result(
    synced: WorktreeCommandResult,
    reconciling: AtomicSeriesActivationObservation,
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """The reconciling-incomplete result, or ``None`` when the pair is current."""

    state = str(synced.payload.get("state", ""))
    current = load_contract(contract.contract_path)
    code_tip, memory_tip, external = source_pair(current)
    if current.code_base_commit == code_tip and (
        not external or current.memory_base_commit == memory_tip
    ):
        return None
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


def _finish_series_sync(
    synced: WorktreeCommandResult,
    reconciling: AtomicSeriesActivationObservation,
    contract: WorktreeContract,
) -> WorktreeCommandResult:
    """The terminal result once the pass ran under a reconciling selection."""

    state = str(synced.payload.get("state", ""))
    if synced.returncode != 0 or state not in {"synced", "already-current"}:
        return _reconciling_result(synced, reconciling.source_fact())
    incomplete = _incomplete_series_result(synced, reconciling, contract)
    if incomplete is not None:
        return incomplete
    current = load_contract(contract.contract_path)
    active = publish_atomic_series_selection(current, "active")
    payload = dict(synced.payload)
    payload["atomicSeriesActivation"] = active.source_fact()
    return WorktreeCommandResult(synced.returncode, payload)


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
    selected = _reconciling_series_selection(contract, args, fetch)
    if isinstance(selected, WorktreeCommandResult):
        return selected
    reconciling = selected
    synced = sync_contract_under_authority(contract, args, fetch=fetch)
    released = _cancelled_series_release(synced, contract, args)
    if released is not None:
        return released
    return _finish_series_sync(synced, reconciling, contract)


def _admission_refusal(request: _AdmissionRefusalRequest) -> WorktreeCommandResult:
    contract = request.contract
    args = request.args
    retry_args: dict[str, object] = {
        "contract_path": contract.contract_path.as_posix(),
        "dry_run": False,
    }
    if args is not None and args.memory_sync_choice is not None:
        retry_args["memory_sync_choice"] = args.memory_sync_choice
    if args is not None and args.resolution_action is not None:
        retry_args["resolution_action"] = args.resolution_action
    observation = getattr(request.error, "observation", None)
    if observation is None:
        try:
            observation = observe_atomic_series(contract)
        except _EXPECTED_ADMISSION_FAILURES:
            observation = None
    admission = atomic_series_admission_projection(
        AtomicSeriesAdmissionRequest(
            operation=request.operation,
            status=request.status,
            detail=request.detail,
            contract=contract,
            observation=observation,
            expected=getattr(request.error, "expected", None),
            observed=getattr(request.error, "observed", None),
        )
    )
    summary = (
        f"Atomic-series admission refused ({request.status}): {request.detail} "
        "Apply the reported corrective action and inspect the supplied worktree_status "
        "address before retrying."
    )
    return WorktreeCommandResult(
        2,
        {
            "state": request.status,
            "status": request.status,
            "summary": summary,
            "detail": request.detail,
            "contract_path": contract.contract_path.as_posix(),
            "retryable": True,
            "nextTool": "worktree_sync",
            "nextArgs": retry_args,
            "admission": admission,
            "retryPrecondition": admission["retryPrecondition"],
            "statusAction": admission["statusAction"],
        },
    )


def _reconciling_result(
    synced: WorktreeCommandResult,
    activation: dict[str, object],
) -> WorktreeCommandResult:
    payload = dict(synced.payload)
    payload["atomicSeriesActivation"] = activation
    if payload.get("state") in {"synced", "already-current"}:
        # The pass itself succeeded; what did not is the selection. Reporting the pass's
        # own success state beside a mid-flight record is the wording that made this shape
        # read as a finished operation, so the state names what is actually true.
        payload["state"] = "atomic-series-reconciling"
    payload["summary"] = _mid_flight_summary(payload.get("summary"), activation)
    # A completed individual pass is not implementation admission when its source
    # moved again.  Selection stays reconciling and the selecting surface blocks.
    return WorktreeCommandResult(2 if synced.returncode == 0 else synced.returncode, payload)


def _mid_flight_summary(refusal: object, activation: dict[str, object]) -> str:
    """Lead with the mid-flight contract, because that is what the caller must act on.

    The refused pass is the symptom. The state the caller has to know about is that the
    contract this call addressed is still selected and reconciling, so every later
    closeout and integrate for it meets the same wall -- and, before this, the only
    thing said about it was the refused pass's own branch complaint, which names
    neither the stuck contract nor what it was doing. Measured: a selecting operation
    refused with "resolved memory ledger dropped parent mapping(s): ..." and the
    reconciling record it left behind was visible only in a payload key nothing pointed
    at.
    """

    record = activation.get("record")
    contract_path = str(record.get("contractPath") or "") if isinstance(record, dict) else ""
    published = str(record.get("selectedAt") or "") if isinstance(record, dict) else ""
    revision = record.get("revision") if isinstance(record, dict) else None
    master = record.get("selectedMaster") if isinstance(record, dict) else None
    if isinstance(master, dict):
        identity = f"The master {master.get('path')} of {master.get('repository')} "
    else:
        identity = "This contract "
    if contract_path:
        identity += f"({contract_path}) "
    since = f" since {published}" if published else ""
    at_revision = f" (revision {revision})" if revision is not None else ""
    exits = (
        "Complete it with worktree_sync(contract_path=..., dry_run=false) -- add "
        "memory_sync_choice='merge-memory' when the memory line has to be merged -- or "
        "cancel it with worktree_sync(contract_path=..., resolution_action='cancel', "
        "dry_run=false)."
    )
    refused = f" The reconciliation itself reported: {refusal}" if refusal else ""
    return (
        f"{identity}is mid-flight: its atomic-series selection has been 'reconciling'"
        f"{since}{at_revision}, left by a source reconciliation that did not complete, and "
        f"nothing else can proceed through this contract until that state is resolved. {exits}"
        f"{refused}"
    )
