"""Shared fail-closed error for closeout queue application and lifecycle services."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from agents_remember.errors import AgentsRememberError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)


class CloseoutQueueError(AgentsRememberError):
    """A queue request is malformed or violates the current mechanistic facts."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


# Capacity refusals are the one refusal family whose source was READ perfectly and is INVALID:
# the graph, or the problem list built from it, is larger than its bound admits. They are
# declared here, beside the codes themselves, because deriving that state from the spelling is
# exactly how the closeout projection came to test the substring "cap-exceeded" and miss every
# code that spells it "capacity-exceeded" -- so a sprint past its graph bound was reported to the
# operator as a source that could not be read, when the truth was the opposite. One declaration
# owns each code and its classification, so a raiser and the classifier cannot drift apart again:
# renaming a code here moves both sides, which a substring test never did.
MASTER_CAPACITY_EXCEEDED = "closeout-queue-master-capacity-exceeded"
EDGE_CAPACITY_EXCEEDED = "closeout-queue-edge-capacity-exceeded"
SOURCE_PROBLEM_CAP_EXCEEDED = "source-problem-cap-exceeded"

CAPACITY_REFUSAL_CODES = frozenset(
    {MASTER_CAPACITY_EXCEEDED, EDGE_CAPACITY_EXCEEDED, SOURCE_PROBLEM_CAP_EXCEEDED}
)


def bounded_queue_failure_detail(
    error: Exception,
    *,
    stage: str,
    side: str,
    name: str,
) -> str:
    """Serialize one stable queue failure without backend text or offending input."""

    return json.dumps(
        public_failure_evidence(
            stage=stage,
            side=side,
            name=name,
            error_type=type(error).__name__,
            observed={"state": "blocked"},
        ),
        sort_keys=True,
    )


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
        raise CloseoutQueueError(
            "closeout-queue-reference-invalid",
            bounded_queue_failure_detail(
                exc,
                stage="queue-request-validation",
                side="request",
                name=label,
            ),
        ) from exc
