"""Resolve the calling hosted seat from plane-injected process identity."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.observer.ambient import ambient
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.terminal_opener import HOSTED_SESSION_ENV


@dataclass(frozen=True)
class AmbientSeatError(ValueError):
    status: str
    detail: str

    def __str__(self) -> str:
        return self.detail


def resolve_ambient_seat(
    catalog: TerminalCatalogPort,
    *,
    environ: Mapping[str, str] | None = None,
) -> TerminalCatalogEntry:
    """Return the one running catalog row proven to own this MCP process.

    The launcher injects ``AR_HOSTED_SESSION_ID`` after scrubbing the parent's value. The
    model never supplies it. When a lifecycle is active, its id must agree with the row as
    a second proof; before lifecycle attachment the hosted identity alone closes bootstrap.
    """

    process_env = os.environ if environ is None else environ
    session_id = process_env.get(HOSTED_SESSION_ENV, "").strip()
    if not session_id:
        raise AmbientSeatError(
            "ambient-seat-unavailable",
            "this MCP process has no plane-injected hosted-seat identity",
        )
    entry = catalog.get(session_id)
    if entry is None or entry.status != "running":
        raise AmbientSeatError(
            "ambient-seat-stale",
            "the plane-injected hosted-seat identity has no running catalog occupant",
        )
    if entry.kind != "harness":
        raise AmbientSeatError(
            "ambient-seat-invalid", "structural agent operations require a hosted harness seat"
        )
    role = process_env.get("AR_SPAWN_ROLE", "").strip()
    if role and role != entry.binding_role:
        raise AmbientSeatError(
            "ambient-seat-mismatch",
            "the process role does not match the catalog's structural seat role",
        )
    amb = ambient()
    lifecycle_id = amb.current.id if amb is not None and amb.current is not None else None
    if lifecycle_id is not None and lifecycle_id != entry.lifecycle_id:
        raise AmbientSeatError(
            "ambient-seat-mismatch",
            "the active lifecycle does not match the plane-injected hosted seat",
        )
    if entry.binding_task_document_ref is None:
        raise AmbientSeatError(
            "ambient-seat-unbound", "the hosted caller is not bound to a task document"
        )
    return entry
