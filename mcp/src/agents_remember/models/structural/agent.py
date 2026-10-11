"""Agent-facing responses for plane-resolved structural seat operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import field_validator

from agents_remember.models.base import ToolResponse
from agents_remember.models.operator_inbox import AdapterDeliveryState, InboxDeliveryState
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.task_document_ref import TaskDocumentRef

StructuralRole = Literal[
    "architect",
    "orchestrator",
    "strategist",
    "designer",
    "investigator",
    "system-specialist",
    "manager",
    "worker",
    "reviewer",
    "curator",
]
"""Roles that occupy canonical task-document seats."""


@dataclass(frozen=True)
class DispatchAgentRequest:
    task_document_ref: TaskDocumentRef
    role: StructuralRole
    brief: str
    label: str | None = None


class StructuralTargetResponse(ToolResponse):
    """A response that identifies work and role but never a runtime occupant."""

    status: str
    taskDocumentRef: TaskDocumentRef | None = None
    role: str
    detail: str | None = None
    # The shared ``structural_payload`` projection (application/structural/outcomes.py:36-39):
    # the producer adds these two keys whenever the outcome carries a delivery state, so every
    # consumer of that producer declares them here rather than per model. ``dispatch_agent``
    # is the call site that populates them today. Declaring them on the shared
    # base keeps the projection's declaration total: a strict consumer that omits a key its
    # own producer can emit is the D53 shape.
    deliveryState: InboxDeliveryState | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None

    @field_validator("role")
    @classmethod
    def _canonical_role(cls, value: str) -> str:
        return canonical_role(value)


class DispatchAgentResponse(StructuralTargetResponse):
    operation: Literal["dispatch_agent"] = "dispatch_agent"
    deliveryState: InboxDeliveryState | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None
