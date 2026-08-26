"""Activation source facts and waiting reasons for closeout projection members."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.models.closeout.projection import ProjectionSourceProblem
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
    activation_waiting_reason,
    observe_atomic_series,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

_SELECTION_REPAIR = (
    "select the intended master with dispatch_agent(role='manager') or resume it with "
    "worktree_start/worktree_attach; the selecting transaction archives a malformed "
    "activation snapshot and atomically publishes replacement authority"
)


@dataclass(frozen=True)
class SeriesActivationProjection:
    source_fact: dict[str, object]
    waiting: tuple[str, ...]
    problem: ProjectionSourceProblem | None = None


def project_series_activation(
    contract: WorktreeContract,
    master_ref: TaskDocumentRef,
) -> SeriesActivationProjection:
    """Strictly observe one live series without granting queue mutation authority."""

    try:
        activation = observe_atomic_series(contract)
    except AtomicSeriesActivationError as exc:
        return SeriesActivationProjection(
            {"state": "unreadable", "errorType": exc.status},
            (),
            _problem(contract.contract_path.as_posix(), exc.status),
        )
    fact = activation.source_fact()
    if activation.state == "unreadable":
        error_type = activation.error_type or "atomic-series-activation-unreadable"
        return SeriesActivationProjection(
            fact,
            (),
            _problem(activation.activation_path.as_posix(), error_type),
        )
    reason = activation_waiting_reason(activation, master_ref)
    return SeriesActivationProjection(fact, (reason,) if reason is not None else ())


def _problem(address: str, error_type: str) -> ProjectionSourceProblem:
    return ProjectionSourceProblem(
        kind="series",
        address=address,
        state="unreadable",
        errorType=error_type,
        repairAction=_SELECTION_REPAIR,
    )
