"""A sprint's closeout-queue projection after a retirement that was completed late.

A fresh retirement publishes the sprint edit and refreshes the sprint's projection in the same
publication. A process that dies after the folder has moved never reaches that refresh, and the
completing request changes no document, so nothing would name the projection again. The completing
request therefore looks at the stored projection itself and refreshes it when it no longer reflects
the task documents. A projection that is current, or a sprint from which none can be built, is left
alone, so a repeated request that has nothing to do changes nothing.
"""

from __future__ import annotations

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.closeout.projection import TaskDocProjectionEffect
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.queue.closeout_projection import capture_projection_source, now_iso
from agents_remember.worktrees.queue.closeout_projection_publication import (
    preview_closeout_projection_effect,
    projection_refresh_failure_effect,
    refresh_closeout_projection,
)

from .task_sprint_context import SprintLinkageRequest


def refresh_stale_projection(
    request: SprintLinkageRequest, sprint_ref: TaskDocumentRef
) -> tuple[TaskDocProjectionEffect, ...]:
    """Refresh the sprint's projection when it is stale; the effect, or nothing when it is current."""

    root = request.coordination_root
    try:
        if not _stale(request, sprint_ref):
            return ()
        return (refresh_closeout_projection(root, sprint_ref),)
    except Exception as exc:  # the projection is disposable output; the retirement stands
        return (projection_refresh_failure_effect(root, sprint_ref, exc),)


def preview_stale_projection(
    request: SprintLinkageRequest, sprint_ref: TaskDocumentRef
) -> tuple[TaskDocProjectionEffect, ...]:
    """What :func:`refresh_stale_projection` would do, without writing projection bytes."""

    if not _stale(request, sprint_ref):
        return ()
    return (preview_closeout_projection_effect(request.coordination_root, sprint_ref),)


def _stale(request: SprintLinkageRequest, sprint_ref: TaskDocumentRef) -> bool:
    timestamp = now_iso()
    store = CloseoutQueueStore(request.coordination_root, sprint_ref)
    stored = store.read_raw(timestamp=timestamp)
    source = capture_projection_source(
        request.coordination_root, sprint_ref, timestamp=timestamp
    ).identity
    if stored.serviceCondition != "valid-built":
        # Nothing built is stored: a refresh helps only when the documents can be built from.
        return source.readable
    effective = store.read_effective(timestamp=timestamp, source=source)
    return effective.serviceCondition != "valid-built"
