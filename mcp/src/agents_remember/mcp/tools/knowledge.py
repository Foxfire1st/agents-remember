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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.application.knowledge_bootstrap_admission import (
    BootstrapRefusal,
    admit_bootstrap_context,
)
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
from agents_remember.application.knowledge_reader.marks import (
    dependent_list,
    weakness_list,
)
from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    selection_for_dataset,
)
from agents_remember.application.knowledge_reader.truth import record_view
from agents_remember.application.knowledge_reports import ReportWriteError, write_report
from agents_remember.application.knowledge_routes_report import (
    family_route_documents,
    working_tree_files,
)
from agents_remember.application.knowledge_worklist.surface import leaf_worklist_fields
from agents_remember.application.knowledge_write_route import (
    BLANK_AUTHORIZATION,
    WaveTarget,
    WriteSubmission,
    crossing_write_report,
    is_converted,
    leaf_write_report,
    load_leaf_contract,
    read_handoff_document,
    unconverted_write_refusal,
    wave_write_report,
)
from agents_remember.application.knowledge_writer import WriteReport
from agents_remember.application.published_intent import (
    SelectedKnowledgeDataset,
    select_knowledge_dataset,
)
from agents_remember.application.worktree_code_state import (
    CODE_STATE_COMMIT,
    CODE_STATE_WORKING_TREE,
    WORKING_TREE_SELECTOR,
    CodeState,
    WorktreeCapture,
    capture_worktree,
    hold_observed_code_tree,
)
from agents_remember.kernel.git_command import (
    GitRunnerOptions,
    git_environment,
    run_git,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index import (
    INDEX_REPOSITORY_ID,
    IndexMismatchError,
    KnowledgeIndex,
    MemoryTreeError,
)
from agents_remember.memory.knowledge_index.projection import text_uuid
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
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
    "WriteToolRequest",
    "knowledge_diff_payload",
    "knowledge_integrity_check_payload",
    "knowledge_read_payload",
    "knowledge_write_payload",
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

    ``record_id`` is a record's text ID (``INV-…``, ``FAM-…``), accepted by the ``invariant`` and
    ``family`` views beside the revision UUIDs (MIK-R73 rule 3): the UUIDs are derived from the
    text IDs and are stored nowhere, so a caller may name either.
    """

    memory_root: str
    view: str
    ordering_input: str | None = None
    limit: int = 32
    continuation: str | None = None
    invariant_revision_id: str | None = None
    family_revision_id: str | None = None
    record_id: str | None = None
    source_path: str | None = None
    repository_root: str | None = None
    code_tree_id: str | None = None
    repository_id: str = INDEX_REPOSITORY_ID
    weakness_kind: str | None = None
    include_closed: bool = False
    stale_only: bool = False
    stands_on: str | None = None


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


def _record_view(
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """One record's truth view: its fields, its links with their states, and its history rows.

    MIK-R73 rule 2. The reader computes the answer (:func:`record_view`), over the dataset this
    read already selected and verified, so the mounted view and the dashboard's record view cannot
    answer differently. The answer is bounded the way every answer is: the link lists and the
    timeline carry their first rows and their totals, and the record's prose is named by its
    location rather than carried (MIK-R109 rule 2).
    """

    if not request.record_id:
        return _refused_read(
            "record",
            repository_id,
            "invalid-input",
            "the record view names a `recordId`: a record's text ID (INV-…, FAM-…, DEC-…, ASM-…)",
        )
    tree = selected.memory_tree
    if tree is None:
        return _refused_read(
            "record",
            repository_id,
            "snapshot_unavailable",
            "the selection names no converted memory tree, so no record can be read",
        )
    memory_root = Path(request.memory_root)
    source = _observed_source_or_none(request, workspace_root)
    code_tree = _code_tree_of(source)
    try:
        selection = selection_for_dataset(
            repository_id=repository_id,
            memory_root=memory_root,
            database_path=selected.database_path,
            tree_key=tree.tree_key,
            code_tree=code_tree,
            coordination_root=None if coordination_root is None else Path(coordination_root),
        )
    except READ_FAILURES as error:
        return _refused_read(
            "record",
            repository_id,
            "snapshot_unavailable",
            f"the selected index could not be opened: {error}",
        )
    try:
        found = record_view(selection, request.record_id)
    except READ_FAILURES as error:
        return _refused_read(
            "record", repository_id, "unavailable", f"the record could not be read: {error}"
        )
    finally:
        selection.close()
        if source is not None:
            source.close()
    if found is None:
        return _refused_read(
            "record",
            repository_id,
            "selector_absent",
            f"the selected tree holds no record {request.record_id}",
        )
    prose = found.pop("prose", None)
    # MIK-R109 rule 2: the mounted answer names the record, its file and its heading; the record's
    # own document stays in the reader's structure (and in the file it names), never in this answer.
    record_document = found.get("record")
    if isinstance(record_document, dict):
        record_document.pop("document", None)
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_read",
        "view": "record",
        "state": "view",
        "repositoryId": repository_id,
        "memoryRoot": memory_root.as_posix(),
        "memoryTree": {"treeKey": tree.tree_key, "indexState": tree.index_state},
        **found,
    }
    if isinstance(prose, dict):
        # The prose file is named, never carried: this read names where a record is.
        answer["prose"] = {
            "path": prose.get("path"),
            "state": prose.get("state"),
        }
    _bound_record_lists(answer)
    return answer


def _one_view(
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """The answer one single-record view holds: a record's truth view, or the marks' listing."""

    if request.view == "record":
        return _record_view(
            request,
            selected,
            repository_id,
            workspace_root=workspace_root,
            coordination_root=coordination_root,
        )
    return _marks_view(
        request,
        selected,
        repository_id,
        workspace_root=workspace_root,
        coordination_root=coordination_root,
    )


def _marks_view(
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """The weaknesses or the dependents of one memory tree, bounded and naming how to read on.

    MIK-R118 rule 6 and MIK-R124 rule 7 read through this same tool. A weakness row holds the id,
    the file, the kind, the sentence and the measurement; a dependent row holds the document, its
    derived state and, where it is stale, each link that moved. Neither row carries the record's
    own text beyond what the requirement names.

    The page is what the answer can actually deliver: the caller's ``limit`` capped at the
    transport's list bound, delivered from ``continuation`` (a ``rows:<n>`` cursor) onward, so the
    counts are true and a page beyond the bound is reachable (F14). An anterior page's cursor is
    the only continuation accepted; a token this view cannot read refuses, never restarts the walk.
    """

    tree = selected.memory_tree
    if tree is None:
        return _refused_read(
            request.view,
            repository_id,
            "snapshot_unavailable",
            "the selection names no converted memory tree, so no record can be read",
        )
    start = _marks_offset(request)
    if isinstance(start, str):
        return _refused_read(request.view, repository_id, "bad_continuation", start)
    memory_root = Path(request.memory_root)
    source = _observed_source_or_none(request, workspace_root)
    code_tree = _code_tree_of(source)
    try:
        selection = selection_for_dataset(
            repository_id=repository_id,
            memory_root=memory_root,
            database_path=selected.database_path,
            tree_key=tree.tree_key,
            code_tree=code_tree,
            coordination_root=None if coordination_root is None else Path(coordination_root),
        )
    except READ_FAILURES as error:
        return _refused_read(
            request.view,
            repository_id,
            "snapshot_unavailable",
            f"the selected index could not be opened: {error}",
        )
    try:
        rows = _marks_rows(request, selection)
    except READ_FAILURES as error:
        return _refused_read(
            request.view, repository_id, "unavailable", f"the records could not be read: {error}"
        )
    finally:
        selection.close()
        if source is not None:
            source.close()
    limit = min(max(1, request.limit), TOOL_ANSWER_LIST_MAX)
    page = rows[start : start + limit]
    remaining = max(0, len(rows) - start - len(page))
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_read",
        "view": request.view,
        "state": "view",
        "repositoryId": repository_id,
        "memoryRoot": memory_root.as_posix(),
        "memoryTree": {"treeKey": tree.tree_key, "indexState": tree.index_state},
        "rows": page,
        "counts": {
            "rowsReturned": len(page),
            "total": len(rows),
            "rowsRemaining": remaining,
            "offset": start,
        },
    }
    if remaining:
        # MIK-R108: the answer names how to read on rather than carrying the rest, and the cursor
        # it names reaches rows the transport's list bound would otherwise hide.
        answer["readOn"] = {
            "how": (
                "the same view answers the next rows: pass `continuation` as given here, or "
                "narrow it with `sourcePath` (a folder), `weaknessKind`, `staleOnly` or `standsOn`"
            ),
            "continuation": _marks_cursor(start + len(page)),
            "rowsRemaining": remaining,
            "limit": limit,
        }
    return answer


def _marks_cursor(start: int) -> str:
    """The continuation a marks view's next page starts at."""

    return f"rows:{start}"


def _marks_offset(request: ReadToolRequest) -> int | str:
    """The row a marks view's page starts at, or why the continuation cannot be read."""

    token = request.continuation
    if token is None or not token.strip():
        return 0
    head, _, tail = token.strip().partition(":")
    if head != "rows" or not tail.isdigit():
        return (
            f"the continuation {token!r} is not one this view minted: it reads `rows:<n>` as the "
            "previous page's `readOn` carried it; narrow the view instead of restarting it"
        )
    return int(tail)


def _marks_rows(request: ReadToolRequest, selection: Any) -> list[dict[str, Any]]:
    """The rows one marks view holds, before the answer's bound is applied."""

    if request.view == "weakness":
        return weakness_list(
            selection,
            folder=request.source_path,
            kind=request.weakness_kind,
            include_closed=request.include_closed,
        )
    return dependent_list(
        selection,
        folder=request.source_path,
        stale_only=request.stale_only,
        stands_on=request.stands_on,
    )


def _observed_source_or_none(
    request: ReadToolRequest, workspace_root: str | None
) -> _ObservedSource | None:
    """The code source a record read assesses its links at, when one resolves.

    A record read is a read of recorded knowledge first: a checkout that resolves no tree still
    answers the record and reports its anchor states as unverifiable, naming why.
    """

    try:
        return _observe_source(request, workspace_root)
    except _NoCodeTreeError:
        return None


def _code_tree_of(source: _ObservedSource | None) -> CodeTree | None:
    """The `CodeTree` a record read assesses links at, or ``None`` when nothing resolved."""

    if source is None or source.repository_root is None or source.code_tree_id is None:
        return None
    return CodeTree(
        Path(source.repository_root),
        source.code_tree_id,
        None if source.capture is None else source.capture.object_directory,
    )


def _bound_record_lists(answer: dict[str, Any]) -> None:
    """Cap the record view's link lists and timeline, each with its total beside it."""

    for key in ("outgoing", "incoming"):
        rows = answer.get(key)
        if not isinstance(rows, list):
            continue
        answer[f"{key}Count"] = len(rows)
        if len(rows) > TOOL_ANSWER_LIST_MAX:
            answer[key] = rows[:TOOL_ANSWER_LIST_MAX]
            answer[f"{key}Truncated"] = True
    timeline = answer.get("timeline")
    if isinstance(timeline, dict) and isinstance(timeline.get("events"), list):
        events = timeline["events"]
        answer["timelineCount"] = len(events)
        if len(events) > TOOL_ANSWER_LIST_MAX:
            answer["timeline"] = {**timeline, "events": events[:TOOL_ANSWER_LIST_MAX]}
            answer["timelineTruncated"] = True


def _refused_read(view: str, repository_id: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "view": view,
        "repositoryId": repository_id,
        "refusalCode": code,
        "refusalDetail": detail,
    }


# MIK-R73 rule 2: the tree views, and `record`, the truth view of one record. MIK-R118 rule 6 and
# MIK-R124 rule 7 read the two record kinds through this same tool: no tool is added.
_ADMITTED_VIEWS: Final = (
    "source_context",
    "invariant",
    "family",
    "record",
    "weakness",
    "dependent",
)


def _submission_refusal(request: ReadToolRequest) -> dict[str, Any] | None:
    """The refusal a read earns before its tree is opened, or ``None``.

    The view name, the admitted-ordering closure and the tree itself are checked here, in
    that order. The ordering check runs **before** the tree is opened because the view
    layer's own refusal is the answer the caller needs and the request model cannot carry the
    unadmitted spelling to it: ``ViewRequest.ordering_input`` is a ``Literal``, so constructing
    the request with a fifth ordering raises instead of refusing.
    """

    view, repository_id = request.view, request.repository_id
    if view not in _ADMITTED_VIEWS:
        return _refused_read(
            view,
            repository_id,
            "unknown_view",
            f"{view!r} is not one of {', '.join(_ADMITTED_VIEWS)}",
        )
    ordering_refusal = require_admitted_ordering_input(_ordering(request))
    if ordering_refusal is not None:
        return _refused_read(view, repository_id, ordering_refusal.code, ordering_refusal.detail)
    unreadable = _memory_tree_refusal(request.memory_root, "knowledge_read")
    return None if unreadable is None else _refused_read(view, repository_id, *unreadable)


def _moved_read_refusal(
    view: str, repository_id: str, moved: SelectionMovedError
) -> dict[str, Any]:
    """The refusal of a selection that moved between selecting and projecting (MIK-R02)."""

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
    return _refused_read(view, repository_id, "snapshot_unavailable", detail)


def _record_seeded(
    request: ReadToolRequest, selected: SelectedKnowledgeDataset
) -> tuple[ReadToolRequest, tuple[str, str] | None]:
    """Resolve a text-ID ``recordId`` to the revision UUID its view reads (MIK-R73 rule 3).

    The UUIDs a read hands out are derived from the text IDs and stored nowhere, so a caller
    may name either: the record's own text ID is resolved through this selection's index to
    the seed of its current revision, and the rest of the read is unchanged. A record the tree
    does not hold, or one of another kind, is ``selector_absent`` naming the ID (Failure) --
    never an empty, complete view that looks like an answer.
    """

    if request.record_id is None:
        return request, None
    view = request.view
    if view not in ("invariant", "family"):
        return request, (
            "unknown_view",
            "recordId is read by the invariant and family views; name one of them",
        )
    tree = selected.memory_tree
    tree_key = None if tree is None else tree.tree_key
    try:
        with KnowledgeIndex(selected.database_path, expected_key=tree_key) as index:
            record = index.record(request.record_id).value
    except (apsw.Error, OSError, ValueError, MemoryTreeError, IndexMismatchError) as error:
        return request, (
            "snapshot_unavailable",
            f"recordId {request.record_id!r} could not be resolved: {error}",
        )
    if record is None or record.kind != view or record.revision is None:
        return request, (
            "selector_absent",
            f"recordId {request.record_id!r} names no {view} this memory tree holds at a revision",
        )
    seed = text_uuid("revision", f"{record.id}@{record.revision}")
    if view == "invariant":
        return replace(request, invariant_revision_id=seed), None
    return replace(request, family_revision_id=seed), None


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


@dataclass
class _ObservedSource:
    """The code source one read observes, and any working-tree capture it must hold until it answers.

    A capture is a scratch store holding the working tree's blobs: it has to outlive the whole read
    (the page's anchors and currentness are observed while the answer is built) and be removed
    afterwards, so the object that observed it owns its removal.
    """

    repository_root: str | None
    code_tree_id: str | None
    code_state: CodeState | None
    capture: WorktreeCapture | None = None

    def close(self) -> None:
        if self.capture is not None:
            self.capture.close()
            self.capture = None


def _observe_source(request: ReadToolRequest, workspace_root: str | None) -> _ObservedSource:
    """Resolve the code source one read observes, capturing its working tree when it differs.

    Three shapes, one rule -- the read answers at the bytes the caller sees (MIK-R73 rule 7):

    * ``codeTreeId: "working-tree"`` with ``repositoryRoot``: capture that checkout and read it;
    * a named tree and root: read exactly that tree, as before;
    * a root alone (or the mount's workspace default): the commit when the checkout is clean, else
      its captured working tree.

    The capture is returned, not closed: the read that asked for it holds it while it observes.
    """

    named_tree = request.code_tree_id
    root = request.repository_root
    if named_tree == WORKING_TREE_SELECTOR:
        target = root if root is not None else workspace_root
        if target is None:
            raise _NoCodeTreeError("the working-tree selector with no repository root")
        capture = capture_worktree(Path(target))
        return _ObservedSource(target, capture.tree_id, CODE_STATE_WORKING_TREE, capture)
    if named_tree is not None and root is not None:
        return _ObservedSource(root, named_tree, CODE_STATE_COMMIT)
    if root is None:
        if named_tree is not None or workspace_root is None:
            return _ObservedSource(None, None, None)
        # The mount's own default: it supplies a repository when it has one, and resolves nothing
        # when it has none -- a workspace that is not a checkout is not a caller's mistake.
        observed, capture = hold_observed_code_tree(Path(workspace_root))
        if observed is None:
            return _ObservedSource(None, None, None)
        return _ObservedSource(workspace_root, observed.tree_id, observed.code_state, capture)
    observed, capture = hold_observed_code_tree(Path(root))
    if observed is None:
        raise _NoCodeTreeError(root)
    return _ObservedSource(root, observed.tree_id, observed.code_state, capture)


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
    refusal = _submission_refusal(request)
    if refusal is not None:
        return (refusal, None, None)
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
        if view in ("record", "weakness", "dependent"):
            # MIK-R73 rule 2: one record, its links with their states and its history. MIK-R118
            # rule 6 and MIK-R124 rule 7: the same tool lists the two record kinds.
            return (
                _one_view(
                    request,
                    selected,
                    repository_id,
                    workspace_root=workspace_root,
                    coordination_root=coordination_root,
                ),
                selected,
                None,
            )
        seeded, seed_refusal = _record_seeded(request, selected)
        if seed_refusal is not None:
            return (_refused_read(view, repository_id, *seed_refusal), None, None)
        request = seeded
        source = _observe_source(request, workspace_root)
        try:
            context = open_read_context(
                selected.database_path,
                repository_id,
                repository_root=None
                if source.repository_root is None
                else Path(source.repository_root),
                code_tree_id=source.code_tree_id,
                code_state=source.code_state,
            )
            # MIK-R02: a tree's pages are cut by the threshold.
            body, shared = read_tree_page(
                request,
                selected,
                context,
                extras=_tree_extras(selected),
                scope=TreeReadScope(workspace_root=workspace_root, provenance=provenance),
            )
        finally:
            # The captured blobs are readable through the whole read and removed with it.
            source.close()
        return body, selected, shared
    except SelectionMovedError as moved:
        return (_moved_read_refusal(view, repository_id, moved), None, None)
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


def _compact_route_row(row: Any) -> Any:
    """One family's route row without the uncovered path list: its count stands in the answer."""

    if not isinstance(row, dict):
        return row
    state = row.get("state")
    if not isinstance(state, dict) or "uncovered" not in state:
        return row
    kept = {key: value for key, value in state.items() if key != "uncovered"}
    kept["uncoveredTotal"] = state.get("uncoveredTotal", len(state["uncovered"]))
    return {**row, "state": kept}


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
    routes = full.get("routes")
    if isinstance(routes, list):
        # MIK-R73 rule 4 keeps the route rows in the answer and MIK-R110 rule 6 keeps the answer
        # bounded: the rows travel compact (counts, not the uncovered path list, which the whole
        # result and the `knowledge-routes` command keep in full), and past the entry cap only the
        # first rows do.
        compact = [_compact_route_row(row) for row in routes[:TOOL_ANSWER_STATUS_ENTRY_MAX]]
        answer["routes"] = (
            compact
            if len(routes) <= TOOL_ANSWER_STATUS_ENTRY_MAX
            else {"count": len(routes), "items": compact, "truncated": True}
        )
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
        tree = knowledge_tree_from_directory(inputs.memory_root)
        parsed = parse_tree(tree)
        report = validate_tree(
            tree,
            bases=[
                knowledge_tree_from_git(inputs.memory_root, base, label=f"base {base}")
                for base in inputs.bases
            ],
            code=CodeDirectory(label=inputs.code_root.as_posix(), root=inputs.code_root),
        )
        # MIK-R73 rule 4: every family of the validated tree, its routes, the
        # mechanical suggestion and whether they differ -- the rows the
        # `agents-remember knowledge-routes` command prints.
        try:
            working_files = working_tree_files(inputs.code_root)
        except KnowledgeTreeReadError:
            # A code root that is not a Git checkout has no working tree to suggest routes from;
            # the validator's report still stands and each route row says so.
            working_files = None
        routes = family_route_documents(parsed, set(), working_files)
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
        "routes": routes,
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


# --- knowledge_write: the mounted writer (MIK-R73 rule 1) ---------------------


@dataclass(frozen=True)
class WriteToolRequest:
    """One ``knowledge_write`` call's arguments as one value.

    ``contract_path`` names a leaf (or a master, with ``crossing``): the contract is the write
    guard, and only the memory worktree it names can be written. ``handoff`` is the hand-off
    document itself (with ``handoff_name``); ``handoff_path`` names one on disk instead. With
    ``repo_id`` and ``wave`` in place of a contract the call writes a taskless wave, as
    ``knowledge-bootstrap`` does.
    """

    contract_path: str | None = None
    handoff: dict[str, Any] | None = None
    handoff_name: str | None = None
    handoff_path: str | None = None
    authorization_ref: str = ""
    commit: bool = False
    crossing: str | None = None
    repo_id: str | None = None
    wave: str | None = None


def _refused_write(code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": "knowledge_write",
        "state": "refused",
        "refusalCode": code,
        "refusalDetail": detail,
    }


def _write_submission(
    request: WriteToolRequest,
) -> tuple[WriteSubmission | None, dict[str, Any] | None]:
    """The submission this call carries, or the refusal its inputs earn before anything runs."""

    if request.handoff is not None and request.handoff_path is not None:
        return None, _refused_write(
            "invalid-input",
            "name the hand-off once: `handoff` (the document) or `handoffPath`, never both",
        )
    if request.handoff is not None:
        document: object = request.handoff
        name = request.handoff_name or "handoff.json"
    elif request.handoff_path is not None:
        path = Path(request.handoff_path)
        document = read_handoff_document(path)
        if isinstance(document, str):
            return None, _refused_write("invalid-input", document)
        name = request.handoff_name or path.name
    else:
        return None, _refused_write(
            "invalid-input",
            "name the hand-off: `handoff` (the document, with `handoffName`) or `handoffPath`",
        )
    return (
        WriteSubmission(
            document=document,
            handoff_path=name,
            authorization_ref=request.authorization_ref,
            commit=request.commit,
        ),
        None,
    )


def _wave_write(
    request: WriteToolRequest, submission: WriteSubmission, config: McpRuntimeConfig
) -> WriteReport | str:
    """The taskless wave route: the admission ``knowledge-bootstrap`` runs, then its write."""

    assert request.repo_id is not None and request.wave is not None
    admitted = admit_bootstrap_context(config, request.repo_id)
    if isinstance(admitted, BootstrapRefusal):
        return f"knowledge_write refuses this wave ({admitted.code}): {admitted.detail}. {admitted.next_action}"
    memory = admitted.admission.memory_worktree
    if memory is None or not is_converted(memory):
        line = "no memory root" if memory is None else memory.as_posix()
        return (
            f"knowledge_write refuses this wave: {line} is not a converted memory tree "
            "(it holds no knowledge/layout.json)"
        )
    return wave_write_report(
        WaveTarget(
            memory_root=memory,
            code_root=admitted.admission.code_worktree,
            wave=request.wave,
            task=admitted.admission.scope,
            coordination_root=admitted.authority.coordination_root,
        ),
        submission,
    )


def _write_route(
    request: WriteToolRequest, submission: WriteSubmission, config: McpRuntimeConfig
) -> WriteReport | str:
    """The report the named route produces, or the refusal that stopped it."""

    if request.contract_path is not None:
        contract = load_leaf_contract(request.contract_path)
        if isinstance(contract, str):
            return contract
        if request.crossing:
            return crossing_write_report(contract, request.crossing, submission)
        if not is_converted(contract.memory_worktree):
            return unconverted_write_refusal(contract)
        return leaf_write_report(contract, submission, config)
    if request.repo_id is not None and request.wave is not None:
        return _wave_write(request, submission, config)
    return (
        "name a leaf by `contractPath`, or a taskless wave by `repoId` with `wave`: "
        "a write is admitted only through the contract that names its memory worktree"
    )


def knowledge_write_payload(request: WriteToolRequest, config: McpRuntimeConfig) -> dict[str, Any]:
    """The mounted `knowledge_write` answer: one envelope around one write outcome."""

    return _tool_payload("knowledge_write", _write_answer(request, config))


def _write_answer(request: WriteToolRequest, config: McpRuntimeConfig) -> dict[str, Any]:
    """Plan or commit one knowledge write through the mounted tool (MIK-R73 rule 1).

    The body is the same function the ``agents-remember knowledge-ingest`` and
    ``knowledge-bootstrap`` commands run, so the tool can do nothing the command cannot: the same
    contract guard, the same authorization reference, the same worklist checks and the same
    refusals. Without ``commit`` it plans and writes nothing; with it, it writes files and commits
    nothing to Git. The whole report is written as a caller-readable file (MIK-R108 rule 3) and the
    answer holds the outcome, the counts, the problems and violations the caller must act on, and
    the report's location.

    Every refusal is one of this function's own returns, so the module keeps one envelope site per
    handler: a refusal is a response document like any other, and the reason it refuses is the
    ``state`` and ``refusalDetail`` the caller branches on.
    """

    submission, refusal = _write_submission(request)
    if refusal is not None:
        return refusal
    assert submission is not None  # a refused submission always carries its refusal
    if not request.authorization_ref.strip():
        # The command refuses a blank authorization before a settings document, a
        # contract or a byte is touched; the tool refuses it at the same place.
        return _refused_write("invalid-input", BLANK_AUTHORIZATION)
    report = _write_route(request, submission, config)
    if isinstance(report, str):
        return _refused_write("invalid-input", report)
    document = report.to_document()
    report_path, report_error = _write_report_path(document, config)
    problems = [{"where": one["where"], "message": one["message"]} for one in document["problems"]]
    violations = list(document["violations"])
    answer = {
        "ok": True,
        "operation": "knowledge_write",
        "state": document["state"],
        "owner": document["owner"],
        "memoryRoot": document["memoryRoot"],
        "codeTree": document["codeTree"],
        "authorization": document["authorization"],
        "counts": {
            "records": len(document["records"]),
            "entries": len(document["entries"]),
            "historyRows": len(document["historyRows"]),
            "evidence": len(document["evidence"]),
            "carried": len(document["carried"]),
            "requirementEndpoints": len(document["requirementEndpoints"]),
        },
        "problems": problems[:TOOL_ANSWER_LIST_MAX],
        "problemsTruncated": len(problems) > TOOL_ANSWER_LIST_MAX,
        "violations": violations[:TOOL_ANSWER_LIST_MAX],
        "violationsTruncated": len(violations) > TOOL_ANSWER_LIST_MAX,
        "provisionalIds": document["provisionalIds"],
        "plannedBeforeRefusal": document["plannedBeforeRefusal"],
        "written": document["written"],
        "removed": document["removed"],
        "reportPath": report_path,
        "reportError": report_error,
    }
    return answer


def _write_report_path(
    document: dict[str, Any], config: McpRuntimeConfig
) -> tuple[str | None, str | None]:
    """Where the whole write report landed, and why it did not when it could not be written.

    A report that cannot be written is named in the answer beside the outcome instead of replacing
    it: the write itself already happened or did not, and the caller must not lose that fact to a
    filesystem error about the report.
    """

    try:
        return write_report("knowledge-write", document, config=config).as_posix(), None
    except ReportWriteError as error:
        return None, f"the report cannot be written ({error.reason}); nothing about it is claimed"
