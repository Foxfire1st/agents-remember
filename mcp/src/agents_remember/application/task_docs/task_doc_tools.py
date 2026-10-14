"""Author the JSON-primary task document and re-render its markdown.

One operation-dispatched entry point behind the ``task_doc`` MCP tool. It loads
(or creates) the ``ar-task-document/v1`` JSON for a task, applies a single edit,
and rewrites both the JSON (the source of truth) and the rendered markdown. The
markdown is never parsed back, and every result is re-validated against the
schema before it is written.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agents_remember.application.direct_execution_grants import refuse_open_direct_sibling
from agents_remember.kernel.direct_execution_grants import DirectGrantError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    TaskDocSourceSnapshot,
    TaskDocument,
    capture_task_doc_source,
    completion_blockers,
    json_path_for,
    markdown_path_for,
    missing_task_doc_source,
    read_task_doc,
    read_task_doc_with_source,
    step_done,
    step_total,
    write_task_docs,
    write_task_docs_and_remove,
)
from agents_remember.tasks.master_sync import MasterSyncError, plan_master_sync
from agents_remember.tasks.readiness import (
    missing_unresolved_master_rows,
)
from agents_remember.worktrees.candidate_identity import resolve_candidate_identity
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_evidence import (
    require_register_sections_valid,
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

from . import task_doc_steps, task_sprint_linkage
from .task_doc_discard import DiscardUnstartedRequest, discard_unstarted_subtask
from .task_doc_phrases import _MUTABLE_FIELDS, _updated_phrase
from .task_doc_publication import (
    TaskDocPublication,
    preview_task_doc_projection_effects,
    publish_task_doc_set,
    task_doc_publication_transaction,
    validate_task_doc_transaction,
)
from .task_doc_removed_operations import removed_read_operation_refusal
from .task_doc_response import (
    graph_titles_for,
    task_doc_preview,
    task_doc_result,
)
from .task_doc_route_review import (
    DEFAULT_TASK_DOC_CALL,
    TaskDocCall,
    TaskDocError,
    _begin_task_review_bound,
    _enforce_branch_addressed_policy,
    _enforce_route_review_authority,
    _record_route_review,  # noqa: F401  # facade re-export (moved to task_doc_route_review.py)
    _record_route_review_bound,
    _record_task_review_bound,
    _RouteReviewBinding,
    _validate,
    candidate_root_for_review,
    repo_root_opt,
    review_settings_limit,
)
from .task_doc_section_scaffolding import scaffold_register_sections
from .task_execution_topology import (
    ExecutionTopologyAuthoringRequest,
    ExecutionTopologyEditRequest,
    ExecutionTopologyError,
    author_execution_graph,
    enforce_execution_topology_edit,
)
from .task_master_completion import require_master_completion
from .task_reopen import (
    task_reopen_tool,  # noqa: F401  # facade re-export (moved to task_reopen.py)
)
from .task_retirement_authority import (
    require_retirement_proofs_unchanged,
    require_row_not_retired,
    retirement_only_refusal,
)

VALID_OPERATIONS = (
    "create",
    "replace",
    "set_status",
    # The step plane is split by intent, mirroring skip_step's exact addressing:
    # set_step updates one existing unit, add_step creates one, remove_step deletes one.
    "set_step",
    "add_step",
    "remove_step",
    "skip_step",
    "set_subtask",
    "remove_subtask",
    "set_section",
    "append_decision",
    "begin_review",
    "record_review",
    "record_route_review",
    "author_execution_graph",
    "set_field",
    # L14: sprint↔master linkage (application/task_docs/task_sprint_linkage.py).
    *task_sprint_linkage.SPRINT_LINKAGE_OPERATIONS,
)

# set_field may only touch the allowlist the phrases module owns (scalars +
# flat string lists); structural edits go through create / replace / the step
# plane / append_decision. Imported here so the applier and the phrase share
# one definition: an ignored field is never claimed changed.


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
    # MIK-R111 rule 2: a text goes in inline or by file. Where a write takes
    # a text, the caller may name a file in its place instead: the whole of
    # ``fields`` (create, replace, set_field) or a section body (set_section)
    # as a file at an absolute path, read once.
    fields_file: str | None = None
    section_body_file: str | None = None


NO_EDIT = TaskDocEdit()
"""The empty edit, for operations that take no edit payload of their own."""


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
class _PreparedTaskDocEdit:
    original: TaskDocument | None
    candidate: TaskDocument
    selected_snapshot: TaskDocSourceSnapshot


@dataclass(frozen=True)
class _TaskDocPrepareRequest:
    config: McpRuntimeConfig
    operation: str
    payload_fields: dict[str, Any]
    edit: TaskDocEdit
    contract: WorktreeContract | None
    task_root: Path
    slug: str | None
    branch_addressed: bool
    repo_id: str = ""


@dataclass(frozen=True)
class _TaskDocCandidateContext:
    config: McpRuntimeConfig
    target: TaskDocTarget
    operation: str
    edit: TaskDocEdit
    task_root: Path
    original: TaskDocument | None
    candidate: TaskDocument
    payload_fields: dict[str, Any]
    selected_snapshot: TaskDocSourceSnapshot


def _branch_addressed_leaf(target: TaskDocTarget) -> TaskDocumentRef | None:
    """The selected leaf of a branch-addressed call, from the target alone.

    The leaf's own file is ``<slug>.json`` in the addressed task root; the
    grant gate resolves and kind-checks it. ``None`` when the target names no
    document, which the gate refuses.
    """
    if target.task_name is None or target.slug is None:
        return None
    return TaskDocumentRef(repository=target.repo_id, path=f"{target.task_name}/{target.slug}.json")


def _refuse_open_direct_sibling_for_task_doc(
    config: McpRuntimeConfig, leaf: TaskDocumentRef | None
) -> None:
    """Refuse a branch-addressed review while another direct leaf is in work."""
    if leaf is None:
        return
    try:
        refuse_open_direct_sibling(config, leaf, "branch_addressed record_route_review")
    except DirectGrantError as error:
        raise TaskDocError(
            f"branch_addressed record_route_review for {leaf.repository}:{leaf.path} "
            f"is refused: {error.detail}"
        ) from error


# By-file text inputs (fields/section bodies) are KB-scale; this budget stops
# a pathological file from turning a write into unbounded work, refusing
# loudly with the bound instead of reading on.
_TASK_DOC_INPUT_MAX_BYTES = 1024 * 1024


def _read_text_file(path: str) -> tuple[str, str]:
    """Read one by-file text input once, or refuse the call in one sentence."""

    candidate = Path(path)
    if not candidate.is_absolute():
        raise TaskDocError(f"task_doc file input must name an absolute path, not {path!r}.")
    if not candidate.is_file():
        raise TaskDocError(f"task_doc file input is no regular file: {path}.")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise TaskDocError(f"task_doc file input cannot be read: {path} ({exc}).") from exc
    if len(raw) > _TASK_DOC_INPUT_MAX_BYTES:
        raise TaskDocError(
            f"task_doc file input too large: {path} holds {len(raw)} bytes, over the "
            f"{_TASK_DOC_INPUT_MAX_BYTES} byte budget; pass the text inline instead."
        )
    digest = hashlib.sha256(raw).hexdigest()
    try:
        return raw.decode("utf-8"), digest
    except UnicodeDecodeError as exc:
        raise TaskDocError(f"task_doc file input is not UTF-8 text: {path}.") from exc


def _resolve_file_texts(
    operation: str, edit: TaskDocEdit
) -> tuple[TaskDocEdit, list[tuple[str, str]]]:
    """Apply by-file text inputs to one edit, or refuse the malformed call.

    Returns the edit with the file's text in place of the inline one, plus
    the (path, SHA-256) rows the answer names. A text goes in inline or by
    file, never both; a file serves only the operations that take that text.
    """

    rows: list[tuple[str, str]] = []
    if edit.fields_file is not None:
        if operation not in ("create", "replace", "set_field"):
            raise TaskDocError(
                f"task_doc {operation} takes no fields file: fields_file serves "
                "create, replace and set_field."
            )
        if edit.fields is not None:
            raise TaskDocError(
                "task_doc takes fields inline or by file, not both: pass only one of "
                "fields and fields_file."
            )
        text, digest = _read_text_file(edit.fields_file)
        try:
            fields = json.loads(text)
        except ValueError as exc:
            raise TaskDocError(
                f"task_doc fields file is not a JSON object: {edit.fields_file}."
            ) from exc
        if not isinstance(fields, dict):
            raise TaskDocError(f"task_doc fields file is not a JSON object: {edit.fields_file}.")
        rows.append((edit.fields_file, digest))
        edit = replace(edit, fields=fields, fields_file=None)
    if edit.section_body_file is not None:
        if operation != "set_section":
            raise TaskDocError(
                f"task_doc {operation} takes no section body file: section_body_file "
                "serves set_section."
            )
        section = edit.section or {}
        if section.get("body") is not None:
            raise TaskDocError(
                "task_doc takes a section body inline or by file, not both: pass only "
                "one of section.body and section_body_file."
            )
        text, digest = _read_text_file(edit.section_body_file)
        rows.append((edit.section_body_file, digest))
        edit = replace(edit, section={**section, "body": text}, section_body_file=None)
    return edit, rows


def _with_file_rows(result: dict[str, Any], file_rows: list[tuple[str, str]]) -> dict[str, Any]:
    """Name the by-file text input the write took in the answer."""

    if file_rows:
        result["inputFile"], result["inputFileSha256"] = file_rows[0]
    return result


def task_doc_tool(
    config: McpRuntimeConfig,
    target: TaskDocTarget,
    *,
    operation: str,
    edit: TaskDocEdit = NO_EDIT,
    call: TaskDocCall = DEFAULT_TASK_DOC_CALL,
) -> dict[str, Any]:
    if operation in ("get", "read_steps"):  # removed reads refuse with the file names
        task_root, _contract = _resolve(
            config, target.repo_id, target.task_name, target.contract_path
        )
        return removed_read_operation_refusal(task_root, target.slug, operation)
    if operation not in VALID_OPERATIONS:
        raise TaskDocError(
            f"unknown operation {operation!r}; expected one of {', '.join(VALID_OPERATIONS)}"
        )
    edit, file_rows = _resolve_file_texts(operation, edit)
    _enforce_branch_addressed_policy(
        config, operation, call.branch_addressed, _branch_addressed_leaf(target)
    )
    if call.branch_addressed and operation == "record_route_review":
        _refuse_open_direct_sibling_for_task_doc(config, _branch_addressed_leaf(target))
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
            dry_run=call.dry_run,
        )
    )
    if special is not None:
        if special.get("ok") is True and "updated" not in special:
            special["updated"] = _updated_phrase(operation, edit, special)
        return _with_file_rows(special, file_rows)

    prepared = _prepare_task_doc_edit(
        _TaskDocPrepareRequest(
            config,
            operation,
            payload_fields,
            edit,
            contract,
            task_root,
            slug,
            call.branch_addressed,
            target.repo_id,
        )
    )
    original = prepared.original
    doc = prepared.candidate
    selected_snapshot = prepared.selected_snapshot

    candidate_context = _TaskDocCandidateContext(
        config,
        target,
        operation,
        edit,
        task_root,
        original,
        doc,
        payload_fields,
        selected_snapshot,
    )
    _validate_task_doc_candidate(candidate_context)
    return _with_file_rows(
        _publish_task_doc_candidate(candidate_context, dry_run=call.dry_run), file_rows
    )


def _validate_task_doc_candidate(context: _TaskDocCandidateContext) -> None:
    config = context.config
    target = context.target
    operation = context.operation
    task_root = context.task_root
    original = context.original
    doc = context.candidate
    require_retirement_proofs_unchanged(original, doc, f"task_doc {operation}")
    _enforce_disposition_authority(operation, original, doc)
    _enforce_route_review_authority(operation, original, doc)
    _enforce_replace_preserves_unresolved_units(operation, original, doc)
    _enforce_preserves_unresolved_master_rows(operation, original, doc)
    _enforce_terminal_status(operation, doc, context.edit)
    require_master_completion(config.coordination_root, target.repo_id, task_root, original, doc)
    _enforce_register_section_shapes(doc)
    try:
        enforce_execution_topology_edit(
            ExecutionTopologyEditRequest(
                coordination_root=config.coordination_root,
                repo_id=target.repo_id,
                task_root=task_root,
                operation=operation,
                original=original,
                candidate=doc,
                fields=context.payload_fields,
            )
        )
    except ExecutionTopologyError as exc:
        raise TaskDocError(str(exc)) from exc


def _publish_task_doc_candidate(
    context: _TaskDocCandidateContext,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    config = context.config
    target = context.target
    operation = context.operation
    task_root = context.task_root
    original = context.original
    doc = context.candidate
    try:
        master_sync = plan_master_sync(task_root, doc)
    except MasterSyncError as exc:
        # MIK-R111 rule 2: the sync failure rides the answer, and the leaf
        # stays exactly what it was -- the edit is refused, not raised.
        failure = str(exc)
        result = task_doc_result(
            operation, doc, json_path_for(task_root, doc), markdown_path_for(task_root, doc)
        )
        result["ok"] = False
        result["code"] = "master-sync-failed"
        result["error"] = failure
        result["detail"] = failure
        result["masterSync"] = {"status": "failed", "error": failure}
        result["nextAction"] = "Repair the parent master document, then retry the write."
        return result
    docs: list[TaskDocument] = [doc]
    source_snapshots = [context.selected_snapshot]
    if master_sync.source_snapshot is not None:
        source_snapshots.append(master_sync.source_snapshot)
    if master_sync.changed and master_sync.master is not None:
        if master_sync.source_snapshot is None:
            raise TaskDocError("changed master synchronization has no accepted source bytes")
        docs.append(master_sync.master)
    if dry_run:
        preview_context = TaskDocPublication(
            config,
            target.repo_id,
            task_root,
            original,
            doc,
            docs,
            tuple(source_snapshots),
        )
        validate_task_doc_transaction(task_doc_publication_transaction(preview_context))
        result = task_doc_preview(operation, doc, task_root, master_sync=master_sync)
        result["updated"] = _updated_phrase(operation, context.edit, result)
        result["projectionEffects"] = [
            effect.model_dump(mode="json")
            for effect in preview_task_doc_projection_effects(preview_context)
        ]
        return result
    publication_context = TaskDocPublication(
        config,
        target.repo_id,
        task_root,
        original,
        doc,
        docs,
        tuple(source_snapshots),
    )
    publication = publish_task_doc_set(publication_context)
    json_path, markdown_path = publication.written[0]
    result = task_doc_result(operation, doc, json_path, markdown_path, master_sync=master_sync)
    result["updated"] = _updated_phrase(operation, context.edit, result)
    result["projectionEffects"] = [
        effect.model_dump(mode="json") for effect in publication.projection_effects
    ]
    return result


def _prepare_task_doc_edit(
    request: _TaskDocPrepareRequest,
) -> _PreparedTaskDocEdit:
    if request.operation == "create":
        doc = _create(request.payload_fields, request.contract, request.task_root)
        return _PreparedTaskDocEdit(
            None,
            doc,
            missing_task_doc_source(json_path_for(request.task_root, doc)),
        )
    json_path = _existing_json(request.task_root, request.slug)
    original, selected_snapshot = read_task_doc_with_source(json_path)
    if request.operation == "replace":
        doc = _replace(request.payload_fields, request.contract, request.task_root, json_path)
    elif request.operation == "begin_review":
        doc = _begin_task_review_bound(
            original,
            request.edit.review,
            limit=review_settings_limit(request.config.coordination_root),
        )
    elif request.operation == "record_review":
        doc = _record_task_review_bound(
            original,
            request.edit.review,
            settings_limit=review_settings_limit(request.config.coordination_root),
            actual_candidate=resolve_candidate_identity(
                candidate_root_for_review(
                    request.contract, repo_root_opt(request.config, request.repo_id)
                )
            ),
        )
    elif request.operation == "record_route_review":
        binding = _RouteReviewBinding(
            contract=request.contract,
            task_root=request.task_root,
            selected_path=json_path,
            branch_addressed=request.branch_addressed,
        )
        doc = _record_route_review_bound(
            original,
            request.edit.review,
            binding,
            settings_limit=review_settings_limit(request.config.coordination_root),
            actual_candidate=resolve_candidate_identity(
                candidate_root_for_review(
                    binding.contract, repo_root_opt(request.config, request.repo_id)
                )
            ),
        )
    else:
        doc = _apply(
            request.operation,
            original,
            request.edit,
            contract=request.contract,
        )
    return _PreparedTaskDocEdit(original, doc, selected_snapshot)


def _sprint_doc_identity(
    context: _TaskDocSpecialContext, result: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The sprint document's identity surface, merged into special-op results.

    The sprint-linkage and execution-graph authoring ops publish inside their own
    functions and return raw operation payloads; the envelope still requires the
    standard task_doc identity (taskId/slug/kind/status/docPath/renderedPath/
    lifecycleId/stepsDone/stepsTotal), so the caller merges it in afterwards -- the
    op succeeded, so the sprint doc exists at task_root/slug.
    """

    task_root = context.task_root
    archive = ((result or {}).get("taskArchive") or {}).get("archivePath")
    if archive and not (task_root / f"{context.target.slug or 'task'}.json").exists():
        # A master retired on its own has moved under 0_archive/ by the time it answers.
        task_root = Path(archive)
    json_path = _existing_json(task_root, context.target.slug)
    doc = read_task_doc(json_path)
    return {
        "taskId": doc.id,
        "slug": doc.slug,
        "kind": doc.kind,
        "status": doc.status,
        "lifecycleId": doc.lifecycleId,
        "docPath": json_path.as_posix(),
        "renderedPath": markdown_path_for(task_root, doc).as_posix(),
        "stepsDone": step_done(doc),
        "stepsTotal": step_total(doc),
    }


def _special_task_doc_operation(context: _TaskDocSpecialContext) -> dict[str, Any] | None:
    if context.operation in task_sprint_linkage.SPRINT_LINKAGE_OPERATIONS:
        try:
            result = task_sprint_linkage.sprint_linkage_operation(
                context.operation,
                task_sprint_linkage.SprintLinkageCall(
                    config=context.config,
                    repo_id=context.target.repo_id,
                    task_root=context.task_root,
                    slug=context.target.slug,
                    fields=context.fields,
                    dry_run=context.dry_run,
                ),
            )
        except task_sprint_linkage.SprintLinkageError as exc:
            raise TaskDocError(str(exc)) from exc
        return {**_sprint_doc_identity(context, result), **result}
    if context.operation == "author_execution_graph":
        repository = context.config.repositories[context.target.repo_id]
        try:
            result = author_execution_graph(
                ExecutionTopologyAuthoringRequest(
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
        return {**_sprint_doc_identity(context), **result}
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
            "single-file change. kind must be one of ['subTask', 'master'], or omitted "
            "to take the contract-derived default."
        )
    if "kind" not in data:
        # Master/leaf only — there is no "light" default. A create against a leaf
        # contract is a subTask; anything else (no contract, or a standalone
        # top-level task) is a master.
        data["kind"] = "subTask" if (contract is not None and contract.kind == "leaf") else "master"
    scaffold_register_sections(data)
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
    elif data.get("kind") != "master":
        _require_bindable_leaf_authoring(task_root)
    return _validate(data)


def _require_bindable_leaf_authoring(task_root: Path) -> None:
    """Refuse a leaf document whose derived master link nothing would ever bind.

    With no contract resolved there is no ``seriesContractPath`` and no
    ``enclosures[]`` to stamp, and this document is not the master that will own
    the series. Two cases reach here and only one of them is repairable:

    * The task root already holds a master document. Authoring a master and its
      leaves before any start is the normal planning flow, and it is allowed
      precisely because the leaf's first ``worktree_start``/``worktree_attach``
      runs the start binding publisher
      (``plan_leaf_doc_lifecycle_restamp`` / ``plan_leaf_doc_enclosure_registration``),
      which writes both derived fields once the series contract exists. This
      allowance is a guarantee, so it is asserted rather than implied.
    * The task root holds no master document at all. Nothing owns the series, no
      operation binds the fields, and the document would silently persist without
      its master link — so it is refused here, with the remedy, instead.
    """

    master_path = task_root / "task.json"
    if master_path.exists():
        return
    raise TaskDocError(
        "refused: this leaf task document would carry no master link "
        f"(seriesContractPath and enclosures) and nothing would ever bind it — task root "
        f"{task_root} has no series contract and no master document ({master_path}). "
        "Author the master document first (task_doc.create with kind 'master' in this same "
        "task root, which owns the series), then author its leaf documents; each leaf's "
        "first worktree_start then binds the link. Alternatively pass the leaf's "
        "contract_path once its enclosure exists, or the master's series contract path."
    )


def _enforce_register_section_shapes(doc: TaskDocument) -> None:
    """Write-time register validation: malformed canonical registers never persist."""
    try:
        require_register_sections_valid(doc)
    except CloseoutQueueError as exc:
        raise TaskDocError(str(exc)) from exc


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
    task_doc_steps.set_step(data, kind=edit.kind, step=edit.step)


def _apply_add_step(data: dict[str, Any], edit: _Edit) -> None:
    task_doc_steps.add_step(data, kind=edit.kind, step=edit.step)


def _apply_remove_step(data: dict[str, Any], edit: _Edit) -> None:
    task_doc_steps.remove_step(data, kind=edit.kind, step=edit.step)


def _apply_skip_step(data: dict[str, Any], edit: _Edit) -> None:
    task_doc_steps.skip_step(
        data,
        kind=edit.kind,
        step=edit.step,
        lifecycle_id=edit.lifecycle_id,
    )


def _apply_set_subtask(data: dict[str, Any], edit: _Edit) -> None:
    if edit.kind != "master":
        raise TaskDocError("set_subtask is only valid for a master document")
    if not edit.subtask:
        raise TaskDocError("set_subtask requires a subtask object")
    number = str(edit.subtask.get("number") or "")
    if any(
        ref.get("number") == number and ref.get("retirement") for ref in data.get("subTasks", [])
    ):
        # Refused here, by the row's number, before the schema would reject the edited row.
        raise retirement_only_refusal(str(data.get("id")), [number], "task_doc set_subtask")
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
    "add_step": _apply_add_step,
    "remove_step": _apply_remove_step,
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


def _enforce_terminal_status(
    operation: str,
    candidate: TaskDocument,
    edit: TaskDocEdit = NO_EDIT,
) -> None:
    """A Completed document admits no new unresolved work -- except a reasoned removal.

    ``remove_step`` is the deliberate exception (developer ruling). Its motivating case
    repaired spurious units on an already-``Completed`` leaf document, and a status gate
    would have forced a full-document ``replace`` that was correctly refused as too
    destructive. The removal's nonblank reason and its appended decision are the audited
    substitute for that gate; ``_apply_remove_step`` refuses a blank reason first.
    """
    if candidate.status != "Completed":
        return
    if operation == "remove_step" and task_doc_steps.step_reason(edit.step) is not None:
        return
    _raise_for_completion_blockers(candidate)


def _raise_for_completion_blockers(doc: TaskDocument) -> None:
    blockers = completion_blockers(doc)
    if not blockers:
        return
    exact = [blocker.model_dump() for blocker in blockers]
    raise TaskDocError(f"task completion refused; unresolved work units: {exact!r}")


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
    """Remove a terminal row, or audit and discard one canonically proven-unstarted leaf.

    ``remove_subtask`` completes task-doc CRUD: it drops the ``SubTaskRef`` by ``number`` from the
    master ``subTasks`` index and deletes the referenced leaf document (``<slug>.json`` + ``.md``)
    -- "remove means remove" -- unless ``subtask.keep_file`` is set, in which case only the index
    row is removed and the leaf doc is left on disk. ``disposition=discard-unstarted`` is the
    distinct planning transition: it requires a nonblank reason, forbids ``keep_file``, proves
    absence of execution authority under the task CAS, and leaves a typed parent audit.
    """
    if not subtask or not subtask.get("number"):
        raise TaskDocError("remove_subtask requires subtask.number")
    disposition = subtask.get("disposition")
    if disposition is not None or subtask.get("reason") is not None:
        return _remove_subtask_with_disposition(context, subtask)
    number = str(subtask["number"])
    keep_file = bool(subtask.get("keep_file"))
    doc, selected_snapshot = read_task_doc_with_source(
        _existing_json(context.task_root, context.target.slug)
    )
    require_row_not_retired(doc, number, "task_doc remove_subtask")
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
    _enforce_terminal_status("remove_subtask", updated)
    leaf_files = _leaf_doc_files(context.task_root, match)
    source_snapshots = [selected_snapshot]
    if leaf_files:
        source_snapshots.append(capture_task_doc_source(leaf_files[0]))
    if context.dry_run:
        preview_context = TaskDocPublication(
            context.config,
            context.target.repo_id,
            context.task_root,
            doc,
            updated,
            [updated],
            tuple(source_snapshots),
        )
        validate_task_doc_transaction(task_doc_publication_transaction(preview_context))
        result = task_doc_preview("remove_subtask", updated, context.task_root)
        result["removedSubtask"] = number
        result["wouldDeleteFiles"] = (
            [] if keep_file else [path.as_posix() for path in leaf_files if path.exists()]
        )
        return result
    deleted: list[str] = []

    def publication() -> list[tuple[Path, Path]]:
        if keep_file:
            return write_task_docs(
                context.task_root,
                [updated],
                graph_titles=graph_titles_for(context.task_root, updated),
            )
        written, removed = write_task_docs_and_remove(
            context.task_root,
            [updated],
            leaf_files,
            graph_titles=graph_titles_for(context.task_root, updated),
        )
        deleted.extend(path.as_posix() for path in removed)
        return written

    published = publish_task_doc_set(
        TaskDocPublication(
            context.config,
            context.target.repo_id,
            context.task_root,
            doc,
            updated,
            [updated],
            tuple(source_snapshots),
            publisher=publication,
        )
    )
    json_path, markdown_path = published.written[0]
    result = task_doc_result("remove_subtask", updated, json_path, markdown_path)
    result["removedSubtask"] = number
    result["deletedFiles"] = deleted
    result["projectionEffects"] = [
        effect.model_dump(mode="json") for effect in published.projection_effects
    ]
    return result


def _remove_subtask_with_disposition(
    context: _TaskDocSpecialContext,
    subtask: dict[str, Any],
) -> dict[str, Any]:
    if subtask.get("disposition") != "discard-unstarted":
        if subtask.get("disposition") is None:
            raise TaskDocError(
                "remove_subtask.reason is valid only with disposition='discard-unstarted'"
            )
        raise TaskDocError("remove_subtask disposition must be 'discard-unstarted' when supplied")
    require_row_not_retired(
        read_task_doc(_existing_json(context.task_root, context.target.slug)),
        str(subtask["number"]),
        "task_doc remove_subtask (discard-unstarted)",
    )
    return discard_unstarted_subtask(
        DiscardUnstartedRequest(
            config=context.config,
            repo_id=context.target.repo_id,
            task_name=context.target.task_name,
            task_root=context.task_root,
            parent_path=_existing_json(context.task_root, context.target.slug),
            subtask=subtask,
            dry_run=context.dry_run,
        )
    )


def _leaf_doc_files(task_root: Path, ref: dict[str, Any]) -> list[Path]:
    """The leaf sub-task's JSON + markdown files implied by a master ``SubTaskRef.file``."""
    file_name = ref.get("file") or ""
    if not file_name:
        return []
    markdown = task_root / file_name
    return [markdown.with_suffix(".json"), markdown]
