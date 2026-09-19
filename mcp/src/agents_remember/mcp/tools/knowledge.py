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
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import (
    VIEW_RENDERER_VERSION,
    read_knowledge_view,
)
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.detection import read_detection_run
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
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
# dataset the caller names, and no handler here opens the write path. The knowledge write plane's
# reachable production entry point is the ``agents-remember knowledge-ingest`` CLI subcommand,
# which calls the admitted operation directly; a second write seam here would be the authority this
# packet forbids the surface to add.
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

# The one file this surface can resolve a whole write from, named so a refusal is actionable rather
# than a dead end. Kept as a constant because two detail strings and a test quote it.
WRITE_ENTRY_POINT = "agents-remember knowledge-ingest"


@dataclass(frozen=True)
class ReadToolRequest:
    """One ``knowledge_read`` call's arguments as one value.

    The tool signature stays flat because FastMCP derives the published input schema from it; this
    value is what the builder consumes, so the builder itself is one argument wide and the flat wire
    shape is preserved without a widened exemption.
    """

    database_path: str
    repository_id: str
    view: str
    ordering_input: str = "stable_ordering"
    limit: int = 32
    continuation: str | None = None
    invariant_revision_id: str | None = None
    family_revision_id: str | None = None
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


def knowledge_read_payload(request: ReadToolRequest) -> dict[str, Any]:
    """Retrieve one named view at one snapshot, through the response-model choke point."""

    return _tool_payload("knowledge_read", _read_result(request))


def _read_result(request: ReadToolRequest) -> dict[str, Any]:
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
        context = open_read_context(
            path,
            repositoryId,
            repository_root=(
                None if request.repository_root is None else Path(request.repository_root)
            ),
            code_tree_id=request.code_tree_id,
        )
        built = _view_request(request)
        if isinstance(built, ViewRefusal):
            return _refused_read(view, repositoryId, built.code, built.detail)
        result = read_knowledge_view(path, context, built)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        code, detail = _unusable_dataset(databasePath, error)
        return _refused_read(view, repositoryId, code, detail)
    if result.state == "refused" or result.payload is None:
        assert result.refusal is not None
        return _refused_read(view, repositoryId, result.refusal.code, result.refusal.detail)
    payload = result.payload
    return {
        "ok": True,
        "state": "view",
        "view": payload.view,
        "repositoryId": repositoryId,
        "snapshot": payload.snapshot.logical_digest,
        "completeWithinDeclaredScope": payload.completeness.complete_within_declared_scope,
        "continuation": None if payload.continuation is None else payload.continuation.token,
        "payload": payload.model_dump(mode="json"),
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
        ordering_input=request.ordering_input,  # type: ignore[arg-type]
        limit=request.limit,
        continuation=token,
    )


def knowledge_change_payload(request: ChangeToolRequest) -> dict[str, Any]:
    """Refuse one mount-side change request, through the response-model choke point.

    This surface records nothing. It has no admitted write operation for any kind, so every kind --
    declared here or not -- is refused as ``registration_absent``, and the refusal names the
    operation that owns the write: :func:`agents_remember.application.knowledge_curator_ingest.
    ingest_curator_list`, reachable as the ``agents-remember knowledge-ingest`` subcommand, which
    resolves a whole hand-off list against a leaf enclosure contract and commits it through the
    admitted batch.

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
            "destination is missing: the knowledge write plane's reachable entry point is the "
            f"{WRITE_ENTRY_POINT!r} subcommand, which commits a whole curator hand-off list "
            "through the admitted batch. Call that, or read the result here with knowledge_read"
        ),
    }


def knowledge_diff_payload(request: DiffToolRequest) -> dict[str, Any]:
    """Compare two exact states, through the response-model choke point."""

    return _tool_payload("knowledge_diff", _diff_result(request))


def _diff_result(request: DiffToolRequest) -> dict[str, Any]:
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
        result = diff_knowledge_scope(
            built,
            before_path=Path(request.before_path),
            after_path=Path(request.after_path),
        )
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        code, detail = _unusable_dataset(request.before_path, error)
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
    }


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


def knowledge_integrity_check_payload(
    *,
    databasePath: str,
    repositoryId: str,
    scopeId: str | None = None,
) -> dict[str, Any]:
    """Report declared structural-rule violations and their limits, through the choke point."""

    return _tool_payload(
        "knowledge_integrity_check",
        _integrity_check_result(
            databasePath=databasePath,
            repositoryId=repositoryId,
            scopeId=scopeId,
        ),
    )


def _integrity_check_result(
    *,
    databasePath: str,
    repositoryId: str,
    scopeId: str | None = None,
) -> dict[str, Any]:
    """The one ``knowledge_integrity_check`` body, before the registered model validates it.

    ``compatible`` is ``None`` and is present. A caller that wants a compatibility decision makes it;
    this operation reports conditions, a traversal scope and the observable limitations of the read,
    which is what ``Doc13:186`` writes for it.
    """

    # The report is a read of a dataset the caller selected, so a selection that cannot be read is
    # reported as a refusal with the same shipped code the application seam uses, rather than
    # escaping as ``ToolError`` -- a report about a file nobody could open is not a report.
    try:
        conditions = _recorded_conditions(Path(databasePath), repositoryId, scopeId)
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
        "limitations": conditions["limitations"],
        "compatible": None,
        "assessment": None,
        "unresolved": conditions["unresolved"],
    }


def _recorded_conditions(
    database_path: Path, repository_id: str, scope_id: str | None
) -> dict[str, Any]:
    """The recorded detection conditions for one namespace, with their recorded limitations."""

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
            return _no_detection_run(scope_id)
        store = OpenedKnowledgeStore(
            database_path=database_path,
            repository_id=repository_id,
            schema=inspect_schema(connection),
            connection=connection,
            resource_lock_path=database_path.with_name(f"{database_path.name}.lock"),
        )
        result = read_detection_run(store, str(rows[0][0]))
    finally:
        connection.close()
    return _condition_report(result, scope_id)


def _no_detection_run(scope_id: str | None) -> dict[str, Any]:
    """The honest report when no detection run is recorded: no conditions and a stated limit."""

    return {
        "conditions": [],
        "scope": scope_id or "registered",
        "limitations": ["no detection run is recorded for this namespace at this snapshot"],
        "unresolved": ["no recorded detection run to report conditions from"],
    }


def _condition_report(result: Any, scope_id: str | None) -> dict[str, Any]:
    """The recorded conditions and limitations of one detection run, and no verdict."""

    if result.state == "refused" or result.run is None:
        return {
            "conditions": [],
            "scope": scope_id or "registered",
            "limitations": ["the recorded detection run could not be read"],
            "unresolved": [None if result.refusal is None else result.refusal.detail],
        }
    conditions = [
        {"code": signal.condition, "matched_facts": [signal.detail]} for signal in result.signals
    ]
    return {
        "conditions": conditions,
        "scope": scope_id or "registered",
        "limitations": list(result.run.limitations),
        "unresolved": [] if conditions else ["no condition matched the recorded scope"],
    }


def knowledge_project_payload(request: ProjectToolRequest) -> dict[str, Any]:
    """Render named read-only views into an explicitly authorized destination, through the choke point."""

    return _tool_payload("knowledge_project", _project_result(request))


def _project_result(request: ProjectToolRequest) -> dict[str, Any]:
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
    report = project_knowledge(
        Path(request.database_path),
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
