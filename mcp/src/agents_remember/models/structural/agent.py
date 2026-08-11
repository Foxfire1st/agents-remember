"""Agent-facing responses for plane-resolved structural seat operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.base import ToolResponse
from agents_remember.models.operator_inbox import AdapterDeliveryState, InboxDeliveryState
from agents_remember.models.task_document_ref import TaskDocumentRef

StructuralRole = Literal[
    "architect",
    "orchestrator",
    "strategist",
    "designer",
    "system-specialist",
    "manager",
    "worker",
    "reviewer",
    "curator",
]
"""Roles that occupy canonical task-document seats."""

AgentMessageKind = Literal[
    "message",
    "gate-response",
    "turn-report",
    "master-handover",
    "nudge",
    "escalation",
    "degradation-alert",
    "decision-item",
    "decision-ruling",
]
"""Agent-authored whole-message kinds; plane-only dispatch/state facts are excluded."""


@dataclass(frozen=True)
class DispatchAgentRequest:
    task_document_ref: TaskDocumentRef
    role: StructuralRole
    brief: str
    label: str | None = None


@dataclass(frozen=True)
class StructuralMessageRequest:
    ask: str
    response: str
    task_document_ref: TaskDocumentRef | None = None
    role: StructuralRole | None = None
    message_kind: AgentMessageKind = "message"
    artifact_path: str | None = None


@dataclass(frozen=True)
class RetireChildRequest:
    task_document_ref: TaskDocumentRef
    role: StructuralRole
    reason: str


@dataclass(frozen=True)
class RenameChildRequest:
    task_document_ref: TaskDocumentRef
    role: StructuralRole
    label: str


class StructuralTargetResponse(ToolResponse):
    """A response that identifies work and role but never a runtime occupant."""

    status: str
    taskDocumentRef: TaskDocumentRef | None = None
    role: str
    detail: str | None = None


class DispatchAgentResponse(StructuralTargetResponse):
    operation: Literal["dispatch_agent"] = "dispatch_agent"
    deliveryState: InboxDeliveryState | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None


class MessageParentResponse(StructuralTargetResponse):
    operation: Literal["message_parent"] = "message_parent"
    deliveryState: InboxDeliveryState | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None


class MessageChildResponse(StructuralTargetResponse):
    operation: Literal["message_child"] = "message_child"
    deliveryState: InboxDeliveryState | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None


class RetireChildResponse(StructuralTargetResponse):
    operation: Literal["retire_child"] = "retire_child"


class RenameChildResponse(StructuralTargetResponse):
    operation: Literal["rename_child"] = "rename_child"


class RenameSelfResponse(StructuralTargetResponse):
    operation: Literal["rename_self"] = "rename_self"
