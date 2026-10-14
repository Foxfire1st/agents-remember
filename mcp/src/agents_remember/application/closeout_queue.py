"""Application boundary for the sprint closeout queue: the declared caller is authorized."""

from __future__ import annotations

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.worktrees.queue.closeout_queue import (
    CloseoutQueueError,
    CloseoutQueueRequest,
    QueueActor,
)
from agents_remember.worktrees.queue.closeout_queue import (
    closeout_queue_tool as apply_closeout_queue,
)


def closeout_queue_tool(
    config: McpRuntimeConfig,
    request: CloseoutQueueRequest,
) -> dict[str, object]:
    """Resolve the caller and apply one authorized queue operation.

    The caller is the identity the request declares (``caller``). The queue's own
    authorization validates the declared role and document pair.

    Trust model: the declared identity is the caller's own statement -- any caller
    able to reach the MCP server may claim any role/document and, passing the
    role/document authorization, act with that identity's authority. The residual
    risk is deployment-level: restrict who may reach the server, and treat the
    declared identity as assertion, not proof.
    """

    return apply_closeout_queue(config, request, actor=_declared_queue_actor(request))


def _declared_queue_actor(request: CloseoutQueueRequest) -> QueueActor:
    """Build the queue actor from the caller identity the request carries."""
    declared = request.caller
    if declared is None:
        raise CloseoutQueueError(
            "closeout-queue-caller-required",
            "ambient closeout queue callers must declare caller (role + task_document_ref)",
        )
    return QueueActor(role=declared.role, task_document_ref=declared.task_document_ref)


__all__ = [
    "CloseoutQueueError",
    "CloseoutQueueRequest",
    "closeout_queue_tool",
]
