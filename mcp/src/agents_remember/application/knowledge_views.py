"""The application seam for the five query views (``KS-R20@v1`` §1, §3).

This is the seventh application seam beside :mod:`knowledge`, :mod:`knowledge_snapshot`,
:mod:`knowledge_merge`, :mod:`knowledge_export`, :mod:`knowledge_read`, :mod:`knowledge_facets` and
:mod:`knowledge_evidence`, and like them it decides no authority and holds no durable state. It
resolves the snapshot, admits the request, builds the reader port, and returns the typed payload the
renderer produced.

**The snapshot resolver is the shipped one.** The snapshot a view declares comes from
``open_read_context``, which reads the identity the dataset at that path actually holds. A view
therefore cannot be handed a snapshot a caller wrote down, and the three comparisons the other seams
make -- bound namespace, declared generation, declared logical digest -- are made here for the same
reason.

**The continuation is checked before any row is read.** Presenting a continuation against another
snapshot is refused with both identities named, and a caller that does it receives no page at all.
There is no re-resolution and no partial answer.

**A walk ends exactly when the selection does.** The position a page starts at is the one the
caller's continuation round-trips to -- a token that does not read back exactly is refused, never
read as position zero -- and ``rows_remaining`` is measured from where that position leaves the
walk. So the last page reports nothing remaining, mints no continuation, and says so in its
completeness statement; a caller paging until exhausted terminates instead of cycling on empty
pages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_view_render import (
    UnadmittedOrderingInput,
    render_curation_queue,
    render_family,
    render_invariant,
    render_review_matrix,
    render_source_context,
)
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.view_source import open_view_reader
from agents_remember.models.knowledge.classification import ORDERING_INPUTS
from agents_remember.models.knowledge.read import (
    KnowledgeReadContext,
    KnowledgeReadSnapshot,
    snapshot_of_context,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.view import (
    VIEW_PURPOSES,
    CurationQueueView,
    FamilyView,
    InvariantView,
    ReviewMatrixView,
    SourceContextView,
    ViewCompleteness,
    ViewCounts,
    ViewRefusal,
    ViewRequest,
    ViewResult,
    ViewScope,
    continuation_for,
    continuation_position,
    require_continuation_snapshot,
    require_distinct_row_subjects,
    view_counts,
)

__all__ = ["VIEW_RENDERER_VERSION", "read_knowledge_view"]

# The renderer/profile version every payload records. It is a real recorded value and not a package
# version read at display time: two payloads produced by different renderer versions are
# distinguishable from the payloads themselves (requirement 4.3).
VIEW_RENDERER_VERSION = "knowledge-view-renderer/1"

# The recorded graph the selection walks, and the traversal policy it walks it under. Both are named
# in :class:`ViewScope` so a completeness statement is scoped to the inputs it was made about.
RECORDED_GRAPH = "recorded-envelope-and-generation-1-entities"
TRAVERSAL_POLICY = "knowledge-view-selection/v1"


def read_knowledge_view(
    database_path: Path,
    context: KnowledgeReadContext,
    request: ViewRequest,
    *,
    resolve_anchor: Any = None,
) -> ViewResult:
    """Read one named view at one declared snapshot, or return its typed refusal."""

    admitted = _admit(context, request)
    if admitted is not None:
        return _refused(request, admitted)
    probe = open_read_only_database(database_path)
    try:
        identity = inspect_schema(probe)
    finally:
        probe.close()
    reader, connection = open_view_reader(
        database_path,
        request.repository_id,
        identity,
        resolve_anchor=resolve_anchor,
    )
    try:
        return _render(reader, context, request)
    finally:
        connection.close()


def _admit(context: KnowledgeReadContext, request: ViewRequest) -> ViewRefusal | None:
    """Every admission check, before a row is read: ordering input, then the continuation binding."""

    if request.ordering_input not in ORDERING_INPUTS:
        return ViewRefusal(
            code="unadmitted_ordering_input",
            view=request.view,
            detail=(
                "the requested ordering names none of the four admitted ordering inputs; no rows "
                "are returned and no default order is substituted"
            ),
            offending_input=request.ordering_input,
            expected=", ".join(ORDERING_INPUTS),
            next_action=(
                "name a declared priority, a registered role, the declared stable ordering or an "
                "explicit trigger rule"
            ),
        )
    if context.knowledge.repository_id != request.repository_id:
        return ViewRefusal(
            code="snapshot_unavailable",
            view=request.view,
            detail="the declared snapshot belongs to another repository namespace",
            offending_input=request.repository_id,
            expected=context.knowledge.repository_id,
            observed=request.repository_id,
            next_action="address the read at the namespace the dataset is bound to",
        )
    if request.continuation is None:
        return None
    return require_continuation_snapshot(
        request.continuation,
        _snapshot_of(context),
    )


def _snapshot_of(context: KnowledgeReadContext) -> KnowledgeReadSnapshot:
    """The exact snapshot one context declares, as the payload and the cursor both name it."""

    return snapshot_of_context(context)


def _refused(request: ViewRequest, refusal: ViewRefusal) -> ViewResult:
    return ViewResult(state="refused", repository_id=request.repository_id, refusal=refusal)


def _render(reader: Any, context: KnowledgeReadContext, request: ViewRequest) -> ViewResult:
    """Dispatch one view to its renderer and assemble the payload around the rows it returned."""

    start = _page_start(request)
    if isinstance(start, ViewRefusal):
        return _refused(request, start)
    try:
        rows, limitations, rule_ids, total, next_position = _RENDERERS[request.view](
            reader, request, start
        )
    except UnadmittedOrderingInput as error:
        return _refused(
            request,
            ViewRefusal(
                code="unadmitted_ordering_input",
                view=request.view,
                detail=f"ordering input {error} is not admitted",
                offending_input=str(error),
                next_action="order on one of the four admitted ordering inputs",
            ),
        )
    snapshot = _snapshot_of(context)
    distinct = require_distinct_row_subjects([row.subject for row in rows])
    if distinct is not None:
        return _refused(request, distinct.model_copy(update={"view": request.view}))
    counts = _counts(reader, request, rows, total, next_position)
    continuation = None
    if counts.rows_remaining.value:
        continuation = continuation_for(
            view=request.view, snapshot=snapshot, position=next_position
        )
    payload = _PAYLOADS[request.view](
        snapshot=snapshot,
        counts=counts,
        completeness=ViewCompleteness(
            complete_within_declared_scope=continuation is None,
            scope=ViewScope(
                snapshot_logical_digest=snapshot.logical_digest,
                recorded_graph=RECORDED_GRAPH,
                traversal_policy=TRAVERSAL_POLICY,
                extractors=(VIEW_RENDERER_VERSION,),
            ),
        ),
        continuation=continuation,
        limitations=limitations,
        ordering_rule_ids=rule_ids,
        renderer_version=VIEW_RENDERER_VERSION,
        rows=rows,
    )
    return ViewResult(state="view", repository_id=request.repository_id, payload=payload)


def _page_start(request: ViewRequest) -> int | ViewRefusal:
    """Where this call starts paging: zero for a first page, or the position its continuation names.

    The cursor is read here and nowhere else, so the seam holds one answer to "what position is
    this". A continuation that is not this view's own token is refused rather than counted as
    position zero: a caller that presents an unreadable cursor hears so, instead of receiving the
    page it already received.
    """

    if request.continuation is None:
        return 0
    return continuation_position(request.continuation, view=request.view)


def _counts(
    reader: Any, request: ViewRequest, rows: tuple[Any, ...], total: int, next_position: int
) -> ViewCounts:
    """The required quantity set, measured from the reader and from the selection itself.

    ``total`` is the selection's own size, returned by the renderer that made it, so a page can never
    report a scope smaller than the walk it is a page of -- which is the packet's own non-conformance
    example. Rows the renderer withheld for want of a class are counted in ``unresolved_references``
    rather than dropped, because a withheld row is an unresolved input and not an absent fact.

    ``rows_remaining`` is measured from where the *walk* stands, not from the start of the selection:
    it is the selection minus the position the next page begins at. Counting ``total - returned``
    instead would report the same "remaining" on every page, and a caller reading that number as the
    end of the walk would page forever.
    """

    registered = reader.registered_counts()
    returned = len(rows)
    return view_counts(
        registered_realizations=registered.registered_realizations,
        registered_families=registered.registered_families,
        rows_returned=returned,
        rows_remaining=max(total - next_position, 0),
        facets_omitted=None if request.view in ("invariant", "source_context") else 0,
        dependency_content_identity_mismatches=(
            _dependency_mismatches(reader) if request.view == "review_matrix" else None
        ),
    )


def _dependency_mismatches(reader: Any) -> int:
    """How many recorded dependencies resolve to different bytes than they record."""

    mismatched = 0
    for row in reader.rows("verification_observation"):
        payload = row.payload
        artifact = payload.get("result_artifact")
        if isinstance(artifact, dict) and artifact.get("digest_checked_against_bytes") is False:
            mismatched += 1
    return mismatched


_RENDERERS: dict[str, Any] = {
    "source_context": render_source_context,
    "invariant": render_invariant,
    "family": render_family,
    "review_matrix": render_review_matrix,
    "curation_queue": render_curation_queue,
}

_PAYLOADS: dict[str, Any] = {
    "source_context": SourceContextView,
    "invariant": InvariantView,
    "family": FamilyView,
    "review_matrix": ReviewMatrixView,
    "curation_queue": CurationQueueView,
}


def view_purposes() -> dict[str, str]:
    """The published one-line purpose of each view, for the interface ``KS-R22@v1`` mounts."""

    return dict(VIEW_PURPOSES)


def open_view_context(
    database_path: Path,
    repository_id: str,
    *,
    repository_root: Path | None = None,
    code_tree_id: str | None = None,
    task_ref: str | None = None,
) -> KnowledgeReadContext | KnowledgeRefusal:  # pragma: no cover - re-exported convenience
    """Resolve the read context a view read is addressed at, through the shipped resolver."""

    return open_read_context(
        database_path,
        repository_id,
        repository_root=repository_root,
        code_tree_id=code_tree_id,
        task_ref=task_ref,
    )
