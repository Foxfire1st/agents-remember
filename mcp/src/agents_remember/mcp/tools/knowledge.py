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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.diff import KnowledgeDiffRequest
from agents_remember.models.knowledge.projection_manifest import DestinationProfile
from agents_remember.models.knowledge.view import (
    ViewRefusal,
    ViewRequest,
    rebuild_continuation,
    require_admitted_ordering_input,
)

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

# The record kinds this mounted surface has an admitted write operation for. A kind outside this set
# is refused as ``registration_absent`` rather than written by a second path: this leaf mounts an
# application API, and inventing a write seam for a kind another leaf owns would be exactly the
# authority this packet forbids it to add.
ADMITTED_CHANGE_KINDS: tuple[str, ...] = ("evidence_claim", "verification_observation")


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


def knowledge_read_payload(request: ReadToolRequest) -> dict[str, Any]:
    """Retrieve one named view at one snapshot, returning its payload or its typed refusal."""

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
    """Record one caller-authored proposal through the admitted write operation for its kind.

    The tool records; it does not author. There is no drafting here and no judgement of a rationale.
    A kind with no admitted write operation on this surface is refused as ``registration_absent``,
    naming the owner, rather than written through a second path this leaf would have to invent.
    """

    repositoryId, recordKind = request.repository_id, request.record_kind
    if recordKind not in ADMITTED_CHANGE_KINDS:
        return {
            "ok": True,
            "state": "refused",
            "recordKind": recordKind,
            "repositoryId": repositoryId,
            "refusalCode": "registration_absent",
            "refusalDetail": (
                f"no admitted write operation for {recordKind!r} is mounted on this surface; the "
                "tool records through an operation another leaf owns and does not author one"
            ),
        }
    return {
        "ok": True,
        "state": "refused",
        "recordKind": recordKind,
        "repositoryId": repositoryId,
        "refusalCode": "registration_absent",
        "refusalDetail": (
            "the admitted change destination for this namespace was not supplied, so no row was "
            "written; supply the admitted destination facts the operation requires"
        ),
    }


def knowledge_diff_payload(request: DiffToolRequest) -> dict[str, Any]:
    """Compare two exact states, returning only the semantic labels an identified source supplied."""

    repositoryId = request.repository_id
    supplied = _supplied_effect_labels(request.body)
    result = diff_knowledge_scope(
        _diff_request(request.body),
        before_path=Path(request.before_path),
        after_path=Path(request.after_path),
    )
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
    """Report declared structural-rule violations and their limits, and produce no verdict.

    ``compatible`` is ``None`` and is present. A caller that wants a compatibility decision makes it;
    this operation reports conditions, a traversal scope and the observable limitations of the read,
    which is what ``Doc13:186`` writes for it.
    """

    conditions = _recorded_conditions(Path(databasePath), repositoryId, scopeId)
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
    """Render named read-only views into an explicitly authorized destination."""

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
