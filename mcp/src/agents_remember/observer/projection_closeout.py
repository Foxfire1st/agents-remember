"""Closeout projection and discarded-task evidence nodes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agents_remember.models.task_document_ref import TaskDocumentRef


class CloseoutProjectionProblemNode(BaseModel):
    """Bounded repair evidence for a non-admitting projection."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    address: str
    state: str
    errorType: str
    repairAction: str


class DiscardUnstartedProofNode(BaseModel):
    """Persisted proof facts retained after an unstarted leaf source is discarded."""

    model_config = ConfigDict(extra="forbid")

    version: str
    taskDocumentRef: TaskDocumentRef
    taskState: str
    enclosureState: str
    locatorState: str
    doorState: str
    operationState: str
    seatState: str
    reviewState: str
    commitState: str
    childJson: dict[str, object]
    childMarkdown: dict[str, object]
    fingerprint: str


class DiscardedSubTaskNode(BaseModel):
    """One master-owned, machine-readable discard-unstarted audit entry."""

    model_config = ConfigDict(extra="forbid")

    number: str
    name: str
    file: str
    scope: str = ""
    disposition: str
    reason: str
    discardedAt: str
    proof: DiscardUnstartedProofNode


__all__ = [
    "CloseoutProjectionProblemNode",
    "DiscardUnstartedProofNode",
    "DiscardedSubTaskNode",
]
