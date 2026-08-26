"""Terminal lifecycle bridge for atomic-series activation authority."""

from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_terminal_atomic_series_selection_if_exact,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


def with_terminal_atomic_series_release(
    contract: WorktreeContract,
    result: WorktreeCommandResult,
    *,
    dry_run: bool,
) -> WorktreeCommandResult:
    """Release an exact selected terminal series after lifecycle locks are gone.

    A paused series cannot clear a newer selection. Corrupt selector evidence is
    reported and retained for the next exact selecting repair, but never turns
    task cleanup into scheduling-owned work.
    """

    if contract.kind != "series" or dry_run or result.returncode != 0:
        return result
    try:
        observation = release_terminal_atomic_series_selection_if_exact(contract)
    except (AtomicSeriesActivationError, OSError, RuntimeError, ValueError) as error:
        payload = dict(result.payload)
        payload["atomicSeriesActivationRelease"] = {
            "state": "release-failed",
            "errorType": type(error).__name__,
            "detail": str(error),
        }
        payload["summary"] = (
            f"{payload.get('summary', 'The terminal series operation completed.')} "
            "The exact activation selection could not be made durably vacant; retry "
            "this terminal operation before deleting its canonical contract."
        )
        return WorktreeCommandResult(2, payload)

    payload = dict(result.payload)
    payload["atomicSeriesActivation"] = observation.source_fact()
    record = observation.record
    exact_contract = bool(
        record is not None
        and Path(record.contractPath).resolve(strict=False)
        == contract.contract_path.resolve(strict=False)
    )
    if observation.state == "unreadable":
        release_state = "unreadable-preserved"
    elif exact_contract and record is not None and record.state == "vacant":
        release_state = "vacant"
    elif record is None:
        release_state = "already-vacant"
    else:
        release_state = "different-selection-preserved"
    payload["atomicSeriesActivationRelease"] = {"state": release_state}
    return WorktreeCommandResult(result.returncode, payload)
