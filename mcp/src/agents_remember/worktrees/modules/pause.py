"""The stop-only master pause: release one master's selection and publish nothing.

A master needs a way to stop. This module is that entire route, and its boundary *is* the
feature: the verb a caller reaches for when they want to stop a master must not be able to
move a ref, create a commit, land anything or write a ledger row, so nothing here imports or
calls the integration, landing, closeout or ledger planes and nothing here runs Git.

What it does instead is release the master's atomic-series activation selection -- the one
durable fact that says this master is exposing implementation work -- and hand the turn back to
the developer. The release reuses the authority sync cancellation and terminal cleanup already
own; the pause adds no second one. The activation record is keyed to the contract, so the path
this route releases is this master's own and releasing it leaves every other master's record
byte-identical.

``worktree_checkpoint_landing`` is the SEPARATE, explicitly requested PUBLICATION that lands
an unfinished master's accumulated line. It is deliberately unreachable from here.
"""

from __future__ import annotations

from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract

# The pause's own next move: stop. It carries a summary and no ``nextTool``/``nextArgs``, so
# the response proposes no continued execution and no call to make -- control is the
# developer's until they ask for the master again.
_PAUSE_NEXT_STEP: dict[str, str] = {
    "summary": (
        "Master paused. Stop here: the turn is the developer's, nothing was published, and no "
        "further step is proposed. The master stands exactly as it was left and is resumable."
    ),
}

_PAUSED_SUMMARY = (
    "Atomic master paused: its atomic-series selection is released and control returns to the "
    "developer. Nothing was published -- no ref moved, no commit was created, no landing ran "
    "and no ledger row was written -- and the master keeps its code and memory work branches, "
    "its worktrees, its enclosure and every unstarted leaf exactly as they were."
)

# The release authority's refusal statuses, explained in the pause's own terms. The guard
# stays exactly where it is -- this only says what its refusal means to the caller who asked
# for a stop.
_RELEASE_REFUSAL_DETAIL: dict[str, str] = {
    "atomic-series-activation-selected-contract-mismatch": (
        "pause cannot release a selection this master does not hold: the addressed activation "
        "record names another atomic master"
    ),
    "atomic-series-activation-selection-missing": (
        "pause found no atomic-series selection to release for this master, so there is "
        "nothing to stop"
    ),
    "atomic-series-activation-release-unreadable": (
        "this master's atomic-series selection is unreadable, so it cannot be released"
    ),
}


def pause_result(
    args: WorktreeArgs,
    current_contract: WorktreeContract,
) -> WorktreeCommandResult:
    """Stop one atomic master: release its selection, publish nothing, hand the turn back.

    The stop and the release are one operation from the caller's perspective on purpose.
    Exposing a bare release would leave the caller to sequence "release, then stop", which is
    the hidden-side-effect shape this route exists to remove; exposing a bare mark would stop
    nothing, because the selection is what says this master is the one working.

    Refusals come from the release authority and are never suppressed here: a master with no
    selection has nothing to stop, and a record this contract does not own is refused rather
    than released.
    """

    assert args.contract_path is not None
    contract = current_contract
    if args.contract_path.resolve() != contract.contract_path.resolve():
        raise RuntimeError("pause contract path does not match the passed current contract")
    if contract.kind != "series":
        return WorktreeCommandResult(
            2,
            _refusal_payload(
                contract,
                status="pause-requires-atomic-master",
                detail=(
                    "pause is defined only for an atomic master's series contract; an ordinary "
                    "leaf owns no atomic-series selection to release"
                ),
            ),
        )
    try:
        released = release_atomic_series_selection(contract)
    except AtomicSeriesActivationError as error:
        return WorktreeCommandResult(
            2,
            _refusal_payload(
                contract,
                status=error.status,
                detail=_RELEASE_REFUSAL_DETAIL.get(error.status, error.detail),
            ),
        )
    return WorktreeCommandResult(0, _paused_payload(contract, released.source_fact()))


def _paused_payload(
    contract: WorktreeContract,
    activation: dict[str, object],
) -> dict[str, object]:
    """The stop's result: what was released, that nothing was published, and no next call."""

    return {
        "state": "paused",
        "status": "paused",
        "paused": True,
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "contractPath": contract.contract_path.as_posix(),
        "enclosurePath": contract.contract_path.as_posix(),
        "summary": _PAUSED_SUMMARY,
        "atomicSeriesActivation": activation,
        "nextStep": dict(_PAUSE_NEXT_STEP),
    }


def _refusal_payload(
    contract: WorktreeContract,
    *,
    status: str,
    detail: str | None,
) -> dict[str, object]:
    """A pause that did not happen, reported without proposing a next call."""

    observed = detail or "the atomic-series selection could not be released"
    return {
        "state": status,
        "status": status,
        "paused": False,
        "contract_path": contract.contract_path.as_posix(),
        "summary": f"Atomic master pause refused ({status}): {observed}",
        "detail": observed,
    }
