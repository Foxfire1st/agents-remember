"""Author the JSON-primary task document and re-render its markdown.

One operation-dispatched controller behind the ``task_doc`` MCP tool. It loads
(or creates) the ``ar-task-document/v1`` JSON for a task, applies a single edit,
and rewrites both the JSON (the source of truth) and the rendered markdown. The
markdown is never parsed back, and every result is re-validated against the
schema before it is written.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.controllers._guards import require_within_coordination
from agents_remember.errors import AgentsRememberError
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.tasks import (
    TaskDocument,
    json_path_for,
    markdown_path_for,
    read_task_doc,
    render_markdown,
    step_done,
    step_total,
    write_task_docs,
)
from agents_remember.tasks.master_sync import MasterSyncError, MasterSyncPlan, plan_master_sync
from agents_remember.tasks.reopen import reopen_task
from agents_remember.worktrees.task_resolver import (
    TaskResolutionError,
    is_enclosure_contract,
    resolve_active_task_root,
    resolve_leaf_enclosure_contract,
    series_contract_path,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

VALID_OPERATIONS = (
    "create",
    "replace",
    "set_status",
    "set_step",
    "set_subtask",
    "remove_subtask",
    "set_section",
    "append_decision",
    "set_field",
    "get",
)

# set_field may only touch these (scalars + flat string lists); structural edits
# go through create / replace / set_step / append_decision.
_MUTABLE_FIELDS = frozenset(
    {
        "title",
        "type",
        "status",
        "objective",
        "design",
        "requirements",
        "openQuestions",
        "references",
        "lifecycleId",
        "seriesContractPath",
        "enclosures",
        "master",
        "codeExamplesNote",
        "statusNote",
    }
)


class TaskDocError(AgentsRememberError):
    """Raised when a task-document operation cannot be completed."""


def task_doc_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    operation: str,
    task_name: str | None = None,
    contract_path: str | None = None,
    slug: str | None = None,
    fields: dict[str, Any] | None = None,
    step: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    subtask: dict[str, Any] | None = None,
    section: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if operation not in VALID_OPERATIONS:
        raise TaskDocError(
            f"unknown operation {operation!r}; expected one of {', '.join(VALID_OPERATIONS)}"
        )
    task_root, contract = _resolve(config, repo_id, task_name, contract_path)
    payload_fields = fields or {}

    if operation == "get":
        json_path = _existing_json(task_root, slug)
        doc = read_task_doc(json_path)
        return _result(operation, doc, json_path, markdown_path_for(task_root, doc))

    if operation == "remove_subtask":
        return _remove_subtask(task_root, slug, subtask, dry_run)

    if operation == "create":
        doc = _create(payload_fields, contract, task_root)
    elif operation == "replace":
        json_path = _existing_json(task_root, slug)
        doc = _replace(payload_fields, contract, task_root, json_path)
    else:
        doc = _apply(
            operation,
            read_task_doc(_existing_json(task_root, slug)),
            fields=payload_fields,
            step=step,
            decision=decision,
            subtask=subtask,
            section=section,
        )

    try:
        master_sync = plan_master_sync(task_root, doc)
    except MasterSyncError as exc:
        raise TaskDocError(str(exc)) from exc
    if dry_run:
        return _preview(operation, doc, task_root, master_sync=master_sync)
    docs: list[TaskDocument] = [doc]
    if master_sync.changed and master_sync.master is not None:
        docs.append(master_sync.master)
    json_path, markdown_path = write_task_docs(task_root, docs)[0]
    return _result(operation, doc, json_path, markdown_path, master_sync=master_sync)


def _resolve(
    config: McpRuntimeConfig,
    repo_id: str,
    task_name: str | None,
    contract_path: str | None,
) -> tuple[Path, WorktreeContract | None]:
    if contract_path:
        path = Path(contract_path)
        contract = _load_contract_opt(path)
        if contract is not None:
            return contract.task_root, contract
        task_root = path.parent.parent.parent if is_enclosure_contract(path) else path.parent
        return task_root, None
    if not task_name:
        raise TaskDocError("task_doc requires either task_name or contract_path")
    task_root = resolve_active_task_root(config.coordination_root, repo_id, task_name)
    contract = _load_contract_opt(series_contract_path(task_root))
    if contract is not None:
        return task_root, contract
    try:
        leaf_path = resolve_leaf_enclosure_contract(config.coordination_root, repo_id, task_name)
    except TaskResolutionError as exc:
        raise TaskDocError(str(exc)) from exc
    if leaf_path is not None:
        leaf_contract = _load_contract_opt(leaf_path)
        if leaf_contract is not None:
            return leaf_contract.task_root, leaf_contract
    return task_root, None


def _load_contract_opt(contract_path: Path) -> WorktreeContract | None:
    if not contract_path.exists():
        return None
    try:
        return load_contract(contract_path)
    except ContractError:
        return None


def _existing_json(task_root: Path, slug: str | None) -> Path:
    stem = slug or "task"
    path = task_root / f"{stem}.json"
    if not path.exists():
        raise TaskDocError(f"task document not found: {path} (create it first)")
    return path


def _create(
    fields: dict[str, Any],
    contract: WorktreeContract | None,
    task_root: Path,
) -> TaskDocument:
    doc = _build_doc(fields, contract, task_root)
    json_path = json_path_for(task_root, doc)
    if json_path.exists():
        raise TaskDocError(f"task document already exists: {json_path}; use an update operation")
    return doc


def _replace(
    fields: dict[str, Any],
    contract: WorktreeContract | None,
    task_root: Path,
    existing_json_path: Path,
) -> TaskDocument:
    doc = _build_doc(fields, contract, task_root)
    replacement_json_path = json_path_for(task_root, doc)
    if replacement_json_path != existing_json_path:
        raise TaskDocError(
            "replace cannot change the task document path; keep the same slug/kind or create a new document"
        )
    return doc


def _build_doc(
    fields: dict[str, Any],
    contract: WorktreeContract | None,
    task_root: Path,
) -> TaskDocument:
    data = dict(fields)
    if data.get("kind") == "light":
        raise TaskDocError(
            "light task documents are no longer supported — author a master, or a "
            "subTask (leaf) under a master. Every task is wrapped master/leaf, even a "
            "single-file change."
        )
    if "kind" not in data:
        # Master/leaf only — there is no "light" default. A create against a leaf
        # contract is a subTask; anything else (no contract, or a standalone
        # top-level task) is a master.
        data["kind"] = "subTask" if (contract is not None and contract.kind == "leaf") else "master"
    if contract is not None:
        # A master spans the series, not one lifecycle, so it never takes a lifecycleId.
        if contract.kind == "leaf" and contract.lifecycle_id and data.get("kind") != "master":
            data.setdefault("lifecycleId", contract.lifecycle_id)
        data.setdefault("seriesContractPath", series_contract_path(task_root).as_posix())
        if contract.kind == "leaf":
            data.setdefault(
                "enclosures",
                [
                    {
                        "leafId": contract.leaf_id,
                        "enclosurePath": contract.contract_path.as_posix(),
                    }
                ],
            )
    return _validate(data)


def _apply(
    operation: str,
    doc: TaskDocument,
    *,
    fields: dict[str, Any],
    step: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    subtask: dict[str, Any] | None,
    section: dict[str, Any] | None,
) -> TaskDocument:
    data = doc.model_dump(by_alias=True)
    if operation == "set_status":
        status = fields.get("status")
        if status is None:
            raise TaskDocError("set_status requires fields.status")
        data["status"] = status
    elif operation == "set_field":
        updates = {key: value for key, value in fields.items() if key in _MUTABLE_FIELDS}
        if not updates:
            raise TaskDocError(
                f"set_field requires at least one of: {', '.join(sorted(_MUTABLE_FIELDS))}"
            )
        data.update(updates)
    elif operation == "set_step":
        if doc.kind == "master":
            raise TaskDocError("set_step is not valid for a master; use set_subtask")
        if not step:
            raise TaskDocError("set_step requires a step object")
        _upsert_step(data, step)
    elif operation == "set_subtask":
        if doc.kind != "master":
            raise TaskDocError("set_subtask is only valid for a master document")
        if not subtask:
            raise TaskDocError("set_subtask requires a subtask object")
        _upsert_subtask(data, subtask)
    elif operation == "set_section":
        if not section:
            raise TaskDocError("set_section requires a section object")
        # A leaf doc may carry freeform sections (R4); the schema validator backstops the
        # leaf "freeform only" rule, so there is no master-only gate here (unlike set_subtask).
        _upsert_section(data, section)
    elif operation == "append_decision":
        if not decision:
            raise TaskDocError("append_decision requires a decision object")
        decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
        decisions.append(decision)
    return _validate(data)


def _upsert_step(data: dict[str, Any], step: dict[str, Any]) -> None:
    if not step.get("id"):
        raise TaskDocError("set_step requires step.id")
    steps: list[dict[str, Any]] = data.setdefault("steps", [])
    parent_id = step.get("parent")
    if parent_id:
        parent = _find(steps, str(parent_id))
        if parent is None:
            raise TaskDocError(f"set_step: parent step {parent_id!r} not found")
        substeps: list[dict[str, Any]] = parent.setdefault("substeps", [])
        _upsert(substeps, step, keys=("title", "status", "note"))
    else:
        _upsert(steps, step, keys=("title", "status"))


def _upsert(
    items: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    keys: tuple[str, ...],
) -> None:
    item_id = str(payload["id"])
    updates = {key: payload[key] for key in keys if key in payload}
    existing = _find(items, item_id)
    if existing is None:
        new_item: dict[str, Any] = {"id": item_id, "title": payload.get("title", item_id)}
        new_item.update(updates)
        items.append(new_item)
    else:
        existing.update(updates)


def _find(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def _upsert_subtask(data: dict[str, Any], subtask: dict[str, Any]) -> None:
    number = subtask.get("number")
    if not number:
        raise TaskDocError("set_subtask requires subtask.number")
    refs: list[dict[str, Any]] = data.setdefault("subTasks", [])
    updates = {key: subtask[key] for key in ("name", "file", "status", "scope") if key in subtask}
    existing = next((ref for ref in refs if ref.get("number") == number), None)
    if existing is None:
        new_ref: dict[str, Any] = {"number": str(number), "name": subtask.get("name", str(number))}
        new_ref.update(updates)
        refs.append(new_ref)
    else:
        existing.update(updates)


def _upsert_section(data: dict[str, Any], section: dict[str, Any]) -> None:
    heading = section.get("heading")
    if not heading:
        raise TaskDocError("set_section requires section.heading")
    sections: list[dict[str, Any]] = data.setdefault("sections", [])
    updates = {key: section[key] for key in ("kind", "body") if key in section}
    existing = next((sec for sec in sections if sec.get("heading") == heading), None)
    if existing is None:
        new_section: dict[str, Any] = {"heading": heading}
        new_section.update(updates)
        sections.append(new_section)
    else:
        existing.update(updates)


def _remove_subtask(
    task_root: Path,
    slug: str | None,
    subtask: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Remove a sub-task row from a master and (by default) delete the leaf doc it points at.

    ``remove_subtask`` completes task-doc CRUD: it drops the ``SubTaskRef`` by ``number`` from the
    master ``subTasks`` index and deletes the referenced leaf document (``<slug>.json`` + ``.md``)
    -- "remove means remove" -- unless ``subtask.keep_file`` is set, in which case only the index
    row is removed and the leaf doc is left on disk.
    """
    if not subtask or not subtask.get("number"):
        raise TaskDocError("remove_subtask requires subtask.number")
    number = str(subtask["number"])
    keep_file = bool(subtask.get("keep_file"))
    doc = read_task_doc(_existing_json(task_root, slug))
    if doc.kind != "master":
        raise TaskDocError("remove_subtask is only valid for a master document")
    data = doc.model_dump(by_alias=True)
    refs: list[dict[str, Any]] = data.get("subTasks", [])
    match = next((ref for ref in refs if ref.get("number") == number), None)
    if match is None:
        raise TaskDocError(f"remove_subtask: subtask {number!r} not found")
    data["subTasks"] = [ref for ref in refs if ref.get("number") != number]
    updated = _validate(data)
    leaf_files = _leaf_doc_files(task_root, match)
    if dry_run:
        result = _preview("remove_subtask", updated, task_root)
        result["removedSubtask"] = number
        result["wouldDeleteFiles"] = (
            [] if keep_file else [path.as_posix() for path in leaf_files if path.exists()]
        )
        return result
    json_path, markdown_path = write_task_docs(task_root, [updated])[0]
    deleted: list[str] = []
    if not keep_file:
        for path in leaf_files:
            if path.exists():
                path.unlink()
                deleted.append(path.as_posix())
    result = _result("remove_subtask", updated, json_path, markdown_path)
    result["removedSubtask"] = number
    result["deletedFiles"] = deleted
    return result


def _leaf_doc_files(task_root: Path, ref: dict[str, Any]) -> list[Path]:
    """The leaf sub-task's JSON + markdown files implied by a master ``SubTaskRef.file``."""
    file_name = ref.get("file") or ""
    if not file_name:
        return []
    markdown = task_root / file_name
    return [markdown.with_suffix(".json"), markdown]


def _validate(data: dict[str, Any]) -> TaskDocument:
    try:
        return TaskDocument.model_validate(data)
    except ValidationError as exc:
        raise TaskDocError(f"invalid task document: {exc}") from exc


def _result(
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


def _preview(
    operation: str,
    doc: TaskDocument,
    task_root: Path,
    *,
    master_sync: MasterSyncPlan | None = None,
) -> dict[str, Any]:
    """Render the would-be document without writing -- the dry-run safety preview.

    Returns the same shape as a real op plus the rendered markdown, a unified diff against the
    on-disk ``.md`` (if any), and ``wouldLose`` -- a non-blank on-disk line the render does not
    reproduce (the signal that adopting this JSON would drop hand-authored content). ``renderedPath``
    is where it *would* write; nothing is written.
    """
    preview = _render_preview(task_root, doc)
    result = _result(
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


def _render_preview(task_root: Path, doc: TaskDocument) -> dict[str, Any]:
    rendered = render_markdown(doc)
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
    master_sync: MasterSyncPlan | None, *, preview: bool
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


def task_reopen_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reopen a completed leaf task under its exact same leaf id (L11).

    Task-domain sibling of ``task_doc``: it resets the leaf's enclosure contract and
    task document back to planning; recreating the worktrees stays ``worktree_start``'s
    job. The response keeps the worktree-command shape (contract state fields), so it
    validates against a ``WorktreeCommandResponse`` subclass in the registry.
    """
    result = reopen_task(
        require_within_coordination(config, contract_path, "contract_path"),
        dry_run=dry_run,
    )
    return {**result.payload, "ok": result.returncode == 0, "operation": "task_reopen"}
