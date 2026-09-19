"""The pipeline between detection, curator judgment and the existing curator worklist.

``KS-R16@v1`` owns the composition and nothing else. ``KS-R14@v1`` emits facts, ``KS-R15@v1`` owns the
authored record, ``KS-R17@v1`` owns the authored edge, and this module is what carries a fact from one
to the next without ever becoming a second source of any of them. Four acts, each with one failure it
exists to prevent:

* :func:`group_detection_facts` deduplicates matches by their recorded subject and declared input set,
  retaining **every** matched condition with its supporting paths and edges (``Doc13:301``). Grouping
  merges; it never drops, and :meth:`…FamilyIntegrityFactGroup.retains` is the review that measures it.
* :func:`compose_status_report` reports the five status owners of ``Doc13:363-369`` separately, each
  with its own closed vocabulary and its own statement of what it does not establish. There is no field
  in the result that could hold a merged verdict.
* :func:`compose_currentness` compares one authored record's recorded binding against the caller's
  measurement of the current world, through ``KS-R15@v1``'s own comparison. It decides no equivalence:
  a moved input is stale, the record stays readable, and reuse is refused.
* :func:`route_family_review` counts the shipped formula's three terms **once**, beside the
  family-review row count, and reports that family rows moved nothing. It calls the shipped
  :func:`…curator_checklist.curator_actionable_count` rather than restating the arithmetic, and the
  section it renders into is ``KS-R15@v1``'s ``knowledgeReview`` -- there is no second worklist, no
  second reports directory and no second counter here.

The one thing this module refuses to do is invent a conclusion. Nothing here reads a rationale, a
path's bytes, a label or a count to decide whether a change matters: a caller that finds a stage in
this file attaching a verdict has found the defect the packet's Failure And Recovery Behavior names,
and the fix is to remove it rather than to author it in code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agents_remember.memory_quality.curator_checklist import curator_actionable_count
from agents_remember.memory_quality.knowledge_review import (
    KNOWLEDGE_REVIEW_HEADING,
    AssessmentSummary,
    AssessmentSummaryInput,
    summarise_assessment_state,
)
from agents_remember.models.knowledge.detection import DetectionSignalPayload
from agents_remember.models.knowledge.family_review import (
    FACT_GROUPING_POLICY_VERSION,
    PIPELINE_STATUS_OWNERS,
    STATUS_OWNER_DECLARATIONS,
    FactMatch,
    FactSupportingEdge,
    FamilyIntegrityFactGroup,
    FamilyReviewRouting,
    FamilyReviewRoutingRow,
    FindingCurrentness,
    PipelineStatusEntry,
    SeparatedStatusReport,
    fact_group_identity,
)
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    SubjectAssessmentStatus,
    assessment_subject_id,
)
from agents_remember.models.lifecycles.review_assessment_binding import (
    assessment_currentness,
    subject_state,
)

__all__ = [
    "FAMILY_CONDITIONS",
    "compose_currentness",
    "compose_status_report",
    "curator_currentness_status",
    "curator_review_status",
    "detector_status",
    "family_review_summaries",
    "group_detection_facts",
    "route_family_review",
]

# The two conditions ``KS-R14@v1`` conditions on a family revision. Their recorded path's first edge is
# that family revision, which is what makes a group's subject derivable from a recorded fact rather
# than from a path, a label or a title.
FAMILY_CONDITIONS: tuple[str, ...] = (
    "source_changed_on_both_sides_joined_to_same_family",
    "one_sided_source_change_with_recorded_siblings",
)

# The number of edges ``KS-R14@v1`` records on a family-conditioned relationship path:
# ``(family_revision, invariant_revision, claim)``. The count is checked rather than assumed, because a
# shorter path would make the first edge something other than a family revision and a group key built
# from it would name the wrong subject.
_FAMILY_PATH_EDGE_COUNT = 3


def group_detection_facts(
    signals: Sequence[DetectionSignalPayload],
) -> tuple[FamilyIntegrityFactGroup, ...]:
    """Deduplicate the run's signals into fact groups, retaining every match.

    A group is one recorded subject under one declared input set. The subject is the family revision a
    family-conditioned signal names, or the invariant revision a single-claim signal names; the input
    signature is the declared sides' snapshot identities. Both come from the signal's own recorded
    fields, so the same signals always group the same way and no name or path participates.
    """

    grouped: dict[str, list[DetectionSignalPayload]] = {}
    for signal in signals:
        grouped.setdefault(_group_key(signal), []).append(signal)
    return tuple(_group_of(tuple(grouped[key])) for key in sorted(grouped))


def _group_key(signal: DetectionSignalPayload) -> str:
    """Return one signal's declared group key: its subject and its declared input signature."""

    subject_id, _families = _subject_of(signal)
    return f"{subject_id}@{_input_signature(signal)}"


def _subject_of(signal: DetectionSignalPayload) -> tuple[str, tuple[str, ...]]:
    """Return one signal's recorded subject id and the family revisions it names, if any.

    Three recorded facts decide the subject, and each is one ``KS-R14@v1`` already carries:

    * a family-conditioned signal's path is ``(family_revision, invariant_revision, claim)`` and its
      first edge is the family revision, required to be that three-edge shape because a shorter path
      would make the first edge something else;
    * any other signal's first edge is the invariant revision its path was built from
      (``detection_walk._claim_signal`` and ``_record_change_signal``);
    * an ``edited_invariant_family_or_evidence_record`` signal's *granularity* says whether the record
      it read was a family's or an invariant's, which is what separates the two subject kinds there.

    The spelling is ``kind:recordId`` -- ``KS-R15@v1``'s own ``assessment_subject_id`` -- so a routing
    row addresses a subject the existing collection can already hold a record for. A subject this
    derivation cannot classify is reported as the defect it is rather than routed to a subject nobody
    recorded.
    """

    if signal.condition in FAMILY_CONDITIONS:
        families = _first_edges(signal, required_edges=_FAMILY_PATH_EDGE_COUNT)
        return (f"family:{'+'.join(families)}", families)
    kind = _subject_kind(signal)
    reached = _first_edges(signal, required_edges=1)
    return (f"{kind}:{'+'.join(reached)}", ())


def _subject_kind(signal: DetectionSignalPayload) -> str:
    """Return the ``KS-R15@v1`` subject kind one signal's recorded facts place it under."""

    granularities = {change.granularity for change in signal.observed_changes}
    if "family_statement_changed" in granularities:
        return "family"
    if granularities & {
        "invariant_statement_changed",
        "source_file_changed",
        "attributed_span_changed",
        "anchor_resolved_elsewhere",
        "realization_claim_record_changed",
    }:
        return "invariant-revision"
    raise ValueError(
        f"the signal {signal.signal_id} carries condition {signal.condition!r} and recorded no "
        "observed change whose granularity places it under a subject kind this pipeline can address; "
        "routing it would name a subject the existing assessment collection cannot hold a record for"
    )


def _first_edges(signal: DetectionSignalPayload, *, required_edges: int) -> tuple[str, ...]:
    """Return the sorted first edge of every recorded path, requiring the declared path shape."""

    edges: list[str] = []
    for path in signal.relationship_paths:
        if len(path.edges) < required_edges:
            raise ValueError(
                f"the signal {signal.signal_id} carries condition {signal.condition!r} and a recorded "
                f"path {path.path_id!r} with {len(path.edges)} edge(s); a condition conditioned on a "
                f"family revision records at least {required_edges}. The pipeline groups by the "
                "recorded subject and refuses to guess one from a shorter path"
            )
        edges.append(path.edges[0])
    return tuple(sorted(set(edges)))


def _input_signature(signal: DetectionSignalPayload) -> str:
    """Return the declared input sides' snapshot identities, as one ordered signature."""

    parts = [
        f"{side.side}={side.context.knowledge.logical_digest}" for side in signal.input_set.sides
    ]
    return f"{signal.input_set.declared}[{'|'.join(sorted(parts))}]"


def _group_of(signals: tuple[DetectionSignalPayload, ...]) -> FamilyIntegrityFactGroup:
    """Build one group from the signals that share its key, retaining every contributing match."""

    subject_id, families = _subject_of(signals[0])
    matches = tuple(_match_of(signal) for signal in signals)
    return FamilyIntegrityFactGroup(
        group_id=fact_group_identity(subject_id, _input_signature(signals[0])),
        subject_id=subject_id,
        family_revision_ids=families,
        input_signature=_input_signature(signals[0]),
        grouping_policy_version=FACT_GROUPING_POLICY_VERSION,
        matches=matches,
    )


def _match_of(signal: DetectionSignalPayload) -> FactMatch:
    """Project one signal's recorded facts into the match a group carries, dropping nothing."""

    edges: list[FactSupportingEdge] = []
    for path in signal.relationship_paths:
        for index, edge_id in enumerate(path.edges):
            edges.append(
                FactSupportingEdge(
                    edge_kind=f"{signal.condition}/step-{index}",
                    from_record_id=path.edges[index - 1] if index else edge_id,
                    to_record_id=edge_id,
                    mapping_snapshot=path.snapshot_side,
                )
            )
    return FactMatch(
        condition=signal.condition,
        sides=tuple(sorted({side.side for side in signal.input_set.sides})),
        supporting_paths=tuple(
            sorted({change.path for change in signal.observed_changes if change.path})
        ),
        supporting_edges=tuple(edges),
        supporting_item_ids=tuple(sorted({change.item_id for change in signal.observed_changes})),
    )


def compose_status_report(observed: Mapping[str, str]) -> SeparatedStatusReport:
    """Report the five owners separately, each with its own status and its own stated limit.

    ``observed`` maps one owner to the status that owner reports. A missing owner is a refusal rather
    than an omitted row, because an omitted row is how five statuses quietly become four -- and the
    ``does_not_establish`` sentence comes from the declaration table rather than from the caller, so a
    caller cannot report a status without its limit.
    """

    declarations = {
        owner: (establishes, limit)
        for owner, _members, establishes, limit in (STATUS_OWNER_DECLARATIONS)
    }
    unknown = tuple(owner for owner in observed if owner not in declarations)
    if unknown:
        raise ValueError(
            f"the status owner {unknown[0]!r} is not one of the five declared owners "
            f"({' | '.join(PIPELINE_STATUS_OWNERS)}); a sixth owner is a second reporting surface"
        )
    entries = []
    for owner in PIPELINE_STATUS_OWNERS:
        status = observed.get(owner)
        if status is None:
            raise ValueError(
                f"the status owner {owner!r} reported nothing; §4.1 keeps the five owners separate "
                "and a missing owner is a collapse, not an absent row"
            )
        establishes, limit = declarations[owner]
        entries.append(
            PipelineStatusEntry(
                owner=owner, status=status, establishes=establishes, does_not_establish=limit
            )
        )
    return SeparatedStatusReport(entries=tuple(entries))


def detector_status(
    signals: Sequence[DetectionSignalPayload],
    *,
    unresolved_inputs: bool = False,
    incomplete_scan: bool = False,
) -> str:
    """Return the detector's own status, in the detector's own vocabulary.

    The precedence is declared rather than incidental: a scan that did not finish cannot be reported as
    a match, and an unresolved declared input is a limitation ``KS-R14@v1`` already recorded, so the
    less complete fact wins. The status says nothing about whether a behavioral conflict exists -- that
    is the curator's, and the owner's declaration repeats it.
    """

    if incomplete_scan:
        return "incomplete-scan"
    if unresolved_inputs:
        return "unresolved-inputs"
    for signal in signals:
        if signal.registered_scope_status == "incomplete_scan":
            return "incomplete-scan"
        if (
            "unread_declared_input" in signal.limitations
            or "unsupported_locator" in signal.limitations
        ):
            return "unresolved-inputs"
    return "matched" if signals else "no-match-under-declared-policy"


def curator_review_status(dispositions: Sequence[str]) -> str:
    """Return the curator's own status: a record exists, is unresolved, or is missing.

    ``unresolved`` is a *member of the vocabulary* rather than a gap, which is §3.4 made visible: a
    curator who could not conclude must be able to report that state, and a subject with no record
    reports ``no-record-recorded`` and is never defaulted to a favourable disposition.
    """

    if not dispositions:
        return "no-record-recorded"
    if "unresolved" in dispositions:
        return "declared-unresolved"
    return "record-recorded"


def compose_currentness(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]],
) -> tuple[FindingCurrentness, ...]:
    """Report every stored record's currentness against the inputs it examined.

    ``current`` is the caller's measurement of the world, keyed by record identity and then by
    ``(kind, name)``. The comparison is ``KS-R15@v1``'s own -- a recorded identity with no current value
    is a mismatch rather than a pass -- and this function adds only the two facts the pipeline owes: the
    record stays readable, and nothing was reinterpreted for the new inputs.
    """

    reported: list[FindingCurrentness] = []
    for assessment in assessments:
        measured = assessment_currentness(assessment, current.get(assessment.assessmentId, {}))
        reported.append(
            FindingCurrentness(
                subject_id=assessment_subject_id(assessment),
                review_record_id=assessment.assessmentId,
                binding_state=measured.binding_state,
                moved_identities=tuple(gap.identity for gap in measured.gaps),
            )
        )
    return tuple(reported)


def curator_currentness_status(bindings: Sequence[FindingCurrentness]) -> str | None:
    """Render the value the shipped ``currentnessStatus`` field carries, or ``None`` when nothing moved.

    This is the *only* currentness string this leaf produces, and it is produced for the field the
    curator-coherence response already declares. There is no second currentness surface: an empty
    sequence answers ``None`` because "nothing is stale" is not a status anybody needs a new field for,
    and a stale binding answers with the exact record identities that moved.
    """

    stale = tuple(binding for binding in bindings if binding.binding_state == "stale")
    if not stale:
        return None
    subjects = ", ".join(sorted({binding.subject_id for binding in stale}))
    return (
        f"stale: {len(stale)} recorded review(s) no longer match their examined inputs ({subjects})"
    )


def route_family_review(
    groups: Sequence[FamilyIntegrityFactGroup],
    *,
    review_ids_by_subject: Mapping[str, Sequence[str]],
    repair_count: int,
    missing_count: int,
    stale_count: int,
) -> FamilyReviewRouting:
    """Route the groups into the existing worklist and report two counts, never one.

    The actionable count is the *shipped* value: it is computed by
    :func:`…curator_checklist.curator_actionable_count` over the three terms this leaf does not own, and
    the family-review row count is carried beside it and folded into nothing. A family-review row is
    not a repair row, a missing-onboarding row or a stale-route-index row, so it cannot move the count
    that gates closeout -- and this function identifies the subject a review row belongs to through the
    record's own ``assessment_subject_id``, never through a manufactured source file.
    """

    rows = tuple(
        FamilyReviewRoutingRow(
            subject_id=_subject_id_of(group),
            group_id=group.group_id,
            matched_condition_count=len(group.matches),
            review_record_ids=tuple(review_ids_by_subject.get(_subject_id_of(group), ())),
        )
        for group in groups
    )
    return FamilyReviewRouting(
        rows=rows,
        report_only_section=KNOWLEDGE_REVIEW_HEADING,
        review_row_count=len(rows),
        repair_count=repair_count,
        missing_count=missing_count,
        stale_count=stale_count,
        actionable_count=curator_actionable_count(repair_count, missing_count, stale_count),
    )


def _subject_id_of(group: FamilyIntegrityFactGroup) -> str:
    """Return the subject identity a group's review rows are addressed by.

    The group's own ``subject_id`` *is* ``KS-R15@v1``'s ``kind:recordId`` spelling, so a routing row
    addresses exactly the subject the existing collection already holds records under and no
    manufactured identity is invented at the routing boundary.
    """

    return group.subject_id


def family_review_summaries(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]],
) -> tuple[AssessmentSummary, ...]:
    """Summarise the stored records for the report-only ``knowledgeReview`` section.

    Both halves are ``KS-R15@v1``'s: :func:`…review_assessment_binding.subject_state` decides each
    subject's state and :func:`…knowledge_review.summarise_assessment_state` builds the row. A subject
    with no stored record produces no row at all -- it is not rendered as a disposition -- and the
    limitation counts come from the projection rather than from a re-reading of the records.
    """

    by_subject: dict[str, list[ReviewAssessment]] = {}
    for assessment in assessments:
        by_subject.setdefault(assessment_subject_id(assessment), []).append(assessment)
    summaries: list[AssessmentSummary] = []
    for subject_id in sorted(by_subject):
        records = by_subject[subject_id]
        state = subject_state(records, current=current)
        summaries.append(
            summarise_assessment_state(
                AssessmentSummaryInput(
                    subjectId=subject_id,
                    assessmentCount=state.assessmentCount,
                    dispositions=tuple(entry.disposition for entry in state.assessments),
                    comparisonRefs=tuple(dict.fromkeys(record.comparisonRef for record in records)),
                    scopeRefs=tuple(dict.fromkeys(record.scopeManifestRef for record in records)),
                    unresolvedCount=state.unresolvedCount,
                    staleCount=state.staleCount,
                )
            )
        )
    return tuple(summaries)


def reported_subject_status(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]],
    subject_id: str,
) -> SubjectAssessmentStatus:
    """Return one subject's reportable state, through ``KS-R15@v1``'s own projection.

    The states are ``none-recorded``, ``unresolved``, ``stale`` and ``current``; there is no
    "compatible" and no default, so a subject nobody reviewed answers ``none-recorded`` rather than the
    absence being rendered as a clearance (``Doc13:104``).
    """

    records = [
        assessment for assessment in assessments if assessment_subject_id(assessment) == subject_id
    ]
    return subject_state(records, current=current).status
