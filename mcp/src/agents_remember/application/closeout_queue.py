"""Ambient-authorized application boundary for the sprint closeout queue."""

from __future__ import annotations

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.ambient_seat import AmbientSeatError, resolve_ambient_seat
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
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

    A plane-injected hosted seat wins when one exists -- the seat path is
    unchanged. When the process has no plane identity at all
    (``ambient-seat-unavailable``), the caller's declared identity (``caller``)
    is used instead and validated by the same queue authorization the seat
    would face: the declaration grants no authority the same role/document pair
    would not have from a seat.

    Trust model: this fallback accepts the caller's self-declared identity --
    any caller able to reach the MCP server may claim any role/document and,
    passing the role/document authorization, act with that identity's authority.
    The mechanism grants no more than a seat with the same pair would, so the
    residual risk is deployment-level: restrict who may reach the server, and
    treat the declared identity as assertion, not plane proof.
    """

    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    try:
        caller = resolve_ambient_seat(catalog)
    except AmbientSeatError as exc:
        if exc.status != "ambient-seat-unavailable":
            raise CloseoutQueueError(
                exc.status,
                "the hosted closeout-queue caller identity could not be resolved",
            ) from exc
        actor = _declared_queue_actor(request)
        return apply_closeout_queue(config, request, actor=actor)
    document = caller.binding_task_document_ref
    if document is None:  # resolve_ambient_seat already proves this; keep the type boundary exact.
        raise CloseoutQueueError(
            "ambient-seat-unbound", "closeout queue callers require a canonical task document"
        )
    _refuse_hosted_declared_conflict(request, caller.binding_role, document)
    return apply_closeout_queue(
        config,
        request,
        actor=QueueActor(role=caller.binding_role, task_document_ref=document),
    )


def _declared_queue_actor(request: CloseoutQueueRequest) -> QueueActor:
    """Build the queue actor from a request-carried ambient caller identity."""
    declared = request.caller
    if declared is None:
        raise CloseoutQueueError(
            "closeout-queue-caller-required",
            "ambient closeout queue callers must declare caller (role + task_document_ref)",
        )
    return QueueActor(role=declared.role, task_document_ref=declared.task_document_ref)


def _refuse_hosted_declared_conflict(
    request: CloseoutQueueRequest, seat_role: str, seat_document: TaskDocumentRef
) -> None:
    """Refuse a request-carried caller that contradicts the hosted seat."""
    declared = request.caller
    if declared is None:
        return
    if declared.role != seat_role or declared.task_document_ref != seat_document:
        raise CloseoutQueueError(
            "closeout-queue-caller-conflict",
            "declared caller conflicts with the plane-injected hosted seat",
        )


__all__ = [
    "CloseoutQueueError",
    "CloseoutQueueRequest",
    "closeout_queue_tool",
]
