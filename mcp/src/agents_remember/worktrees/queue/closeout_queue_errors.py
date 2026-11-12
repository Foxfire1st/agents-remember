"""Shared fail-closed error for closeout queue application and lifecycle services."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from agents_remember.errors import AgentsRememberError
from agents_remember.models.closeout.projection import MAX_CLOSEOUT_TEXT
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import CommandedMasterMissingError, TaskDocumentRefError
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


# The missing-master text opens with the sprint, the master and where the master was found under
# ``0_archive`` and closes with the sentence that names the repairing operation. A text too long
# for one closeout text field keeps both ends: the closing share holds that sentence several times
# over, and the opening share is everything else the field can carry.
_MISSING_MASTER_CLOSING_CHARACTERS = 1024
_MISSING_MASTER_ELISION = " [...] "


def missing_commanded_master_text(error: BaseException | None) -> str | None:
    """Return the repair text of a sprint that commands a missing master, else ``None``.

    Topology failures are bounded to their status because their text is reader text. This one is
    the exception: the text is product-authored and is itself the repair, so every queue surface
    shows it instead of a bounded record. A text longer than one closeout text field (many archived
    documents answer to the same master) keeps its opening, which names the sprint, the master and
    the first location, and its closing sentence, which names the operation; only the middle of
    the location list is dropped.
    """

    if not isinstance(error, CommandedMasterMissingError):
        return None
    text = str(error)
    if len(text) <= MAX_CLOSEOUT_TEXT:
        return text
    opening = MAX_CLOSEOUT_TEXT - _MISSING_MASTER_CLOSING_CHARACTERS - len(_MISSING_MASTER_ELISION)
    closing = text[-_MISSING_MASTER_CLOSING_CHARACTERS:]
    return f"{text[:opening]}{_MISSING_MASTER_ELISION}{closing}"


def queue_topology_failure_detail(error: TaskDocumentRefError, *, stage: str, name: str) -> str:
    """Name a missing commanded master; bound every other task-topology failure as before."""

    return missing_commanded_master_text(error) or bounded_queue_failure_detail(
        error,
        stage=stage,
        side="task-document",
        name=name,
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
