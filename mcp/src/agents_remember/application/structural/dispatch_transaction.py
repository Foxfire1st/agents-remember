"""One idempotent spawn-and-brief transaction for a canonical role seat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from agents_remember.application.structural.outcomes import (
    StructuralOutcome,
    structural_payload,
)
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import StructuralDispatchError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.structural_dispatch import (
    dispatch_brief_status,
    dispatch_brief_viable,
    pinned_dispatch_brief,
)
from agents_remember.serving.terminal_catalog import (
    DispatchBriefReceiptStore,
    TerminalCatalog,
)

SpawnAttempt = Callable[[], dict[str, Any]]
BriefSpawned = Callable[[str], dict[str, Any]]
RetireGeneration = Callable[[str], bool]


@dataclass(frozen=True)
class DispatchTransaction:
    """The durable owners and specialized operations for one canonical seat attempt."""

    document: TaskDocumentRef
    role: str
    catalog: TerminalCatalog
    inbox_store: OperatorInboxStore
    admitted_spawn: SpawnAttempt
    retry_spawn: SpawnAttempt
    brief_spawned: BriefSpawned
    retire_generation: RetireGeneration


@dataclass(frozen=True)
class DispatchEvidenceRuntime:
    """The one owner API for reading and repairing a generation's dispatch evidence."""

    document: TaskDocumentRef
    role: str
    catalog: TerminalCatalog
    inbox_store: OperatorInboxStore


def execute_dispatch_transaction(transaction: DispatchTransaction) -> dict[str, Any]:
    """Converge spawn, pinned-brief publication, and one failed-generation recovery."""

    spawned = transaction.admitted_spawn()
    for attempt in range(2):
        status = str(spawned.get("status", "spawn-refused"))
        if status == "spawned-unbriefed":
            return transaction.brief_spawned(cast(str, spawned["session"]))
        if status != "seat-taken":
            return structural_payload(
                StructuralOutcome(
                    "dispatch_agent",
                    False,
                    status,
                    transaction.document,
                    transaction.role,
                    cast(str | None, spawned.get("detail")),
                )
            )
        owner_id = spawned.get("ownerSession")
        if not isinstance(owner_id, str) or not owner_id:
            return _reconciliation_refusal(
                transaction,
                "seat occupancy did not identify its private current generation",
            )
        existing = _reconcile_existing_dispatch(transaction, owner_id=owner_id)
        if existing is not None:
            return existing
        if attempt == 0:
            spawned = transaction.retry_spawn()
    return _reconciliation_refusal(
        transaction,
        "seat changed twice while reconciling one locked dispatch",
    )


def _reconcile_existing_dispatch(
    transaction: DispatchTransaction,
    *,
    owner_id: str,
) -> dict[str, Any] | None:
    """Return a convergent result, or retire one failed generation and request a retry."""

    occupant = transaction.catalog.get(owner_id)
    if occupant is None or occupant.status != "running":
        return None
    try:
        outcome = reconcile_dispatch_evidence(_evidence_runtime(transaction), owner_id=owner_id)
    except (OSError, ValueError) as exc:
        return _reconciliation_refusal(transaction, str(exc))
    if outcome is not None:
        return outcome
    if not transaction.retire_generation(owner_id):
        return _reconciliation_refusal(
            transaction,
            "failed dispatch generation could not be retired",
        )
    return None


def _evidence_runtime(transaction: DispatchTransaction) -> DispatchEvidenceRuntime:
    return DispatchEvidenceRuntime(
        document=transaction.document,
        role=transaction.role,
        catalog=transaction.catalog,
        inbox_store=transaction.inbox_store,
    )


def reconcile_dispatch_evidence(
    runtime: DispatchEvidenceRuntime,
    *,
    owner_id: str,
) -> dict[str, Any] | None:
    """Project one generation's durable brief, repairing its receipt when possible.

    ``None`` is a positive statement that the current generation has no viable durable brief and
    may be retired by the transaction owner. Read or repair ambiguity raises instead, so callers
    never turn an unknown post-commit state into destructive rollback.
    """

    occupant = runtime.catalog.get(owner_id)
    if occupant is None or occupant.status != "running":
        raise StructuralDispatchError(
            "the current seat disappeared while its durable brief was reconciled"
        )
    occupant, brief = _load_dispatch_evidence(runtime, occupant, owner_id)
    return _dispatch_evidence_outcome(runtime, occupant, brief)


def _load_dispatch_evidence(
    runtime: DispatchEvidenceRuntime,
    occupant: TerminalCatalogEntry,
    owner_id: str,
) -> tuple[TerminalCatalogEntry, OperatorInboxEntry | None]:
    """Read and repair one generation's durable brief evidence without exposing its id."""

    brief = pinned_dispatch_brief(
        runtime.inbox_store,
        document=runtime.document,
        role=runtime.role,
        occupant_id=owner_id,
    )
    if brief is not None and occupant.dispatch_brief_entry_id not in {None, brief.id}:
        raise StructuralDispatchError(
            "the current seat's pinned-brief receipt contradicts durable inbox evidence"
        )
    if brief is not None and occupant.dispatch_brief_entry_id is None:
        updated = DispatchBriefReceiptStore(runtime.catalog).bind(owner_id, entry_id=brief.id)
        if updated is None:
            raise StructuralDispatchError(
                "the current seat disappeared while its pinned-brief receipt was repaired"
            )
        occupant = updated
    return occupant, brief


def _dispatch_evidence_outcome(
    runtime: DispatchEvidenceRuntime,
    occupant: TerminalCatalogEntry,
    brief: OperatorInboxEntry | None,
) -> dict[str, Any] | None:
    """Project valid evidence; ``None`` means the generation is proven unbriefed/failed."""

    if brief is not None and dispatch_brief_viable(brief):
        return structural_payload(
            StructuralOutcome(
                "dispatch_agent",
                True,
                dispatch_brief_status(brief),
                runtime.document,
                runtime.role,
                brief.deliveryDetail,
                brief.deliveryState,
                brief.adapterDeliveryState,
            )
        )
    if brief is None and occupant.dispatch_brief_entry_id is not None:
        return structural_payload(
            StructuralOutcome(
                "dispatch_agent",
                True,
                "dispatch-queued",
                runtime.document,
                runtime.role,
                "the current seat retains its durable pinned-brief receipt",
                "queued",
            )
        )
    if brief is None and occupant.spawned_by_kind not in {"ambient", "plane"}:
        return structural_payload(
            StructuralOutcome(
                "dispatch_agent",
                False,
                "seat-taken",
                runtime.document,
                runtime.role,
                "the current seat was not created by a reconcilable dispatch generation",
            )
        )
    return None


def _reconciliation_refusal(
    transaction: DispatchTransaction,
    detail: str,
) -> dict[str, Any]:
    return structural_payload(
        StructuralOutcome(
            "dispatch_agent",
            False,
            "dispatch-reconciliation-refused",
            transaction.document,
            transaction.role,
            detail,
        )
    )
