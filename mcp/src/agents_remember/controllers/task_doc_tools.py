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
    write_task_doc,
)
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

    if dry_run:
        return _preview(operation, doc, task_root)
    json_path, markdown_path = write_task_doc(task_root, doc)
    return _result(operation, doc, json_path, markdown_path)


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
    data.setdefault("kind", "light")
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
) -> dict[str, Any]:
    return {
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


def _preview(operation: str, doc: TaskDocument, task_root: Path) -> dict[str, Any]:
    """Render the would-be document without writing -- the dry-run safety preview.

    Returns the same shape as a real op plus the rendered markdown, a unified diff against the
    on-disk ``.md`` (if any), and ``wouldLose`` -- a non-blank on-disk line the render does not
    reproduce (the signal that adopting this JSON would drop hand-authored content). ``renderedPath``
    is where it *would* write; nothing is written.
    """
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
    would_lose = any(line.strip() and line not in rendered_lines for line in existing.splitlines())
    result = _result(operation, doc, json_path_for(task_root, doc), markdown_path)
    result["dryRun"] = True
    result["rendered"] = rendered
    result["diff"] = diff
    result["wouldLose"] = would_lose
    return result
