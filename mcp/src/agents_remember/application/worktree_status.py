"""Read-only configured worktree status projections for tools and context packets.

Composes a domain service (the worktree lifecycle) into the wire model the served
contract declares, owning neither the domain state nor the vocabulary. It used to sit
in ``worktrees/`` -- which meant a domain package importing ``models`` for a response
type, the one edge that made ``models`` and ``worktrees`` mutually dependent
(``layers.toml``). Application context packets and the public worktree status tool
compose the appropriate projection here; neither reconstructs lifecycle authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
    TerminalConfiguredContractAccepted,
    admit_configured_terminal_contract,
    project_configured_contract_refusal,
)
from agents_remember.application.lifecycle.lifecycle_control_authority import (
    LifecycleCallerError,
    completed_disposition_authorized,
    resolve_lifecycle_caller,
)
from agents_remember.application.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LocationDecisionPayload,
    configured_lifecycle_operation_location,
    location_decision_payload,
    observe_contract_read_failure,
    primary_operation_projection,
    unreadable_status_operations,
)
from agents_remember.application.memory_mode_refusal import (
    memory_mode_refusal_evidence,
    memory_mode_refusal_payload,
    removed_memory_mode_fields_from_evidence,
)
from agents_remember.errors import MemoryModeUnsupportedError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.models.worktree import (
    AtomicSeriesActivationFact,
    SourceLineageProjection,
    SyncOperationProjection,
    WorktreeState,
    WorktreeSummary,
)
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.activation.atomic_series_activation import (
    atomic_series_status_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    require_contract_matches_lifecycle_operation_location,
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.lifecycle.observation.projection import (
    current_operation_projections,
)
from agents_remember.worktrees.modules.guidance import WorktreeStatusPayload
from agents_remember.worktrees.sync_transaction_state import observe_sync_operation
from agents_remember.worktrees.worktree_contract import ContractError, load_contract


def worktree_status_packet(
    config: McpRuntimeConfig,
    contract_path: Path | None,
) -> WorktreeSummary:
    """The context packet's ``worktree`` block, built as the model rather than validated into it.

    This used to return ``dict[str, Any]`` for the caller to ``model_validate``, and that
    ``Any`` is what let a value the state machine emits and the model rejects survive every
    type check up to the moment the packet was built -- at which point the ValidationError
    escaped the ``@server.tool()`` handler, because nothing on the path catches one.
    Constructing the model here puts the checker on the seam instead: each field below is
    assigned from a producer that declares the same vocabulary the field does.
    """
    if contract_path is None:
        return WorktreeSummary(state="inactive")
    resolved = contract_path.resolve()
    try:
        _, location = configured_lifecycle_operation_location(config, resolved)
    except LifecycleOperationLocationError as error:
        return _location_decision_summary(resolved, error)
    sync_operation = observe_sync_operation(
        location.worktree_group,
        contract_path=resolved,
    )
    try:
        contract = load_contract(resolved)
    except MemoryModeUnsupportedError as error:
        return _contract_read_failure_summary(resolved, location, error, sync_operation)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        # What is left here is a document that is not a contract at all: no front matter, an
        # unrecognized schema, a required field missing, an external-memory contract with no
        # memory repository. A cell whose *value* is outside its vocabulary is NOT one of
        # these -- the reader substitutes the declared fallback and reports it (see
        # `unknownContractCells` below), because refusing it here would only have made the
        # packet honest about a task that `worktree_closeout_apply`, `worktree_integrate`,
        # `worktree_cleanup`, `worktree_sync` and `worktree_abandon` had all simultaneously
        # stopped being able to touch.
        #
        # The one exception is a *removed* memory mode, which is caught above this clause rather
        # than here: it is not an unreadable token to degrade around but a specific state whose
        # owner has to be told the recorded value, the supported set and the route out, so it
        # must not be re-published as a generic parse failure.
        return _contract_read_failure_summary(resolved, location, error, sync_operation)
    try:
        require_contract_matches_lifecycle_operation_location(contract, location)
    except LifecycleOperationLocationError as error:
        return _location_decision_summary(resolved, error, sync_operation=sync_operation)
    return _summary_from_status_payload(
        git_worktree_manager.status_payload(contract),
        lifecycle_operation=primary_operation_projection(
            current_operation_projections(
                contract.contract_path,
                contract=contract,
                location=location,
            )
        ),
        sync_operation=sync_operation,
        atomic_series_activation=(
            AtomicSeriesActivationFact.model_validate(atomic_series_status_projection(contract))
            if contract.kind == "series"
            else None
        ),
    )


def _contract_read_failure_summary(
    contract_path: Path,
    location: LifecycleOperationLocation,
    error: Exception,
    sync_operation: SyncOperationProjection | None,
) -> WorktreeSummary:
    """Project one failed ``load_contract`` onto the context packet's worktree summary.

    Two shapes share this body because they share every consequence except the operator text.
    A *removed* memory mode is a specific, previously supported state: it is reported with the
    recorded value, the supported set and the route out, and its typed status is published. Any
    other unreadable document keeps the generic classification.

    ``load_contract`` deliberately translates file absence into ``ContractError``, and the live
    path is classified only after locator authority has already been proven, so deletion affects
    the contract surface without hiding the retained root journal.
    """
    removed_mode = error if isinstance(error, MemoryModeUnsupportedError) else None
    missing = removed_mode is None and (
        isinstance(error, FileNotFoundError) or not contract_path.exists()
    )
    failure = public_failure_evidence(
        stage="contract-read",
        side="contract",
        name=contract_path.name,
        error_type=type(error).__name__,
        observed=(
            memory_mode_refusal_evidence(removed_mode)
            if removed_mode is not None
            else {"state": "missing" if missing else "unreadable"}
        ),
    )
    observation = observe_contract_read_failure(location, failure)
    retained = primary_operation_projection(list(observation.operations))
    if observation.decision is not None and removed_mode is None:
        return _contract_read_decision_summary(
            contract_path,
            missing=missing,
            failure=failure,
            decision=observation.decision,
            sync_operation=sync_operation,
        )
    return WorktreeSummary(
        state="missingContract" if missing else "invalidContract",
        contractPath=contract_path.as_posix(),
        enclosurePath=contract_path.as_posix(),
        error=(
            removed_mode.detail
            if removed_mode is not None
            else (
                "the canonical worktree contract is missing"
                if missing
                else "the canonical worktree contract is unreadable or invalid"
            )
        ),
        status=removed_mode.status if removed_mode is not None else None,
        errorEvidence=failure,
        lifecycleOperation=retained,
        syncOperation=sync_operation,
    )


def _location_decision_summary(
    contract_path: Path,
    error: LifecycleOperationLocationError,
    *,
    sync_operation: SyncOperationProjection | None = None,
) -> WorktreeSummary:
    """Carry the shared locator decision without inventing a context-only dialect."""

    decision = location_decision_payload(error)
    return _developer_decision_summary(
        contract_path,
        state="missingContract" if not contract_path.exists() else "invalidContract",
        decision=decision,
        sync_operation=sync_operation,
    )


def _contract_read_decision_summary(
    contract_path: Path,
    *,
    missing: bool,
    failure: dict[str, object],
    decision: LocationDecisionPayload,
    sync_operation: SyncOperationProjection | None,
) -> WorktreeSummary:
    return _developer_decision_summary(
        contract_path,
        state="missingContract" if missing else "invalidContract",
        decision=decision,
        error_evidence=failure,
        sync_operation=sync_operation,
    )


def _developer_decision_summary(
    contract_path: Path,
    *,
    state: WorktreeState,
    decision: LocationDecisionPayload,
    error_evidence: dict[str, object] | None = None,
    sync_operation: SyncOperationProjection | None = None,
) -> WorktreeSummary:
    """Project one shared typed lifecycle-location/read decision onto context."""

    return WorktreeSummary(
        state=state,
        contractPath=contract_path.as_posix(),
        enclosurePath=contract_path.as_posix(),
        error=decision["summary"],
        errorEvidence=error_evidence,
        status=decision["status"],
        summary=decision["summary"],
        detail=decision["detail"],
        expected=decision["expected"],
        observed=decision["observed"],
        nextAction=decision["nextAction"],
        developerDecisionRequired=decision["developerDecisionRequired"],
        decisionSurface=decision["decisionSurface"],
        syncOperation=sync_operation,
    )


def _summary_from_status_payload(
    payload: WorktreeStatusPayload,
    *,
    lifecycle_operation: LifecycleOperationProjection | None = None,
    sync_operation: SyncOperationProjection | None = None,
    atomic_series_activation: AtomicSeriesActivationFact | None = None,
) -> WorktreeSummary:
    """Project a snake_case status payload onto the camelCase wire model, field by field.

    ``nextTool``/``nextArgs``/``nextRequiredArgs`` are read with ``.get`` because
    ``next_guidance`` deliberately *omits* them when there is nothing to call -- the ``done``
    phases have no next tool. This projection used to substitute ``""``/``{}``/``[]`` for the
    absent keys, inventing a ``nextTool`` value that no producer declares and that the wire
    vocabulary therefore rejected on 153 of the 213 contracts on disk. The fields are
    optional and the packet is dumped with ``exclude_none``, so omission is the shape.

    The omission is declared for all three keys. Measured across the 213 contracts on disk,
    48 responses that previously carried ``"nextRequiredArgs": []`` now omit the key. ``[]``
    was not something a producer said -- ``next_guidance`` writes the key only when the next
    call needs an argument the caller has to supply -- it was this projection filling a hole,
    the same act that put an un-declarable ``""`` in ``nextTool``. An absent
    ``nextRequiredArgs`` means what an empty list meant: the next call needs nothing beyond
    ``nextArgs``. ``ContractBoundaryTests`` pins it so it cannot move again unannounced.
    """
    source_lineage = payload.get("source_lineage")
    return WorktreeSummary(
        state="active",
        taskId=payload["task_id"],
        taskName=payload["task_name"],
        workflowKind=payload["workflow_kind"],
        memoryMode=payload["memory_mode"],
        kind=payload["kind"],
        leafId=payload["leaf_id"],
        contractPath=payload["contract_path"],
        enclosurePath=payload["enclosure_path"],
        worktreeGroup=payload["worktree_group"],
        codeWorktree=payload["code_worktree"],
        codeWorktreeExists=payload["code_worktree_exists"],
        codeWorktreeDirty=payload["code_worktree_dirty"],
        memoryWorktree=payload["memory_worktree"],
        memoryWorktreeExists=payload["memory_worktree_exists"],
        memoryWorktreeDirty=payload["memory_worktree_dirty"],
        ledgerPath=payload["ledger_path"],
        humanReviewStatus=payload["human_review_status"],
        approvedForCommit=payload["approved_for_commit"],
        closeoutStatus=payload["closeout_status"],
        integrationStatus=payload["integration_status"],
        cleanup=payload["cleanup"],
        phase=payload["phase"],
        nextOperation=payload["nextOperation"],
        nextTool=payload.get("nextTool"),
        nextArgs=payload.get("nextArgs"),
        nextRequiredArgs=payload.get("nextRequiredArgs"),
        unknownContractCells=payload.get("unknown_contract_cells"),
        lifecycleOperation=lifecycle_operation,
        syncOperation=sync_operation,
        atomicSeriesActivation=(
            atomic_series_activation if atomic_series_activation is not None else None
        ),
        sourceLineage=(
            SourceLineageProjection.model_validate(source_lineage)
            if source_lineage is not None
            else None
        ),
    )


def project_contract_status(
    config: McpRuntimeConfig,
    result: dict[str, Any],
    path: Path,
    caller: DeclaredCaller | None,
) -> dict[str, Any]:
    terminal = admit_configured_terminal_contract(config, path.as_posix())
    if isinstance(terminal, TerminalConfiguredContractAccepted):
        _project_terminal_contract_status(result, terminal)
        _replace_operation_status(result, [])
        return result
    if isinstance(terminal, ConfiguredContractRefused) and terminal.status.startswith(
        "terminal-archive-"
    ):
        result.update(project_configured_contract_refusal(terminal, operation="worktree_status"))
        _replace_operation_status(result, [])
        return result
    try:
        resolved_caller = resolve_lifecycle_caller(config, caller)
    except LifecycleCallerError as exc:
        return {
            "ok": False,
            "operation": "worktree_status",
            "state": "refused",
            "status": exc.status,
            "detail": exc.detail,
        }
    read_failure = result.get("contractReadFailure")
    if isinstance(read_failure, dict):
        operations = unreadable_status_operations(config, result, path, read_failure)
    else:
        operations = _readable_status_operations(config, result, path, resolved_caller)
    _restore_reader_reason(result, read_failure)
    _replace_operation_status(result, operations)
    return result


def _restore_reader_reason(
    result: dict[str, Any],
    read_failure: object,
) -> None:
    """Re-state a reader verdict that the shared observation replaced with a synthesis.

    ``unreadable_status_operations`` publishes a synthesised publication-loss decision whenever
    the enclosure retains no journal operation, whatever the read failure actually was. For a
    contract that records the removed memory mode nothing was lost -- the contract is present and
    readable -- so that decision states something false and drops the recorded value, the
    supported set and the route. The reader's verdict is restored from its own evidence block,
    and only over that one synthesised state: a real retained operation, or a real
    location decision, keeps the answer it produced.
    """
    if not isinstance(read_failure, dict):
        return
    if result.get("state") != "operation-contract-publication-lost":
        return
    fields = removed_memory_mode_fields_from_evidence(read_failure)
    if fields is not None:
        result.update({"ok": False, **fields})


def _readable_status_operations(
    config: McpRuntimeConfig,
    result: dict[str, Any],
    path: Path,
    resolved_caller: DeclaredCaller | None,
) -> list[LifecycleOperationProjection]:
    try:
        contract = load_contract(path)
        location = require_matching_lifecycle_operation_location(contract)
        return current_operation_projections(
            path,
            allow_completed_disposition=completed_disposition_authorized(
                contract,
                resolved_caller,
            ),
            caller=resolved_caller,
            contract=contract,
            location=location,
        )
    except LifecycleOperationLocationError as exc:
        result.update(location_decision_payload(exc))
        return []
    except MemoryModeUnsupportedError as exc:
        # A retained generation still outranks the refusal -- its recovery route is executable --
        # so the shared observation runs first and the refusal only re-states the reason where
        # that observation found no retained journal and synthesised its publication-loss
        # decision. Nothing was lost here: the contract is present and readable, and it records a
        # removed mode, so publishing "the proven initial contract is missing or unreadable"
        # would tell the operator something false.
        failure = public_failure_evidence(
            stage="contract-read",
            side="contract",
            name=path.name,
            error_type=type(exc).__name__,
            observed=memory_mode_refusal_evidence(exc),
        )
        operations = unreadable_status_operations(config, result, path, failure)
        result.update({"ok": False, **memory_mode_refusal_payload(exc)})
        _restore_reader_reason(result, failure)
        return operations
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        detail = "the canonical worktree contract is unreadable"
        result.update(
            {
                "ok": False,
                "state": "worktree-contract-unreadable",
                "status": "worktree-contract-unreadable",
                "summary": detail,
                "detail": detail,
            }
        )
        return unreadable_status_operations(
            config,
            result,
            path,
            public_failure_evidence(
                stage="contract-read",
                side="contract",
                name=path.name,
                error_type=type(exc).__name__,
                observed={"state": "missing" if not path.exists() else "unreadable"},
            ),
        )


def _replace_operation_status(
    result: dict[str, Any], operations: list[LifecycleOperationProjection]
) -> None:
    result.pop("contractReadFailure", None)
    result.pop("lifecycleOperation", None)
    result["lifecycleOperations"] = [
        operation.model_dump(mode="json", exclude_none=True) for operation in operations
    ]


def _project_terminal_contract_status(
    result: dict[str, Any],
    accepted: TerminalConfiguredContractAccepted,
) -> None:
    authority = accepted.authority
    archive = authority.archive
    completed = authority.state == "cleanup-completed"
    status = "terminal-cleanup-completed" if completed else "terminal-archive-ready"
    result.update(
        {
            "ok": True,
            "state": status,
            "status": status,
            "summary": (
                "Terminal cleanup is complete and the external enclosure archive remains proven."
                if completed
                else "Terminal archive proof is durable; resume the accepted cleanup operation."
            ),
            "terminalArchive": {
                "state": "terminal-archive-proven",
                "cleanupOperation": archive.cleanupOperation,
                "cleanupArguments": archive.cleanupArguments.model_dump(mode="json"),
                "cleanupRequestId": archive.cleanupRequestId,
                "archivePath": accepted.locator.terminalArchivePath,
                "archiveSha256": accepted.locator.terminalArchiveSha256,
                "receiptPath": accepted.locator.terminalReceiptPath,
                "contractState": authority.state,
            },
        }
    )
    if completed:
        result.pop("nextAction", None)
        return
    result.update(
        {
            "nextAction": archive.cleanupOperation,
            "nextTool": archive.cleanupOperation,
            "nextArgs": _terminal_cleanup_next_args(
                accepted.contract_path,
                archive.cleanupArguments.model_dump(mode="json"),
            ),
        }
    )


def _terminal_cleanup_next_args(
    contract_path: Path,
    accepted_arguments: dict[str, object],
) -> dict[str, object]:
    return {
        "contract_path": contract_path.as_posix(),
        "dry_run": False,
        **accepted_arguments,
    }
