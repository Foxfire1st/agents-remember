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

from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.models.worktree import SourceLineageProjection, WorktreeSummary
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.lifecycle_operations import latest_operation_projection
from agents_remember.worktrees.modules.guidance import WorktreeStatusPayload
from agents_remember.worktrees.worktree_contract import ContractError, load_contract


def worktree_status_packet(contract_path: Path | None) -> WorktreeSummary:
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
    if not resolved.exists():
        return WorktreeSummary(
            state="missingContract",
            contractPath=resolved.as_posix(),
            enclosurePath=resolved.as_posix(),
        )
    try:
        contract = load_contract(resolved)
    except ContractError as error:
        # What is left here is a document that is not a contract at all: no front matter, an
        # unrecognized schema, a required field missing, an external-memory contract with no
        # memory repository. A cell whose *value* is outside its vocabulary is NOT one of
        # these -- the reader substitutes and reports it (see `unknownContractCells` below),
        # because refusing it here would only have made the packet honest about a task that
        # `worktree_closeout_apply`, `worktree_integrate`, `worktree_cleanup`, `worktree_sync`
        # and `worktree_abandon` had all simultaneously stopped being able to touch.
        return WorktreeSummary(
            state="invalidContract",
            contractPath=resolved.as_posix(),
            enclosurePath=resolved.as_posix(),
            error=str(error),
        )
    return _summary_from_status_payload(
        git_worktree_manager.status_payload(contract),
        lifecycle_operation=latest_operation_projection(contract.contract_path),
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
