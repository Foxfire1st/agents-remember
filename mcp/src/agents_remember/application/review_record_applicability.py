"""Which supplied records one selected subject's review may display, and why (``ICR-R26@v1``).

The defect this module removes is F09's: the adapter handed the candidate-wide review matrix and
every supplied assessment to the selected subject's panes, so a valid assessment of a *sibling*
invariant was displayed in both panes of a different invariant. Complete delivery (``ICR-R14@v1``)
and correct attribution are separately falsifiable, and this is the attribution half.

**What the classification reads, and nothing else.** The recorded population one selection reaches
(:mod:`agents_remember.application.review_recorded_selection` -- the selected subject and its
retained revisions, the identities its recorded relationships reach, the recorded relationship rows
and revisions, and the comparison identity ICR-R11 publishes) and each record's *own* recorded
bindings. No label, path spelling, display version or revision *nearest* to the selection
participates, because none of them is a statement an author made about which subject a record is
about.

**One projection, six outcomes.** A record whose own recorded subject/revision binding names the
selected subject is ``direct`` in the displayed generation and ``historical`` when its recorded
generation input is another one -- kept inspectable, with the input identity it really examined, and
never promoted to this generation's result. A record whose recorded subject is a *different*
identity the selection reaches through an explicit recorded relationship is displayed as labelled
:class:`~agents_remember.models.knowledge.review_applicability.ReviewContextRecord` context. A
record whose recorded subject is a subject this comparison records and the selection's relationships
do not reach is ``unrelated``: it is not displayed as a judgment on the selected subject at all, and
the pane's summary counts it and names how to reach it. A record whose binding cannot be resolved is
displayed ``unresolved`` with its actual references and the reason, so it cannot imply support for
the selected invariant. A record bound to the compared candidate rather than to any subject -- and
every record of a review that selected no subject -- is ``candidate``: displayed as the candidate's
own input, never as a subject's judgment.

**Filtering is stated, never silent.** The panes' own collections carry only the records the
selected subject may be judged by; the six counts per supplied collection travel beside them, so the
complete population an owner answered with stays visible as arithmetic even when a record is not
displayed under this subject. The source inventory is untouched by any of this: it is measured from
the two bound code trees and never from a record selection, and record provenance -- author, role,
exact input identities, evidence references -- is carried verbatim on every displayed value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_evidence_records import AUTHORED_EFFECT_KINDS
from agents_remember.application.review_record_rendering import (
    ReviewClaimRecord,
    ReviewRecordInputs,
)
from agents_remember.application.review_recorded_selection import (
    ApplicabilitySources,
    ComparisonFacts,
    RecordedIdentity,
    RecordedSelection,
    RecordedSnapshots,
    selected_subject_population,
)
from agents_remember.application.review_revision_comparison import (
    SubjectRevisionSelection,
)
from agents_remember.application.review_subject_catalogue import read_subject_catalogue
from agents_remember.models.knowledge.detection import DetectionSignalPayload
from agents_remember.models.knowledge.evidence import VerificationObservationPayload
from agents_remember.models.knowledge.review import ReviewEntry, ReviewSurfaceRequest
from agents_remember.models.knowledge.review_applicability import (
    REVIEW_APPLICABILITY_CLASSES,
    ReviewApplicabilityClass,
    ReviewApplicabilitySummary,
    ReviewContextRecord,
    ReviewDisplayedApplicability,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow
from agents_remember.models.lifecycles.review_assessment import ReviewAssessment

__all__ = [
    "AppliedRecords",
    "apply_review_applicability",
    "review_applicability",
    "task_context_applicability",
]

# The two collections the review matrix page owns: their ``supplied`` count is the rows that page
# returned, while the owner's complete collection count travels on ``ICR-R14@v1``'s evidence channel.
_MATRIX_OWNED: tuple[ReviewApplicabilityClass, ...] = ("authored_effects", "evidence_claims")


def _empty_state_counts() -> dict[str, int]:
    """A fresh count per display treatment, so a collection's partition starts from nothing."""

    return {
        "direct": 0,
        "historical": 0,
        "context": 0,
        "candidate": 0,
        "unresolved": 0,
        "unrelated": 0,
    }


# The one generated-input edge of an assessment's examined-input declaration that names the
# candidate generation it examined: the analysis is in the module docstring of
# :mod:`agents_remember.application.review_recorded_selection`'s consumer, and the pair identity
# (``candidate-state``/``knowledge-candidate-pair``) is deliberately not compared, because this
# surface does not re-derive it and comparing it to a snapshot digest would be a similarity dressed
# as a match.
_CANDIDATE_TREE_KIND = "code-tree"
_CANDIDATE_TREE_NAME = "candidate"

# What the context record's own label falls back to when a record carries no short recorded label.
_NO_LABEL = "recorded"

_UNRESOLVED_DETAIL = (
    "the record's own recorded references name neither the selected subject's revisions nor any "
    "recorded relationship this selection reaches, so this composition cannot establish which "
    "subject it belongs to; it is displayed with those references and never as support for the "
    "selected subject"
)
_UNRESOLVED_SUBJECT_DETAIL = (
    "the record's recorded subject could not be resolved against the subjects this comparison "
    "records, so it is displayed with the references it carries and never as support for the "
    "selected subject"
)
_CANDIDATE_DETAIL = (
    "this review selected no subject, so the record is displayed as the candidate's own input with "
    "its recorded subject beside it; no subject was selected, so nothing here is a judgment on one"
)
_EXCLUDED_DETAIL = (
    "{count} supplied record(s) name a subject this comparison records and the selected subject's "
    "recorded relationships do not reach; they are not displayed as judgments on the selected "
    "subject, and each is reachable through its own subject's review"
)


@dataclass
class AppliedRecords:
    """The supplied records as one selection may display them, with each record's own label.

    ``labels`` is keyed by the record's own identity and carries the treatment only for the records
    that stay in a judgment collection; ``context`` carries the labelled context rows, and
    ``summaries`` carries the six-way count of every supplied collection -- including the records
    this review does not display as the selected subject's judgments. The projection is the port the
    panes consume (:class:`~agents_remember.application.review_record_rendering.
    ReviewApplicabilityProjection`), so a pane never reaches the comparison or the store through it.
    """

    labels: dict[str, ReviewDisplayedApplicability] = field(default_factory=dict)
    context: list[ReviewContextRecord] = field(default_factory=list)
    summaries: dict[str, ReviewApplicabilitySummary] = field(default_factory=dict)
    # The selection this projection was made against, carried so the pane that renders the selected
    # statements reads the same value the classification did rather than being handed it twice.
    revision_selection: SubjectRevisionSelection | None = None

    def label_of(self, record_id: str) -> ReviewDisplayedApplicability | None:
        """The treatment one supplied record earned, or ``None`` when it is not displayed here."""

        return self.labels.get(record_id)

    def context_of(
        self, classes: Sequence[ReviewApplicabilityClass]
    ) -> tuple[ReviewContextRecord, ...]:
        """The labelled context rows of the named collections, in recorded order."""

        return tuple(row for row in self.context if row.records in classes)

    def summaries_of(
        self, classes: Sequence[ReviewApplicabilityClass]
    ) -> tuple[ReviewApplicabilitySummary, ...]:
        """The six-way counts of the named collections, in the order given."""

        return tuple(self.summaries[name] for name in classes if name in self.summaries)

    def displayed_rows(self, rows: Sequence[ReviewMatrixRow]) -> tuple[ReviewMatrixRow, ...]:
        """The matrix rows this subject may be judged by, in the matrix's own order.

        A row whose recorded references resolved to another subject's identity is not here, whether
        it was displayed as context or not displayed at all; a row whose references resolved to
        nothing is here with the ``unresolved`` label, because dropping it would report an
        unresolvable binding as an absence of records.
        """

        return tuple(row for row in rows if row.subject.record_id in self.labels)


def review_applicability(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    records: ReviewRecordInputs,
    rows: Sequence[ReviewMatrixRow],
    facts: ComparisonFacts,
) -> AppliedRecords:
    """Classify one review's supplied records against the subject its own request selected.

    The composition calls this once, with the values it already holds: the resolution the request
    was answered from (so the known-subject catalogue is read from the same candidate, lazily, and
    only if some record names a subject the selection does not reach), the request's own selector,
    and one :class:`ComparisonFacts` carrying the comparison's union items, ICR-R07's revision
    selection, ICR-R11's comparison identity and ICR-R08's recorded relationship union. Assembling
    those into :class:`ApplicabilitySources` is this module's own business, so the adapter adds one
    call and no policy.
    """

    return apply_review_applicability(
        records,
        rows,
        ApplicabilitySources(
            comparison=facts,
            selector=request.selector,
            known_subjects=lambda: read_subject_catalogue(resolved),
            snapshots=RecordedSnapshots(
                repository_id=resolved.repository_id,
                before=resolved.baseline_database,
                after=resolved.candidate_database,
            ),
        ),
    )


def apply_review_applicability(
    records: ReviewRecordInputs,
    rows: Sequence[ReviewMatrixRow],
    sources: ApplicabilitySources,
) -> AppliedRecords:
    """Classify every supplied record against one selection, before any pane displays it.

    The one entry the composition calls: the selection's recorded population is read once, and the
    four supplied collections are each classified against it. A record that may be displayed carries
    its label in ``labels``; one displayed as another subject's context becomes a
    :class:`ReviewContextRecord`; and every collection's six-way count is recorded in ``summaries``,
    including the records this review does not display as the selected subject's judgments.
    """

    selection = selected_subject_population(sources)
    known = _KnownSubjects(sources.known_subjects)
    applied = AppliedRecords(revision_selection=sources.comparison.selected)
    _classify_assessments(applied, records.assessments, selection, known)
    _classify_signals(applied, records.signals, selection)
    _classify_observations(applied, records.observations)
    _classify_rows(applied, records, rows, selection)
    return applied


def task_context_applicability(records: ReviewRecordInputs) -> AppliedRecords:
    """Classify every supplied record of a review that selected no subject.

    A task-context review compares no operand, so there is no selected subject for a record to be
    judged against and no comparison generation either. Every supplied record is therefore displayed
    as the candidate's own input -- ``candidate`` -- with its own recorded subject carried beside it,
    which is the one label that neither claims the record applies to a subject nor reports a binding
    as unresolved when nothing was asked of it.
    """

    applied = AppliedRecords()
    counts: dict[ReviewApplicabilityClass, dict[str, int]] = {}
    for assessment in records.assessments:
        identity = _assessment_identity(assessment)
        applied.labels[assessment.assessmentId] = _candidate(
            "assessments",
            assessment.assessmentId,
            _assessment_references(assessment),
            identity,
        )
        _count_displayed(counts, "assessments", "candidate")
    for entry in records.signals:
        applied.labels[entry.signal_id] = _candidate(
            "detection_signals", entry.signal_id, _signal_references(entry)
        )
        _count_displayed(counts, "detection_signals", "candidate")
    for entry in records.observations:
        applied.labels[entry.command_name] = _observation_label(entry)
        _count_displayed(counts, "verification_observations", "candidate")
    supplied = {
        "assessments": len(records.assessments),
        "detection_signals": len(records.signals),
        "verification_observations": len(records.observations),
        # The two matrix-owned collections are read by no pane of a task-context review, and their
        # ``not_selected`` channel says so; the claims the owner *did* supply are counted here so a
        # reader sees the population the owner answered with beside the pane that displays none.
        "authored_effects": 0,
        "evidence_claims": len(records.claims),
    }
    for name in REVIEW_APPLICABILITY_CLASSES:
        counted = counts.get(name, _empty_state_counts())
        if name == "evidence_claims":
            counted = counted | {"candidate": supplied[name]}
        applied.summaries[name] = _summary(
            name, supplied[name], counted, matrix_owned=name in _MATRIX_OWNED
        )
    return applied


def _count_displayed(
    counts: dict[ReviewApplicabilityClass, dict[str, int]],
    records: ReviewApplicabilityClass,
    state: str,
) -> None:
    """Count one displayed record under its collection and treatment."""

    counted = counts.setdefault(records, _empty_state_counts())
    counted[state] += 1


def _classify_assessments(
    applied: AppliedRecords,
    assessments: Sequence[ReviewAssessment],
    selection: RecordedSelection,
    known: _KnownSubjects,
) -> None:
    """Project every stored assessment onto the treatment its own recorded binding earns."""

    counts = _empty_state_counts()
    for assessment in assessments:
        identity = _assessment_identity(assessment)
        revisions = _assessment_revisions(assessment)
        references = _assessment_references(assessment)
        if identity is None:
            applied.labels[assessment.assessmentId] = _comparison_subject_label(
                assessment, references, selection
            )
            counts[applied.labels[assessment.assessmentId].state] += 1
            continue
        if selection.is_subject(identity):
            applied.labels[assessment.assessmentId] = _subject_bound_label(
                assessment, identity, revisions, references, selection
            )
            counts[applied.labels[assessment.assessmentId].state] += 1
            continue
        path = selection.relationship_of(identity)
        if path is not None:
            applied.context.append(
                _context_record(
                    _Context(
                        records="assessments",
                        record_id=assessment.assessmentId,
                        label=f"assessment/{assessment.subject.kind}",
                        identity=identity,
                        relationship=path,
                        revision_ids=revisions,
                        references=references,
                        detail=(
                            f"this assessment's recorded subject is the {identity.kind} "
                            f"{identity.record_id}, which the selected subject's recorded "
                            f"relationships reach through {path}; it is displayed as labelled "
                            "context with its own subject visible and none of its finding, which "
                            "belongs to that subject's own review"
                        ),
                    ),
                    author_ref=assessment.provenance.authorRef,
                    role_ref=assessment.provenance.authorRole,
                )
            )
            counts["context"] += 1
            continue
        if known.holds(identity):
            counts["unrelated"] += 1
            continue
        applied.labels[assessment.assessmentId] = _unresolved(
            "assessments",
            assessment.assessmentId,
            references,
            _UNRESOLVED_SUBJECT_DETAIL,
            identity,
        )
        counts["unresolved"] += 1
    applied.summaries["assessments"] = _summary(
        "assessments", len(assessments), counts, matrix_owned=False
    )


def _subject_bound_label(
    assessment: ReviewAssessment,
    identity: RecordedIdentity,
    revisions: tuple[str, ...],
    references: tuple[str, ...],
    selection: RecordedSelection,
) -> ReviewDisplayedApplicability:
    """The treatment one assessment earns whose own recorded subject is the selected one."""

    if not selection.subject_revisions:
        return _unresolved(
            "assessments",
            assessment.assessmentId,
            references,
            "the record's recorded subject is the selected subject, and this comparison selected "
            "no revision of it, so the revision binding the record carries could not be resolved "
            "against a selection",
            identity,
        )
    recorded = _revision_binding(revisions, selection, identity)
    if recorded.foreign:
        return _unresolved(
            "assessments",
            assessment.assessmentId,
            references,
            f"this assessment judges the selected {identity.kind} {identity.record_id} and its own "
            f"recorded revision reference(s) {', '.join(recorded.foreign)} name revisions of another "
            "identity this comparison records, so the revision binding contradicts the subject "
            "binding and could not be resolved",
            identity,
        )
    if not recorded.of_subject:
        return _unresolved(
            "assessments",
            assessment.assessmentId,
            references,
            f"this assessment judges the selected {identity.kind} {identity.record_id} and its own "
            f"recorded revision reference(s) {', '.join(sorted(revisions))} name no revision this "
            "comparison records for that subject, so the revision binding could not be resolved; a "
            "reference that is not a recorded revision of the subject is never matched against the "
            "selection's revisions",
            identity,
        )
    declared = _declared_candidate_tree(assessment)
    displayed = None if selection.generation is None else selection.generation.after_code_tree_id
    if declared is not None and displayed is not None and declared != displayed:
        return ReviewDisplayedApplicability(
            records="assessments",
            record_id=assessment.assessmentId,
            state="historical",
            subject_kind=identity.kind,
            subject_id=identity.record_id,
            subject_revision_ids=revisions,
            references=references,
            detail=(
                f"this assessment judges the selected {identity.kind} {identity.record_id} and its "
                f"own recorded input code-tree:candidate is {declared}, which is not the candidate "
                f"tree {displayed} the displayed comparison bound; it is displayed as historical "
                "input of that subject and never as this generation's result, and its dependency "
                "currentness is measured separately (ICR-R15@v1)"
            ),
        )
    matched = sorted(set(recorded.of_subject) & selection.subject_revisions)
    if matched:
        return ReviewDisplayedApplicability(
            records="assessments",
            record_id=assessment.assessmentId,
            state="direct",
            subject_kind=identity.kind,
            subject_id=identity.record_id,
            subject_revision_ids=revisions,
            references=references,
            detail=(
                f"this assessment's recorded subject is the selected {identity.kind} "
                f"{identity.record_id} and its recorded revisions {', '.join(matched)} are "
                f"{_retention_basis(matched, selection)}"
                + (
                    ""
                    if declared is None
                    else f"; its recorded input code-tree:candidate is {declared}, the candidate "
                    "tree the displayed comparison bound"
                )
            ),
        )
    return ReviewDisplayedApplicability(
        records="assessments",
        record_id=assessment.assessmentId,
        state="historical",
        subject_kind=identity.kind,
        subject_id=identity.record_id,
        subject_revision_ids=revisions,
        references=references,
        detail=(
            f"this assessment judges the selected {identity.kind} {identity.record_id} on the "
            f"recorded revision(s) {', '.join(sorted(recorded.of_subject))}, which the recorded "
            "population this classification read does not carry for that subject; it is displayed "
            "as historical input of that subject rather than beside the revisions this comparison "
            "selected"
        ),
    )


def _retention_basis(matched: Sequence[str], selection: RecordedSelection) -> str:
    """What a `direct` label may truthfully say about *why* those revisions matched.

    The match is made against the recorded population this classification read, which is the
    selection's own: the snapshots' recorded revisions of the subject, not the comparison page's
    retained list. When ICR-R07's own recorded selection also lists every matched revision (it is
    published for the page the caller asked for, so at a bounded page it may list fewer), the label
    cites it; when it does not, the label names the basis it actually used instead of asserting a
    published list that does not carry those revisions (``ICR-R26@v1``).
    """

    published = selection.published_retained
    if published and set(matched) <= published:
        return "revisions ICR-R07's own recorded selection lists as retained for that subject"
    return (
        "revisions the two snapshots record for that subject, which this classification reads from "
        "the selection itself rather than from the retained list this page published"
    )


@dataclass(frozen=True)
class _RevisionBinding:
    """How one record's recorded revision references resolved against the selected subject.

    ``of_subject`` are the references that name a revision this comparison records *for the subject*,
    and ``foreign`` are the ones that name a revision it records for a different identity. A
    reference in neither is not a recorded revision at all, and it is reported as unresolved rather
    than matched: a record that carries an identity where a revision belongs is exactly the malformed
    binding the packet's Failure And Recovery Behavior refuses to resolve.
    """

    of_subject: tuple[str, ...]
    foreign: tuple[str, ...]


def _revision_binding(
    revisions: Sequence[str], selection: RecordedSelection, identity: RecordedIdentity
) -> _RevisionBinding:
    """Resolve a record's recorded revision references against the selected subject's identity."""

    of_subject: list[str] = []
    foreign: list[str] = []
    for revision in revisions:
        recorded = selection.identity_of(revision)
        if recorded is None:
            continue
        if recorded == identity:
            of_subject.append(revision)
        else:
            foreign.append(revision)
    return _RevisionBinding(of_subject=tuple(sorted(of_subject)), foreign=tuple(sorted(foreign)))


def _comparison_subject_label(
    assessment: ReviewAssessment,
    references: tuple[str, ...],
    selection: RecordedSelection,
) -> ReviewDisplayedApplicability:
    """The treatment one comparison-subject assessment earns.

    A comparison subject is the one subject kind whose recorded identity is an *authored reference*
    rather than a stored identity, so the only recorded match this surface can make is exact
    equality with the displayed comparison's own published reference or binding digest. Anything
    else is unresolved -- never a match by similarity, label or nearest generation, which is the
    packet's own Failure And Recovery Behavior.
    """

    recorded = assessment.subject.comparisonRef or assessment.comparisonRef
    generation = selection.generation
    if generation is not None and recorded in (generation.reference, generation.binding_digest):
        return ReviewDisplayedApplicability(
            records="assessments",
            record_id=assessment.assessmentId,
            state="direct",
            subject_kind="comparison",
            subject_id=recorded,
            references=references,
            detail=(
                "this assessment's recorded comparison reference is the displayed comparison's own "
                "published reference, so it judges the comparison this review displays"
            ),
        )
    return _unresolved(
        "assessments",
        assessment.assessmentId,
        references,
        "this assessment judges a comparison through an authored reference, and that reference is "
        "not the displayed comparison's own published reference or binding digest; the comparison "
        "it judges could not be resolved, so it is displayed with the references it carries and "
        "never as support for the selected subject",
        RecordedIdentity(kind="comparison", record_id=recorded),
    )


def _classify_signals(
    applied: AppliedRecords,
    signals: Sequence[DetectionSignalPayload],
    selection: RecordedSelection,
) -> None:
    """Project every supplied detection signal onto its recorded relationship paths.

    A signal records the relationship rows it walked and the item each walk reached. Those are the
    only bindings it carries, so a path that reaches one of the selection's own revisions or
    relationships is direct evidence about the subject (or labelled context when it reaches a
    related identity), and a path that reaches nothing this selection holds is unresolved -- shown
    with the paths it recorded, never dropped and never read as support.
    """

    counts = _empty_state_counts()
    for entry in signals:
        matched = _match_references(_signal_bindings(entry), selection)
        references = _signal_references(entry)
        if matched.state == "context" and matched.identity is not None:
            applied.context.append(
                _context_record(
                    _Context(
                        records="detection_signals",
                        record_id=entry.signal_id,
                        label=entry.condition,
                        identity=matched.identity,
                        relationship=selection.relationship_of(matched.identity)
                        or f"recorded relationship {matched.reference}",
                        references=references,
                        detail=(
                            f"this detection signal's recorded path reached "
                            f"{matched.reference}, which belongs to the {matched.identity.kind} "
                            f"{matched.identity.record_id} that the selected subject's recorded "
                            "relationships reach; it is displayed as labelled context and never as "
                            "a judgment on the selected subject"
                        ),
                    )
                )
            )
            counts["context"] += 1
            continue
        if matched.state == "direct" and matched.identity is not None:
            applied.labels[entry.signal_id] = ReviewDisplayedApplicability(
                records="detection_signals",
                record_id=entry.signal_id,
                state="direct",
                subject_kind=matched.identity.kind,
                subject_id=matched.identity.record_id,
                references=references,
                detail=(
                    f"this detection signal's recorded path reached {matched.reference}, a recorded "
                    f"revision or relationship of the selected {matched.identity.kind} "
                    f"{matched.identity.record_id}"
                ),
            )
            counts["direct"] += 1
            continue
        applied.labels[entry.signal_id] = _unresolved(
            "detection_signals", entry.signal_id, references, _UNRESOLVED_DETAIL
        )
        counts["unresolved"] += 1
    applied.summaries["detection_signals"] = _summary(
        "detection_signals", len(signals), counts, matrix_owned=False
    )


def _classify_observations(
    applied: AppliedRecords, observations: Sequence[VerificationObservationPayload]
) -> None:
    """Label every supplied verification observation as the candidate's own execution input.

    An observation records the candidate it tested and no subject at all, and the evidence owner
    selected it by that candidate binding. It is therefore ``candidate``: displayed as the
    candidate's own input, never as a judgment on the selected subject -- which is a different fact
    from an unresolved binding, and neither is a clearance.
    """

    counts = _empty_state_counts()
    for entry in observations:
        applied.labels[entry.command_name] = _observation_label(entry)
        counts["candidate"] += 1
    applied.summaries["verification_observations"] = _summary(
        "verification_observations", len(observations), counts, matrix_owned=False
    )


def _classify_rows(
    applied: AppliedRecords,
    records: ReviewRecordInputs,
    rows: Sequence[ReviewMatrixRow],
    selection: RecordedSelection,
) -> None:
    """Project every matrix row onto the recorded references the row itself carries.

    The review matrix is candidate-wide and a row's own ``subject`` is the *record* it shows rather
    than the identity that record is about, so a row is classified by the references it records: an
    evidence claim's own subject revision (read from the claim's owner), an effect claim's
    realization references, a requirement revision's own revision identity. A row none of whose
    references this selection holds is unresolved -- the composition cannot establish which subject
    it belongs to -- and it is displayed with those references and that reason rather than as a
    judgment on the selected subject.
    """

    counts: dict[ReviewApplicabilityClass, dict[str, int]] = {}
    for row in rows:
        kind = _row_class(row)
        if kind is None:
            continue
        references = _row_references(row, records)
        matched = _match_references(_row_bindings(row, records), selection)
        counted = counts.setdefault(kind, _empty_state_counts())
        if matched.state == "context" and matched.identity is not None:
            applied.context.append(
                _context_record(
                    _Context(
                        records=kind,
                        record_id=row.subject.record_id,
                        label=row.subject.record_kind,
                        identity=matched.identity,
                        relationship=selection.relationship_of(matched.identity)
                        or f"recorded relationship {matched.reference}",
                        references=references,
                        detail=(
                            f"this matrix row records {matched.reference}, which belongs to the "
                            f"{matched.identity.kind} {matched.identity.record_id} the selected "
                            "subject's recorded relationships reach; it is displayed as labelled "
                            "context and never as a judgment on the selected subject"
                        ),
                    )
                )
            )
            counted["context"] += 1
            continue
        if matched.state == "direct" and matched.identity is not None:
            applied.labels[row.subject.record_id] = ReviewDisplayedApplicability(
                records=kind,
                record_id=row.subject.record_id,
                state="direct",
                subject_kind=matched.identity.kind,
                subject_id=matched.identity.record_id,
                references=references,
                detail=(
                    f"this matrix row records {matched.reference}, a recorded revision or "
                    f"relationship of the selected {matched.identity.kind} "
                    f"{matched.identity.record_id}"
                ),
            )
            counted["direct"] += 1
            continue
        applied.labels[row.subject.record_id] = _unresolved(
            kind, row.subject.record_id, references, _UNRESOLVED_DETAIL
        )
        counted["unresolved"] += 1
    for name in _MATRIX_OWNED:
        supplied = sum(1 for row in rows if _row_class(row) == name)
        applied.summaries[name] = _summary(
            name, supplied, counts.get(name, _empty_state_counts()), matrix_owned=True
        )


# --- the record bindings each class records ---------------------------------------------------


def _assessment_identity(assessment: ReviewAssessment) -> RecordedIdentity | None:
    """The subject identity one assessment records, or ``None`` for a comparison subject.

    The subject kinds are the vocabulary's own: an ``invariant-revision`` or ``family`` subject names
    a canonical identity, a ``knowledge-record`` subject names a stored record this surface resolves
    by identity, and a ``comparison`` subject judges a comparison rather than a knowledge identity --
    which is why it has no identity here and is classified against the displayed comparison instead.
    """

    kind = str(assessment.subject.kind)
    if kind == "invariant-revision":
        return RecordedIdentity(kind="invariant", record_id=str(assessment.subject.recordId))
    if kind == "family":
        return RecordedIdentity(kind="family", record_id=str(assessment.subject.recordId))
    if kind == "knowledge-record":
        return RecordedIdentity(kind=kind, record_id=str(assessment.subject.recordId))
    return None


def _assessment_revisions(assessment: ReviewAssessment) -> tuple[str, ...]:
    return (*assessment.subject.beforeRevisionIds, *assessment.subject.afterRevisionIds)


def _assessment_bindings(assessment: ReviewAssessment) -> tuple[str, ...]:
    """The raw recorded identities one assessment's classification is decided from."""

    declared = _declared_candidate_tree(assessment)
    return (
        *_assessment_revisions(assessment),
        *(() if declared is None else (declared,)),
        assessment.comparisonRef,
        assessment.subject.comparisonRef or "",
    )


def _assessment_references(assessment: ReviewAssessment) -> tuple[str, ...]:
    """The same bindings, spelled as the recorded fields a reader can go and re-read."""

    declared = _declared_candidate_tree(assessment)
    references = [
        f"subject:{assessment.subject.kind}:{assessment.subject.recordId}",
        *(f"revision:{revision}" for revision in _assessment_revisions(assessment)),
        f"comparisonRef:{assessment.comparisonRef}",
        f"scopeManifestRef:{assessment.scopeManifestRef}",
    ]
    if declared is not None:
        references.append(f"code-tree:candidate:{declared}")
    if assessment.subject.comparisonRef is not None:
        references.append(f"subject.comparisonRef:{assessment.subject.comparisonRef}")
    return tuple(references)


def _declared_candidate_tree(assessment: ReviewAssessment) -> str | None:
    """The candidate code tree one assessment's own examined-input declaration names, if it does.

    This is the one *generation-bearing* input this surface can compare: the declaration records it
    as a Git object and the comparison publishes the candidate tree it captured, so equality is an
    identity match and inequality is another candidate -- never a nearest generation.
    """

    for edge in assessment.examinedInputs.declaration.edges:
        if edge.kind == _CANDIDATE_TREE_KIND and edge.name == _CANDIDATE_TREE_NAME:
            return str(edge.digest)
    return None


def _signal_bindings(entry: DetectionSignalPayload) -> tuple[str, ...]:
    """The raw recorded identities one detection signal's classification is decided from."""

    return tuple(
        binding
        for path in entry.relationship_paths
        for binding in (path.reached_item_id, *path.edges)
    )


def _signal_references(entry: DetectionSignalPayload) -> tuple[str, ...]:
    return tuple(f"{path.path_id}:{path.reached_item_id}" for path in entry.relationship_paths) or (
        f"signal:{entry.signal_id} (no recorded relationship path)",
    )


def _row_class(row: ReviewMatrixRow) -> ReviewApplicabilityClass | None:
    """Which displayed collection one matrix row belongs to, or ``None`` when no pane shows it."""

    if row.subject.record_kind == "evidence_claim":
        return "evidence_claims"
    if row.subject.record_kind in AUTHORED_EFFECT_KINDS:
        return "authored_effects"
    return None


def _row_bindings(row: ReviewMatrixRow, records: ReviewRecordInputs) -> tuple[str, ...]:
    """The raw recorded identities one matrix row's classification is decided from.

    The row's own identity is deliberately absent: it is the record the row shows, not a reference
    to anything, so matching it would make every row look resolved against itself.
    """

    claim = _claim_of(row, records)
    return (
        *(
            ()
            if claim is None or claim.subject_revision_id is None
            else (claim.subject_revision_id,)
        ),
        *(() if row.requirement_revision_id is None else (row.requirement_revision_id,)),
        *(() if row.requirement_record_id is None else (row.requirement_record_id,)),
        *(() if row.proposed_effect_claim_id is None else (row.proposed_effect_claim_id,)),
        *(() if row.preservation_claim_id is None else (row.preservation_claim_id,)),
        *row.evidence_claim_ids,
        *row.record_ids,
    )


def _row_references(row: ReviewMatrixRow, records: ReviewRecordInputs) -> tuple[str, ...]:
    """The same bindings, spelled as the recorded fields a reader can go and re-read."""

    claim = _claim_of(row, records)
    references = [
        *(
            ()
            if claim is None or claim.subject_revision_id is None
            else (f"claim.subjectRevision:{claim.subject_revision_id}",)
        ),
        *(
            ()
            if row.requirement_revision_id is None
            else (f"requirement_revision:{row.requirement_revision_id}",)
        ),
        *(
            ()
            if row.proposed_effect_claim_id is None
            else (f"effect_claim:{row.proposed_effect_claim_id}",)
        ),
        *(
            ()
            if row.preservation_claim_id is None
            else (f"preservation_claim:{row.preservation_claim_id}",)
        ),
        *(f"evidence_claim:{claim_id}" for claim_id in row.evidence_claim_ids),
        *(f"record:{record_id}" for record_id in row.record_ids),
    ]
    return tuple(references) or (f"{row.subject.record_kind}:{row.subject.record_id}",)


def _claim_of(row: ReviewMatrixRow, records: ReviewRecordInputs) -> ReviewClaimRecord | None:
    for claim in records.claims:
        if claim.claim_id == row.subject.record_id:
            return claim
    return None


# --- the recorded match -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Matched:
    """What one record's recorded bindings resolved to, or that nothing resolved."""

    state: str
    identity: RecordedIdentity | None = None
    reference: str | None = None


def _match_references(bindings: Sequence[str], selection: RecordedSelection) -> _Matched:
    """Resolve a record's own recorded references against the selection's recorded population.

    Three recorded indexes are consulted, in one order: the relationship rows the union holds (a row
    under the selected subject's identity is direct, one under a related identity is context), the
    revisions the selection reached, and the recorded realization paths. A reference that resolves to
    no identity this selection reaches leaves the record unresolved, which is a different state from
    a record that resolved to *another* subject's identity.
    """

    for reference in bindings:
        if not reference:
            continue
        identity = (
            selection.identity_of_relationship(reference)
            or selection.identity_of(reference)
            or selection.path_identity.get(reference)
        )
        if identity is None:
            continue
        state = "direct" if selection.is_subject(identity) else "context"
        return _Matched(state=state, identity=identity, reference=reference)
    return _Matched(state="unresolved")


class _KnownSubjects:
    """R09's catalogue, read at most once and only when a record names a subject to resolve.

    The read is deferred because the ordinary review has nothing to resolve: a record whose own
    subject the selection reaches is classified from the comparison's own union, and only a record
    that names some *other* identity asks whether that identity is a subject this comparison records
    -- which is the difference between "another subject, displayed under its own review" and "an
    identity nothing records, exposed as unresolved".
    """

    def __init__(self, read: Callable[[], Sequence[ReviewEntry]]) -> None:
        self._read = read
        self._subjects: tuple[ReviewEntry, ...] | None = None

    def _catalogue(self) -> tuple[ReviewEntry, ...]:
        if self._subjects is None:
            self._subjects = tuple(self._read())
        return self._subjects

    def holds(self, identity: RecordedIdentity) -> bool:
        """Whether this comparison records the identity under exactly this kind."""

        return any(
            row.selector_kind == identity.kind and row.selector_id == identity.record_id
            for row in self._catalogue()
        )

    def holds_identity(self, record_id: str) -> bool:
        """Whether this comparison records the identity under any kind it lists."""

        return any(row.selector_id == record_id for row in self._catalogue())


# --- the labels and summaries -----------------------------------------------------------------


@dataclass(frozen=True)
class _Context:
    """One record displayed as labelled context, as the fields its pane value carries.

    It exists so the one construction site per collection stays a named-fields value rather than a
    ten-argument call: a context row is one fact per field, and a positional call with ten of them is
    how a row comes to display one record's identity beside another record's subject.
    """

    records: ReviewApplicabilityClass
    record_id: str
    label: str
    identity: RecordedIdentity
    relationship: str
    detail: str
    revision_ids: tuple[str, ...] = ()
    references: tuple[str, ...] = ()


def _context_record(
    context: _Context, *, author_ref: str | None = None, role_ref: str | None = None
) -> ReviewContextRecord:
    """One labelled context row: the record's own identity, its true subject and the path reached."""

    return ReviewContextRecord(
        records=context.records,
        record_id=context.record_id,
        label=context.label or _NO_LABEL,
        subject_kind=context.identity.kind,
        subject_id=context.identity.record_id,
        subject_revision_ids=context.revision_ids,
        relationship=context.relationship,
        author_ref=author_ref,
        role_ref=role_ref,
        references=context.references,
        detail=context.detail,
    )


def _unresolved(
    records: ReviewApplicabilityClass,
    record_id: str,
    references: tuple[str, ...],
    detail: str,
    subject: RecordedIdentity | None = None,
) -> ReviewDisplayedApplicability:
    """One record whose binding could not be resolved, displayed with its actual references."""

    return ReviewDisplayedApplicability(
        records=records,
        record_id=record_id,
        state="unresolved",
        subject_kind=None if subject is None else subject.kind,
        subject_id=None if subject is None else subject.record_id,
        references=references or (f"record:{record_id}",),
        detail=detail,
    )


def _candidate(
    records: ReviewApplicabilityClass,
    record_id: str,
    references: tuple[str, ...],
    subject: RecordedIdentity | None = None,
) -> ReviewDisplayedApplicability:
    """One record bound to the compared candidate rather than to a subject of this selection."""

    return ReviewDisplayedApplicability(
        records=records,
        record_id=record_id,
        state="candidate",
        subject_kind=None if subject is None else subject.kind,
        subject_id=None if subject is None else subject.record_id,
        references=references or (f"record:{record_id}",),
        detail=_CANDIDATE_DETAIL,
    )


def _observation_label(entry: VerificationObservationPayload) -> ReviewDisplayedApplicability:
    """One verification observation, displayed as the candidate's own execution input."""

    return ReviewDisplayedApplicability(
        records="verification_observations",
        record_id=entry.command_name,
        state="candidate",
        references=_observation_references(entry),
        detail=(
            "the evidence owner selected this observation by the candidate binding the record itself "
            "carries, and the record names no invariant or family: it is displayed as the "
            "candidate's own execution input and never as a judgment on the selected subject"
        ),
    )


def _observation_references(entry: VerificationObservationPayload) -> tuple[str, ...]:
    candidate = (
        None if entry.knowledge_candidate is None else entry.knowledge_candidate.logical_digest
    )
    return tuple(
        reference
        for reference in (
            f"observation:{entry.command_name}",
            None if candidate is None else f"knowledge_candidate:{candidate}",
            f"code_candidate_tree:{entry.code_candidate_tree_id}",
        )
        if reference is not None
    )


def _summary(
    records: ReviewApplicabilityClass,
    supplied: int,
    counts: Mapping[str, int],
    *,
    matrix_owned: bool,
) -> ReviewApplicabilitySummary:
    """One supplied collection's six-way count, with the exclusion stated when there is one."""

    displayed = (
        counts.get("direct", 0)
        + counts.get("historical", 0)
        + counts.get("context", 0)
        + counts.get("candidate", 0)
        + counts.get("unresolved", 0)
    )
    unrelated = counts.get("unrelated", 0)
    detail = (
        f"{supplied} supplied record(s) of this class: {displayed} displayed with the treatment each "
        f"record's own recorded binding earned and {unrelated} not displayed as the selected "
        "subject's judgments"
    )
    if matrix_owned:
        detail += (
            "; the review matrix page supplied these rows, and the owner's complete collection count "
            "travels on the evidence channel"
        )
    if unrelated:
        detail += ". " + _EXCLUDED_DETAIL.format(count=unrelated)
    return ReviewApplicabilitySummary(
        records=records,
        supplied=supplied,
        direct=counts.get("direct", 0),
        historical=counts.get("historical", 0),
        context=counts.get("context", 0),
        candidate=counts.get("candidate", 0),
        unresolved=counts.get("unresolved", 0),
        unrelated=unrelated,
        detail=detail,
    )
