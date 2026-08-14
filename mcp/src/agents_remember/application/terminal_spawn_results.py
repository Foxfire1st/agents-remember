"""Public spawn-session refusal payloads at the application boundary."""

from __future__ import annotations

from typing import Any

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal import SpawnAgentSessionStatus
from agents_remember.models.worktree import SourceLineageProjection
from agents_remember.serving.terminal_opener import OpenTerminalResult


def spawn_refusal(
    status: SpawnAgentSessionStatus,
    harness: str | None,
    kind: str,
    *,
    detail: str | None = None,
    source_lineage: SourceLineageProjection | None = None,
) -> dict[str, Any]:
    """Build one validated pre-spawn refusal without creating a host process."""
    return {
        "ok": False,
        "operation": "spawn_agent_session",
        "status": status,
        "session": "",
        "harness": harness,
        "kind": kind if kind in ("harness", "terminal") else None,
        "detail": detail,
        "sourceLineage": source_lineage,
    }


def open_terminal_refusal(
    result: OpenTerminalResult,
    *,
    harness: str | None,
    kind: str,
    session_id: str,
    task_document_ref: TaskDocumentRef | None,
) -> dict[str, Any] | None:
    """Translate a non-opened terminal outcome into its public refusal payload."""
    status_map: tuple[tuple[SpawnAgentSessionStatus, str], ...] = (
        ("bad-kind", "bad-kind"),
        ("launch-selection-invalid", "launch-conflict"),
        ("task-binding-required", "task-binding-required"),
        ("task-binding-invalid", "task-binding-invalid"),
        ("source-lineage-stale", "source-lineage-stale"),
        ("source-lineage-unavailable", "source-lineage-unavailable"),
    )
    mapped: SpawnAgentSessionStatus | None = next(
        (public for public, internal in status_map if internal == result.status), None
    )
    if mapped is not None:
        return spawn_refusal(
            mapped,
            harness,
            kind,
            detail=result.detail,
            source_lineage=(
                result.source_lineage if mapped.startswith("source-lineage-") else None
            ),
        )
    if result.status == "seat-taken":
        return {
            "ok": False,
            "operation": "spawn_agent_session",
            "status": "seat-taken",
            "session": session_id,
            "harness": harness,
            "kind": result.kind,
            "taskDocumentRef": (
                task_document_ref.model_dump() if task_document_ref is not None else None
            ),
            "seatRole": result.seat_role,
            "ownerSession": result.owner_session_id,
        }
    return None
