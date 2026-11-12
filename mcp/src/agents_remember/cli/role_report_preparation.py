"""Name a launch's report and settle its locator before any artifact is written."""

from __future__ import annotations

import uuid
from pathlib import Path

from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.cli.immutable_artifact import canonical_path
from agents_remember.worktrees.role_handover_locator import ensure_locator, locator_directory

REPORTS_DIRECTORY = "role-launch"


def role_report_path(
    context: RoleLaunchContext,
    workspace: dict[str, str],
    *,
    request_id: uuid.UUID | None = None,
) -> str:
    if request_id is None:
        raise ValueError("A role report requires its durable requestId.")
    report_root = Path(workspace["taskReportAccessRoot"]) / REPORTS_DIRECTORY
    selected = context.effective_task
    if selected is None:
        report_root = report_root / context.role
    report_name = (
        f"{selected.document.id}-{context.role}-{request_id}.md" if selected else f"{request_id}.md"
    )
    report = report_root / report_name
    ensure_locator(locator_directory(str(request_id)), canonical_path(report).parent)
    report.parent.mkdir(parents=True, exist_ok=True)
    return report.as_posix()
