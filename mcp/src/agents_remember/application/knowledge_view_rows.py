"""The three view renderers of the knowledge reader: source context, invariant, family.

Moved text-identical out of ``knowledge_view_render.py`` along its own section boundary, so that the
candidate selection and ordering half and this rendering half each stay well under the file-size
limit. The candidate vocabulary (``Candidate``, ``OrderedSet``, ordering and the authored decision
reader) stays in ``knowledge_view_render``.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents_remember.application.knowledge_view_render import (
    Candidate,
    Page,
    _classified,
    _family_candidates,
    _invariant_candidates,
    _page,
    _position,
    _source_context_candidates,
    order_candidates,
)
from agents_remember.models.knowledge.view import (
    FamilyRow,
    InvariantRow,
    KnowledgeViewReader,
    SourceContextRow,
    UnresolvedLimitation,
    ViewRequest,
)


def _render(candidates: Sequence[Candidate], request: ViewRequest, offset: int) -> Page:
    """Order the candidates, take the page that starts at ``offset``, and bound it.

    The offset is the one the seam admitted from the caller's continuation. No token is read here, so
    there is no second parse and no fallback to the start of the walk: a page is a function of the
    selection, the limit and the cursor alone.
    """

    ordered = order_candidates(candidates, request.ordering_input)
    rows, next_position = _page(ordered.ordered, request.limit, offset)
    return Page(ordered=ordered, rows=rows, start=offset, next_position=next_position)


def render_source_context(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[SourceContextRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The source-context view's rows, its limitations, its rule ids, its selection's size and the
    position the next page starts at."""

    page = _render(_source_context_candidates(reader, request), request, offset)
    rows: list[SourceContextRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            SourceContextRow(
                subject=candidate.subject,
                fact_kind=candidate.fact_kind,  # type: ignore[arg-type]
                statement=candidate.statement,
                role=candidate.role,  # type: ignore[arg-type]
                path=candidate.path,
                locator=candidate.locator,
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


def render_invariant(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[InvariantRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The invariant view's rows, its limitations, its rule ids, its selection's size and the
    position the next page starts at.

    A realization row carries its own recorded location, exactly as the source-context and family
    views report it. ``_invariant_candidates`` set the role, path and locator on that candidate from
    the start -- this projection copied none of them, so the view that answers "where is this
    invariant realized" reported the claim, its revision and its authored rationale and no place.
    Nothing is derived here: the fields the candidate already holds are the ones that travel, so the
    three views cannot disagree about one claim's location.
    """

    page = _render(_invariant_candidates(reader, request), request, offset)
    rows: list[InvariantRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            InvariantRow(
                subject=candidate.subject,
                fact_kind=candidate.fact_kind,  # type: ignore[arg-type]
                statement=candidate.statement,
                role=candidate.role,  # type: ignore[arg-type]
                path=candidate.path,
                locator=candidate.locator,
                essential_conditions=candidate.essential_conditions,
                conditions_omitted=candidate.conditions_omitted,
                lifecycle=candidate.lifecycle,
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )


def render_family(
    reader: KnowledgeViewReader, request: ViewRequest, offset: int
) -> tuple[
    tuple[FamilyRow, ...],
    tuple[UnresolvedLimitation, ...],
    tuple[str, ...],
    int,
    int,
]:
    """The family view's rows, with each row's change locus left as the field it was recorded as.

    A member's location travels with the row, exactly as the source-context view reports it. The
    candidate carried the recorded path and locator from the start; the projection copied neither,
    so a family read answered "where is this realized" with a claim id, an invariant revision id
    and an authored rationale -- and the rationale is authored prose, which is not a location even
    when a filename happens to appear in it. Both views decode the one recorded locator through the
    same adapter, so the two cannot disagree about one claim's place.
    """

    page = _render(_family_candidates(reader, request), request, offset)
    rows: list[FamilyRow] = []
    for index, candidate in enumerate(page.rows):
        provenance = _classified(candidate)
        assert provenance is not None
        rows.append(
            FamilyRow(
                subject=candidate.subject,
                fact_kind=candidate.fact_kind,  # type: ignore[arg-type]
                statement=candidate.statement,
                role=candidate.role,  # type: ignore[arg-type]
                path=candidate.path,
                locator=candidate.locator,
                change_locus=candidate.change_locus,  # type: ignore[arg-type]
                lifecycle=candidate.lifecycle,
                order=_position(page.start + index, provenance, request.ordering_input),
                provenance=provenance,
            )
        )
    return (
        tuple(rows),
        page.ordered.limitations,
        page.ordered.rule_ids,
        len(page.ordered.ordered),
        page.next_position,
    )
