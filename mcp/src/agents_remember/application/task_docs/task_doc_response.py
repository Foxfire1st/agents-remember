"""Shared response and dry-run rendering for task-document mutations."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from agents_remember.tasks import (
    SprintGraphTitles,
    TaskDocument,
    json_path_for,
    markdown_path_for,
    read_graph_titles,
    render_markdown,
    step_done,
    step_total,
)
from agents_remember.tasks.master_sync import MasterSyncPlan


def task_doc_result(
    operation: str,
    doc: TaskDocument,
    json_path: Path,
    markdown_path: Path,
    *,
    master_sync: MasterSyncPlan | None = None,
) -> dict[str, Any]:
    result = {
        "ok": True,
        "operation": f"task_doc.{operation}",
        "taskId": doc.id,
        "slug": doc.slug,
        "kind": doc.kind,
        "status": doc.status,
        "lifecycleId": doc.lifecycleId,
        "docPath": json_path.as_posix(),
        "renderedPath": markdown_path.as_posix(),
        "stepsDone": step_done(doc),
        "stepsTotal": step_total(doc),
    }
    sync_payload = _master_sync_payload(master_sync, preview=False)
    if sync_payload is not None:
        result["masterSync"] = sync_payload
    return result


def task_doc_preview(
    operation: str,
    doc: TaskDocument,
    task_root: Path,
    *,
    master_sync: MasterSyncPlan | None = None,
) -> dict[str, Any]:
    """Return the validated would-be document, rendered diff, and loss signal."""

    preview = _render_preview(task_root, doc)
    result = task_doc_result(
        operation,
        doc,
        json_path_for(task_root, doc),
        preview["markdownPath"],
        master_sync=master_sync,
    )
    result["dryRun"] = True
    result["rendered"] = preview["rendered"]
    result["diff"] = preview["diff"]
    result["wouldLose"] = preview["wouldLose"]
    sync_payload = _master_sync_payload(master_sync, preview=True)
    if sync_payload is not None:
        result["masterSync"] = sync_payload
    return result


def graph_titles_for(task_root: Path, doc: TaskDocument) -> SprintGraphTitles | None:
    """Resolve joined master/leaf titles for a sprint execution-graph render."""

    if doc.executionGraph is None:
        return None
    return read_graph_titles(task_root.parents[1], doc.executionGraph)


def _render_preview(task_root: Path, doc: TaskDocument) -> dict[str, Any]:
    rendered = render_markdown(doc, graph_titles=graph_titles_for(task_root, doc))
    markdown_path = markdown_path_for(task_root, doc)
    existing = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
    diff = "".join(
        difflib.unified_diff(
            existing.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{markdown_path.name} (on disk)",
            tofile=f"{markdown_path.name} (rendered)",
        )
    )
    rendered_lines = set(rendered.splitlines())
    return {
        "markdownPath": markdown_path,
        "rendered": rendered,
        "diff": diff,
        "wouldLose": any(
            line.strip() and line not in rendered_lines for line in existing.splitlines()
        ),
    }


def _master_sync_payload(
    master_sync: MasterSyncPlan | None,
    *,
    preview: bool,
) -> dict[str, Any] | None:
    if (
        master_sync is None
        or master_sync.status == "none"
        or master_sync.master is None
        or master_sync.master_json_path is None
    ):
        return None
    master_root = master_sync.master_json_path.parent
    markdown_path = markdown_path_for(master_root, master_sync.master)
    status = master_sync.status
    if preview and status == "created":
        status = "would-create"
    elif preview and status == "updated":
        status = "would-update"
    payload: dict[str, Any] = {
        "status": status,
        "masterDocPath": master_sync.master_json_path.as_posix(),
        "renderedPath": markdown_path.as_posix(),
        "subtaskNumber": master_sync.subtask_number,
    }
    if preview:
        rendered = _render_preview(master_root, master_sync.master)
        payload["rendered"] = rendered["rendered"]
        payload["diff"] = rendered["diff"]
        payload["wouldLose"] = rendered["wouldLose"]
    return payload
