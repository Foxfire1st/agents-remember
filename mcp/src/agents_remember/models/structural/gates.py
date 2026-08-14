"""Response models for the ``gate_*`` control-plane tools (slice 6a).

AR-owned, operation-bearing responses (strict ``ToolResponse``s, not the
persisted ``GateRecord``). ``GateKind`` / ``GateState`` reuse the record's
Literals so the response contract is as drift-proof as the record itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field

from agents_remember.kernel.primitives.gate_vocab import (
    GATE_KINDS,
    GateKind,
    GateState,
    coerce_gate_kind,
)
from agents_remember.models.base import StrictResponseModel, ToolResponse
from agents_remember.models.task_document_ref import TaskDocumentRef


@dataclass(frozen=True)
class StructuralLifecycleGateRequest:
    """Agent-authored gate fields; seat identity is derived from ambient plane state."""

    kind: GateKind
    ask: dict[str, Any] | None = None
    packet: dict[str, Any] | None = None
    required_decision: list[str] | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    wait: bool = True


@dataclass(frozen=True)
class StructuralGateDecisionRequest:
    """A structural gate decision that never carries private gate correlations."""

    task_document_ref: TaskDocumentRef
    kind: GateKind
    decision: str
    note: str | None = None
    evidence_refs: list[dict[str, Any]] | None = None


class GateCreateResponse(ToolResponse):
    """Internal compatibility ``gate_create`` payload: a freshly opened gate."""

    gateId: str
    kind: GateKind
    state: GateState
    lifecycleId: str | None = None


class InternalLifecycleGateResponse(ToolResponse):
    """Trusted exact gate response retained below the public structural boundary."""

    gate: dict[str, Any]
    lifecycle: dict[str, Any]
    wait: dict[str, Any]
    ask: dict[str, Any] | None = None


class InternalGateDecideResponse(ToolResponse):
    """Trusted exact decision response retained below the public structural boundary."""

    gateId: str
    state: GateState
    decidedBy: str
    decidedVia: str
    decidingRole: str | None = None
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)


class GateWaitResponse(ToolResponse):
    """Internal compatibility ``gate_wait`` payload."""

    gateId: str
    state: GateState
    timedOut: bool
    decidedBy: str | None = None
    decidedVia: str | None = None
    decisionNote: str | None = None


class GateResponseWaitResponse(ToolResponse):
    """Internal compatibility ``gate_response_wait`` payload."""

    gateId: str
    state: GateState
    timedOut: bool
    entryCount: int
    entries: list[dict[str, Any]]
    decidedBy: str | None = None
    decidedVia: str | None = None
    decisionNote: str | None = None


class InternalGateListResponse(ToolResponse):
    """Trusted exact gate list retained below the public structural boundary."""

    lifecycleId: str | None = None
    gates: list[dict[str, Any]]


class StructuralGateResponse(ToolResponse):
    """One agent-visible gate outcome addressed only by document and role."""

    status: str
    taskDocumentRef: TaskDocumentRef | None = None
    role: str | None = None
    kind: GateKind | None = None
    state: GateState | None = None
    detail: str | None = None


class LifecycleGateResponse(StructuralGateResponse):
    """``lifecycle_gate`` without lifecycle or gate correlations."""

    waitState: str | None = None
    timedOut: bool = False


class GateDecideResponse(StructuralGateResponse):
    """``gate_decide`` resolved from one child task document plus kind."""

    decidedVia: str | None = None
    decidingRole: str | None = None
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)


class StructuralGateSummary(StrictResponseModel):
    """One gate list row without a tool operation envelope or private ids."""

    taskDocumentRef: TaskDocumentRef
    kind: GateKind
    state: GateState
    decidingRole: str | None = None
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)


class GateListResponse(StructuralGateResponse):
    """``gate_list`` projected over this seat's task-document scope."""

    gates: list[StructuralGateSummary] = Field(default_factory=list)


__all__ = [
    "GATE_KINDS",
    "GateCreateResponse",
    "GateDecideResponse",
    "GateKind",
    "GateResponseWaitResponse",
    "GateState",
    "GateWaitResponse",
    "InternalGateDecideResponse",
    "InternalGateListResponse",
    "InternalLifecycleGateResponse",
    "LifecycleGateResponse",
    "StructuralGateDecisionRequest",
    "StructuralLifecycleGateRequest",
    "coerce_gate_kind",
]
