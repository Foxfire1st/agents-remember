"""The factual ``knowledgeReview`` section of the curator's one checklist artifact.

``design/retrieval-review-design.md:348`` asks for a factual ``knowledgeReview`` section carrying
comparison/scope references and counted limitations, and ``:29`` restates the boundary: the later
increment "adds factual knowledge-review links here without duplicating the checklist or silently
changing its actionable count."

Both halves of that boundary are implemented here rather than promised:

* **Report-only.** :func:`knowledge_review_section` returns lines and limitations. It returns **no
  count**, and it cannot: :func:`write_curator_checklist`'s ``curatorActionableCount`` is computed from
  repairable findings, missing onboarding and stale route indexes, and nothing in this module is an
  input to that arithmetic. An unresolved family signal therefore raises no gate and adds no finding
  per detector match -- `Doc13:422` reserves gate consequences for an explicit design/authority
  decision and ``KS-R16@v1`` owns that requirement.
* **One artifact.** The section is rendered into the same ``curator-memory-quality.md`` file the
  shipped renderer already atomically replaces. There is no second checklist, no second attestation
  and no parallel count, which is what ``:348``'s "without duplicating the checklist" forbids.

The section reports what is *there*: the comparison and scope references an assessment recorded, and
the counted limitations of the collection. A subject with no assessment is not rendered as a
disposition; it is simply not a knowledge-review link, and the section says how many assessments
exist rather than how many subjects were cleared.
"""

from __future__ import annotations

from dataclasses import dataclass

# The heading, spelled once. A reader greps for this and the checklist's own tests assert it.
KNOWLEDGE_REVIEW_HEADING = "knowledgeReview"

# The limitation codes this section counts. Each one is a *fact about the collection*, never a
# verdict about an assessment's content: ``unresolved`` counts records whose author could not
# conclude, ``stale`` counts records whose binding moved, and ``partial-scope`` counts records whose
# scope manifest is not the one the current candidate declares.
UNRESOLVED_DISPOSITION = "unresolved"
STALE_BINDING = "stale"
PARTIAL_SCOPE = "partial-scope"


@dataclass(frozen=True)
class AssessmentSummary:
    """One subject's stored assessment history, as the checklist reports it.

    Every field is a count or an identity. There is deliberately no ``verdict`` field: a summary that
    carried one would be the checklist inventing a conclusion the curator authored, which is exactly
    the promotion ``Doc13:104`` forbids in the other direction.
    """

    subjectId: str
    assessmentCount: int
    comparisonRefs: tuple[str, ...] = ()
    scopeRefs: tuple[str, ...] = ()
    unresolvedCount: int = 0
    staleCount: int = 0
    dispositions: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeReview:
    """The whole factual section: the lines to render and the limitations they count."""

    lines: list[str]
    limitations: tuple[str, ...]
    assessmentCount: int
    subjectCount: int


def knowledge_review_section(summaries: tuple[AssessmentSummary, ...]) -> KnowledgeReview:
    """Render the factual section for the assessments the authority currently holds.

    An empty collection renders an explicit *no rows* statement rather than a favourable default.
    ``None.`` is the shipped renderer's own spelling for an empty section, and reusing it is how this
    section says "there is no recorded assessment" without inventing a state the read layer would
    then have to reconcile.
    """

    limitations = _limitations(summaries)
    lines = [f"## {KNOWLEDGE_REVIEW_HEADING}", ""]
    lines.append(
        "Report-only. This section records what the curator-coherence authority holds; it adds no "
        "finding, no gate and nothing to `curatorActionableCount`."
    )
    lines.append("")
    lines.append(f"- Recorded assessments: {sum(item.assessmentCount for item in summaries)}")
    lines.append(f"- Reviewed subjects: {len(summaries)}")
    lines.append(f"- Counted limitations: {', '.join(limitations) if limitations else 'none'}")
    lines.append("")
    if not summaries:
        lines.extend(["_None recorded._", ""])
    else:
        lines.extend(
            [
                "| Subject | Assessments | Dispositions | Comparison | Scope |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        for summary in summaries:
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(summary.subjectId),
                        str(summary.assessmentCount),
                        _cell(", ".join(summary.dispositions) or "none"),
                        _cell(", ".join(summary.comparisonRefs) or "none"),
                        _cell(", ".join(summary.scopeRefs) or "none"),
                    )
                )
                + " |"
            )
        lines.append("")
    lines.extend(
        [
            "| Limitation | Count | Meaning |",
            "| --- | ---: | --- |",
            f"| `{UNRESOLVED_DISPOSITION}` | {_count(summaries, 'unresolvedCount')} | "
            "The author examined the inputs and could not conclude |",
            f"| `{STALE_BINDING}` | {_count(summaries, 'staleCount')} | "
            "The recorded inputs moved; the finding stays readable and is not reused |",
            f"| `{PARTIAL_SCOPE}` | {_partial_scope_count(summaries)} | "
            "No comparison or scope reference is recorded for the subject |",
            "",
        ]
    )
    return KnowledgeReview(
        lines=lines,
        limitations=limitations,
        assessmentCount=sum(item.assessmentCount for item in summaries),
        subjectCount=len(summaries),
    )


@dataclass(frozen=True)
class AssessmentSummaryInput:
    """One subject's projected assessment state, as the caller measured it.

    A value object rather than a long keyword list: the summary is built from a projection, and
    naming the projection's fields once here means a new limitation is added in one place instead of
    at every call site that assembles a summary.
    """

    subjectId: str
    assessmentCount: int
    dispositions: tuple[str, ...] = ()
    comparisonRefs: tuple[str, ...] = ()
    scopeRefs: tuple[str, ...] = ()
    unresolvedCount: int = 0
    staleCount: int = 0


def summarise_assessment_state(measured: AssessmentSummaryInput) -> AssessmentSummary:
    """Build one subject's summary from a projection, refusing a count that contradicts its records."""

    if measured.assessmentCount < 0 or measured.unresolvedCount < 0 or measured.staleCount < 0:
        raise ValueError("an assessment summary counts records, so no count may be negative")
    if measured.unresolvedCount > measured.assessmentCount:
        raise ValueError("the unresolved count cannot exceed the records it counts")
    if measured.staleCount > measured.assessmentCount:
        raise ValueError("the stale count cannot exceed the records it counts")
    if len(measured.dispositions) != measured.assessmentCount:
        raise ValueError("the dispositions listed must be one per recorded assessment")
    return AssessmentSummary(
        subjectId=measured.subjectId,
        assessmentCount=measured.assessmentCount,
        comparisonRefs=tuple(measured.comparisonRefs),
        scopeRefs=tuple(measured.scopeRefs),
        unresolvedCount=measured.unresolvedCount,
        staleCount=measured.staleCount,
        dispositions=tuple(measured.dispositions),
    )


def _limitations(summaries: tuple[AssessmentSummary, ...]) -> tuple[str, ...]:
    """Return the counted limitation codes, in a stable order, omitting the zero counts."""

    counted: list[str] = []
    if _count(summaries, "unresolvedCount"):
        counted.append(UNRESOLVED_DISPOSITION)
    if _count(summaries, "staleCount"):
        counted.append(STALE_BINDING)
    if _partial_scope_count(summaries):
        counted.append(PARTIAL_SCOPE)
    return tuple(counted)


def _count(summaries: tuple[AssessmentSummary, ...], field: str) -> int:
    return sum(int(getattr(summary, field)) for summary in summaries)


def _partial_scope_count(summaries: tuple[AssessmentSummary, ...]) -> int:
    return sum(1 for summary in summaries if not summary.comparisonRefs or not summary.scopeRefs)


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


__all__ = [
    "KNOWLEDGE_REVIEW_HEADING",
    "PARTIAL_SCOPE",
    "STALE_BINDING",
    "UNRESOLVED_DISPOSITION",
    "AssessmentSummary",
    "AssessmentSummaryInput",
    "KnowledgeReview",
    "knowledge_review_section",
    "summarise_assessment_state",
]
