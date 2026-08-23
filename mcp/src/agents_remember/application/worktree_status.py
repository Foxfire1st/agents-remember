"""Read-only worktree status projection for context packets.

Composes a domain service (the worktree lifecycle) into the wire model the served
contract declares, owning neither the domain state nor the vocabulary. It used to sit
in ``worktrees/`` -- which meant a domain package importing ``models`` for a response
type, the one edge that made ``models`` and ``worktrees`` mutually dependent
(``layers.toml``). Nothing else in ``worktrees`` needed it: its only caller is
``application.context_packet``, one import away from here.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.lifecycle.lifecycle_operation_location import (
    LocationDecisionPayload,
    configured_lifecycle_operation_location,
    location_decision_payload,
    observe_contract_read_failure,
    primary_operation_projection,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.models.worktree import (
    SourceLineageProjection,
    WorktreeState,
    WorktreeSummary,
)
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    require_contract_matches_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    current_operation_projections,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.modules.guidance import WorktreeStatusPayload
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
    try:
        contract = load_contract(resolved)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        # What is left here is a document that is not a contract at all: no front matter, an
        # unrecognized schema, a required field missing, an external-memory contract with no
        # memory repository. A cell whose *value* is outside its vocabulary is NOT one of
        # these -- the reader substitutes and reports it (see `unknownContractCells` below),
        # because refusing it here would only have made the packet honest about a task that
        # `worktree_closeout_apply`, `worktree_integrate`, `worktree_cleanup`, `worktree_sync`
        # and `worktree_abandon` had all simultaneously stopped being able to touch.
        # ``load_contract`` deliberately translates file absence into ``ContractError``.
        # Classify the live path only after locator authority has already been proven, so
        # deletion affects the contract surface without hiding the retained root journal.
        missing = isinstance(error, FileNotFoundError) or not resolved.exists()
        failure = public_failure_evidence(
            stage="contract-read",
            side="contract",
            name=resolved.name,
            error_type=type(error).__name__,
            observed={"state": "missing" if missing else "unreadable"},
        )
        observation = observe_contract_read_failure(location, failure)
        retained = primary_operation_projection(list(observation.operations))
        if observation.decision is not None:
            return _contract_read_decision_summary(
                resolved,
                missing=missing,
                failure=failure,
                decision=observation.decision,
            )
        return WorktreeSummary(
            state="missingContract" if missing else "invalidContract",
            contractPath=resolved.as_posix(),
            enclosurePath=resolved.as_posix(),
            error=(
                "the canonical worktree contract is missing"
                if missing
                else "the canonical worktree contract is unreadable or invalid"
            ),
            errorEvidence=failure,
            lifecycleOperation=retained,
        )
    try:
        require_contract_matches_lifecycle_operation_location(contract, location)
    except LifecycleOperationLocationError as error:
        return _location_decision_summary(resolved, error)
    return _summary_from_status_payload(
        git_worktree_manager.status_payload(contract),
        lifecycle_operation=primary_operation_projection(
            current_operation_projections(
                contract.contract_path,
                contract=contract,
                location=location,
            )
        ),
    )


def _location_decision_summary(
    contract_path: Path,
    error: LifecycleOperationLocationError,
) -> WorktreeSummary:
    """Carry the shared locator decision without inventing a context-only dialect."""

    decision = location_decision_payload(error)
    return _developer_decision_summary(
        contract_path,
        state="missingContract" if not contract_path.exists() else "invalidContract",
        decision=decision,
    )


def _contract_read_decision_summary(
    contract_path: Path,
    *,
    missing: bool,
    failure: dict[str, object],
    decision: LocationDecisionPayload,
) -> WorktreeSummary:
    return _developer_decision_summary(
        contract_path,
        state="missingContract" if missing else "invalidContract",
        decision=decision,
        error_evidence=failure,
    )


def _developer_decision_summary(
    contract_path: Path,
    *,
    state: WorktreeState,
    decision: LocationDecisionPayload,
    error_evidence: dict[str, object] | None = None,
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
    )


def _summary_from_status_payload(
    payload: WorktreeStatusPayload,
    *,
    lifecycle_operation: LifecycleOperationProjection | None = None,
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
        sourceLineage=(
            SourceLineageProjection.model_validate(source_lineage)
            if source_lineage is not None
            else None
        ),
    )
