"""Ambient-authorized application boundary for the sprint closeout queue."""

from __future__ import annotations

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.ambient_seat import AmbientSeatError, resolve_ambient_seat
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueError,
    CloseoutQueueRequest,
    QueueActor,
)
from agents_remember.worktrees.closeout_queue import (
    closeout_queue_tool as apply_closeout_queue,
)


def closeout_queue_tool(
    config: McpRuntimeConfig,
    request: CloseoutQueueRequest,
) -> dict[str, object]:
    """Resolve the hosted structural caller and apply one authorized queue operation."""

    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    try:
        caller = resolve_ambient_seat(catalog)
    except AmbientSeatError as exc:
        raise CloseoutQueueError(exc.status, str(exc)) from exc
    document = caller.binding_task_document_ref
    if document is None:  # resolve_ambient_seat already proves this; keep the type boundary exact.
        raise CloseoutQueueError(
            "ambient-seat-unbound", "closeout queue callers require a canonical task document"
        )
    return apply_closeout_queue(
        config,
        request,
        actor=QueueActor(role=caller.binding_role, task_document_ref=document),
    )


__all__ = [
    "CloseoutQueueError",
    "CloseoutQueueRequest",
    "closeout_queue_tool",
]
