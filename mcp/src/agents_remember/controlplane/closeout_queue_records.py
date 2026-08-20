"""Bounded transaction vocabulary for the durable closeout queue state."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, field_validator, model_validator

from agents_remember.controlplane.durable_store import DurableRecord
from agents_remember.models.queue.closeout_queue import (
    MAX_QUEUE_SHORT_TEXT,
    MAX_QUEUE_TEXT,
    CloseoutQueueState,
    QueueEventAction,
)


class CloseoutQueuePendingTransaction(DurableRecord):
    """One write-ahead transaction; recovery publishes it once or proves it published."""

    transactionKind: Literal["queue-mutation", "sprint-status"]
    requestId: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    requestFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: QueueEventAction
    recordedAt: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    actor: str = Field(max_length=MAX_QUEUE_TEXT)
    rationale: str = Field(default="", max_length=MAX_QUEUE_TEXT)
    previousRevision: int = Field(ge=0)
    sprintCompleted: bool | None = None
    state: CloseoutQueueState

    @field_validator("requestId", "recordedAt", "actor")
    @classmethod
    def _nonblank_metadata(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("queue transaction identity, time, and actor must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _revision_advances_once(self) -> CloseoutQueuePendingTransaction:
        if self.state.revision != self.previousRevision + 1:
            raise ValueError("queue transaction must advance the state by exactly one revision")
        if self.transactionKind == "sprint-status":
            expected_fingerprint = hashlib.sha256(
                self.state.model_dump_json(exclude_none=True).encode()
            ).hexdigest()
            if (
                self.action != "reclaim-sprint"
                or self.sprintCompleted is None
                or self.requestFingerprint != expected_fingerprint
                or self.state.closed != self.sprintCompleted
                or self.state.candidates
                or self.state.activeBlocker is not None
                or self.state.appliedRequests
            ):
                raise ValueError(
                    "sprint-status transaction requires one quiescent exact closed/open target"
                )
            return self
        if self.sprintCompleted is not None:
            raise ValueError("queue mutation cannot carry sprint status intent")
        receipt = next(
            (item for item in self.state.appliedRequests if item.requestId == self.requestId),
            None,
        )
        if (
            receipt is None
            or receipt.fingerprint != self.requestFingerprint
            or receipt.revision != self.state.revision
        ):
            raise ValueError("queue transaction state must carry its exact retry receipt")
        return self
