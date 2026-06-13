"""Author the JSON-primary task document and re-render its markdown.

One operation-dispatched controller behind the ``task_doc`` MCP tool. It loads
(or creates) the ``ar-task-document/v1`` JSON for a task, applies a single edit,
and rewrites both the JSON (the source of truth) and the rendered markdown. The
markdown is never parsed back, and every result is re-validated against the
schema before it is written.
"""

from __future__ import annotations

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
    step_done,
    step_total,
    write_task_doc,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
    task_root_candidates,
)

VALID_OPERATIONS = ("create", "set_status", "set_step", "append_decision", "set_field", "get")

# set_field may only touch these (scalars + flat string lists); structural edits
# go through create / set_step / append_decision.
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
        "contractPath",
        "master",
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
    else:
        doc = _apply(
            operation,
            read_task_doc(_existing_json(task_root, slug)),
            fields=payload_fields,
            step=step,
            decision=decision,
        )

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
        return path.parent, _load_contract_opt(path)
    if not task_name:
        raise TaskDocError("task_doc requires either task_name or contract_path")
    candidates = task_root_candidates(config.coordination_root, repo_id, task_name)
    for root in candidates:
        if root.exists():
            return root, _load_contract_opt(root / "contract.md")
    return candidates[0], None


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
    data = dict(fields)
    data.setdefault("kind", "light")
    if contract is not None:
        if contract.lifecycle_id:
            data.setdefault("lifecycleId", contract.lifecycle_id)
        data.setdefault("contractPath", (task_root / "contract.md").as_posix())
    doc = _validate(data)
    json_path = json_path_for(task_root, doc)
    if json_path.exists():
        raise TaskDocError(f"task document already exists: {json_path}; use an update operation")
    return doc


def _apply(
    operation: str,
    doc: TaskDocument,
    *,
    fields: dict[str, Any],
    step: dict[str, Any] | None,
    decision: dict[str, Any] | None,
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
        if not step:
            raise TaskDocError("set_step requires a step object")
        _upsert_step(data, step)
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
