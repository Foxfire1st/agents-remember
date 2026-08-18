"""Author the JSON-primary task document and re-render its markdown.

One operation-dispatched entry point behind the ``task_doc`` MCP tool. It loads
(or creates) the ``ar-task-document/v1`` JSON for a task, applies a single edit,
and rewrites both the JSON (the source of truth) and the rendered markdown. The
markdown is never parsed back, and every result is re-validated against the
schema before it is written.
"""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    completion_blockers,
    json_path_for,
    markdown_path_for,
    read_task_doc,
    render_markdown,
    step_done,
    step_total,
    write_task_docs,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.tasks.master_sync import MasterSyncError, MasterSyncPlan, plan_master_sync
from agents_remember.tasks.readiness import (
    completed_master_rows_to_validate,
    missing_unresolved_master_rows,
)
from agents_remember.worktrees.integration_branch_authority import (
    require_topology_publication_authority,
)
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.route_review import RouteReviewError, build_route_review
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

from .task_doc_queue_scope import QueueScopeError, governing_queue_scope
from .task_execution_topology import (
    ExecutionTopologyEditRequest,
    ExecutionTopologyError,
    ExecutionTopologyMigrationRequest,
    enforce_execution_topology_edit,
    migrate_execution_topology,
    require_commanded_masters_completed,
)
from .worktree_tools import end_ambient_lifecycle_if_anchored

VALID_OPERATIONS = (
    "create",
    "replace",
    "set_status",
    "set_step",
    "skip_step",
    "set_subtask",
    "remove_subtask",
    "set_section",
    "append_decision",
    "record_route_review",
    "migrate_execution_topology",
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
        "master",
        "codeExamplesNote",
        "statusNote",
        "orchestrates",
        "integrationBranch",
        "executionNature",
        "executionGraph",
    }
)


class TaskDocError(AgentsRememberError):
    """Raised when a task-document operation cannot be completed."""


@dataclass(frozen=True)
class TaskDocTarget:
    """Which task document a ``task_doc`` call addresses.

    The task itself is located from the repo plus either its name or its contract
    path; ``slug`` then picks the document inside that task root (the leaf's own
    ``task.json`` by default).
    """

    repo_id: str
    task_name: str | None = None
    contract_path: str | None = None
    slug: str | None = None


@dataclass(frozen=True)
class TaskDocEdit:
    """The single edit a ``task_doc`` call applies, in the shape the operation names.

    Each operation draws from exactly one of these: ``set_status``/``set_field`` from
    ``fields``, ``set_step`` from ``step``, and so on. The rest stay unset.
    """

    fields: dict[str, Any] | None = None
    step: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    subtask: dict[str, Any] | None = None
    section: dict[str, Any] | None = None
    review: dict[str, Any] | None = None


NO_EDIT = TaskDocEdit()
"""The read-only form, for operations (``get``) that change nothing."""


@dataclass(frozen=True)
class _TaskDocSpecialContext:
    config: McpRuntimeConfig
    target: TaskDocTarget
    task_root: Path
    operation: str
    edit: TaskDocEdit
    fields: dict[str, Any]
    dry_run: bool


@dataclass(frozen=True)
class _TaskDocPublication:
    config: McpRuntimeConfig
    target: TaskDocTarget
    task_root: Path
    original: TaskDocument | None
    candidate: TaskDocument
    documents: list[TaskDocument]
    publisher: Callable[[], list[tuple[Path, Path]]] | None = None


def task_doc_tool(
    config: McpRuntimeConfig,
    target: TaskDocTarget,
    *,
    operation: str,
    edit: TaskDocEdit = NO_EDIT,
    dry_run: bool = False,
) -> dict[str, Any]:
    if operation not in VALID_OPERATIONS:
        raise TaskDocError(
            f"unknown operation {operation!r}; expected one of {', '.join(VALID_OPERATIONS)}"
        )
    task_root, contract = _resolve(config, target.repo_id, target.task_name, target.contract_path)
    payload_fields = edit.fields or {}
    slug = target.slug

    special = _special_task_doc_operation(
        _TaskDocSpecialContext(
            config=config,
            target=target,
            task_root=task_root,
            operation=operation,
            edit=edit,
            fields=payload_fields,
            dry_run=dry_run,
        )
    )
    if special is not None:
        return special

    original: TaskDocument | None = None
    if operation == "create":
        doc = _create(payload_fields, contract, task_root)
    elif operation == "replace":
        json_path = _existing_json(task_root, slug)
        original = read_task_doc(json_path)
        doc = _replace(payload_fields, contract, task_root, json_path)
    elif operation == "record_route_review":
        json_path = _existing_json(task_root, slug)
        original = read_task_doc(json_path)
        doc = _record_route_review(original, edit.review, contract, task_root, json_path)
    else:
        original = read_task_doc(_existing_json(task_root, slug))
        doc = _apply(operation, original, edit, contract=contract)

    _enforce_disposition_authority(operation, original, doc)
    _enforce_route_review_authority(operation, original, doc)
    _enforce_replace_preserves_unresolved_units(operation, original, doc)
    _enforce_preserves_unresolved_master_rows(operation, original, doc)
    _enforce_terminal_status(doc)
    _enforce_completed_master_rows(task_root, operation, original, doc, edit)
    try:
        enforce_execution_topology_edit(
            ExecutionTopologyEditRequest(
                coordination_root=config.coordination_root,
                repo_id=target.repo_id,
                task_root=task_root,
                operation=operation,
                original=original,
                candidate=doc,
                fields=payload_fields,
            )
        )
    except ExecutionTopologyError as exc:
        raise TaskDocError(str(exc)) from exc

    try:
        master_sync = plan_master_sync(task_root, doc)
    except MasterSyncError as exc:
        raise TaskDocError(str(exc)) from exc
    docs: list[TaskDocument] = [doc]
    if master_sync.changed and master_sync.master is not None:
        docs.append(master_sync.master)
    if dry_run:
        preview_context = _TaskDocPublication(config, target, task_root, original, doc, docs)
        with integration_authority_lock(config.coordination_root, target.repo_id):
            _validate_task_doc_publication_authority(preview_context)
        return _preview(operation, doc, task_root, master_sync=master_sync)
    written = _publish_task_doc_set(
        _TaskDocPublication(config, target, task_root, original, doc, docs)
    )
    json_path, markdown_path = written[0]
    return _result(operation, doc, json_path, markdown_path, master_sync=master_sync)


def _publish_task_doc_set(context: _TaskDocPublication) -> list[tuple[Path, Path]]:
    def publication() -> list[tuple[Path, Path]]:
        with integration_authority_lock(
            context.config.coordination_root,
            context.target.repo_id,
        ):
            _validate_task_doc_publication_authority(context)
            return (
                context.publisher()
                if context.publisher is not None
                else write_task_docs(context.task_root, context.documents)
            )

    try:
        scope = governing_queue_scope(
            context.config.coordination_root,
            context.target.repo_id,
            context.task_root,
            context.original,
            context.candidate,
        )
    except QueueScopeError as exc:
        raise TaskDocError(str(exc)) from exc
    if scope is None:
        return publication()
    queue = CloseoutQueueStore(context.config.coordination_root, scope.sprint_ref)
    if context.candidate.kind != "master" or not context.candidate.orchestrates:
        if scope.owning_master is None:
            raise TaskDocError("governed master/leaf edit has no owning master queue scope")
        return queue.publish_task_facts_update(
            publication,
            owning_master=scope.owning_master,
            topology_stable=_task_topology_stable(context.original, context.candidate),
        )
    return cast(
        list[tuple[Path, Path]],
        queue.publish_sprint_update(
            publication,
            completed=context.candidate.status == "Completed",
            recorded_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            validate_completion=lambda: require_commanded_masters_completed(
                TaskDocumentTopology(context.config.coordination_root),
                scope.sprint_ref,
                {scope.sprint_ref: context.candidate},
            ),
        ),
    )


def _validate_task_doc_publication_authority(context: _TaskDocPublication) -> None:
    repository = context.config.repositories[context.target.repo_id]
    try:
        require_topology_publication_authority(
            context.config.coordination_root,
            context.target.repo_id,
            repository.path,
            repository.memory_root,
            _task_doc_publication_overrides(context),
        )
    except RuntimeError as exc:
        raise TaskDocError(str(exc)) from exc


def _task_doc_publication_overrides(
    context: _TaskDocPublication,
) -> dict[TaskDocumentRef, TaskDocument]:
    root = (context.config.coordination_root / "tasks" / context.target.repo_id).resolve(
        strict=False
    )
    overrides: dict[TaskDocumentRef, TaskDocument] = {}
    for document in context.documents:
        path = json_path_for(context.task_root, document).resolve(strict=False)
        if not path.is_relative_to(root):
            raise TaskDocError(f"task document publication escapes tasks root: {path}")
        ref = TaskDocumentRef(
            repository=context.target.repo_id,
            path=path.relative_to(root).as_posix(),
        )
        overrides[ref] = document
    return overrides


def _task_topology_stable(original: TaskDocument | None, candidate: TaskDocument) -> bool:
    """Whether an in-blocker task update preserves scheduling identity and membership."""

    if original is None or original.kind != candidate.kind:
        return False
    stable_fields = ("id", "slug", "title", "repo", "orchestrates", "executionNature")
    if any(getattr(original, field) != getattr(candidate, field) for field in stable_fields):
        return False
    if candidate.kind != "master":
        return True

    def identity(row: SubTaskRef) -> tuple[str, str, str | None]:
        return row.number, row.name, row.file

    return [identity(row) for row in original.subTasks] == [
        identity(row) for row in candidate.subTasks
    ]


def _special_task_doc_operation(context: _TaskDocSpecialContext) -> dict[str, Any] | None:
    if context.operation == "get":
        json_path = _existing_json(context.task_root, context.target.slug)
        doc = read_task_doc(json_path)
        return _result(
            context.operation,
            doc,
            json_path,
            markdown_path_for(context.task_root, doc),
        )
    if context.operation == "migrate_execution_topology":
        repository = context.config.repositories[context.target.repo_id]
        try:
            return migrate_execution_topology(
                ExecutionTopologyMigrationRequest(
                    coordination_root=context.config.coordination_root,
                    repo_id=context.target.repo_id,
                    code_repository=repository.path,
                    memory_repository=repository.memory_root,
                    task_root=context.task_root,
                    slug=context.target.slug,
                    fields=context.fields,
                    dry_run=context.dry_run,
                )
            )
        except ExecutionTopologyError as exc:
            raise TaskDocError(str(exc)) from exc
    if context.operation == "remove_subtask":
        return _remove_subtask(
            context,
            context.edit.subtask,
        )
    return None


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


def _record_route_review(
    doc: TaskDocument,
    payload: dict[str, Any] | None,
    contract: WorktreeContract | None,
    task_root: Path,
    selected_path: Path,
) -> TaskDocument:
    if doc.kind == "master":
        raise TaskDocError("record_route_review is valid only for a leaf task document")
    if contract is None:
        raise TaskDocError("record_route_review requires the leaf worktree contract")
    if payload is None:
        raise TaskDocError("record_route_review requires a review object")
    try:
        resolved = resolve_terminal_leaf_doc(
            task_root,
            contract.leaf_id,
            asserted_path=selected_path,
        )
    except TerminalLeafResolutionError as exc:
        raise TaskDocError(str(exc)) from exc
    if resolved is None or resolved[0].resolve() != selected_path.resolve():
        raise TaskDocError(
            "record_route_review target is not the exact task document bound to the leaf contract"
        )
    try:
        review = build_route_review(contract, task_root, payload)
    except (RouteReviewError, ValidationError) as exc:
        raise TaskDocError(str(exc)) from exc
    data = doc.model_dump(by_alias=True)
    data["routeReview"] = review.model_dump(mode="json")
    return _validate(data)


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
        data["seriesContractPath"] = series_contract_path(task_root).as_posix()
        if contract.kind == "leaf":
            data["enclosures"] = [
                {
                    "leafId": contract.leaf_id,
                    "enclosurePath": contract.contract_path.as_posix(),
                }
            ]
    return _validate(data)


@dataclass(frozen=True)
class _Edit:
    """The one edit payload bundle an ``_apply`` operation may draw from."""

    kind: str
    fields: dict[str, Any]
    step: dict[str, Any] | None
    decision: dict[str, Any] | None
    subtask: dict[str, Any] | None
    section: dict[str, Any] | None
    lifecycle_id: str | None


def _apply_set_status(data: dict[str, Any], edit: _Edit) -> None:
    status = edit.fields.get("status")
    if status is None:
        raise TaskDocError("set_status requires fields.status")
    data["status"] = status


def _apply_set_field(data: dict[str, Any], edit: _Edit) -> None:
    updates = {key: value for key, value in edit.fields.items() if key in _MUTABLE_FIELDS}
    if not updates:
        raise TaskDocError(
            f"set_field requires at least one of: {', '.join(sorted(_MUTABLE_FIELDS))}"
        )
    data.update(updates)


def _apply_set_step(data: dict[str, Any], edit: _Edit) -> None:
    if edit.kind == "master":
        raise TaskDocError("set_step is not valid for a master; use set_subtask")
    if not edit.step:
        raise TaskDocError("set_step requires a step object")
    _upsert_step(data, edit.step)


def _apply_skip_step(data: dict[str, Any], edit: _Edit) -> None:
    if edit.kind == "master":
        raise TaskDocError("skip_step is not valid for a master; use set_subtask")
    if not edit.step:
        raise TaskDocError("skip_step requires step={id, reason, parent?}")
    reason = edit.step.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise TaskDocError("skip_step requires a nonblank step.reason")
    target, qualified_id = _exact_step_target(data, edit.step)
    if target.get("status") == "done":
        raise TaskDocError(
            f"skip_step requires an unresolved target; {qualified_id} is already done"
        )
    recorded_at = datetime.now(UTC).isoformat(timespec="seconds")
    target["status"] = "done"
    target["disposition"] = {
        "kind": "intentionalSkip",
        "reason": reason.strip(),
        "recordedAt": recorded_at,
        "recordedVia": "task_doc.skip_step",
        "lifecycleId": edit.lifecycle_id,
    }
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(
        {
            "at": recorded_at,
            "decision": f"Intentionally skip step {qualified_id}.",
            "rationale": reason.strip(),
        }
    )


def _apply_set_subtask(data: dict[str, Any], edit: _Edit) -> None:
    if edit.kind != "master":
        raise TaskDocError("set_subtask is only valid for a master document")
    if not edit.subtask:
        raise TaskDocError("set_subtask requires a subtask object")
    _upsert_subtask(data, edit.subtask)


def _apply_set_section(data: dict[str, Any], edit: _Edit) -> None:
    if not edit.section:
        raise TaskDocError("set_section requires a section object")
    # A leaf doc may carry freeform sections (R4); the schema validator backstops the
    # leaf "freeform only" rule, so there is no master-only gate here (unlike set_subtask).
    _upsert_section(data, edit.section)


def _apply_append_decision(data: dict[str, Any], edit: _Edit) -> None:
    if not edit.decision:
        raise TaskDocError("append_decision requires a decision object")
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(edit.decision)


# Operations that mutate an existing document in place. Anything absent here (create,
# replace, get, remove_subtask, reopen) is handled before `_apply` and re-validates unchanged.
_MUTATIONS: dict[str, Callable[[dict[str, Any], _Edit], None]] = {
    "set_status": _apply_set_status,
    "set_field": _apply_set_field,
    "set_step": _apply_set_step,
    "skip_step": _apply_skip_step,
    "set_subtask": _apply_set_subtask,
    "set_section": _apply_set_section,
    "append_decision": _apply_append_decision,
}


def _apply(
    operation: str,
    doc: TaskDocument,
    edit: TaskDocEdit,
    *,
    contract: WorktreeContract | None = None,
) -> TaskDocument:
    data = doc.model_dump(by_alias=True)
    mutate = _MUTATIONS.get(operation)
    if mutate is not None:
        lifecycle_id = None
        if operation == "skip_step":
            lifecycle_id = (
                contract.lifecycle_id if contract is not None else None
            ) or doc.lifecycleId
        mutate(
            data,
            _Edit(
                kind=doc.kind,
                fields=edit.fields or {},
                step=edit.step,
                decision=edit.decision,
                subtask=edit.subtask,
                section=edit.section,
                lifecycle_id=lifecycle_id,
            ),
        )
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
        updated = _upsert(substeps, step, keys=("title", "status", "note"))
    else:
        updated = _upsert(steps, step, keys=("title", "status"))
    if "status" in step:
        # An explicit status edit records executed/reworked state, not the old skip decision.
        updated.pop("disposition", None)


def _upsert(
    items: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    item_id = str(payload["id"])
    updates = {key: payload[key] for key in keys if key in payload}
    existing = _find(items, item_id)
    if existing is None:
        new_item: dict[str, Any] = {"id": item_id, "title": payload.get("title", item_id)}
        new_item.update(updates)
        items.append(new_item)
        return new_item
    else:
        existing.update(updates)
        return existing


def _exact_step_target(data: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    step_id = str(payload.get("id") or "").strip()
    if not step_id:
        raise TaskDocError("skip_step requires step.id")
    steps: list[dict[str, Any]] = data.setdefault("steps", [])
    parent_id = str(payload.get("parent") or "").strip()
    if not parent_id:
        matches = [step for step in steps if str(step.get("id")) == step_id]
        return _one_exact_match(matches, f"top-level step {step_id!r}"), step_id
    parents = [step for step in steps if str(step.get("id")) == parent_id]
    parent = _one_exact_match(parents, f"parent step {parent_id!r}")
    children = [sub for sub in parent.get("substeps", []) if str(sub.get("id")) == step_id]
    child = _one_exact_match(children, f"substep {parent_id!r}/{step_id!r}")
    return child, f"{parent_id}/{step_id}"


def _one_exact_match(matches: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not matches:
        raise TaskDocError(f"skip_step: {label} not found")
    if len(matches) > 1:
        raise TaskDocError(f"skip_step: {label} is ambiguous")
    return matches[0]


def _disposition_entries(doc: TaskDocument) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for step in doc.steps:
        if step.disposition is not None:
            entries.append(
                (step.id, step.disposition.model_dump_json(exclude_none=True, by_alias=True))
            )
        entries.extend(
            (
                f"{step.id}/{sub.id}",
                sub.disposition.model_dump_json(exclude_none=True, by_alias=True),
            )
            for sub in step.substeps
            if sub.disposition is not None
        )
    return sorted(entries)


def _enforce_disposition_authority(
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> None:
    candidate_entries = _disposition_entries(candidate)
    if operation == "create" and candidate_entries:
        raise TaskDocError(
            "create cannot author intentional-skip disposition; use task_doc.skip_step"
        )
    if (
        operation == "replace"
        and original is not None
        and candidate_entries != _disposition_entries(original)
    ):
        raise TaskDocError(
            "replace cannot add, remove, or change intentional-skip disposition; "
            "use task_doc.skip_step or set_step with an explicit status"
        )


def _enforce_route_review_authority(
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> None:
    candidate_review = candidate.routeReview.model_dump_json() if candidate.routeReview else None
    original_review = (
        original.routeReview.model_dump_json()
        if original is not None and original.routeReview is not None
        else None
    )
    if operation == "create" and candidate_review is not None:
        raise TaskDocError(
            "create cannot author route-review evidence; use task_doc.record_route_review"
        )
    if operation == "replace" and candidate_review != original_review:
        raise TaskDocError(
            "replace cannot add, remove, or change route-review evidence; "
            "use task_doc.record_route_review"
        )


def _step_unit_counts(doc: TaskDocument) -> Counter[tuple[str, str | None]]:
    counts: Counter[tuple[str, str | None]] = Counter()
    for step in doc.steps:
        counts[(step.id, None)] += 1
        counts.update((step.id, sub.id) for sub in step.substeps)
    return counts


def _unresolved_step_unit_counts(doc: TaskDocument) -> Counter[tuple[str, str | None]]:
    unresolved: Counter[tuple[str, str | None]] = Counter()
    for step in doc.steps:
        if step.status != "done":
            unresolved[(step.id, None)] += 1
        unresolved.update((step.id, sub.id) for sub in step.substeps if sub.status != "done")
    return unresolved


def _enforce_replace_preserves_unresolved_units(
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> None:
    if operation != "replace" or original is None or original.kind == "master":
        return
    missing = sorted(
        (_unresolved_step_unit_counts(original) - _step_unit_counts(candidate)).elements(),
        key=lambda identity: (identity[0], identity[1] or ""),
    )
    if missing:
        qualified = [parent if child is None else f"{parent}/{child}" for parent, child in missing]
        raise TaskDocError(
            "replace cannot remove or rename unresolved work units; resolve them with "
            f"set_step or task_doc.skip_step first: {qualified!r}"
        )


def _enforce_preserves_unresolved_master_rows(
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> None:
    if (
        operation not in {"replace", "set_subtask", "remove_subtask"}
        or original is None
        or original.kind != "master"
    ):
        return
    missing = missing_unresolved_master_rows(original, candidate)
    if missing:
        qualified = [f"{number}@{file or '<implicit>'}" for number, file in missing]
        raise TaskDocError(
            f"{operation} cannot remove, rename, or repoint unresolved master rows; "
            "mark the unchanged exact row Completed first: "
            f"{qualified!r}"
        )


def _enforce_terminal_status(candidate: TaskDocument) -> None:
    if candidate.status == "Completed":
        _raise_for_completion_blockers(candidate)


def _raise_for_completion_blockers(doc: TaskDocument) -> None:
    blockers = completion_blockers(doc)
    if not blockers:
        return
    exact = [blocker.model_dump() for blocker in blockers]
    raise TaskDocError(f"task completion refused; unresolved work units: {exact!r}")


def _enforce_completed_master_rows(
    task_root: Path,
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
    edit: TaskDocEdit,
) -> None:
    if candidate.kind != "master":
        return
    targeted_number = None
    if operation == "set_subtask" and edit.subtask:
        targeted_number = str(edit.subtask.get("number") or "")
    for ref in completed_master_rows_to_validate(
        candidate,
        original=original,
        targeted_number=targeted_number,
    ):
        _validate_completed_master_row(task_root, ref)


def _validate_completed_master_row(task_root: Path, ref: SubTaskRef) -> None:
    asserted = (task_root / Path(ref.file).with_suffix(".json")) if ref.file else None
    try:
        resolved = resolve_terminal_leaf_doc(
            task_root,
            ref.number,
            asserted_path=asserted,
        )
    except TerminalLeafResolutionError as exc:
        raise TaskDocError(f"cannot mark master row {ref.number!r} Completed: {exc}") from exc
    if resolved is None:
        raise TaskDocError(
            f"cannot mark master row {ref.number!r} Completed: no leaf task document exists"
        )
    _path, leaf = resolved
    try:
        _raise_for_completion_blockers(leaf)
    except TaskDocError as exc:
        raise TaskDocError(f"cannot mark master row {ref.number!r} Completed: {exc}") from exc


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
    context: _TaskDocSpecialContext,
    subtask: dict[str, Any] | None,
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
    doc = read_task_doc(_existing_json(context.task_root, context.target.slug))
    if doc.kind != "master":
        raise TaskDocError("remove_subtask is only valid for a master document")
    data = doc.model_dump(by_alias=True)
    refs: list[dict[str, Any]] = data.get("subTasks", [])
    match = next((ref for ref in refs if ref.get("number") == number), None)
    if match is None:
        raise TaskDocError(f"remove_subtask: subtask {number!r} not found")
    data["subTasks"] = [ref for ref in refs if ref.get("number") != number]
    updated = _validate(data)
    _enforce_preserves_unresolved_master_rows("remove_subtask", doc, updated)
    _enforce_terminal_status(updated)
    _enforce_completed_master_rows(
        context.task_root,
        "remove_subtask",
        doc,
        updated,
        TaskDocEdit(subtask=subtask),
    )
    leaf_files = _leaf_doc_files(context.task_root, match)
    if context.dry_run:
        preview_context = _TaskDocPublication(
            context.config,
            context.target,
            context.task_root,
            doc,
            updated,
            [updated],
        )
        with integration_authority_lock(
            context.config.coordination_root,
            context.target.repo_id,
        ):
            _validate_task_doc_publication_authority(preview_context)
        result = _preview("remove_subtask", updated, context.task_root)
        result["removedSubtask"] = number
        result["wouldDeleteFiles"] = (
            [] if keep_file else [path.as_posix() for path in leaf_files if path.exists()]
        )
        return result
    deleted: list[str] = []

    def publication() -> list[tuple[Path, Path]]:
        written = write_task_docs(context.task_root, [updated])
        if not keep_file:
            for path in leaf_files:
                if path.exists():
                    path.unlink()
                    deleted.append(path.as_posix())
        return written

    json_path, markdown_path = _publish_task_doc_set(
        _TaskDocPublication(
            context.config,
            context.target,
            context.task_root,
            doc,
            updated,
            [updated],
            publication,
        )
    )[0]
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
    confined_contract_path = require_within_coordination(config, contract_path, "contract_path")
    lifecycle_id = load_contract(confined_contract_path).lifecycle_id
    result = reopen_task(confined_contract_path, dry_run=dry_run)
    if not dry_run and result.returncode == 0 and result.payload.get("state") == "reopened":
        # Reopen retires the completed task's attribution. Ending that exact ambient
        # anchor makes the next worktree_start mint a fresh lifecycle instead of
        # silently promoting and restamping the completed lifecycle id.
        end_ambient_lifecycle_if_anchored(lifecycle_id, outcome="completed")
    return {**result.payload, "ok": result.returncode == 0, "operation": "task_reopen"}
