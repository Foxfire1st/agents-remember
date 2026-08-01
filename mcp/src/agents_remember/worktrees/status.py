"""Read-only worktree status projection for context packets."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.worktree import WorktreeSummary

from . import git_worktree_manager
from .modules.guidance import WorktreeStatusPayload
from .worktree_contract import ContractError, load_contract


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
    return _summary_from_status_payload(git_worktree_manager.status_payload(contract))


def _summary_from_status_payload(payload: WorktreeStatusPayload) -> WorktreeSummary:
    """Project a snake_case status payload onto the camelCase wire model, field by field.

    ``nextTool``/``nextArgs``/``nextRequiredArgs`` are read with ``.get`` because
    ``next_guidance`` deliberately *omits* them when there is nothing to call -- the ``done``
    phases have no next tool. This projection used to substitute ``""``/``{}``/``[]`` for the
    absent keys, inventing a ``nextTool`` value that no producer declares and that the wire
    vocabulary therefore rejected on 153 of the 213 contracts on disk. The fields are
    optional and the packet is dumped with ``exclude_none``, so omission is the shape.

    That omission is DECLARED, for all three keys, and it is a wire change worth stating
    plainly: measured across the 213 contracts on disk, 48 responses that previously carried
    ``"nextRequiredArgs": []`` now omit the key. It stays omitted. ``[]`` was not something a
    producer said -- ``next_guidance`` writes the key only when the next call needs an
    argument the caller has to supply -- it was this projection filling a hole, which is the
    same act that put an un-declarable ``""`` in ``nextTool``. An absent ``nextRequiredArgs``
    means what an empty list meant: the next call needs nothing beyond ``nextArgs``. There is
    no third state for it to be confused with, so the empty list carried no information the
    absent key does not, and one rule for all three keys is worth more than a byte-identical
    response for one of them. ``ContractBoundaryTests`` pins it so it cannot move again
    unannounced.
    """
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
    )
