"""Payload builders for the five mounted ``knowledge_*`` operation families.

One builder per operation, each of which validates its wire request, delegates to the application
seam that owns the operation, and returns the typed shape the response model declares. Nothing here
decides anything: no classification is computed, no effect label is inferred, no draft is authored,
no rationale is judged, no ambiguity is resolved by choosing, no missing assessment is filled, and no
compatibility verdict is produced. Where a handler would have to decide something, it returns the
unresolved state instead.

Two of the five are quoted at requirement level and their builders are the reason this module is
short:

* ``knowledge_read`` returns "recorded claims and assessments as attributed records"; the payload it
  returns is the view payload itself, so the classification rule has exactly one implementation.
* ``knowledge_diff`` includes "semantic effect labels ... only when supplied by an identified
  agent/assessment, not inferred from the diff"; the builder collects the labels the comparison
  already carries and never derives one.

**Every public builder here returns ``_tool_payload(...)``, like every other adapter module.**
The five builders used to hand a raw ``dict`` straight to the transport: the registered response
models (``models/tools/knowledge_responses.py``) were therefore never met by a payload, the
registration-agreement check found five registered models with no adapter entry, and the
choke-point sweep counted 19 modules instead of 20. The body each builder produces is unchanged and
now lives in one private ``*_result`` helper; what the public name does is exactly what every other
module's entry point does -- route the body through the choke point so it is validated against
``TOOL_RESPONSE_MODELS["knowledge_*"]`` before it reaches the wire.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw
from pydantic import ValidationError

from agents_remember.application.knowledge_diff import diff_knowledge_scope
from agents_remember.application.knowledge_projection import (
    ProjectionOptions,
    project_knowledge,
)
from agents_remember.application.knowledge_proofs import tree_view_proofs
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import (
    VIEW_RENDERER_VERSION,
    read_knowledge_view,
)
from agents_remember.application.knowledge_worklist.surface import leaf_worklist_fields
from agents_remember.application.published_intent import (
    SelectedKnowledgeDataset,
    memory_tree_block,
    select_knowledge_dataset,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.detection import (
    detection_input_digest,
    detection_input_identity,
    read_detection_run,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory.knowledge_index import IndexMismatchError, MemoryTreeError
from agents_remember.models.knowledge.diff import KnowledgeDiffRequest
from agents_remember.models.knowledge.projection_manifest import DestinationProfile
from agents_remember.models.knowledge.view import (
    ViewRefusal,
    ViewRequest,
    rebuild_continuation,
    require_admitted_ordering_input,
)

from .base import _tool_payload

__all__ = [
    "ChangeToolRequest",
    "DiffToolRequest",
    "IntegrityCheckRequest",
    "ProjectToolRequest",
    "ReadToolRequest",
    "knowledge_change_payload",
    "knowledge_diff_payload",
    "knowledge_integrity_check_payload",
    "knowledge_project_payload",
    "knowledge_read_payload",
]

# The record kinds this surface is asked about, and the answer every one of them earns.
#
# This mounted surface does NOT write. It is a read/render/projection surface: ``knowledge_read``,
# ``knowledge_diff``, ``knowledge_integrity_check`` and ``knowledge_project`` all answer from a
# dataset the caller names, and no handler here opens the write path. The knowledge write plane has
# ONE writer — the admitted batch operation ``knowledge_change``'s refusal names — and BOTH shipped
# CLI entry points reach it: the ``agents-remember knowledge-ingest`` subcommand for a leaf
# enclosure's ordinary route, and the ``agents-remember knowledge-bootstrap`` subcommand for a
# repository with no enclosure in scope. A second write seam here would be the authority this packet
# forbids the surface to add.
#
# The earlier spelling advertised two "admitted" kinds and then refused both anyway, which made the
# tool's own description false and told the caller to supply an input its published signature cannot
# carry. Both are removed: the set below is what the surface may be *asked* for, the refusal is
# unconditional for every member of it, and the reason names where the write actually happens.
DECLARED_CHANGE_KINDS: tuple[str, ...] = (
    "evidence_claim",
    "verification_observation",
    "invariant_revision",
    "assumption",
    "semantic_change_set",
    "requirement_revision",
)

# The curator-list write plane's two shipped CLI entry points, named so a refusal is actionable
# rather than a dead end. One writer sits behind both -- ``ingest_curator_list``, reached from
# ``cli/knowledge_ingest.py`` and ``cli/knowledge_bootstrap.py`` -- so both are named wherever a
# model is told where the write plane is reachable. Kept as constants because the refusal detail
# and a test quote them.
WRITE_ENTRY_POINT = "agents-remember knowledge-ingest"
TASKLESS_WRITE_ENTRY_POINT = "agents-remember knowledge-bootstrap"

# The two shapes a source-resolution half can take, and the bound on how long resolving one may
# take: a Git call that hangs must not hold a mounted read open.
_TREE_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_GIT_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ReadToolRequest:
    """One ``knowledge_read`` call's arguments as one value.

    The tool signature stays flat because FastMCP derives the published input schema from it; this
    value is what the builder consumes, so the builder itself is one argument wide and the flat wire
    shape is preserved without a widened exemption.

    ``database_path`` and ``repository_id`` are the *selection*, and the caller owns both. Nothing
    this server holds can answer either one: the runtime config carries no knowledge database or
    namespace field, a repository's coordination declaration (settings, ``context_packet``) names the
    code root, the memory root and the coordination paths, and no released helper resolves a
    repository or a task to a knowledge SQLite path. The published schema makes them required rather
    than optional-with-a-default precisely because a default would be this surface inventing a
    selection. So a cold planner that has not been told the pair cannot discover it here; it is
    supplied by the party that created or holds the dataset, exactly as ``databasePath`` is supplied
    to every other ``knowledge_*`` operation.
    """

    database_path: str
    repository_id: str
    view: str
    ordering_input: str = "stable_ordering"
    limit: int = 32
    continuation: str | None = None
    invariant_revision_id: str | None = None
    family_revision_id: str | None = None
    source_path: str | None = None
    repository_root: str | None = None
    code_tree_id: str | None = None


@dataclass(frozen=True)
class ChangeToolRequest:
    """One ``knowledge_change`` call's arguments as one value."""

    database_path: str
    repository_id: str
    record_kind: str
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiffToolRequest:
    """One ``knowledge_diff`` call's arguments as one value."""

    database_path: str
    repository_id: str
    before_path: str
    after_path: str
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProjectToolRequest:
    """One ``knowledge_project`` call's arguments as one value."""

    database_path: str
    repository_id: str
    destination_root: str
    profile_id: str = "default"
    formats: tuple[str, ...] = ("markdown",)
    views: tuple[dict[str, Any], ...] = ()
    authorized_overwrites: tuple[str, ...] = ()


def _refused_read(view: str, repository_id: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "view": view,
        "repositoryId": repository_id,
        "refusalCode": code,
        "refusalDetail": detail,
    }


def _unusable_dataset(database_path: str, error: BaseException) -> tuple[str, str]:
    """The refusal code and detail one failure to open or read a dataset earns.

    Three facts, and they are different facts: a path that is not a file at all is an *absent
    selection*, a database SQLite refuses to read (a directory, an empty file, a file that is not a
    database, a file another process holds) is an *unusable snapshot*, and anything else that
    surfaces from the file system is unreadable input. Answering any of them with an exception
    would make the same input a typed refusal on the ``read_knowledge_scope`` seam and a traceback
    on this one, which is the divergence the family's own docstring forbids.

    The codes are the shipped literals and not surface-local spellings: a caller branches on
    ``selected_input_unavailable`` or ``snapshot_unavailable`` exactly as it does against the
    application seam.
    """

    path = Path(database_path)
    if not path.is_file():
        return (
            "selected_input_unavailable",
            f"the selected knowledge database is absent or is not a file: {path}",
        )
    if isinstance(error, KnowledgeStorageError):
        return (
            "snapshot_unavailable",
            f"the selected snapshot is not this dataset: {error} (at {path})",
        )
    return (
        "snapshot_unavailable",
        f"the selected snapshot could not be read: {error} (at {path})",
    )


def _select(path: str, coordination_root: str | None) -> SelectedKnowledgeDataset:
    """Resolve one caller-selected dataset path: a converted memory tree reads through its index.

    ``databasePath`` keeps its published meaning -- the dataset a read opens -- and gains one
    resolution (MIK-R23 rule 6): a path naming a converted memory tree (its root, or the published
    ``knowledge.sqlite`` location inside it) is read through the index of that tree's current
    state, and the response names the tree and the index state. Every other path is opened as
    before, so an unconverted tree keeps today's database selection and refusals.
    """

    return select_knowledge_dataset(
        Path(path), coordination_root=None if coordination_root is None else Path(coordination_root)
    )


# Every way resolving and opening a selection can fail, so each handler refuses the same inputs
# the same way: an index that cannot be built (the tree, its Git objects, the cache) is
# ``snapshot_unavailable`` naming the tree; everything else is the dataset refusal it was before.
_SELECTION_FAILURES = (
    IndexMismatchError,
    MemoryTreeError,
    GitPreparationError,
    KnowledgeStorageError,
    apsw.Error,
    OSError,
)


def _selection_refusal(path: str, error: BaseException) -> tuple[str, str]:
    if isinstance(error, MemoryTreeError | GitPreparationError):
        return ("snapshot_unavailable", f"the selected memory tree could not be indexed: {error}")
    return _unusable_dataset(path, error)


def _index_complete(*selected: SelectedKnowledgeDataset) -> bool | None:
    """``False`` when any side was read from a partial index, ``True`` when all were complete.

    ``None`` when no side is a memory tree: a database has no index state. A partial index is never
    presented as complete (MIK-R23, Failure), so every surface that states completeness is forced
    to ``False`` by it.
    """

    trees = [item.memory_tree for item in selected if item.memory_tree is not None]
    if not trees:
        return None
    return all(tree.index_state == "complete" for tree in trees)


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
    root without a tree gets that root's own current tree, resolved from it.
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
    return root, _current_code_tree(root)


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
) -> dict[str, Any]:
    """Retrieve one named view at one snapshot, through the response-model choke point."""

    return _tool_payload(
        "knowledge_read",
        _read_result(request, workspace_root=workspace_root, coordination_root=coordination_root),
    )


def _read_result(
    request: ReadToolRequest,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """The one ``knowledge_read`` body, before the registered model validates it."""

    databasePath, repositoryId, view = (
        request.database_path,
        request.repository_id,
        request.view,
    )
    path = Path(databasePath)
    if view not in (
        "source_context",
        "invariant",
        "family",
        "review_matrix",
        "curation_queue",
    ):
        return _refused_read(
            view,
            repositoryId,
            "unknown_view",
            f"{view!r} is not one of the five named query views",
        )
    # The admitted-ordering closure is checked **before** the dataset is opened, because the view
    # layer's own refusal is the answer the caller needs and the request model cannot carry the
    # unadmitted spelling to it: ``ViewRequest.ordering_input`` is a ``Literal``, so constructing the
    # request with a fifth ordering raises instead of refusing, and the caller received a tool error
    # where §2.5 promises ``unadmitted_ordering_input`` with the offending input. Asking the view
    # module's one closure check first is what keeps the ordering refusal reachable from this surface
    # (adversarial coverage review finding ``A-2``; the refusal itself is L20's, unchanged).
    ordering_refusal = require_admitted_ordering_input(request.ordering_input)
    if ordering_refusal is not None:
        return _refused_read(view, repositoryId, ordering_refusal.code, ordering_refusal.detail)
    # The dataset is opened inside the boundary, because every failure to open or read it is a fact
    # about the selection and not a programming error: the application seam this builder delegates
    # to already turns all three into typed refusals, and a transport that let them escape as
    # ``ToolError`` would refuse the same input on one surface and raise on another.
    try:
        selected = _select(databasePath, coordination_root)
        path = selected.database_path
        repository_root, code_tree_id = _source_resolution(request, workspace_root)
        context = open_read_context(
            path,
            repositoryId,
            repository_root=None if repository_root is None else Path(repository_root),
            code_tree_id=code_tree_id,
        )
        built = _view_request(request)
        if isinstance(built, ViewRefusal):
            return _refused_read(view, repositoryId, built.code, built.detail)
        result = read_knowledge_view(path, context, built)
        proofs = (
            None
            if selected.memory_tree is None or result.state == "refused"
            else tree_view_proofs(selected.database_path, selected.memory_tree.tree_key, request)
        )
    except _SELECTION_FAILURES as error:
        return _refused_read(view, repositoryId, *_selection_refusal(str(path), error))
    if result.state == "refused" or result.payload is None:
        assert result.refusal is not None
        return _refused_read(view, repositoryId, result.refusal.code, result.refusal.detail)
    payload = result.payload
    body = payload.model_dump(mode="json")
    complete = payload.completeness.complete_within_declared_scope
    if _index_complete(selected) is False:
        # The view is complete within what the index holds, and the index is not the whole tree.
        complete = False
        body["completeness"]["complete_within_declared_scope"] = False
    return {
        "ok": True,
        "state": "view",
        "view": payload.view,
        "repositoryId": repositoryId,
        "snapshot": payload.snapshot.logical_digest,
        "completeWithinDeclaredScope": complete,
        "continuation": None if payload.continuation is None else payload.continuation.token,
        "payload": body,
        "memoryTree": memory_tree_block(selected.memory_tree),
        "indexComplete": _index_complete(selected),
        "proofs": proofs,
    }


def _view_request(request: ReadToolRequest) -> ViewRequest | ViewRefusal:
    """The validated view request one tool call describes, with its continuation rebuilt.

    A continuation token crosses this surface as one opaque string, and the binding it carries is
    read back out of it here: this call knows the view a caller asked for, but not the snapshot the
    token was minted at, and a snapshot identity invented at the transport would refuse every
    continuation this surface had just returned. Text this substrate did not mint is refused as
    ``continuation_unreadable`` rather than presented to the seam as a position.
    """

    token = None
    if request.continuation is not None:
        rebuilt = rebuild_continuation(request.continuation, view=request.view)
        if isinstance(rebuilt, ViewRefusal):
            return rebuilt
        token = rebuilt
    return ViewRequest(
        view=request.view,  # type: ignore[arg-type]
        repository_id=request.repository_id,
        invariant_revision_id=request.invariant_revision_id,
        family_revision_id=request.family_revision_id,
        source_path=request.source_path,
        ordering_input=request.ordering_input,  # type: ignore[arg-type]
        limit=request.limit,
        continuation=token,
    )


def knowledge_change_payload(request: ChangeToolRequest) -> dict[str, Any]:
    """Refuse one mount-side change request, through the response-model choke point.

    This surface records nothing. It has no admitted write operation for any kind, so every kind --
    declared here or not -- is refused as ``registration_absent``, and the refusal names the writer
    that can record: :func:`agents_remember.application.knowledge_curator_ingest.
    ingest_curator_list`, the one batch operation that resolves a whole hand-off list and commits it
    through the admitted batch. Two shipped CLI subcommands reach it -- ``agents-remember
    knowledge-ingest`` for a leaf enclosure's ordinary route and ``agents-remember
    knowledge-bootstrap`` for a repository with no enclosure in scope -- and both are named so a
    caller is not pointed at half the route.

    The reason is deliberately the *same* for a declared kind and for one this surface has never
    heard of: the surface has nothing to add in either case, and two spellings of "this tool does
    not write" would suggest the first one might.
    """

    return _tool_payload("knowledge_change", _change_result(request))


def _change_result(request: ChangeToolRequest) -> dict[str, Any]:
    """The one ``knowledge_change`` body, before the registered model validates it."""

    repositoryId, recordKind = request.repository_id, request.record_kind
    return {
        "ok": True,
        "state": "refused",
        "recordKind": recordKind,
        "repositoryId": repositoryId,
        "refusalCode": "registration_absent",
        "refusalDetail": (
            f"this mounted surface does not write, so no {recordKind!r} row was written and no "
            "destination is missing: the knowledge write plane has one writer, and both of its "
            f"shipped CLI entry points reach it -- {WRITE_ENTRY_POINT!r} for a leaf enclosure's "
            f"ordinary route and {TASKLESS_WRITE_ENTRY_POINT!r} for a repository with no enclosure "
            "in scope. On a converted memory tree (knowledge/layout.json) both write knowledge "
            "files through the curator file writer (MIK-R12) instead of the database. Call one of "
            "those, or read the result here with knowledge_read"
        ),
    }


def knowledge_diff_payload(
    request: DiffToolRequest, *, coordination_root: str | None = None
) -> dict[str, Any]:
    """Compare two exact states, through the response-model choke point."""

    return _tool_payload(
        "knowledge_diff", _diff_result(request, coordination_root=coordination_root)
    )


def _diff_result(
    request: DiffToolRequest, *, coordination_root: str | None = None
) -> dict[str, Any]:
    """The one ``knowledge_diff`` body, before the registered model validates it.

    Only the semantic labels an identified source supplied are carried; the builder never derives
    one from the change.
    """

    repositoryId = request.repository_id
    supplied = _supplied_effect_labels(request.body)
    try:
        built = _diff_request(request.body)
    except ValidationError as invalid:
        # A body the shipped request model refuses is a caller error this surface can *name*: the
        # validation message lists the exact missing or malformed field, and returning it as a typed
        # refusal keeps the operation's contract ("the comparison refused, here is why") instead of
        # converting it into a transport-level tool failure.
        return {
            "ok": True,
            "state": "refused",
            "repositoryId": repositoryId,
            "refusalCode": "invalid_payload",
            "refusalDetail": (
                "the comparison body is not a valid KnowledgeDiffRequest: "
                f"{invalid.error_count()} validation error(s); {invalid}"
            ),
        }
    try:
        before = _select(request.before_path, coordination_root)
        after = _select(request.after_path, coordination_root)
        result = diff_knowledge_scope(
            built,
            before_path=before.database_path,
            after_path=after.database_path,
        )
    except _SELECTION_FAILURES as error:
        code, detail = _selection_refusal(request.before_path, error)
        return {
            "ok": True,
            "state": "refused",
            "repositoryId": repositoryId,
            "refusalCode": code,
            "refusalDetail": detail,
        }
    if result.state == "refused":
        return {
            "ok": True,
            "state": "refused",
            "repositoryId": repositoryId,
            "refusalCode": None if result.refusal is None else result.refusal.code,
            "refusalDetail": None if result.refusal is None else result.refusal.detail,
        }
    return {
        "ok": True,
        "state": "compared",
        "repositoryId": repositoryId,
        "semanticEffectLabels": supplied,
        "payload": result.model_dump(mode="json"),
        "memoryTrees": _memory_trees(before, after),
        "indexComplete": _index_complete(before, after),
    }


def _memory_trees(
    before: SelectedKnowledgeDataset, after: SelectedKnowledgeDataset
) -> dict[str, Any] | None:
    """The memory-tree binding of each side read through an index, or ``None`` for two databases."""

    sides = {
        side: memory_tree_block(selected.memory_tree)
        for side, selected in (("before", before), ("after", after))
        if selected.memory_tree is not None
    }
    return sides or None


def _diff_request(request: dict[str, Any] | None) -> Any:
    """The shipped diff request one tool call describes, taken from the caller's own validated body.

    The namespace is **not** written into the body here. ``KnowledgeDiffRequest`` declares no
    ``repository_id`` and its base model forbids undeclared fields, so injecting one made every call
    of this tool raise ``validation error for KnowledgeDiffRequest: repository_id -- Extra inputs are
    not permitted`` before any comparison ran. Nothing was lost with the injection: a comparison's
    namespace is named twice already, by the two sides' own ``KnowledgeReadContext``, each of which
    carries the ``repository_id`` its snapshot must belong to. The regression this case exists for is
    the adversarial coverage review's finding ``A-2``: the family had a roster row and no functional
    case, which is what let a body that could never validate ship as a mounted operation.
    """

    return KnowledgeDiffRequest.model_validate(dict(request or {}))


def _supplied_effect_labels(request: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The effect labels an identified agent or assessment supplied, and nothing else.

    The builder reads them from the caller's own body. It never derives one from the change, because
    "Semantic effect labels are included only when supplied by an identified agent/assessment, not
    inferred from the diff" is the whole of what this operation promises.
    """

    body = request or {}
    supplied = body.get("semantic_effect_labels") or body.get("semanticEffectLabels") or []
    labels: list[dict[str, Any]] = []
    for entry in supplied:
        if isinstance(entry, dict) and entry.get("supplied_by"):
            labels.append(dict(entry))
    return labels


@dataclass(frozen=True)
class IntegrityCheckRequest:
    """One ``knowledge_integrity_check`` call's inputs, as the registered tool received them."""

    databasePath: str | None = None
    repositoryId: str | None = None
    scopeId: str | None = None
    runId: str | None = None
    inputDigest: str | None = None
    contractPath: str | None = None


def knowledge_integrity_check_payload(request: IntegrityCheckRequest) -> dict[str, Any]:
    """Report declared structural-rule violations and their limits, through the choke point.

    ``contractPath`` names a leaf by its series contract and adds the leaf's latest persisted
    MIK-R08 worklist (:func:`leaf_worklist_fields`). A dataset (``databasePath`` with
    ``repositoryId``) and a leaf may be named together or alone; naming neither is refused.
    """

    if request.databasePath is not None and request.repositoryId is not None:
        result = _integrity_check_result(
            databasePath=request.databasePath,
            repositoryId=request.repositoryId,
            scopeId=request.scopeId,
            runId=request.runId,
            inputDigest=request.inputDigest,
        )
    elif request.contractPath is not None and request.databasePath is None:
        result = {
            "ok": True,
            "state": "reported",
            "repositoryId": request.repositoryId,
            "compatible": None,
        }
    else:
        result = {
            "ok": True,
            "state": "refused",
            "repositoryId": request.repositoryId,
            "refusalCode": "selected_input_unavailable",
            "refusalDetail": (
                "name a knowledge dataset (databasePath with repositoryId), a leaf "
                "(contractPath), or both"
            ),
            "compatible": None,
        }
    if request.contractPath is not None:
        result.update(leaf_worklist_fields(request.contractPath))
    return _tool_payload("knowledge_integrity_check", result)


def _integrity_check_result(
    *,
    databasePath: str,
    repositoryId: str,
    scopeId: str | None = None,
    runId: str | None = None,
    inputDigest: str | None = None,
) -> dict[str, Any]:
    """The one ``knowledge_integrity_check`` body, before the registered model validates it.

    ``compatible`` is ``None`` and is present. A caller that wants a compatibility decision makes it;
    this operation reports conditions, a traversal scope and the observable limitations of the read,
    which is what ``Doc13:186`` writes for it.

    The scope selects a run and never borrows another scope's run. Within one scope a namespace may
    hold several runs -- the same policy re-executed over a moved snapshot is exactly that case --
    so the exact ``runId`` or the exact ``inputDigest`` a caller names selects among them, and the
    response always carries the selected run's identity, its input identity and the digest over it.
    A caller is never left holding conditions it cannot tie to the inputs they were measured over.
    """

    # The report is a read of a dataset the caller selected, so a selection that cannot be read is
    # reported as a refusal with the same shipped code the application seam uses, rather than
    # escaping as ``ToolError`` -- a report about a file nobody could open is not a report.
    try:
        conditions = _recorded_conditions(
            Path(databasePath),
            repositoryId,
            scopeId,
            selector=_ExactInputSelector(run_id=runId, input_digest=inputDigest),
        )
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        code, detail = _unusable_dataset(databasePath, error)
        return {
            "ok": True,
            "state": "refused",
            "repositoryId": repositoryId,
            "refusalCode": code,
            "refusalDetail": detail,
            "compatible": None,
        }
    return {
        "ok": True,
        "state": "reported",
        "repositoryId": repositoryId,
        "conditions": conditions["conditions"],
        "traversalScope": conditions["scope"],
        "selectedRunId": conditions["selectedRunId"],
        "inputDigest": conditions["inputDigest"],
        "inputIdentities": conditions["inputIdentities"],
        "matchingRunIds": conditions["matchingRunIds"],
        "exactInputSelector": conditions["exactInputSelector"],
        "limitations": conditions["limitations"],
        "compatible": None,
        "assessment": None,
        "unresolved": conditions["unresolved"],
    }


@dataclass(frozen=True)
class _ExactInputSelector:
    """The exact run selector a caller named, as one value.

    ``runId`` and ``inputDigest`` are two spellings of one request -- "report the run with this
    identity", "report the run measured over these inputs" -- so they travel together and are echoed
    together. A pair of ``None``s is a fact worth reporting rather than an absence: it says the run
    below was selected by scope alone, and that ``matchingRunIds`` is where a narrower request would
    come from.
    """

    run_id: str | None = None
    input_digest: str | None = None

    def as_wire(self) -> dict[str, Any] | None:
        """The selector as the response spells it, or ``None`` when the caller named none."""

        if self.run_id is None and self.input_digest is None:
            return None
        return {"runId": self.run_id, "inputDigest": self.input_digest}


def _recorded_conditions(
    database_path: Path,
    repository_id: str,
    scope_id: str | None,
    *,
    selector: _ExactInputSelector | None = None,
) -> dict[str, Any]:
    """The recorded detection conditions for the requested scope and exact inputs, with their limits.

    The scope SELECTS the run; it is not echoed beside one. Before this, every ``detection_run``
    record was read in ``record_id`` order and the first was reported, whatever scope the caller
    asked for -- so with more than one run recorded the answer was whichever run happened to sort
    first, while the response still named the requested scope. A caller reading "scope X" beside
    conditions measured over scope Y was told something false about a real dataset.

    The run's own ``governing_route_id`` is the registered traversal scope the tool documents, so the
    match is against that. Within the selected scope, an exact ``run_id`` or ``input_digest`` picks
    one run out of several instead of the report silently choosing the first identity-sorted match
    and omitting which one it read. A caller that names no scope keeps the previous behaviour -- the
    first recorded run -- and a caller whose selection matches no run is told so, with the runs that
    did match carried back, rather than handed a different run's conditions.
    """

    resolved = selector or _ExactInputSelector()
    connection = open_read_only_database(database_path)
    try:
        rows = tuple(
            connection.execute(
                "SELECT record_id FROM knowledge_record WHERE repository_id = ? AND kind = ? "
                "ORDER BY record_id",
                (repository_id, "detection_run"),
            )
        )
        if not rows:
            return _no_detection_run(scope_id, resolved)
        store = OpenedKnowledgeStore(
            database_path=database_path,
            repository_id=repository_id,
            schema=inspect_schema(connection),
            connection=connection,
            resource_lock_path=database_path.with_name(f"{database_path.name}.lock"),
        )
        run_ids = tuple(str(row[0]) for row in rows)
        result = _run_for_scope(store, run_ids, scope_id, selector=resolved)
        matching = _runs_in_scope(store, run_ids, scope_id)
        if result is None:
            return _no_run_for_scope(scope_id, resolved, matching)
    finally:
        connection.close()
    return _condition_report(result, scope_id, resolved, matching)


def _no_detection_run(scope_id: str | None, selector: _ExactInputSelector) -> dict[str, Any]:
    """The honest report when no detection run is recorded: no conditions and a stated limit."""

    return _report_facts(
        scope=scope_id or "registered",
        selector=selector,
        matching_runs=[],
        limitations=["no detection run is recorded for this namespace at this snapshot"],
        unresolved=["no recorded detection run to report conditions from"],
    )


def _run_for_scope(
    store: OpenedKnowledgeStore,
    run_ids: Iterable[str],
    scope_id: str | None,
    *,
    selector: _ExactInputSelector | None = None,
) -> Any:
    """The first recorded run this request selects, or ``None`` when nothing matches.

    With no scope named, the first recorded run is the answer -- the behaviour this operation always
    had, kept so a caller that does not scope its request is not newly refused. With one named, a run
    measured over a different scope is not a weaker answer but the wrong one, so the search
    continues and a namespace with no matching run reports that instead.

    An exact ``run_id`` or ``input_digest`` is applied as well, and unlike the scope it *is* a
    binding: a run whose recorded identity or input digest does not match the caller's is a
    different execution, so the search continues past it and a request that names inputs nothing was
    measured over reports no run rather than the first one that shares a scope.
    """

    resolved = selector or _ExactInputSelector()
    for candidate_id in run_ids:
        result = read_detection_run(store, candidate_id)
        run = result.run
        if scope_id is not None and (run is None or run.governing_route_id != scope_id):
            continue
        if resolved.run_id is not None and candidate_id != resolved.run_id:
            continue
        if resolved.input_digest is not None and (
            run is None or detection_input_digest(run) != resolved.input_digest
        ):
            continue
        return result
    return None


def _runs_in_scope(
    store: OpenedKnowledgeStore, run_ids: Iterable[str], scope_id: str | None
) -> list[dict[str, Any]]:
    """Every recorded run the requested scope selects, as identity facts for the caller."""

    matching: list[dict[str, Any]] = []
    for candidate_id in run_ids:
        result = read_detection_run(store, candidate_id)
        run = result.run
        if run is None:
            continue
        if scope_id is not None and run.governing_route_id != scope_id:
            continue
        matching.append(_run_identity(run))
    return matching


def _run_identity(run: Any) -> dict[str, Any]:
    """One run's identity facts: its own id, its registered scope and the digest of its inputs."""

    return {
        "runId": run.run_id,
        "governingRouteId": run.governing_route_id,
        "inputDigest": detection_input_digest(run),
    }


def _no_run_for_scope(
    scope_id: str | None,
    selector: _ExactInputSelector,
    matching_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """The honest report when no recorded run matches the requested scope and exact inputs."""

    detail = (
        f"no recorded detection run measured the requested scope {scope_id!r}; the conditions "
        "of another scope's run are not reported in its place"
        if scope_id is not None
        else "no recorded detection run is recorded for this namespace"
    )
    return _report_facts(
        scope=scope_id or "registered",
        selector=selector,
        matching_runs=matching_runs,
        limitations=[detail],
        unresolved=[
            "no recorded detection run matches the requested scope and exact inputs; the carried "
            "matchingRunIds list names the runs this scope does hold"
        ],
    )


def _report_facts(
    *,
    scope: str,
    selector: _ExactInputSelector,
    matching_runs: list[dict[str, Any]],
    limitations: list[str],
    unresolved: list[str | None],
) -> dict[str, Any]:
    """One report's common facts: the scope, the exact selector echoed, and what matched."""

    return {
        "conditions": [],
        "scope": scope,
        "selectedRunId": None,
        "inputDigest": None,
        "inputIdentities": [],
        "matchingRunIds": matching_runs,
        "exactInputSelector": selector.as_wire(),
        "limitations": limitations,
        "unresolved": unresolved,
    }


def _condition_report(
    result: Any,
    scope_id: str | None,
    selector: _ExactInputSelector,
    matching_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """The recorded conditions and limitations of one detection run, and no verdict.

    The run the conditions came from is named here, beside the input identity and the digest over
    it, so "these conditions" and "the inputs they were measured over" are one answer. Before this
    the response omitted all three, which left a caller unable to tell a report about its own
    candidate from a report about another run in the same scope.

    ``matchingRunIds`` carries every run this scope holds, the selected one included, so a caller
    that received the first identity-sorted match can see that it was one of several and issue an
    exact request from the response it already has.
    """

    if result.state == "refused" or result.run is None:
        return _report_facts(
            scope=scope_id or "registered",
            selector=selector,
            matching_runs=matching_runs,
            limitations=["the recorded detection run could not be read"],
            unresolved=[None if result.refusal is None else result.refusal.detail],
        )
    conditions = [
        {"code": signal.condition, "matched_facts": [signal.detail]} for signal in result.signals
    ]
    return {
        "conditions": conditions,
        "scope": result.run.governing_route_id,
        "selectedRunId": result.run.run_id,
        "inputDigest": detection_input_digest(result.run),
        "inputIdentities": [detection_input_identity(result.run)],
        "matchingRunIds": matching_runs,
        "exactInputSelector": selector.as_wire(),
        "limitations": list(result.run.limitations),
        "unresolved": [] if conditions else ["no condition matched the recorded scope"],
    }


def knowledge_project_payload(
    request: ProjectToolRequest, *, coordination_root: str | None = None
) -> dict[str, Any]:
    """Render named read-only views into an explicitly authorized destination, through the choke point."""

    return _tool_payload(
        "knowledge_project", _project_result(request, coordination_root=coordination_root)
    )


def _project_result(
    request: ProjectToolRequest, *, coordination_root: str | None = None
) -> dict[str, Any]:
    """The one ``knowledge_project`` body, before the registered model validates it."""

    destinationRoot = request.destination_root
    profile = DestinationProfile(
        profile_id=request.profile_id,
        destination_root=destinationRoot,
        formats=request.formats or ("markdown",),  # type: ignore[arg-type]
        renderer_version=VIEW_RENDERER_VERSION,
    )
    requests = _projection_requests(request.repository_id, request.views)
    if not requests:
        return _refused_project(
            destinationRoot,
            "unresolved_projection_input",
            "no view was named to project, so no artifact is emitted",
        )
    try:
        selected = _select(request.database_path, coordination_root)
    except _SELECTION_FAILURES as error:
        return _refused_project(destinationRoot, *_selection_refusal(request.database_path, error))
    report = project_knowledge(
        selected.database_path,
        profile,
        requests,
        ProjectionOptions(authorized_overwrites=request.authorized_overwrites),
    )
    if report.state == "refused" or report.refusal is not None:
        assert report.refusal is not None
        return _refused_project(destinationRoot, report.refusal.code, report.refusal.detail)
    return {
        "ok": True,
        "state": "projected",
        "destinationRoot": report.destination_root,
        "rendererVersion": report.renderer_version,
        "manifestGeneration": report.manifest_generation,
        "published": [
            outcome.destination_relative_path
            for outcome in report.outcomes
            if outcome.state in ("published", "unchanged")
        ],
        "retained": [entry.model_dump(mode="json") for entry in report.retained],
        "discrepancies": [entry.model_dump(mode="json") for entry in report.discrepancies],
        "memoryTree": memory_tree_block(selected.memory_tree),
        "indexComplete": _index_complete(selected),
    }


def _projection_requests(
    repository_id: str, views: tuple[dict[str, Any], ...]
) -> tuple[tuple[ViewRequest, str], ...]:
    """One view request per named projection input, each with the identity it is about."""

    requests: list[tuple[ViewRequest, str]] = []
    for entry in views:
        view = str(entry.get("view", ""))
        subject = str(entry.get("subject", view))
        requests.append(
            (
                ViewRequest(
                    view=view,  # type: ignore[arg-type]
                    repository_id=repository_id,
                    invariant_revision_id=entry.get("invariantRevisionId"),
                    family_revision_id=entry.get("familyRevisionId"),
                    ordering_input=str(entry.get("orderingInput", "stable_ordering")),  # type: ignore[arg-type]
                ),
                subject,
            )
        )
    return tuple(requests)


def _refused_project(destination_root: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "destinationRoot": destination_root,
        "rendererVersion": VIEW_RENDERER_VERSION,
        "refusalCode": code,
        "refusalDetail": detail,
    }
