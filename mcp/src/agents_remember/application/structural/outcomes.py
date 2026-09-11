"""Stable caller-facing outcomes for structural agent operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.models.task_document_ref import TaskDocumentRef


@dataclass(frozen=True)
class StructuralOutcome:
    operation: str
    ok: bool
    status: str
    document: TaskDocumentRef | None
    role: str
    detail: str | None = None
    delivery_state: str | None = None
    adapter_delivery_state: str | None = None


def structural_payload(outcome: StructuralOutcome) -> dict[str, Any]:
    """Return stable work identity and structural delivery state, never occupant identity."""

    payload: dict[str, Any] = {
        "ok": outcome.ok,
        "operation": outcome.operation,
        "status": outcome.status,
        "role": outcome.role,
    }
    if outcome.document is not None:
        payload["taskDocumentRef"] = outcome.document.model_dump()
    if outcome.detail is not None:
        payload["detail"] = outcome.detail
    if outcome.delivery_state is not None:
        payload["deliveryState"] = outcome.delivery_state
    if outcome.adapter_delivery_state is not None:
        payload["adapterDeliveryState"] = outcome.adapter_delivery_state
    return payload
