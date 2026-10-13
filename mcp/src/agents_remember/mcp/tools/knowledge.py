"""Payload builders for the three mounted ``knowledge_*`` operations.

One builder per operation, each of which validates its wire request, delegates to the application
seam that owns the operation, and returns the typed shape the response model declares. Nothing here
decides anything: no classification is computed, no effect label is inferred, no draft is authored
and no ambiguity is resolved by choosing.

**Knowledge is text (MIK-R26 rule 5).** ``knowledge_read`` and ``knowledge_integrity_check`` select a
*memory tree* by its root directory -- a converted tree, one that holds ``knowledge/layout.json`` --
and ``knowledge_read`` reads it through the derived index of that tree's current state (MIK-R23).
``knowledge_diff`` names a memory repository and two of its Git revisions. No operation accepts a
database path: an unconverted tree or a database file is refused as ``legacy-format``, and the
refusal names the crossing sync, the conversion command and the line that holds the converted
memory. The namespace a caller once supplied (``repositoryId``) is the index's own constant, which
the server supplies.

* ``knowledge_read`` returns "recorded claims and assessments as attributed records"; the payload it
  returns is the view payload itself, so the classification rule has exactly one implementation.
* ``knowledge_diff`` serves the Git diff of the knowledge files between two trees (records, history,
  sidecars and onboarding cards), with their patches, grouped by record and by source path. It
  includes no semantic effect label: none is inferred from the diff.
* ``knowledge_integrity_check`` runs the knowledge validator (MIK-R22) over a memory tree and
  returns a leaf's latest change-to-knowledge worklist (MIK-R08).

The write side is not mounted: knowledge is written by the curator file writer, through
``agents-remember knowledge-ingest`` and ``agents-remember knowledge-bootstrap``.

**Every public builder here returns ``_tool_payload(...)``, like every other adapter module:** the
body is validated against ``TOOL_RESPONSE_MODELS["knowledge_*"]`` before it reaches the wire.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_file_diff import (
    held_paths,
    knowledge_file_diff,
    narrowed_diff,
)
from agents_remember.application.knowledge_names import (
    NameResolver,
    SelectionMovedError,
    SelectionProvenance,
    _memory_commit,
    _memory_dirty,
    project_knowledge_body,
)
from agents_remember.application.knowledge_paging import threshold_block
from agents_remember.application.knowledge_paging.currentness import WalkCurrentness
from agents_remember.application.knowledge_paging.tree_read import (
    DEFAULT_ORDERING,
    PageExtras,
    ReadSubject,
    TreeExtras,
    TreeReadScope,
    read_tree_page,
)
from agents_remember.application.knowledge_proofs import tree_view_proofs
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_reports import ReportWriteError, write_report
from agents_remember.application.knowledge_worklist.surface import leaf_worklist_fields
from agents_remember.application.published_intent import (
    SelectedKnowledgeDataset,
    select_knowledge_dataset,
)
from agents_remember.kernel.git_command import (
    GitRunnerOptions,
    git_environment,
    run_git,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index import (
    INDEX_REPOSITORY_ID,
    IndexMismatchError,
    MemoryTreeError,
)
from agents_remember.memory_quality.knowledge_validator.report import ValidationReport
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    KnowledgeTreeReadError,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import validate_tree
from agents_remember.models.knowledge.review_trees import ReviewKnowledgeTreeDiff
from agents_remember.models.knowledge.view import require_admitted_ordering_input
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_STATUS_ENTRY_MAX,
)
from agents_remember.worktrees.cutover_lock import LEGACY_FORMAT_CODE, legacy_format_refusal
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import load_contract

from .base import _tool_payload

__all__ = [
    "DiffToolRequest",
    "IntegrityCheckRequest",
    "ReadToolRequest",
    "knowledge_diff_payload",
    "knowledge_integrity_check_payload",
    "knowledge_read_payload",
]

# The two shapes a source-resolution half can take, and the bound on how long resolving one may
# take: a Git call that hangs must not hold a mounted read open.
_TREE_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_GIT_TIMEOUT_SECONDS = 20
# What ``afterRevision`` reports when the caller named none: the memory working tree.
WORKING_TREE = "working tree"


@dataclass(frozen=True)
class ProjectionNaming:
    """How one projected answer locates: the locating, the epoch, the resolver.

    The resolver is sizing's shared instance, revalidated for moves; without
    one, the provenance opens a bound resolver, and without that a fresh one.
    """

    relative_locations: bool = False
    provenance: SelectionProvenance | None = None
    resolver: NameResolver | None = None


@dataclass(frozen=True)
class ReadToolRequest:
    """One ``knowledge_read`` call's arguments as one value.

    The tool signature stays flat because FastMCP derives the published input schema from it; this
    value is what the builder consumes.

    ``memory_root`` is the *selection*, and the caller owns it: the root directory of a converted
    memory tree (``memoryTree.memoryRoot`` of a published-intent block, a leaf's memory worktree, or
    the repository's memory root). ``repository_id`` is not a wire argument: a memory tree's index
    has one constant namespace (:data:`INDEX_REPOSITORY_ID`), which the server supplies and every
    response states.
    """

    memory_root: str
    view: str
    ordering_input: str | None = None
    limit: int = 32
    continuation: str | None = None
    invariant_revision_id: str | None = None
    family_revision_id: str | None = None
    source_path: str | None = None
    repository_root: str | None = None
    code_tree_id: str | None = None
    repository_id: str = INDEX_REPOSITORY_ID


@dataclass(frozen=True)
class DiffToolRequest:
    """One ``knowledge_diff`` call's arguments as one value.

    ``memory_root`` is a memory repository (or one of its worktrees); ``before`` is a Git revision or
    tree of it (``HEAD`` by default). ``after`` is another revision or tree, or ``None`` for the
    memory working tree, so uncommitted knowledge changes can be seen. ``record_id`` and ``path``
    optionally narrow the answer.
    """

    memory_root: str
    before: str = "HEAD"
    after: str | None = None
    record_id: str | None = None
    path: str | None = None


def _refused_read(view: str, repository_id: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "view": view,
        "repositoryId": repository_id,
        "refusalCode": code,
        "refusalDetail": detail,
    }


def _memory_tree_refusal(memory_root: str, operation: str) -> tuple[str, str] | None:
    """Why ``memory_root`` is not a converted memory tree this operation can read, or ``None``.

    MIK-R26 rule 5: no registered tool accepts a database path, and no tool reads unconverted
    memory. A path that does not exist is an absent selection; an unconverted tree or a database
    file is ``legacy-format``, and the refusal names how that memory converts and where converted
    memory of the same repository can be read meanwhile.
    """

    path = Path(memory_root)
    if path.is_dir() and (path / LAYOUT_MARKER_PATH).is_file():
        return None
    if not path.exists():
        return (
            "selected_input_unavailable",
            f"the selected memory root does not exist: {path}; name the root directory of a "
            f"converted memory tree (one that holds {LAYOUT_MARKER_PATH})",
        )
    if path.is_dir():
        return (
            LEGACY_FORMAT_CODE,
            legacy_format_refusal(path, operation=operation, subject=f"the memory tree {path}"),
        )
    return (
        LEGACY_FORMAT_CODE,
        legacy_format_refusal(
            path.parent,
            operation=operation,
            subject=f"the file {path}",
            database_file=True,
        ),
    )


def _select(path: str, coordination_root: str | None) -> SelectedKnowledgeDataset:
    """Resolve one caller-selected converted memory tree to the index of its current state.

    The caller has already established that ``path`` is a converted tree's root
    (:func:`_memory_tree_refusal`); the tree's key is recomputed and its index reused or built
    (MIK-R23 rule 6), and the response names the tree and the index state.
    """

    return select_knowledge_dataset(
        Path(path),
        coordination_root=None if coordination_root is None else Path(coordination_root),
    )


class _NoCodeTreeError(ValueError):
    """A named ``repositoryRoot`` resolves no tree: it has no commit, or it is not a repository.

    The read context refuses a root without a tree, so this was a raw ``ValidationError`` out of the
    tool (carried to L01 from L02); it is now the named refusal below.
    """

    def __init__(self, repository_root: str) -> None:
        super().__init__(
            f"repositoryRoot {repository_root} resolves no code tree to read anchors at (it has no "
            "commit, or it is not a Git repository); name a repository with a commit, add "
            "codeTreeId, or omit repositoryRoot"
        )


# Every way resolving and opening a selected memory tree can fail, so each handler refuses the same
# inputs the same way: the tree, its Git objects or the cache could not give an index.
_SELECTION_FAILURES = (
    _NoCodeTreeError,
    IndexMismatchError,
    MemoryTreeError,
    GitPreparationError,
    KnowledgeStorageError,
    apsw.Error,
    OSError,
)


def _selection_refusal(path: str, error: BaseException) -> tuple[str, str]:
    if isinstance(error, _NoCodeTreeError):
        return ("selected_input_unavailable", str(error))
    return (
        "snapshot_unavailable",
        f"the selected memory tree could not be indexed: {error} (at {path})",
    )


def _source_resolution(
    request: ReadToolRequest, workspace_root: str | None
) -> tuple[str | None, str | None]:
    """The source-resolution pair one read context may carry, completed rather than half-supplied.

    A read resolves recorded anchors against a tree, and the shipped context model refuses a pair
    that names only one of the two: ``repository_root`` without ``code_tree_id`` is an incomplete
    request, not a narrower one. An ordinary caller supplies neither and still needs an answer, and
    the mount's own default supplied the root alone -- so a schema-conformant call raised a raw
    ``ValidationError`` out of the context constructor instead of returning a view or a typed
    refusal.

    The pair is completed here for the same reason the context refuses it: if a tree is to be named,
    both halves of it are named. The workspace default is used only when the caller names no
    repository at all, which is the behaviour the mount already documented; a caller that names a
    root without a tree gets that root's own current tree, resolved from it, and a root that has none
    is refused by name (:class:`_NoCodeTreeError`).
    """

    if request.code_tree_id is not None and request.repository_root is not None:
        return request.repository_root, request.code_tree_id
    root = request.repository_root
    if root is None:
        if request.code_tree_id is not None or workspace_root is None:
            return None, None
        # The mount's own default supplies a *repository*, never half a resolution request: a
        # workspace that is not the repository a tree could be read from is not named at all,
        # because the context model refuses a root without a tree and answering with one would
        # replace a caller's minimal read with a refusal about the mount's configuration.
        tree_id = _current_code_tree(workspace_root)
        return (workspace_root, tree_id) if tree_id is not None else (None, None)
    tree_id = _current_code_tree(root)
    if tree_id is None:
        raise _NoCodeTreeError(root)
    return root, tree_id


def _current_code_tree(repository_root: str) -> str | None:
    """The tree id of one repository root's current commit, or ``None`` when it has no tree.

    Nothing is invented when the root is not a repository, Git is absent or Git does not answer in
    time: the context is then built with neither half, which the shipped resolver reports as "no
    source resolution was requested" rather than as a resolution that silently failed.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", repository_root, "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    tree_id = completed.stdout.strip()
    return tree_id if _TREE_ID_PATTERN.match(tree_id) else None


def knowledge_read_payload(
    request: ReadToolRequest,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
    relative_locations: bool = False,
) -> dict[str, Any]:
    """Retrieve one named view of a memory tree, through the response-model choke point.

    The application layer computes the whole result; the answer names records
    instead of carrying them (MIK-R109 rule 2): each row holds the text ID,
    kind, computed state and memory-checkout location, and record text stays
    behind the answer's log. With ``relative_locations`` the emitted files
    are memory-root-relative (the answered absolute ``memoryRoot`` is the
    join base); otherwise they are absolute with the commit beside the
    answer, for a caller with no memory worktree.
    """

    body, selected, shared = _read_result(
        request,
        workspace_root=workspace_root,
        coordination_root=coordination_root,
        relative_locations=relative_locations,
    )
    if body["state"] == "refused":
        # MIK-R02 rule 1: a refusal states the threshold too (a page states it in page).
        body.setdefault("threshold", threshold_block())
    elif selected is None:
        # Unreachable: a non-refused body always carries the selection that
        # read it. The guard refuses rather than projecting unbound rows.
        code, detail = _selection_refusal(
            request.memory_root,
            MemoryTreeError(f"{request.memory_root} read without a selection"),
        )
        body = _refused_read(request.view, request.repository_id, code, detail)
    else:
        body = _projected_body(
            body,
            request,
            selected,
            naming=ProjectionNaming(
                relative_locations=relative_locations,
                resolver=shared,
            ),
        )
    return _tool_payload("knowledge_read", body)


def _projected_body(
    body: dict[str, Any],
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    *,
    naming: ProjectionNaming | None = None,
) -> dict[str, Any]:
    """One answer naming the records one full body carries, or a typed refusal.

    The projection reuses the read's own selection: paths, commit
    provenance and currentness all bind the same snapshot the rows were
    read from, never a later re-selection's. The shared naming resolver
    (sizing's instance) is revalidated for moves and reused with its
    caches; without one, the provenance opens a bound resolver, and
    without that a fresh one. When the checkout moved between selection
    and projection, the answer is a ``snapshot_unavailable`` refusal
    naming the move instead of mixed rows.
    """

    naming = naming if naming is not None else ProjectionNaming()
    resolver = naming.resolver
    tree = selected.memory_tree
    if tree is None:
        code, detail = _selection_refusal(
            request.memory_root,
            MemoryTreeError(f"{request.memory_root} selected no memory tree"),
        )
        return _refused_read(request.view, request.repository_id, code, detail)
    if resolver is None:
        try:
            resolver = NameResolver.open(
                memory_root=Path(request.memory_root),
                index_path=selected.database_path,
                tree_key=tree.tree_key,
                relative_locations=naming.relative_locations,
                selection=naming.provenance,
            )
        except SelectionMovedError as moved:
            return _refused_read(
                request.view,
                request.repository_id,
                "snapshot_unavailable",
                f"the memory checkout moved between selection and projection "
                f"(selection {moved.selection_commit}, now {moved.current_commit}); reread",
            )
    else:
        try:
            resolver.revalidate()
        except SelectionMovedError as moved:
            return _refused_read(
                request.view,
                request.repository_id,
                "snapshot_unavailable",
                f"the memory checkout moved between selection and projection "
                f"(selection {moved.selection_commit}, now {moved.current_commit}); reread",
            )
    return project_knowledge_body(body, resolver)


def _read_result(
    request: ReadToolRequest,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
    relative_locations: bool = False,
) -> tuple[dict[str, Any], SelectedKnowledgeDataset | None, NameResolver | None]:
    """The one ``knowledge_read`` body with the selection that read it.

    The selection rides beside the body so the naming projection binds
    the same snapshot -- paths, commit provenance and currentness never
    mix a later re-selection's state with these rows. The shared naming
    resolver rides beside the selection: one instance serves sizing and
    projection, move-validated at projection. The epoch (commit and
    dirtiness) is captured at selection time for the move validation.
    """

    repository_id, view = request.repository_id, request.view
    if view not in (
        "source_context",
        "invariant",
        "family",
    ):
        return (
            _refused_read(
                view,
                repository_id,
                "unknown_view",
                f"{view!r} is not one of the three named query views",
            ),
            None,
            None,
        )
    # The admitted-ordering closure is checked **before** the tree is opened, because the view
    # layer's own refusal is the answer the caller needs and the request model cannot carry the
    # unadmitted spelling to it: ``ViewRequest.ordering_input`` is a ``Literal``, so constructing the
    # request with a fifth ordering raises instead of refusing.
    ordering_refusal = require_admitted_ordering_input(_ordering(request))
    if ordering_refusal is not None:
        return (
            _refused_read(view, repository_id, ordering_refusal.code, ordering_refusal.detail),
            None,
            None,
        )
    unreadable = _memory_tree_refusal(request.memory_root, "knowledge_read")
    if unreadable is not None:
        return (_refused_read(view, repository_id, *unreadable), None, None)
    # The tree is opened inside the boundary, because every failure to index or read it is a fact
    # about the selection and not a programming error.
    try:
        # The epoch brackets the selection: commit and dirtiness are read
        # before the tree opens and again after, so a move or dirty edit
        # during selection refuses instead of binding a mixed epoch.
        before_commit = _memory_commit(Path(request.memory_root))
        before_dirty = _memory_dirty(Path(request.memory_root))
        selected = _select(request.memory_root, coordination_root)
        # The epoch binds the selection: commit and dirtiness are captured
        # here, at selection time, for the projection's move validation --
        # never re-probed as provenance at projection time.
        provenance = SelectionProvenance(
            commit=_memory_commit(Path(request.memory_root)),
            dirty=_memory_dirty(Path(request.memory_root)),
            relative=relative_locations,
        )
        if (provenance.commit, provenance.dirty) != (before_commit, before_dirty):
            # A move or dirty edit during selection refuses through the
            # same moved-selection refusal as the projection seam: no
            # mixed epoch is ever bound to rows.
            raise SelectionMovedError(before_commit, provenance.commit)
        repository_root, code_tree_id = _source_resolution(request, workspace_root)
        context = open_read_context(
            selected.database_path,
            repository_id,
            repository_root=None if repository_root is None else Path(repository_root),
            code_tree_id=code_tree_id,
        )
        # MIK-R02: a tree's pages are cut by the threshold.
        body, shared = read_tree_page(
            request,
            selected,
            context,
            extras=_tree_extras(selected),
            scope=TreeReadScope(workspace_root=workspace_root, provenance=provenance),
        )
        return body, selected, shared
    except SelectionMovedError as moved:
        if moved.selection_commit == moved.current_commit:
            detail = (
                "the memory checkout changed between selection and projection "
                f"(selection {moved.selection_commit}); reread"
            )
        else:
            detail = (
                "the memory checkout moved between selection and projection "
                f"(selection {moved.selection_commit}, now {moved.current_commit}); reread"
            )
        return (
            _refused_read(
                view,
                repository_id,
                "snapshot_unavailable",
                detail,
            ),
            None,
            None,
        )
    except _SELECTION_FAILURES as error:
        return (
            _refused_read(view, repository_id, *_selection_refusal(request.memory_root, error)),
            None,
            None,
        )


def _ordering(request: ReadToolRequest) -> str:
    """The ordering a read uses: the caller's, or the default only when the caller named none."""

    return DEFAULT_ORDERING if request.ordering_input is None else request.ordering_input


def _tree_extras(selected: SelectedKnowledgeDataset) -> TreeExtras:
    """What a memory-tree page carries beside its rows, prepared once per page.

    The proofs of its subject (MIK-R28), and the currentness of every invariant the page returns
    (MIK-R03) at the walk's code tree: the tree the caller named on a fresh read, or the tree the
    continuation binds when the walk resumes, so one walk reports one tree's states on every page.
    """

    tree = selected.memory_tree
    assert tree is not None

    def prepare(subject: ReadSubject, code: CodeTree | None, candidates: list[Any]) -> PageExtras:
        proofs = tree_view_proofs(selected.database_path, tree.tree_key, subject)
        states = WalkCurrentness(selected.database_path, tree.tree_key, code, candidates)
        return lambda body: {"proofs": proofs, "currentness": states.document(body)}

    return prepare


def knowledge_diff_payload(
    request: DiffToolRequest, *, coordination_root: str | None = None
) -> dict[str, Any]:
    """One ``knowledge_diff`` answer, through the response-model choke point.

    The full comparison is a caller-readable report (MIK-R109 rule 3): the
    answer holds the two trees compared, the count of changes by kind, the
    records whose change asks for a decision up to the list bound, and the
    report's location. The comparison behind both is unchanged for the
    reviewer in the dashboard (:func:`knowledge_file_diff`).
    """

    body = _diff_result(request, coordination_root=coordination_root)
    return _tool_payload("knowledge_diff", body)


def _diff_result(request: DiffToolRequest, coordination_root: str | None = None) -> dict[str, Any]:
    """The one ``knowledge_diff`` body, before the registered model validates it (MIK-R26 rule 5).

    The comparison is the Git diff of the knowledge files between two trees of the memory
    repository: record, history and census files, onboarding sidecars and the onboarding cards,
    each with its patch, grouped by record and by source path (:func:`knowledge_file_diff`, over
    the reviewer's own computation). The whole comparison is written as a caller-readable report
    (MIK-R109 rule 3); the answer names the two trees, counts the changes by kind, names the
    changed records up to the list bound, and locates the report. A tree that holds no layout
    marker is unconverted and refused as ``legacy-format``; a directory inside the repository is
    refused with the repository root to pass; a ``record_id`` or a ``path`` that names nothing in
    either tree is refused by name, not answered with an empty diff; a report that cannot be
    written refuses the call with the reason, leaving nothing partial behind. The builder reads
    files and Git objects only: it infers no semantic effect label and opens no database.
    """

    def refused(code: str, detail: str) -> dict[str, Any]:
        return {
            "ok": True,
            "state": "refused",
            "memoryRoot": request.memory_root,
            "refusalCode": code,
            "refusalDetail": detail,
        }

    root = Path(request.memory_root)
    if root.is_file():
        return refused(
            LEGACY_FORMAT_CODE,
            legacy_format_refusal(
                root.parent,
                operation="knowledge_diff",
                subject=f"the file {root}",
                database_file=True,
            ),
        )
    if not root.is_dir():
        return refused(
            "selected_input_unavailable",
            f"the selected memory root does not exist: {root}; name a memory repository",
        )
    inside = _repository_holding(root)
    if inside is not None:
        return refused(
            "selected_input_unavailable",
            f"the selected memory root {root} is a directory inside the memory repository at "
            f"{inside}, and the knowledge files are found from the repository root: pass "
            f"{inside} as memoryRoot, and name a file or a source with `path` to narrow the answer",
        )
    trees = _diff_trees(root, request)
    if isinstance(trees, _Refusal):
        return refused(trees.code, trees.detail)
    compared = _compared(root, request, trees)
    if isinstance(compared, _Refusal):
        return refused(compared.code, compared.detail)
    return _reported_body(request, trees, compared, coordination_root)


def _repository_holding(root: Path) -> Path | None:
    """The root of the repository that ``root`` lies inside, when ``root`` is not that root itself.

    Git reads its pathspecs from the directory it runs in, so a directory below the repository root
    would be compared as if it held the whole memory tree and answer that nothing changed.
    """

    try:
        named = run_git(
            root,
            ["rev-parse", "--show-toplevel"],
            GitRunnerOptions(timeout=_GIT_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError, GitPreparationError):
        return None
    top = named.stdout.strip()
    if named.returncode != 0 or not top:
        return None
    return None if Path(top).resolve() == root.resolve() else Path(top)


def _reported_body(
    request: DiffToolRequest,
    trees: tuple[str, str],
    diff: ReviewKnowledgeTreeDiff,
    coordination_root: str | None,
) -> dict[str, Any]:
    """The ``compared`` body: counts and decision-asking records beside a report.

    The whole comparison — what the answer held before — is written as a
    caller-readable report (MIK-R109 rule 3). The answer holds the two trees
    compared, the count of changes by kind, every changed record up to the
    list bound in decision order, and the report's location.
    """

    dump = diff.model_dump(mode="json")
    try:
        report_path = write_report(
            "knowledge-diff",
            {
                "operation": "knowledge_diff",
                "memoryRoot": request.memory_root,
                "beforeRevision": request.before,
                "afterRevision": request.after or WORKING_TREE,
                "beforeTree": trees[0],
                "afterTree": trees[1],
                "changedFiles": diff.changed_files,
                "records": dump["records"],
                "sources": dump["sources"],
                "history": dump["history"],
                "other": dump["other"],
            },
            coordination_root=coordination_root,
        )
    except ReportWriteError as error:
        if error.residual is None:
            detail = error.reason + "; nothing partial was left behind"
        else:
            detail = error.reason
        return {
            "ok": True,
            "state": "refused",
            "memoryRoot": request.memory_root,
            "refusalCode": "report-unwritable",
            "refusalDetail": detail,
        }
    counts = _change_counts(diff)
    named = _decision_records(diff)
    return {
        "ok": True,
        "state": "compared",
        "memoryRoot": request.memory_root,
        "beforeRevision": request.before,
        "afterRevision": request.after or WORKING_TREE,
        "beforeTree": trees[0],
        "afterTree": trees[1],
        "counts": counts,
        "decisionRecords": named[:TOOL_ANSWER_LIST_MAX],
        "decisionRecordCount": len(named),
        "report": str(report_path),
    }


def _change_counts(diff: ReviewKnowledgeTreeDiff) -> dict[str, int]:
    """The count of changes by kind: changed files by Git status, groups by listing."""

    counts = {
        "changedFiles": diff.changed_files,
        "records": len(diff.records),
        "sources": len(diff.sources),
        "history": len(diff.history),
        "other": len(diff.other),
    }
    for group in (*diff.records, *diff.sources):
        for change in group.files:
            counts[change.status] = counts.get(change.status, 0) + 1
    for change in (*diff.history, *diff.other):
        counts[change.status] = counts.get(change.status, 0) + 1
    return counts


# A changed record asks for a decision soonest when knowledge appeared or
# vanished (an added or deleted record file), next when a code-side claim
# about it changed (its entries), then renames, type changes and edits.
_DECISION_ORDER = {
    "added": 0,
    "deleted": 1,
    "entries-changed": 2,
    "renamed": 3,
    "type-changed": 4,
    "modified": 5,
}


def _group_change(statuses: set[str], has_entries: bool) -> str:
    """The one change kind naming a record group, in decision order."""

    if "added" in statuses:
        return "added"
    if "deleted" in statuses:
        return "deleted"
    if has_entries:
        return "entries-changed"
    if "renamed" in statuses:
        return "renamed"
    if "type_changed" in statuses:
        return "type-changed"
    return "modified"


def _decision_records(diff: ReviewKnowledgeTreeDiff) -> list[dict[str, Any]]:
    """Every changed record group as ID, kind, change and index path.

    The path is the authoritative record path inside the memory tree, not
    joined with the checkout: the answered absolute ``memoryRoot`` is the
    join base, so large comparisons stay within their bound on any root
    length (MIK-R109 rule 3).
    """

    named = []
    for group in diff.records:
        statuses = {change.status for change in group.files}
        change = _group_change(statuses, bool(group.entries))
        path = group.files[0].path if group.files else None
        named.append(
            {
                "recordId": group.record_id,
                "kind": group.kind,
                "change": change,
                "path": path,
            }
        )
    named.sort(key=lambda entry: (_DECISION_ORDER[entry["change"]], entry["recordId"]))
    return named


@dataclass(frozen=True)
class _Refusal:
    """Why one ``knowledge_diff`` request is refused: the shipped code and the sentence."""

    code: str
    detail: str


def _diff_trees(root: Path, request: DiffToolRequest) -> tuple[str, str] | _Refusal:
    """The tree each side of the request names, or the refusal the first unusable side earns."""

    trees: list[str] = []
    for named in (request.before, request.after):
        resolved = _working_tree(root) if named is None else _resolve_tree(root, named)
        revision = WORKING_TREE if named is None else named
        if isinstance(resolved, str):
            return _Refusal("selected_input_unavailable", resolved)
        if not resolved.converted:
            return _Refusal(
                LEGACY_FORMAT_CODE,
                legacy_format_refusal(
                    root,
                    operation="knowledge_diff",
                    subject=f"the memory tree {root} at {revision} ({resolved.tree})",
                ),
            )
        trees.append(resolved.tree)
    return trees[0], trees[1]


def _compared(
    root: Path, request: DiffToolRequest, trees: tuple[str, str]
) -> ReviewKnowledgeTreeDiff | _Refusal:
    """The diff of the two trees, cut to what the request selects, or why it cannot be answered."""

    try:
        selected = narrowed_diff(knowledge_file_diff(root, *trees), request.record_id, request.path)
        absent = _absent_selector(root, request, trees, selected)
    except (OSError, ValueError, GitPreparationError) as error:
        return _Refusal("snapshot_unavailable", f"the two memory trees cannot be compared: {error}")
    return selected if absent is None else _Refusal("selector_absent", absent)


def _absent_selector(
    root: Path,
    request: DiffToolRequest,
    trees: tuple[str, str],
    selected: ReviewKnowledgeTreeDiff,
) -> str | None:
    """Why a selector names nothing in either tree, or ``None`` when each names something.

    A record or a file that a tree holds and that did not change is a true empty answer; a selector
    that names nothing would read the same, so it is refused by name. The trees are listed only
    when a selector was given and selected nothing.
    """

    if (request.record_id is None and request.path is None) or selected.changed_files:
        return None
    held = held_paths(root, trees)
    searched = (
        f"{request.before} ({trees[0]}) and {request.after or WORKING_TREE} ({trees[1]}) were "
        "searched"
    )
    if request.record_id is not None and not held.holds_record(request.record_id):
        return (
            f"neither memory tree holds a record {request.record_id!r}: {searched}; name the "
            "identifier of a record file under knowledge/, for example INV-… or FAM-…"
        )
    if request.path is not None and not held.names(request.path):
        return (
            f"`path` {request.path!r} names nothing in either memory tree: {searched}; name a "
            "knowledge file by its path in the memory repository (under knowledge/ or "
            "onboarding/), or a source file or route by its path in the code repository"
        )
    return None


def _working_tree(root: Path) -> _ResolvedTree | str:
    """The memory working tree as a Git tree id, captured through a private index.

    The same capture the closeout and the reviewer use: the index is a scratch file, no ref, branch
    or real index moves, and the tree's objects are unreferenced until something names them.
    """

    try:
        with tempfile.TemporaryDirectory(prefix="ar-knowledge-diff-") as scratch:
            tree = worktree_candidate_tree(root, Path(scratch) / "index")
        marker = run_git(
            root,
            ["cat-file", "-e", f"{tree}:{LAYOUT_MARKER_PATH}"],
            GitRunnerOptions(timeout=_GIT_TIMEOUT_SECONDS),
        )
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        GitPreparationError,
    ) as error:
        return f"the memory working tree at {root} cannot be captured: {error}"
    return _ResolvedTree(tree, marker.returncode == 0)


@dataclass(frozen=True)
class _ResolvedTree:
    tree: str
    converted: bool


def _resolve_tree(root: Path, revision: str) -> _ResolvedTree | str:
    """The tree ``revision`` names in the repository at ``root``, or why it names none."""

    options = GitRunnerOptions(timeout=_GIT_TIMEOUT_SECONDS)
    try:
        named = run_git(
            root,
            ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{tree}}"],
            options,
        )
        if named.returncode != 0 or not _TREE_ID_PATTERN.match(named.stdout.strip()):
            return f"{revision!r} names no tree in the Git repository at {root}"
        tree = named.stdout.strip()
        marker = run_git(root, ["cat-file", "-e", f"{tree}:{LAYOUT_MARKER_PATH}"], options)
    except (OSError, subprocess.SubprocessError, GitPreparationError) as error:
        return f"Git could not read the memory tree {revision!r} at {root}: {error}"
    return _ResolvedTree(tree, marker.returncode == 0)


# How many violations one answer lists. The validator's report of a whole tree checked without a
# base can hold thousands of rows (every anchor of every card); the whole result carries all of
# them -- the log's detail file is the report's location -- while the answer keeps the first
# entries up to this bound. The bound is the tool-answer list bound, no second number.
MAX_LISTED_VIOLATIONS = TOOL_ANSWER_LIST_MAX


@dataclass(frozen=True)
class IntegrityCheckRequest:
    """One ``knowledge_integrity_check`` call's inputs, as the registered tool received them."""

    memoryRoot: str | None = None
    codeRoot: str | None = None
    baseCommits: tuple[str, ...] | None = None
    contractPath: str | None = None


@dataclass(frozen=True)
class _ValidatorInputs:
    """The memory tree the validator reads, its paired code checkout and its comparison bases."""

    memory_root: Path
    code_root: Path
    bases: tuple[str, ...]


def knowledge_integrity_check_payload(request: IntegrityCheckRequest) -> dict[str, Any]:
    """Run the knowledge validator over one memory tree, through the choke point (MIK-R26 rule 5).

    ``contractPath`` names a leaf by its contract: the validator then reads the leaf's memory
    worktree against its recorded memory base and its code worktree, and the response adds the
    leaf's latest persisted MIK-R08 worklist (:func:`leaf_worklist_fields`). ``memoryRoot`` with
    ``codeRoot`` names a tree and its paired code checkout directly. Naming neither is refused.
    """

    result = _integrity_check_result(request)
    if request.contractPath is not None:
        result.update(leaf_worklist_fields(request.contractPath))
    answer = None if result.get("state") == "refused" else _integrity_answer(result)
    return _tool_payload("knowledge_integrity_check", result, answer=answer)


def _integrity_answer(full: Mapping[str, Any]) -> dict[str, Any]:
    """MIK-R110 rule 6: one integrity whole result answered within the default bound.

    The outcome, the comparison bases, the validation outcome with its counts
    by rule and first findings, and the worklist state. The envelope fields
    stay with the whole result, as on every other projection. The violation
    rows are capped at the status entry cap with the total count beside them;
    the full listing stays in the whole result the log records.
    """

    answer = {
        key: value
        for key, value in full.items()
        if key not in ENVELOPE_STRIPPED_FIELDS and value is not None
    }
    validation = full.get("validation")
    if isinstance(validation, dict):
        violations = validation.get("violations")
        if isinstance(violations, list):
            if len(violations) > TOOL_ANSWER_STATUS_ENTRY_MAX:
                answer["validation"] = {
                    **validation,
                    "violations": {
                        "count": len(violations),
                        "items": violations[:TOOL_ANSWER_STATUS_ENTRY_MAX],
                        "truncated": True,
                    },
                }
        elif isinstance(violations, dict) and isinstance(violations.get("items"), list):
            items = violations["items"]
            count = violations.get("count", len(items))
            if len(items) > TOOL_ANSWER_STATUS_ENTRY_MAX or bool(
                count > TOOL_ANSWER_STATUS_ENTRY_MAX
            ):
                answer["validation"] = {
                    **validation,
                    "violations": {
                        **violations,
                        "items": items[:TOOL_ANSWER_STATUS_ENTRY_MAX],
                        "truncated": True,
                    },
                }
    return answer


def _refused_check(memory_root: str | None, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "memoryRoot": memory_root,
        "refusalCode": code,
        "refusalDetail": detail,
    }


def _validator_inputs(
    request: IntegrityCheckRequest,
) -> _ValidatorInputs | tuple[str, str]:
    """The trees one call names, or the refusal code and detail for a call that names too little."""

    memory_root, code_root, bases = (
        request.memoryRoot,
        request.codeRoot,
        request.baseCommits,
    )
    if request.contractPath is not None and (memory_root is None or code_root is None):
        try:
            contract = load_contract(Path(request.contractPath))
        except (ValueError, OSError) as error:
            return (
                "selected_input_unavailable",
                f"contractPath {request.contractPath} cannot be loaded as a worktree contract: "
                f"{error}",
            )
        if memory_root is None and contract.memory_worktree is not None:
            memory_root = str(contract.memory_worktree)
            if bases is None and contract.memory_base_commit:
                bases = (contract.memory_base_commit,)
        if code_root is None:
            code_root = str(contract.code_worktree)
    if memory_root is None or code_root is None:
        return (
            "selected_input_unavailable",
            "name a leaf (contractPath), or a memory tree with its paired code checkout "
            "(memoryRoot with codeRoot); the validator checks anchors against the code tree",
        )
    return _ValidatorInputs(Path(memory_root), Path(code_root), tuple(bases or ()))


def _integrity_check_result(request: IntegrityCheckRequest) -> dict[str, Any]:
    """The one ``knowledge_integrity_check`` body, before the registered model validates it.

    The validator is the one :func:`validate_tree` every commit route runs, over the tree's working
    files: against the bases named (a leaf's recorded memory base by default), or with no base, in
    which case every anchor is checked for path existence. It writes nothing and opens no database.
    """

    inputs = _validator_inputs(request)
    if not isinstance(inputs, _ValidatorInputs):
        return _refused_check(request.memoryRoot, *inputs)
    memory_root = str(inputs.memory_root)
    unreadable = _memory_tree_refusal(memory_root, "knowledge_integrity_check")
    if unreadable is not None:
        return _refused_check(memory_root, *unreadable)
    if not inputs.code_root.is_dir():
        return _refused_check(
            memory_root,
            "selected_input_unavailable",
            f"the paired code checkout does not exist: {inputs.code_root}",
        )
    try:
        report = validate_tree(
            knowledge_tree_from_directory(inputs.memory_root),
            bases=[
                knowledge_tree_from_git(inputs.memory_root, base, label=f"base {base}")
                for base in inputs.bases
            ],
            code=CodeDirectory(label=inputs.code_root.as_posix(), root=inputs.code_root),
        )
    except (OSError, KnowledgeTreeReadError, ValueError) as error:
        return _refused_check(
            memory_root,
            "snapshot_unavailable",
            f"the validator's inputs could not be read: {error}",
        )
    return {
        "ok": True,
        "state": "reported",
        "memoryRoot": memory_root,
        "codeRoot": inputs.code_root.as_posix(),
        "bases": list(inputs.bases),
        "validation": _validation_block(report),
    }


def _validation_block(report: ValidationReport) -> dict[str, Any]:
    """The validator's report as the response carries it: every count, and every finding.

    Refusing violations are listed before report-only findings, so a truncated answer never
    hides a refusal behind findings that refuse nothing. The whole result carries the full
    listing -- the log's detail file is the report's location -- while the answer keeps the
    first findings up to the list bound (MIK-R110 rule 6); ``violationsTruncated`` says
    whether the bound dropped rows. The counts always cover the whole report.
    """

    counts: dict[tuple[str, bool], int] = {}
    for violation in report.violations:
        key = (violation.rule, violation.report_only)
        counts[key] = counts.get(key, 0) + 1
    listed = [*report.refusals, *report.reports]
    return {
        "candidate": report.candidate,
        "ok": report.ok,
        "refusalCount": len(report.refusals),
        "reportCount": len(report.reports),
        "byRule": [
            {"rule": rule, "reportOnly": report_only, "count": count}
            for (rule, report_only), count in sorted(counts.items())
        ],
        "violations": [violation.to_document() for violation in listed],
        "violationsTruncated": len(listed) > MAX_LISTED_VIOLATIONS,
    }
