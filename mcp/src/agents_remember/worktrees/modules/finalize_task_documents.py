"""Exact task-document completion targets and their atomic publication at finalization."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocSourceReadError,
    TaskDocSourceSnapshot,
    TaskDocument,
    current_task_doc_source,
    read_graph_titles,
    read_task_doc_with_source,
    write_task_doc_batch,
    write_task_docs,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.leaf_doc import (
    require_task_document_in_place,
    resolve_terminal_leaf_doc,
)
from agents_remember.tasks.master_sync import (
    demote_completed_master_if_unresolved,
    folder_master_json_path,
)
from agents_remember.tasks.retired_rows import refuse_retired_row
from agents_remember.tasks.sprint_rows import (
    master_rows,
    membership_removal_action,
    sprint_census,
)
from agents_remember.tasks.store import read_task_doc
from agents_remember.worktrees.abandoned_row_landing import require_abandoned_rows_unlanded
from agents_remember.worktrees.queue.closeout_projection_publication import (
    preview_closeout_projection_effect,
)
from agents_remember.worktrees.task_fact_publication import (
    preview_contract_task_facts,
    publish_contract_task_facts,
    publish_task_fact_mutation,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class FinalizeArgs:
    contract_path: Path
    task_doc_path: Path | None = None
    master_doc_path: Path | None = None
    subtask_number: str = ""
    dry_run: bool = False
    teardown_providers: bool = True


class FinalizeTaskDocumentError(ValueError):
    """The finalizer could not prove its exact task-document completion targets."""


@dataclass(frozen=True)
class FinalizeTaskTargets:
    leaf_path: Path | None = None
    leaf: TaskDocument | None = None
    completed_leaf: TaskDocument | None = None
    leaf_source: TaskDocSourceSnapshot | None = None
    parent_path: Path | None = None
    parent: TaskDocument | None = None
    parent_row: SubTaskRef | None = None
    completed_parent: TaskDocument | None = None
    parent_source: TaskDocSourceSnapshot | None = None
    # A commanding sprint that holds no single row for the master: its path and the linkage fact.
    parent_skip: tuple[Path, dict[str, Any]] | None = None
    # Other members of the commanding sprint whose documents could not be read.
    parent_facts: tuple[dict[str, Any], ...] = ()


def _resolve_task_targets(
    contract: WorktreeContract,
    args: FinalizeArgs,
) -> FinalizeTaskTargets:
    if contract.kind != "leaf":
        if args.task_doc_path is not None:
            raise FinalizeTaskDocumentError(
                "a series contract cannot assert a leaf task-document completion target"
            )
        return resolve_series_targets(contract, args)
    resolved = resolve_terminal_leaf_doc(
        contract.task_root,
        contract.leaf_id,
        asserted_path=args.task_doc_path,
    )
    if resolved is None:
        return _resolve_parent_target(contract, args, None, None)
    leaf_path, resolved_leaf = resolved
    try:
        leaf, leaf_source = read_task_doc_with_source(leaf_path)
    except (OSError, ValueError) as exc:
        raise FinalizeTaskDocumentError(
            f"cannot capture exact leaf task-document source {leaf_path}: {exc}"
        ) from exc
    if leaf != resolved_leaf:
        raise FinalizeTaskDocumentError(
            "contract-bound leaf task document changed during finalization preflight; retry"
        )
    require_task_document_in_place(leaf_path, leaf, FinalizeTaskDocumentError)
    return _resolve_parent_target(contract, args, leaf_path, leaf, leaf_source)


def _resolve_parent_target(
    contract: WorktreeContract,
    args: FinalizeArgs,
    leaf_path: Path | None,
    leaf: TaskDocument | None,
    leaf_source: TaskDocSourceSnapshot | None = None,
) -> FinalizeTaskTargets:
    if leaf is None or leaf_path is None:
        if args.master_doc_path is None and not args.subtask_number:
            return FinalizeTaskTargets()
        raise FinalizeTaskDocumentError(
            "cannot complete an immediate parent row without a contract-bound leaf document"
        )
    if leaf.master:
        target = _named_parent(contract.task_root, args, leaf.master, leaf.id)
    else:
        target = _folder_parent(contract.task_root, args, leaf)
    if target is None:
        if _parent_asserted(args):
            raise FinalizeTaskDocumentError(
                "standalone leaf has no immediate parent reference to assert"
            )
        return FinalizeTaskTargets(
            leaf_path=leaf_path,
            leaf=leaf,
            completed_leaf=_leaf_completion_candidate(leaf),
            leaf_source=leaf_source,
        )
    _check_parent_row_path(target.path, target.row, leaf_path)
    completed_parent = _parent_completion_candidate(target.document, target.row.number)
    return FinalizeTaskTargets(
        leaf_path=leaf_path,
        leaf=leaf,
        completed_leaf=_leaf_completion_candidate(leaf),
        leaf_source=leaf_source,
        parent_path=target.path,
        parent=target.document,
        parent_row=target.row,
        completed_parent=completed_parent,
        parent_source=target.source,
    )


@dataclass(frozen=True)
class _ParentTarget:
    """The leaf's immediate parent master, its captured source, and the one row naming the leaf."""

    path: Path
    document: TaskDocument
    source: TaskDocSourceSnapshot
    row: SubTaskRef


def _named_parent(
    task_root: Path, args: FinalizeArgs, master_ref: str, leaf_id: str
) -> _ParentTarget:
    expected_parent = _expected_parent_path(task_root, master_ref)
    _assert_parent_arguments(args, expected_parent, leaf_id)
    parent, parent_source = _read_parent(expected_parent)
    return _ParentTarget(expected_parent, parent, parent_source, _exact_parent_row(parent, leaf_id))


def _folder_parent(task_root: Path, args: FinalizeArgs, leaf: TaskDocument) -> _ParentTarget | None:
    """The master that lists a leaf naming none, resolved by the master sync's rule (MIK-R38).

    A leaf without a ``master`` reference belongs to its folder's ``task.json`` exactly as the
    task-document master sync decides it, so the row that sync keeps current is the row finalize
    completes. That master is held to every check a named master meets: it must be a readable
    master with exactly one row for the leaf, pointing at this leaf's file. Only a missing folder
    master, or one listing no row for the leaf, leaves the leaf standalone as before.
    """
    folder_master = folder_master_json_path(task_root, leaf)
    if folder_master is None:
        return None
    expected_parent = folder_master.resolve(strict=False)
    parent, parent_source = _read_parent(expected_parent)
    if not any(row.number == leaf.id for row in parent.subTasks):
        if _parent_asserted(args):
            raise FinalizeTaskDocumentError(
                f"folder master {expected_parent} lists no row {leaf.id!r}; the leaf finalizes "
                "standalone, so there is no immediate parent to assert"
            )
        return None
    _assert_parent_arguments(args, expected_parent, leaf.id)
    return _ParentTarget(expected_parent, parent, parent_source, _exact_parent_row(parent, leaf.id))


def _parent_asserted(args: FinalizeArgs) -> bool:
    return args.master_doc_path is not None or bool(args.subtask_number)


def _assert_parent_arguments(
    args: FinalizeArgs,
    expected_parent: Path,
    leaf_id: str,
) -> None:
    if (
        args.master_doc_path is not None
        and args.master_doc_path.resolve(strict=False) != expected_parent
    ):
        raise FinalizeTaskDocumentError(
            f"master_doc_path {args.master_doc_path.resolve(strict=False)} is not the leaf's "
            f"immediate parent {expected_parent}"
        )
    if args.subtask_number and args.subtask_number != leaf_id:
        raise FinalizeTaskDocumentError(
            f"subtask_number {args.subtask_number!r} does not identify leaf {leaf_id!r}"
        )


def _read_parent(parent_path: Path) -> tuple[TaskDocument, TaskDocSourceSnapshot]:
    try:
        parent, source = read_task_doc_with_source(parent_path)
    except (OSError, ValueError) as exc:
        raise FinalizeTaskDocumentError(
            f"cannot read immediate parent task document {parent_path}: {exc}"
        ) from exc
    if parent.kind != "master":
        raise FinalizeTaskDocumentError(
            f"immediate parent path is not a master task document: {parent_path}"
        )
    require_task_document_in_place(parent_path, parent, FinalizeTaskDocumentError)
    return parent, source


def _exact_parent_row(parent: TaskDocument, subtask_number: str) -> SubTaskRef:
    rows = [row for row in parent.subTasks if row.number == subtask_number]
    if len(rows) != 1:
        raise FinalizeTaskDocumentError(
            f"immediate parent must contain exactly one row {subtask_number!r}; found {len(rows)}"
        )
    return rows[0]


def _check_parent_row_path(parent_path: Path, row: SubTaskRef, leaf_path: Path) -> None:
    if not row.file:
        return
    row_leaf_path = (parent_path.parent / Path(row.file).with_suffix(".json")).resolve(strict=False)
    if row_leaf_path != leaf_path.resolve(strict=False):
        raise FinalizeTaskDocumentError(
            f"parent row {row.number!r} points at {row_leaf_path}, not leaf {leaf_path}"
        )


def _expected_parent_path(task_root: Path, master_ref: str) -> Path:
    root = task_root.resolve(strict=False)
    candidate = (root / Path(master_ref).with_suffix(".json")).resolve(strict=False)
    if candidate.parent != root:
        raise FinalizeTaskDocumentError(
            f"leaf master reference must resolve to a direct child of {root}: {master_ref!r}"
        )
    return candidate


def _reconcile_task_documents(
    contract: WorktreeContract,
    targets: FinalizeTaskTargets,
    *,
    dry_run: bool,
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    if contract.kind == "series" and targets.leaf_path is not None:
        return reconcile_series_documents(contract, targets, dry_run=dry_run)
    updates, documents = _completion_updates(targets, dry_run=dry_run)
    projection_effects: list[dict[str, object]] = []
    if dry_run and documents:
        _require_finalize_sources_current(targets)
        projection_effects = [
            effect.model_dump(by_alias=True)
            for effect in preview_contract_task_facts(contract, tuple(documents))
        ]
    elif documents:
        if targets.leaf_path is None:
            raise FinalizeTaskDocumentError("completion candidates have no task-document root")
        task_root = targets.leaf_path.parent
        published = publish_contract_task_facts(
            contract,
            lambda: write_task_docs(task_root, documents),
            documents=tuple(documents),
            validate=lambda: _require_finalize_sources_current(targets),
        )
        projection_effects = [
            effect.model_dump(by_alias=True) for effect in published.projection_effects
        ]
    else:
        _require_finalize_sources_current(targets)
    return updates, projection_effects


def _completion_updates(
    targets: FinalizeTaskTargets, *, dry_run: bool
) -> tuple[dict[str, Any], list[TaskDocument]]:
    """Prepare changed completion documents separately from their atomic publication."""
    updates: dict[str, Any] = {}
    documents: list[TaskDocument] = []
    if targets.leaf_path is None or targets.leaf is None:
        updates["leaf"] = {
            "state": "skipped",
            "reason": "no contract-bound leaf task document authored",
        }
    else:
        if targets.completed_leaf is None:
            raise FinalizeTaskDocumentError("preflighted leaf completion candidate is missing")
        if targets.completed_leaf != targets.leaf:
            documents.append(targets.completed_leaf)
        updates["leaf"] = _task_update_payload(
            targets.leaf_path,
            targets.completed_leaf,
            dry_run=dry_run,
        )
        if targets.completed_leaf == targets.leaf:
            updates["leaf"]["state"] = "already-completed"

    if targets.parent_path is None or targets.parent is None or targets.parent_row is None:
        updates["parent"] = {"state": "skipped", "reason": "leaf has no immediate parent"}
    else:
        if targets.completed_parent is None:
            raise FinalizeTaskDocumentError("preflighted parent completion candidate is missing")
        if targets.completed_parent != targets.parent:
            documents.append(targets.completed_parent)
        updates["parent"] = _task_update_payload(
            targets.parent_path,
            targets.completed_parent,
            dry_run=dry_run,
        )
        if targets.completed_parent == targets.parent:
            updates["parent"]["state"] = "already-completed"
        updates["parent"]["subtaskNumber"] = targets.parent_row.number
    return updates, documents


def _require_finalize_sources_current(targets: FinalizeTaskTargets) -> None:
    for source in (targets.leaf_source, targets.parent_source):
        if source is None:
            continue
        try:
            current = current_task_doc_source(source)
        except TaskDocSourceReadError as exc:
            raise FinalizeTaskDocumentError(
                f"task document became unreadable before finalization: {exc.evidence()}"
            ) from exc
        if current != source:
            raise FinalizeTaskDocumentError(
                "task document source changed after finalization preflight; re-read and retry"
            )


def _leaf_completion_candidate(doc: TaskDocument) -> TaskDocument:
    if doc.status == "Completed":
        return doc
    data = doc.model_dump(by_alias=True)
    data["status"] = "Completed"
    data["decisions"] = _finalized_decisions(data)
    return TaskDocument.model_validate(data)


def _parent_completion_candidate(
    doc: TaskDocument,
    subtask_number: str,
) -> TaskDocument:
    refuse_retired_row(doc, subtask_number, "finalization of this task", FinalizeTaskDocumentError)
    data = doc.model_dump(by_alias=True)
    refs = data["subTasks"]
    index = next((idx for idx, ref in enumerate(refs) if ref["number"] == subtask_number), None)
    if index is None:
        raise FinalizeTaskDocumentError(
            f"preflighted parent row disappeared before reconciliation: {subtask_number!r}"
        )
    refs[index]["status"] = "Completed"
    updated = TaskDocument.model_validate(data)
    updated = demote_completed_master_if_unresolved(updated)
    if updated == doc:
        return doc
    data = updated.model_dump(by_alias=True)
    data["decisions"] = _finalized_decisions(data)
    return TaskDocument.model_validate(data)


def _task_update_payload(
    path: Path,
    doc: TaskDocument,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": "would-update" if dry_run else "updated",
        "docPath": path.as_posix(),
        "status": doc.status,
    }
    if not dry_run:
        payload["renderedPath"] = path.with_suffix(".md").as_posix()
    return payload


def _finalized_decisions(data: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "at": datetime.now().astimezone().isoformat(timespec="minutes"),
            "decision": "Finalize task lifecycle.",
            "rationale": "The finalizer proved the task commit landed on the parent target branch, cleanup completed, and task documents could be reconciled.",
        },
        *data.get("decisions", []),
    ]


def resolve_series_targets(contract: WorktreeContract, args: FinalizeArgs) -> FinalizeTaskTargets:
    """The master's own document and, where a sprint commands it, its row on that sprint.

    Only the documents this needs are read in full: the master's and its commanding sprint's. A
    sprint that commands the master without one row for it (a legacy seat row that does not
    correlate, or no row at all) is a linkage fact, not a broken linkage: the master's document
    is completed and the sprint row is reported as skipped.
    """

    path = contract.task_root / "task.json"
    if not _holds_master_document(path):
        # An ordinary standalone task (no task document, or one that is not a master) has no
        # master or sprint row to complete: it finalizes as a task without a master document.
        return _resolve_parent_target(contract, args, None, None)
    master, source = _read_own_document(path)
    require_abandoned_rows_unlanded(
        contract.task_root, master, then="retry finalization", error=FinalizeTaskDocumentError
    )
    completed = FinalizeTaskTargets(
        leaf_path=path,
        leaf=master,
        completed_leaf=_leaf_completion_candidate(master),
        leaf_source=source,
    )
    repository_tasks = contract.coordination_root / "tasks" / contract.repo_name
    own_ref = TaskDocumentRef(
        repository=contract.repo_name, path=f"{contract.task_root.name}/task.json"
    )
    census = sprint_census(repository_tasks, path, own_ref, master)
    if census.unreadable_naming:
        raise FinalizeTaskDocumentError(
            f"task document {census.unreadable_naming[0]} cannot be read and names master "
            f"{master.id!r}, so it may be the sprint that commands it; repair or remove that file "
            "and retry finalization"
        )
    sprints = census.commanding
    if len(sprints) > 1:
        raise FinalizeTaskDocumentError(
            f"master {master.id!r} is commanded by several sprints "
            f"({', '.join(sprint.as_posix() for sprint in sprints)}); remove it from all but one "
            f"({_removal_actions(repository_tasks, sprints, own_ref, path, master)}) and retry "
            "finalization"
        )
    if not sprints:
        if _parent_asserted(args):
            raise FinalizeTaskDocumentError(
                f"master {master.id!r} has no commanding sprint to assert"
            )
        return completed
    sprint_path = sprints[0]
    sprint, sprint_source = _read_series_document(sprint_path, "commanding sprint")
    topology = TaskDocumentTopology(contract.coordination_root)
    unreadable = _unreadable_other_members(sprint, repository_tasks, path, master)
    try:
        ref = topology.canonical_ref(contract.repo_name, path)
        sprint_ref = topology.canonical_ref(contract.repo_name, sprint_path)
        # The sprint's linkage is validated without the members that cannot be read: this
        # master's row is found by its reference or its seat document, and neither needs them.
        overrides = {ref: master, sprint_ref: _without_members(sprint, unreadable)}
        topology.commanded_masters(topology.resolve(sprint_ref, overrides), overrides=overrides)
        topology.validate_sprint_linkage(sprint_ref, overrides=overrides)
    except TaskDocumentRefError as exc:
        raise FinalizeTaskDocumentError(
            f"master {master.id!r}: {exc.status}: {exc}; repair sprint linkage and retry finalization"
        ) from exc
    completed = replace(
        completed,
        parent_facts=tuple(
            {
                "kind": "sprint-member-unreadable",
                "entry": entry,
                "file": member.as_posix(),
                "detail": f"cannot be read ({problem}); this finalization did not need it",
            }
            for entry, (member, problem) in unreadable.items()
        ),
    )
    rows = master_rows(sprint, sprint_path.parent, ref)
    row = rows.row
    if row is None:
        if _parent_asserted(args):
            raise FinalizeTaskDocumentError(
                f"sprint {sprint_ref.key} holds no single row for master {ref.key} to assert "
                f"({rows.fact})"
            )
        return replace(completed, parent_skip=(sprint_path, rows.fact or {}))
    _assert_parent_arguments(args, sprint_path.resolve(), row.number)
    return replace(
        completed,
        parent_path=sprint_path,
        parent=sprint,
        parent_row=row,
        completed_parent=_parent_completion_candidate(sprint, row.number),
        parent_source=sprint_source,
    )


def _unreadable_other_members(
    sprint: TaskDocument, repository_tasks: Path, own: Path, master: TaskDocument
) -> dict[str, tuple[Path, str]]:
    """Read admitted canonical members; an unbound title is not a missing folder."""

    paths = _admitted_member_paths(sprint, repository_tasks)
    aliases = {own.parent.name, master.id, master.title}
    problems: dict[str, tuple[Path, str]] = {}
    for relative in sorted(paths):
        member = repository_tasks / relative
        if member.resolve(strict=False) == own.resolve(strict=False):
            continue
        try:
            member.lstat()
        except FileNotFoundError:
            # A genuinely missing canonical member remains in linkage validation.
            continue
        except OSError:
            pass
        try:
            document = read_task_doc(member)
        except (OSError, ValueError) as exc:
            problems[relative] = (member, f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        else:
            aliases.update((member.parent.name, document.id, document.title))
    unreadable: dict[str, tuple[Path, str]] = {}
    for entry in sprint.orchestrates:
        relative = f"{entry}/task.json"
        if relative in problems:
            unreadable[entry] = problems[relative]
        elif entry not in aliases and relative not in paths:
            raise FinalizeTaskDocumentError(
                f"sprint member entry {entry!r} has unresolved identity: no admitted canonical "
                "reference binds that title or id to a master document; repair sprint linkage "
                "by using the intended master's canonical folder in orchestrates, matching its "
                "typed masterRef or executionGraph reference, then retry finalization; no "
                "member was dropped or treated as a missing folder"
            )
    return unreadable


def _admitted_member_paths(sprint: TaskDocument, repository_tasks: Path) -> set[str]:
    """Canonical references already declared by rows/graph or an existing folder entry."""

    typed = [row.masterRef for row in sprint.subTasks if row.masterRef is not None]
    if len(typed) != len(set(typed)):
        raise FinalizeTaskDocumentError(
            "task-sprint-linkage-row-duplicate: multiple sprint rows link the same canonical "
            "master reference; repair sprint linkage and retry finalization"
        )
    refs = set(typed)
    if sprint.executionGraph is not None:
        refs.update(sprint.executionGraph.master_refs())
    return {ref.path for ref in refs if ref.repository == sprint.repo} | {
        f"{entry}/task.json" for entry in sprint.orchestrates if (repository_tasks / entry).is_dir()
    }


def _without_members(sprint: TaskDocument, entries: dict[str, tuple[Path, str]]) -> TaskDocument:
    """The sprint as it reads for a validation that leaves the named members out."""

    if not entries:
        return sprint
    paths = {f"{entry}/task.json" for entry in entries}
    return sprint.model_copy(
        update={
            "orchestrates": [entry for entry in sprint.orchestrates if entry not in entries],
            "subTasks": [
                row
                for row in sprint.subTasks
                if row.masterRef is None
                or row.masterRef.repository != sprint.repo
                or row.masterRef.path not in paths
            ],
        }
    )


def _removal_actions(
    repository_tasks: Path,
    sprints: tuple[Path, ...],
    master_ref: TaskDocumentRef,
    master_path: Path,
    master: TaskDocument,
) -> str:
    """For each sprint that commands the master: the edit that removes it there, and works."""

    names = {master_path.parent.name, master.id, master.title}
    actions = []
    for sprint_path in sprints:
        key = f"{master_ref.repository}/{sprint_path.relative_to(repository_tasks).as_posix()}"
        try:
            document = read_task_doc(sprint_path)
        except (OSError, ValueError):
            actions.append(f"on sprint {key}: repair that document first, it cannot be read")
            continue
        actions.append(membership_removal_action(key, document, master_ref, names))
    return "; ".join(actions)


def _read_own_document(path: Path) -> tuple[TaskDocument, TaskDocSourceSnapshot]:
    """The task's own document, which decides whether a master is being finalized.

    A deliberate difference from earlier builds, which finalized a task whose ``task.json``
    could not be read as an ordinary standalone task: its kind cannot be known, and a master
    must not be finalized as if it were none. Such a task is refused and the file is named.
    """

    try:
        return _read_parent(path)
    except FinalizeTaskDocumentError as exc:
        raise FinalizeTaskDocumentError(
            f"the task's own document {path} cannot be read, so it is not known whether it is a "
            f"master and this task is not finalized: {exc}; restore or repair that file and "
            "retry finalization"
        ) from exc


def _read_series_document(path: Path, role: str) -> tuple[TaskDocument, TaskDocSourceSnapshot]:
    try:
        return _read_parent(path)
    except FinalizeTaskDocumentError as exc:
        raise FinalizeTaskDocumentError(
            f"the {role} document {path} cannot be used to finalize: {exc}; restore or repair that "
            "task document and retry finalization"
        ) from exc


def _holds_master_document(path: Path) -> bool:
    """Whether ``path`` is a readable master document; an unreadable one is refused by the caller."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        # Failed inspection does not prove absence; the owning reader names the refusal.
        return True
    try:
        return read_task_doc_with_source(path)[0].kind == "master"
    except (OSError, ValueError):
        return True


def reconcile_series_documents(
    contract: WorktreeContract, targets: FinalizeTaskTargets, *, dry_run: bool
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    if targets.leaf_path is None or targets.completed_leaf is None:
        raise FinalizeTaskDocumentError(
            "series finalization has no proven canonical master target; "
            f"restore the master document {contract.task_root / 'task.json'} and retry"
        )
    documents = [(targets.leaf_path.parent, targets.completed_leaf)]
    updates = {
        "master": _task_update_payload(targets.leaf_path, targets.completed_leaf, dry_run=dry_run)
    }
    if targets.parent_path is not None and targets.completed_parent is not None:
        documents.append((targets.parent_path.parent, targets.completed_parent))
        updates["sprint"] = _task_update_payload(
            targets.parent_path, targets.completed_parent, dry_run=dry_run
        )
        assert targets.parent_row is not None
        updates["sprint"]["subtaskNumber"] = targets.parent_row.number
        if targets.parent_facts:
            updates["sprint"]["linkageFacts"] = list(targets.parent_facts)
    elif targets.parent_skip is not None:
        sprint_path, fact = targets.parent_skip
        updates["sprint"] = {
            "state": "skipped",
            "reason": "sprint-holds-no-single-row-for-this-master",
            "docPath": sprint_path.as_posix(),
            "linkageFact": fact,
            "detail": "The sprint commands this master without one row that stands for it, so "
            "no sprint row was completed; task_doc linkage_report on the sprint shows the fact.",
        }
        if targets.parent_facts:
            updates["sprint"]["linkageFacts"] = list(targets.parent_facts)
    topology = TaskDocumentTopology(contract.coordination_root)
    overrides = {
        topology.canonical_ref(contract.repo_name, root / "task.json"): document
        for root, document in documents
    }
    sprint_ref = (
        topology.canonical_ref(contract.repo_name, targets.parent_path)
        if targets.parent_path is not None
        else None
    )
    scopes = (sprint_ref,) if sprint_ref is not None else ()
    _require_finalize_sources_current(targets)
    if dry_run:
        effects = tuple(
            preview_closeout_projection_effect(contract.coordination_root, ref, overrides=overrides)
            for ref in scopes
        )
    else:
        graph = (
            targets.completed_parent.executionGraph
            if targets.completed_parent is not None
            else None
        )
        published = publish_task_fact_mutation(
            contract.coordination_root,
            validate=lambda: _require_finalize_sources_current(targets),
            projection_scopes=lambda: scopes,
            publication=lambda: write_task_doc_batch(
                documents,
                graph_titles=read_graph_titles(contract.coordination_root / "tasks", graph)
                if graph is not None
                else None,
            ),
        )
        effects = published.projection_effects
    return updates, [effect.model_dump(by_alias=True) for effect in effects]
