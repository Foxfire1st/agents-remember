"""Shared fail-closed error for closeout queue application and lifecycle services."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agents_remember.errors import AgentsRememberError
from agents_remember.models.task_document_ref import TaskDocumentRef


class CloseoutQueueError(AgentsRememberError):
    """A queue request is malformed or violates the current mechanistic facts."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(f"{status}: {detail}")


def queue_task_ref(
    raw: TaskDocumentRef | dict[str, Any] | None,
    label: str,
) -> TaskDocumentRef:
    """Validate one request-carried task-document reference, fail closed."""

    if raw is None:
        raise CloseoutQueueError("closeout-queue-reference-required", f"{label} is required")
    if isinstance(raw, TaskDocumentRef):
        return raw
    try:
        return TaskDocumentRef.model_validate(raw)
    except ValidationError as exc:
        raise CloseoutQueueError("closeout-queue-reference-invalid", f"{label}: {exc}") from exc
