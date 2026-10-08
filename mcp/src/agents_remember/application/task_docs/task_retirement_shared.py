"""What the retire operation's steps share: the request payload, the archive hook and source digests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from agents_remember.models.task_retirement import RetirementEdgeSelection
from agents_remember.worktrees.services import ReviewArtifactCleanupRequest

from ..review_artifact_cleanup import cleanup_review_artifacts
from ..review_artifact_receipts import DELETION_KEYS
from .task_sprint_candidates import SprintLinkageError
from .task_sprint_context import SprintLinkageRequest, _Payload


class RetireMasterPayload(_Payload):
    reason: str = Field(min_length=1, max_length=4096)
    removeEdges: list[RetirementEdgeSelection] = Field(default_factory=list, max_length=2048)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("retire_master requires a nonblank reason")
        return value.strip()


def archive_hook(request: SprintLinkageRequest, task_root: Path) -> dict[str, Any]:
    """Run the unchanged archive hook; whatever it raises is a reported failure, never a crash.

    The retirement is already committed when this runs, so no exception may leave the operation's
    state unclear. This is as broad as finalization's own handling of the same hook.
    """

    try:
        return cleanup_review_artifacts(
            ReviewArtifactCleanupRequest(
                task_root=task_root,
                task_name=task_root.name,
                code_repository=request.code_repository,
                memory_repository=request.memory_repository,
                dry_run=request.dry_run,
            )
        )
    except Exception as exc:  # the retirement is committed: a failed hook is reported, not raised
        return {"state": "failed", "detail": f"{type(exc).__name__}: {exc}"}


RESTART_NOTICE = (
    "Restart required: every agents-remember MCP server and the dashboard that was started "
    "before this build predates master retirement and cannot parse a sprint that holds a "
    "retired row; restart them before they read this sprint."
)


def hook_failed(report: dict[str, Any]) -> bool:
    """Whether the hook's report names a failure: one it lists, or the hook itself failing."""

    return bool(report.get("failures")) or report.get("state") == "failed"


def hook_remainder(report: dict[str, Any]) -> int:
    """How many artifacts a dry run of the hook lists as still to delete."""

    return sum(len(report.get(key, [])) for key in DELETION_KEYS)


def require_root_master_folder(
    request: SprintLinkageRequest, master_key: str, source: Path
) -> None:
    """Retire only a master whose folder sits directly under ``tasks/<repository>/``.

    The archive location ``0_archive/<name>`` is defined for root task folders only, so a master
    nested inside another task's folder is refused (dry run included) before anything changes.
    """

    tasks = (request.coordination_root / "tasks" / request.repo_id).resolve()
    if source.resolve().parent != tasks:
        raise SprintLinkageError(
            f"master {master_key} sits in {source.resolve().parent}, nested inside another task's folder; "
            "the archive location 0_archive/<name> is defined only for root task folders under "
            f"{tasks}, so task_doc.retire_master will not move it. Keep it in place, or move it to "
            "a root task folder first"
        )


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def optional_digest(payload: bytes | None) -> str | None:
    return digest(payload) if payload is not None else None
