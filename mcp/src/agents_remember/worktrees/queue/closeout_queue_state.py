"""Pure closeout queue request and initial-state construction."""

from __future__ import annotations

import hashlib
import json
from typing import cast

from agents_remember.models.queue.closeout_queue import (
    CloseoutQueueRequest,
    CloseoutQueueState,
    QueueAction,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

from .closeout_queue_errors import CloseoutQueueError

_ACTIONS = frozenset(
    {
        "status",
        "declare",
        "withdraw",
        "set-grade",
        "set-admission",
        "select",
        "release-selection",
        "acquire-blocker",
        "release-blocker",
        "abort-blocker",
    }
)


def queue_action(value: str) -> QueueAction:
    action = value.strip()
    if action not in _ACTIONS:
        raise CloseoutQueueError(
            "closeout-queue-action-invalid", f"unsupported closeout queue action: {value!r}"
        )
    return cast(QueueAction, action)


def initial_queue_state(
    sprint_ref: TaskDocumentRef,
    graph_revision: str,
    timestamp: str,
) -> CloseoutQueueState:
    return CloseoutQueueState(
        sprintTaskDocumentRef=sprint_ref,
        revision=0,
        graphRevision=graph_revision,
        candidates={},
        activeBlocker=None,
        appliedRequests=[],
        updatedAt=timestamp,
    )


def queue_request_fingerprint(request: CloseoutQueueRequest, actor_identity: str) -> str:
    payload = {
        "request": request.model_dump(mode="json", exclude_none=True),
        "actor": actor_identity,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
