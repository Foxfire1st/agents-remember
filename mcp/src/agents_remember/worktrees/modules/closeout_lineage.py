"""Self-healing for the closeout-family source-lineage guard.

A closeout boundary proves its transitive super -> master -> leaf ancestry before it
touches Git. The routine break is parallel-landing decay: the source branch moved while
the work branch kept its own commits, so ``ahead`` is the work branch's normal work and
``behind`` is what has to travel downstream. The existing journaled sync already carries
exactly that -- fast-forward where the descendant has no own commits, merge where it does
-- so this module runs it for the exact contract that owns each stale edge and re-reads
the lineage.

Only what the sync cannot settle comes back to an agent: an edge whose evidence cannot be
proven, a merge conflict the sync retained, or a sync that refused admission. Previews
observe only -- they refuse with the guidance the applying call acts on and mutate nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.source_lineage import (
    SourceLineageProjection,
    lineage_refusal,
    source_lineage_for_contract,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

# The one sync state whose conflict is resolved in the exact sync worktree, not here.
_RETAINED_CONFLICT_STATE = "sync-resolution-required"

# The duties every retained conflict hands back. The closeout commits the code and the
# external-memory content as one transaction, so neither leg completes while a conflict
# stands.
_RESOLUTION_DUTIES = (
    "Check both worktrees -- code and memory -- for conflicts.",
    "A retained merge conflict completes the closeout for neither code nor memory.",
    "After resolving code conflicts, re-run the targeted test utility before retrying the closeout.",
    "For memory conflicts, run the memory tooling first and then make the memory adjustments.",
    "Small changes may be made ad hoc by the orchestrating or managing agent; larger changes "
    "must be returned to the responsible worker and/or curator agent.",
)
# Only an unprovable edge reaches this duty: a carried merge is a mechanical settlement,
# while a missing contract, branch, or comparison cannot be settled by anyone but a human.
_DIVERGENCE_DUTY = (
    "This break is beyond simple fast-forwarding and is escalated to the human developer."
)
_PREVIEW_DUTY = (
    "The applying closeout call carries a stale source break itself; this preview only "
    "reports it, and an unprovable break escalates to the human developer."
)
_SYNC_REFUSED_DUTY = (
    "Settle the reported sync state with worktree_sync for this contract before retrying "
    "the closeout."
)


@dataclass(frozen=True)
class HealedSourceLineage:
    """The current lineage, plus the contract identity the sync left on disk."""

    contract: WorktreeContract
    projection: SourceLineageProjection


class SourceLineageRefusal(RuntimeError):
    """A lineage boundary that could not proceed; carries its typed guidance payload.

    A ``RuntimeError`` so every existing caller keeps its refusal contract, and so the
    message an MCP tool renders still states the whole guidance.
    """

    def __init__(self, status: str, message: str, payload: dict[str, object]) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def heal_current_source_lineage(
    contract: WorktreeContract,
    *,
    operation: str,
    dry_run: bool,
) -> HealedSourceLineage:
    """Return current lineage, carrying a stale break the existing sync can settle.

    A work branch that owns its own commits is the normal case, so a ``diverged`` edge is
    an ordinary stale edge: ``behind > 0`` is the trigger, and the sync fast-forwards or
    merges as that edge requires. Only an unprovable projection, a retained conflict, or a
    refused sync comes back without continuing. ``dry_run`` callers observe only.
    """

    projection = source_lineage_for_contract(contract)
    if lineage_refusal(projection) is None:
        assert projection is not None
        return HealedSourceLineage(contract, projection)
    assert projection is not None
    _require_settleable(projection, operation=operation, dry_run=dry_run)
    for recovery in projection.recoveries:
        _sync_stale_edge(Path(recovery.contractPath), operation=operation)
    refreshed = load_contract(contract.contract_path)
    healed = source_lineage_for_contract(refreshed)
    if lineage_refusal(healed) is not None:
        assert healed is not None
        raise _still_stale(operation, healed)
    assert healed is not None
    return HealedSourceLineage(refreshed, healed)


def _require_settleable(
    projection: SourceLineageProjection,
    *,
    operation: str,
    dry_run: bool,
) -> None:
    """Refuse what only a human or a retained-conflict resolution can settle."""

    if projection.state == "unavailable":
        status, detail = _projected_refusal(projection)
        raise _refusal(operation, status, detail, (_DIVERGENCE_DUTY,))
    if dry_run:
        status, detail = _projected_refusal(projection)
        raise _refusal(operation, status, detail, (_PREVIEW_DUTY,))


def _sync_stale_edge(contract_path: Path, *, operation: str) -> None:
    """Run the existing sync for one stale edge's owning contract."""

    result = sync_result(WorktreeArgs(contract_path=contract_path))
    if str(result.payload.get("state", "")) == _RETAINED_CONFLICT_STATE:
        raise _retained_conflict(operation, result)
    if result.returncode != 0:
        raise _sync_refused(operation, contract_path, result)


def _projected_refusal(projection: SourceLineageProjection) -> tuple[str, str]:
    """The public status/detail of one projection the caller already proved stale."""

    refusal = lineage_refusal(projection)
    assert refusal is not None
    return refusal


def _retained_conflict(operation: str, result: WorktreeCommandResult) -> SourceLineageRefusal:
    resolution = result.payload.get("resolution")
    known = resolution if isinstance(resolution, dict) else {}
    side = known.get("side")
    where = f"the {side} sync worktree" if isinstance(side, str) else "a sync worktree"
    return _refusal(
        operation,
        "source-lineage-sync-conflict",
        f"the automatic source sync retained a merge conflict in {where}.",
        _RESOLUTION_DUTIES,
        **{
            key: value
            for key, value in (
                ("resolution", resolution if isinstance(resolution, dict) else None),
                ("nextOperation", "continue_sync_resolution"),
                ("nextTool", result.payload.get("nextTool")),
                ("nextArgs", result.payload.get("nextArgs")),
                ("cancelArgs", result.payload.get("cancelArgs")),
            )
            if value is not None
        },
    )


def _sync_refused(
    operation: str,
    contract_path: Path,
    result: WorktreeCommandResult,
) -> SourceLineageRefusal:
    state = result.payload.get("state")
    summary = str(result.payload.get("summary", "the sync reported no state"))
    next_args = result.payload.get("nextArgs")
    return _refusal(
        operation,
        "source-lineage-sync-refused",
        f"the automatic source sync did not complete ({state}): {summary}",
        (_SYNC_REFUSED_DUTY, *_RESOLUTION_DUTIES),
        nextOperation="sync_source_lineage",
        nextTool="worktree_sync",
        nextArgs=(
            next_args
            if isinstance(next_args, dict)
            else {"contract_path": contract_path.as_posix(), "dry_run": False}
        ),
        syncState=state,
    )


def _still_stale(operation: str, projection: SourceLineageProjection) -> SourceLineageRefusal:
    status, detail = _projected_refusal(projection)
    return _refusal(
        operation,
        status,
        detail,
        (
            "The automatic source sync completed, but the lineage is still stale; sync the "
            "remaining ordered parent edges before retrying the closeout.",
        ),
    )


def _refusal(
    operation: str,
    status: str,
    detail: str,
    guidance: tuple[str, ...],
    **evidence: object,
) -> SourceLineageRefusal:
    """One refusal shape: today's lead sentence, then the guidance in the message."""

    lead = f"{operation} requires current transitive source lineage ({status}): {detail}"
    message = " ".join((lead, *guidance))
    payload: dict[str, object] = {
        "state": "blocked",
        "status": status,
        "summary": detail,
        "detail": message,
        "guidance": list(guidance),
        **evidence,
    }
    return SourceLineageRefusal(status, message, payload)


__all__ = [
    "HealedSourceLineage",
    "SourceLineageRefusal",
    "heal_current_source_lineage",
]
