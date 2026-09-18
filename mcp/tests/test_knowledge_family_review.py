"""``KS-R16@v1`` §2, §3, §4 and §5: facts, separated statuses, currentness, routing and the decision.

These cases protect the pipeline *between* the record leaves. They occupy the ``unit-regression`` lane
because what they measure is a composition over typed records -- which matches a merge keeps, which
status an owner may report, what a moved input does to a binding, and which counts a worklist row may
move -- rather than a process, a publication or a Git object.

Every case names the failure it catches: a merge that silently dropped a contributing match, a
semantic field reachable through a pipeline record, a missing review rendered as a clearance, five
statuses collapsed into one verdict, a stale binding reused as if it were current, a family-review row
folded into the shipped actionability formula, and a gate-consequence decision that cannot name its
owner.
"""

from __future__ import annotations

from typing import get_args
from uuid import uuid4

import pytest
from agents_remember.memory_quality.curator_checklist import curator_actionable_count
from agents_remember.memory_quality.family_review import (
    compose_currentness,
    compose_status_report,
    curator_currentness_status,
    curator_review_status,
    detector_status,
    family_review_summaries,
    group_detection_facts,
    reported_subject_status,
    route_family_review,
)
from agents_remember.memory_quality.knowledge_review import (
    KNOWLEDGE_REVIEW_HEADING,
    knowledge_review_section,
)
from agents_remember.models.knowledge import family_review, registered_scope
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.detection import (
    DETECTION_EXTRACTOR_VERSION,
    DETECTION_POLICY_VERSION,
    NO_SEMANTIC_ASSESSMENT_LIMITATION,
    DetectionCounterpartProbe,
    DetectionInputSide,
    DetectionObservedChange,
    DetectionRecordedInputSet,
    DetectionRelationshipPath,
    DetectionScopeManifest,
    DetectionSignalPayload,
    conclusion_bearing_fields,
    observed_basis_detail,
)
from agents_remember.models.knowledge.family_review import (
    CLOSEOUT_READINESS_DECIDER,
    FACT_GROUPING_POLICY_VERSION,
    FAMILY_REVIEW_ROUTING_SURFACES,
    PIPELINE_STATUS_OWNERS,
    RECORDED_GATE_CONSEQUENCE_DECISION,
    STATUS_VOCABULARIES,
    FactMatch,
    FamilyReviewGateDecision,
    FamilyReviewRouting,
    FamilyReviewRoutingRow,
    FindingCurrentness,
)
from agents_remember.models.knowledge.read import KnowledgeReadContext
from agents_remember.models.knowledge.result import KnowledgeRefusalCode
from agents_remember.models.lifecycles.evidence_dependencies import (
    build_evidence_dependencies,
    dependency,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentProvenance,
    AssessmentSubject,
    ExaminedInputs,
    ReviewAssessment,
    ReviewAssessmentRevision,
)
from pydantic import ValidationError

pytestmark = pytest.mark.evidence_unit

REPOSITORY_ID = str(uuid4())
ROUTE_ID = str(uuid4())
FAMILY_REVISION = str(uuid4())
INVARIANT_REVISION = str(uuid4())
DIGEST_BEFORE = "b" * 64
DIGEST_AFTER = "c" * 64
CONCERNING = "source_changed_on_both_sides_joined_to_same_family"


def _side(side: str, digest: str) -> DetectionInputSide:
    return DetectionInputSide(
        side=side,  # type: ignore[arg-type]
        context=KnowledgeReadContext(
            repository_id=REPOSITORY_ID,
            knowledge=SnapshotIdentity(
                repository_id=REPOSITORY_ID,
                schema_version="ar-knowledge-sqlite/v6",
                logical_digest=digest,
            ),
        ),
        selector_digest=digest,
        selector_policy_version="recorded-family-frontier/v1",
    )


def _input_set() -> DetectionRecordedInputSet:
    return DetectionRecordedInputSet(
        declared="union_of_both_sides",
        sides=(_side("before", DIGEST_BEFORE), _side("after", DIGEST_AFTER)),
        counterpart_probe=(
            DetectionCounterpartProbe(
                item_id="claim-a", item_kind="realization", coverage="selected_both"
            ),
        ),
    )


def _manifest() -> DetectionScopeManifest:
    return DetectionScopeManifest(
        manifest_ref="manifest-17",
        retention_required=False,
        destination_kind="enclosure_local",
        retention_basis="retention is required only when a durable record needs the manifest",
    )


def signal(*, claims: tuple[str, ...], condition: str = CONCERNING) -> DetectionSignalPayload:
    """Build one valid facts-only signal over the shared family, as ``KS-R14@v1`` emits it."""

    paths = tuple(
        DetectionRelationshipPath(
            path_id=f"family:{FAMILY_REVISION}/claim:{claim}",
            snapshot_side="after",
            edges=(FAMILY_REVISION, INVARIANT_REVISION, claim),
            reached_item_id=claim,
        )
        for claim in claims
    )
    changes = tuple(
        DetectionObservedChange(
            item_id=claim,
            item_kind="realization",
            granularity="source_file_changed",
            path=f"src/{claim}.py",
            locator_kind="file",
        )
        for claim in claims
    )
    limitations = (NO_SEMANTIC_ASSESSMENT_LIMITATION,)
    return DetectionSignalPayload(
        signal_id=str(uuid4()),
        repository_id=REPOSITORY_ID,
        governing_route_id=ROUTE_ID,
        condition=condition,  # type: ignore[arg-type]
        input_set=_input_set(),
        observed_changes=changes,
        relationship_paths=paths,
        extractor_version=DETECTION_EXTRACTOR_VERSION,
        policy_version=DETECTION_POLICY_VERSION,
        scope_manifest=_manifest(),
        registered_scope_status="complete_for_declared_policy",
        unmapped_changed_paths=(),
        limitations=limitations,
        detail=observed_basis_detail(
            condition=condition,
            relationship_paths=tuple(path.path_id for path in paths),
            limitations=limitations,
        ),
    )


def assessment(
    *,
    disposition: str = "concern_found",
    finding: str = "the combination can exceed the budget under the examined assumptions",
    digests: tuple[str, str] = (DIGEST_BEFORE, DIGEST_AFTER),
    subject_kind: str = "family",
) -> ReviewAssessment:
    """Build one stored assessment through the authored/revision shapes ``KS-R15@v1`` declares."""

    revision = ReviewAssessmentRevision(
        assessmentId=f"assessment-{uuid4()}",
        subject=AssessmentSubject(
            kind=subject_kind,  # type: ignore[arg-type]
            recordId=FAMILY_REVISION,
            beforeRevisionIds=(FAMILY_REVISION,),
        ),
        disposition=disposition,  # type: ignore[arg-type]
        finding="" if disposition == "no_concern_found" else finding,
        rationale="the inspected call path permits all attempts and has no earlier shared deadline",
        comparisonRef="comparison-B-M",
        scopeManifestRef="scope-B-M",
        evidenceRefs=(),
    )
    declaration = build_evidence_dependencies(
        "review-assessment/v1",
        (
            dependency("candidate-state", "knowledge-snapshot/candidate", digests[1]),
            dependency("code-tree", "source-tree/candidate", digests[1], algorithm="git-object"),
            dependency("task-intent", "task/260915-KS-L16", digests[0]),
            dependency("semantic-topology", "policy/family-detection", digests[0]),
            dependency("evidence-bytes", "review-fixture-assumptions", digests[0]),
            dependency("validator", "evidence-dependency-validator", digests[0]),
        ),
    )
    return ReviewAssessment(
        **revision.model_dump(),
        examinedInputs=ExaminedInputs(declaration=declaration),
        provenance=AssessmentProvenance(
            authorRef="agent:curator",
            authorRole="curator",
            publicationRef="notes/reports/curator-memory-quality.json",
        ),
    )


def current_measurement(
    record: ReviewAssessment, *, digest: str = "d" * 64
) -> dict[str, dict[tuple[str, str], tuple[str, str]]]:
    """Return a measurement of the world in which one named input has moved.

    The digest is a value no recorded edge carries, so the measurement is a genuine move rather than a
    re-statement of the recorded binding: a comparison fed the recorded values back would report
    ``current`` and prove nothing about the clause.
    """

    recorded = record.examinedInputs.identities
    measured = dict(recorded)
    first = sorted(measured)[0]
    measured[first] = (recorded[first][0], digest)
    return {record.assessmentId: measured}


# ---------------------------------------------------------------------------
# §2: facts, and the merge that may not drop one.


def test_signals_about_one_subject_and_one_input_set_merge_into_one_group() -> None:
    """§2.2: dedup may group by family and input snapshots, and that is exactly what it groups by.

    Catches a grouping key that reached for a path, a label or a title: two runs over the same
    recorded subject and the same declared inputs would then produce different groups.
    """

    first, second = signal(claims=("claim-a",)), signal(claims=("claim-b",))
    groups = group_detection_facts((first, second))

    assert len(groups) == 1
    assert groups[0].family_revision_ids == (FAMILY_REVISION,)
    assert groups[0].grouping_policy_version == FACT_GROUPING_POLICY_VERSION
    assert groups[0].subject_id == f"family:{FAMILY_REVISION}"
    assert groups[0].matches


def test_a_merge_retains_every_contributing_condition_path_and_edge() -> None:
    """§2.2: grouping must retain every matched condition and its supporting paths and edges.

    Catches the lossy merge: two contributing claims arrive as one group and both must still be
    findable in it, with both paths and every edge step preserved.
    """

    groups = group_detection_facts((signal(claims=("claim-a",)), signal(claims=("claim-b",))))
    group = groups[0]

    assert group.matched_conditions() == (CONCERNING, CONCERNING)
    assert group.supporting_paths() == ("src/claim-a.py", "src/claim-b.py")
    assert len(group.supporting_edges()) == 6
    assert group.retains(
        (
            FactMatch(
                condition=CONCERNING,
                supporting_paths=("src/claim-a.py",),
                supporting_item_ids=("claim-a",),
            ),
            FactMatch(
                condition=CONCERNING,
                supporting_paths=("src/claim-b.py",),
                supporting_item_ids=("claim-b",),
            ),
        )
    )


def test_a_merge_that_dropped_a_contributing_match_is_reported_as_not_retaining() -> None:
    """§2.2: ``retains`` is a measurement, so a group that lost a match answers ``False``.

    Catches the assurance that replaces the comparison -- a merge asserted to be lossless rather than
    checked against the matches that contributed to it.
    """

    group = group_detection_facts((signal(claims=("claim-a",)),))[0]

    assert not group.retains(
        (FactMatch(condition=CONCERNING, supporting_item_ids=("claim-missing",)),)
    )


def test_no_pipeline_record_declares_a_conclusion_bearing_field() -> None:
    """§2.1: the review is mechanical, so it is run over every record this leaf adds.

    Catches a verdict, severity, explanation or compatibility field arriving in the pipeline's own
    vocabulary: ``KS-R14@v1``'s own review function reads the *declared* field set, so a field is
    reported whether or not any payload happens to populate it.
    """

    reviewed = (
        family_review.FactMatch,
        family_review.FactSupportingEdge,
        family_review.FamilyIntegrityFactGroup,
        family_review.PipelineStatusEntry,
        family_review.SeparatedStatusReport,
        family_review.FindingCurrentness,
        family_review.FamilyReviewRoutingRow,
        family_review.FamilyReviewRouting,
        family_review.FamilyReviewGateDecision,
        registered_scope.RegisteredScopeManifest,
        registered_scope.RegisteredScopeMembership,
        registered_scope.FollowedScopeEdge,
    )
    for model in reviewed:
        assert conclusion_bearing_fields(model) == (), model.__name__


def test_a_subject_with_no_stored_record_reports_no_record_and_never_a_clearance() -> None:
    """§4.2 and §3.1: missing stays missing -- a group is not a finding and absence is not a pass.

    Catches the promotion and the default together: the group carries no record identity, the report
    answers ``no-record-recorded``, and ``KS-R15@v1``'s own projection answers ``none-recorded``
    rather than a disposition nobody authored.
    """

    group = group_detection_facts((signal(claims=("claim-a",)),))[0]

    assert group.review_record_ids == ()
    assert curator_review_status(()) == "no-record-recorded"
    assert reported_subject_status((), {}, f"family:{FAMILY_REVISION}") == "none-recorded"
    assert "no_concern_found" not in group.model_dump_json()


def test_a_family_condition_recorded_on_a_shorter_path_is_reported_as_a_defect() -> None:
    """§2.2: the group subject comes from the recorded path shape, so a wrong shape is not guessed at.

    Catches a group keyed on something that happens to be an edge but is not the family revision: the
    pipeline reports the defect rather than filing the match under a subject nobody recorded.
    """

    broken = signal(claims=("claim-a",)).model_copy(
        update={
            "relationship_paths": (
                DetectionRelationshipPath(
                    path_id="claim:claim-a",
                    snapshot_side="after",
                    edges=(INVARIANT_REVISION, "claim-a"),
                    reached_item_id="claim-a",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="refuses to guess one from a shorter path"):
        group_detection_facts((broken,))


# ---------------------------------------------------------------------------
# §4.1 and §4.3-§4.6: the five statuses, and per-input currentness.


def test_the_five_status_owners_are_reported_separately_with_their_own_limits() -> None:
    """§4.1: five owners, five vocabularies, and each row names what it does not establish.

    Catches the collapse into one verdict: every owner is present exactly once, its status comes from
    its own closed vocabulary, and the limit sentence travels with it.
    """

    report = compose_status_report(
        {
            "structural-validator": "declared-checks-passed",
            "detector": "matched",
            "curator-reviewer": "no-record-recorded",
            "verification-runner": "assertions-executed",
            "authority-currentness": "dependencies-match",
        }
    )

    assert tuple(entry.owner for entry in report.entries) == PIPELINE_STATUS_OWNERS
    assert all(entry.does_not_establish.strip() for entry in report.entries)
    assert all(entry.status in STATUS_VOCABULARIES[entry.owner] for entry in report.entries)
    assert conclusion_bearing_fields(type(report)) == ()


def test_a_status_outside_its_owners_vocabulary_and_a_missing_owner_are_both_refused() -> None:
    """§4.1: one owner may not report another's fact, and an omitted owner is a collapse.

    Catches the two quiet collapses: a detector reporting a currentness status, and a report carrying
    four rows where the design declares five.
    """

    with pytest.raises(ValidationError, match="not one of its declared"):
        compose_status_report(
            {
                "structural-validator": "declared-checks-passed",
                "detector": "dependencies-match",
                "curator-reviewer": "no-record-recorded",
                "verification-runner": "assertions-executed",
                "authority-currentness": "dependencies-match",
            }
        )
    with pytest.raises(ValueError, match="reported nothing"):
        compose_status_report(
            {
                "structural-validator": "declared-checks-passed",
                "detector": "matched",
                "curator-reviewer": "no-record-recorded",
                "verification-runner": "assertions-executed",
            }
        )


def test_the_detector_and_curator_statuses_come_from_the_records_that_exist() -> None:
    """§4.1/§3.4: the two owners whose fact this leaf reads report their own state, including unresolved.

    Catches a default in either direction: no signals is ``no-match-under-declared-policy`` and never
    a clearance, and an unresolved authored record is a reported state rather than a gap.
    """

    assert detector_status(()) == "no-match-under-declared-policy"
    assert detector_status((signal(claims=("claim-a",)),)) == "matched"
    assert detector_status((), incomplete_scan=True) == "incomplete-scan"
    assert detector_status((), unresolved_inputs=True) == "unresolved-inputs"
    assert curator_review_status(("concern_found",)) == "record-recorded"
    assert curator_review_status(("unresolved",)) == "declared-unresolved"
    assert curator_review_status(()) == "no-record-recorded"


def test_a_current_binding_matches_and_one_moved_input_makes_it_stale() -> None:
    """§4.3/§4.4: currentness compares recorded identities and never decides equivalence.

    Catches the two failures the clause names: carrying a verdict forward over a moved input, and
    reporting "not measured" as still matching.
    """

    record = assessment()
    matched = compose_currentness(
        (record,), {record.assessmentId: dict(record.examinedInputs.identities)}
    )
    moved = compose_currentness((record,), current_measurement(record))

    assert matched[0].binding_state == "current"
    assert matched[0].moved_identities == ()
    assert moved[0].binding_state == "stale"
    assert len(moved[0].moved_identities) == 1


def test_a_stale_binding_stays_readable_is_reuse_refused_and_is_never_reinterpreted() -> None:
    """§4.5/§4.4: stale is labelled, readable, not reused and not re-judged for the new inputs.

    Catches reuse of a stale subrecord without a contract permitting it, and the reinterpretation the
    design forbids by name -- the old judgment applied to inputs it never examined.
    """

    record = assessment()
    stale = compose_currentness((record,), current_measurement(record))[0]

    assert stale.record_readable is True
    assert stale.reuse_permitted is False
    assert stale.reinterpreted_for_new_inputs is False
    assert stale.review_record_id == record.assessmentId
    assert record.disposition == "concern_found"
    assert "no longer match" in (curator_currentness_status((stale,)) or "")
    assert curator_currentness_status(()) is None


def test_a_currentness_claiming_stale_with_nothing_moved_is_refused() -> None:
    """§4.4: the state follows from the comparison, so a state its own facts contradict is refused.

    Catches a status written by hand: a binding labelled stale with no moved identity claims a
    comparison nobody made, exactly as one labelled current over a moved input does.
    """

    with pytest.raises(ValidationError, match="follows from the comparison"):
        FindingCurrentness(
            subject_id=f"family:{FAMILY_REVISION}",
            review_record_id="assessment-1",
            binding_state="stale",
        )
    with pytest.raises(ValidationError, match="stale binding is readable and is not reused"):
        FindingCurrentness(
            subject_id=f"family:{FAMILY_REVISION}",
            review_record_id="assessment-1",
            binding_state="stale",
            moved_identities=("evidence-bytes:review-fixture-assumptions",),
            reuse_permitted=True,
        )


def test_the_report_only_section_renders_a_stored_record_and_no_row_for_a_missing_one() -> None:
    """§5.2/§4.2: the checklist section reports what is stored and invents no disposition.

    Catches both halves of the report-only boundary: a stored record appears as a row in the existing
    ``knowledgeReview`` section, and a subject nobody reviewed produces no row at all rather than a
    favourable one.
    """

    record = assessment()
    rows = family_review_summaries(
        (record,), {record.assessmentId: dict(record.examinedInputs.identities)}
    )
    section = knowledge_review_section(rows)

    assert KNOWLEDGE_REVIEW_HEADING in "\n".join(section.lines)
    assert section.subjectCount == 1
    assert section.assessmentCount == 1
    assert knowledge_review_section(()).subjectCount == 0
    assert "_None recorded._" in "\n".join(knowledge_review_section(()).lines)


# ---------------------------------------------------------------------------
# §5: routing, the two counts, and the recorded decision.


def test_a_family_review_row_does_not_move_the_shipped_actionable_count() -> None:
    """§5.3: two counts, never one -- the shipped three terms are untouched by a family-review row.

    Catches the new gate in its most likely disguise: a family row folded into the count that gates
    closeout. The count is the shipped function's own value, not a restatement of the arithmetic.
    """

    routing = route_family_review(
        group_detection_facts((signal(claims=("claim-a",)),)),
        review_ids_by_subject={f"family:{FAMILY_REVISION}": ("assessment-1",)},
        repair_count=2,
        missing_count=1,
        stale_count=3,
    )

    assert routing.review_row_count == 1
    assert routing.actionable_count == curator_actionable_count(2, 1, 3) == 6
    assert routing.family_rows_are_report_only()
    assert routing.decides_closeout_readiness == CLOSEOUT_READINESS_DECIDER
    assert routing.report_only_section == KNOWLEDGE_REVIEW_HEADING


def test_a_routing_row_naming_a_third_surface_and_a_row_count_that_is_not_its_rows_are_refused() -> (
    None
):
    """§5.2/§5.7: no third surface, and a count that is not its rows is not a count.

    Catches a second worklist arriving as a routing target, and a row count that drifted from the rows
    it claims to summarise.
    """

    assert FAMILY_REVIEW_ROUTING_SURFACES == (
        "curator-coherence-assessment-collection",
        "knowledgeReview",
    )
    with pytest.raises(ValidationError, match="a third surface"):
        FamilyReviewRoutingRow(
            subject_id=f"family:{FAMILY_REVISION}",
            group_id="family-review/x/y",
            matched_condition_count=1,
            routes_to=("a-new-worklist-database",),
        )
    with pytest.raises(ValidationError, match="gains no fourth"):
        FamilyReviewRouting(
            rows=(),
            review_row_count=0,
            repair_count=0,
            missing_count=0,
            stale_count=0,
            actionable_count=1,
        )


def test_the_recorded_gate_decision_names_its_owner_and_every_required_field() -> None:
    """§5.4: the question is closed by a record that carries its owner and nothing invented.

    Catches the two ways an authority question is lost: a decision with no owner (which §5.4 says is
    escalated, not satisfied) and a decision whose authority basis is blank.
    """

    decision = RECORDED_GATE_CONSEQUENCE_DECISION

    assert decision.owner.role.strip()
    assert decision.owner.identity_ref.strip()
    assert decision.owner.ruling_basis.strip()
    assert decision.durable_home.strip()
    assert decision.question.strip() and decision.outcome.strip()
    assert decision.rationale.strip() and decision.revisit_when.strip()
    assert decision.effective_from.strip()
    assert decision.authority_basis == (
        "design/retrieval-review-design.md:348",
        "Doc13:422",
        "PLANNING.md:32",
    )


def test_the_recorded_decision_creates_no_gate_and_no_new_refusal_code_or_status_value() -> None:
    """§5.5/§5.6: the decision rules the signal report-only, and adds nothing that could gate.

    Catches a gate arriving as data: the decision cannot represent one, the shipped refusal-code
    vocabulary is unchanged by this leaf, and the readiness value is not a status any owner reports.
    """

    assert RECORDED_GATE_CONSEQUENCE_DECISION.creates_gate is False
    declared_fields = set(FamilyReviewGateDecision.model_fields) - {"creates_gate"}
    assert not any("gate" in name for name in declared_fields)
    for vocabulary in STATUS_VOCABULARIES.values():
        assert "ready-for-closeout" not in vocabulary
        assert "closeoutReady" not in " ".join(vocabulary)
    assert not any("family_review" in code for code in get_args(KnowledgeRefusalCode))
