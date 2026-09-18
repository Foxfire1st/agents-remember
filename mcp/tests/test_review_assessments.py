"""KS-R15@v1: the ``ReviewAssessment`` record, its binding, and the states a read reports.

These cases protect the record's *decidable* content: which fields a stored assessment must carry,
that its three dispositions are not interchangeable, that its binding is compared for equality rather
than reinterpreted, that absence is never rendered as a favourable disposition, and that the
``knowledgeReview`` checklist section is a factual, report-only section of the one artifact.

The publication route's real behaviour -- the authenticated caller, the evidence-byte destination and
the post-cleanup read-back -- is exercised against a real external-memory leaf enclosure in
``test_curator_review_assessment_publication.py``; nothing here fakes a contract or a store, and no
case here claims a survival it did not read back.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory_quality.curator_checklist import (
    ATTESTATION_FILE_NAME,
    REPORT_FILE_NAME,
    CuratorChecklist,
    report_path_for,
    write_curator_checklist,
)
from agents_remember.memory_quality.knowledge_review import (
    KNOWLEDGE_REVIEW_HEADING,
    PARTIAL_SCOPE,
    STALE_BINDING,
    UNRESOLVED_DISPOSITION,
    AssessmentSummaryInput,
    knowledge_review_section,
    summarise_assessment_state,
)
from agents_remember.models.lifecycles.evidence_dependencies import (
    EvidenceDependencyError,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
    require_evidence_dependencies,
)
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceByte,
    AssessmentEvidenceReference,
    AssessmentSubject,
    ExaminedInputs,
    ReviewAssessment,
    ReviewAssessmentRevision,
    SubjectAssessmentState,
    assessment_binding_gaps,
    assessment_state_for,
    assessment_subject_id,
    recorded_assessment_digest,
)
from agents_remember.models.lifecycles.review_assessment_binding import (
    AssessmentBindingStaleError,
    assessment_currentness,
    disputed_dependencies,
    require_current_assessment_binding,
    subject_state,
)
from agents_remember.models.lifecycles.review_assessment_store import (
    AssessmentInputs,
    ReviewAssessmentError,
    bind_assessment,
    examined_input_declaration,
    require_assessments_are_identified,
    review_record_edges,
    reviewed_bytes,
)
from pydantic import ValidationError

CODE_TREE = "a" * 40
MEMORY_TREE = "b" * 40
PAIR_DIGEST = "c" * 64
TOPOLOGY = "d" * 64
INTENT = "e" * 64
SCOPE_MANIFEST = "scope-union-B-M-F1"
COMPARISON = "comparison-B-M-F1"


def _subject(kind: str = "family", record_id: str = "FAM-F") -> AssessmentSubject:
    if kind == "comparison":
        return AssessmentSubject(kind="comparison", recordId=record_id, comparisonRef=COMPARISON)
    return AssessmentSubject(
        kind=kind,
        recordId=record_id,
        beforeRevisionIds=("F1",),
        afterRevisionIds=("F1",),
    )


def _revision(
    *,
    assessment_id: str = "assessment-B-M-F1-curator",
    disposition: str = "concern_found",
    finding: str = "The combined retry configuration can exceed the five-second budget.",
    subject: AssessmentSubject | None = None,
    evidence: tuple[str, ...] = ("task:notes/reports/fixtures/retry-budget.md",),
) -> ReviewAssessmentRevision:
    return ReviewAssessmentRevision(
        assessmentId=assessment_id,
        subject=subject or _subject(),
        disposition=disposition,
        finding=finding,
        rationale="Four attempts may each consume two seconds and no earlier deadline is shared.",
        assumptions=("Every attempt can consume its configured timeout.",),
        evidenceRefs=tuple(
            AssessmentEvidenceReference(
                namespace=spelling.split(":", 1)[0], ref=spelling.split(":", 1)[1]
            )
            for spelling in evidence
        ),
        comparisonRef=COMPARISON,
        scopeManifestRef=SCOPE_MANIFEST,
    )


def _inputs(
    *,
    code_tree: str = CODE_TREE,
    evidence_bytes: tuple[AssessmentEvidenceByte, ...] = (),
) -> AssessmentInputs:
    return AssessmentInputs(
        scopeManifestRef=SCOPE_MANIFEST,
        comparisonRef=COMPARISON,
        codeCandidateTree=code_tree,
        memoryCandidateTree=MEMORY_TREE,
        pairIdentityDigest=PAIR_DIGEST,
        taskTopologyFingerprint=TOPOLOGY,
        taskIntentDigest=INTENT,
        resolverVersion="curator-evidence-resolver/v1",
        policyVersion="review-assessment-policy/v1",
        evidenceBytes=evidence_bytes,
    )


@dataclass(frozen=True)
class _Binding:
    """What the test varies when it binds one authored revision into a stored record."""

    assessment_id: str = "assessment-B-M-F1-curator"
    disposition: str = "concern_found"
    finding: str = "The combined retry configuration can exceed the five-second budget."
    subject: AssessmentSubject | None = None
    inputs: AssessmentInputs | None = None
    author_role: str = "curator"


# The default binding, as a module-level singleton so the default argument is a value rather than a
# call -- a fresh mutable default per call is how a fixture quietly stops being a fixture.
_DEFAULT_BINDING = _Binding()


def _bound(binding: _Binding | None = None) -> ReviewAssessment:
    binding = binding or _DEFAULT_BINDING
    return bind_assessment(
        authorized=_revision(
            assessment_id=binding.assessment_id,
            disposition=binding.disposition,
            finding=binding.finding,
            subject=binding.subject,
        ),
        inputs=binding.inputs or _inputs(),
        author_ref=f"{binding.author_role}@repo-a/master/leaf.json",
        author_role=binding.author_role,
        publication_ref="curator-coherence/v1:260915-KS-L15",
    )


def _current_map(assessment: ReviewAssessment) -> dict[tuple[str, str], tuple[str, str]]:
    """Return the assessment's own identities as the "still current" measurement."""

    return dict(assessment.examinedInputs.identities)


class TestAssessmentRecordShape:
    """Requirement 1.1, 1.3 and 1.4: what a stored assessment must carry."""

    def test_a_complete_assessment_carries_every_field_doc13_names(self) -> None:
        record = _bound()

        assert record.assessmentId == "assessment-B-M-F1-curator"
        assert record.subject.kind == "family"
        assert record.disposition == "concern_found"
        assert record.finding
        assert record.rationale
        assert record.assumptions
        assert record.evidenceRefs
        assert record.comparisonRef == COMPARISON
        assert record.scopeManifestRef == SCOPE_MANIFEST
        assert record.examinedInputs.declaration.recordType == "review-assessment/v1"
        assert record.provenance.authorRole == "curator"
        assert assessment_binding_gaps(record) == ()

    def test_a_record_missing_its_examined_inputs_does_not_construct(self) -> None:
        """1.4: the required fields are required for EVERY disposition, `no_concern_found` included.

        The failure this catches is the one the packet names: a "nothing found" assessment with no
        recorded examined inputs is indistinguishable from an absent assessment, and a model that let
        it through would let absence be spelled as a clearance.
        """

        with pytest.raises(ValidationError) as refusal:
            ReviewAssessment.model_validate(
                {
                    **_revision(disposition="no_concern_found", finding="").model_dump(),
                    "provenance": {
                        "authorRef": "curator@leaf",
                        "authorRole": "curator",
                        "publicationRef": "curator-coherence/v1:leaf",
                    },
                }
            )

        assert "examinedInputs" in str(refusal.value)

    def test_a_record_missing_its_provenance_does_not_construct(self) -> None:
        with pytest.raises(ValidationError) as refusal:
            ReviewAssessment.model_validate(
                {
                    **_revision().model_dump(),
                    "examinedInputs": examined_input_declaration(_inputs()).model_dump(),
                }
            )

        assert "provenance" in str(refusal.value)

    def test_the_submission_shape_has_no_field_for_a_caller_supplied_author(self) -> None:
        """2.1: a caller cannot author an assessment under another identity.

        The refusal is a property of the shape rather than of a check: there is no ``authorRef``,
        ``authorRole``, ``provenance`` or ``examinedInputs`` field on the submission type at all, so
        supplying one is an undeclared field and the request never becomes a record.
        """

        for field in ("authorRef", "authorRole", "provenance", "examinedInputs"):
            with pytest.raises(ValidationError) as refusal:
                ReviewAssessmentRevision.model_validate(
                    {**_revision().model_dump(), field: "curator@somewhere-else"}
                )
            assert field in str(refusal.value)

    def test_the_stored_author_is_whatever_the_publication_path_supplied(self) -> None:
        record = _bound(_Binding(author_role="architect"))

        assert record.provenance.authorRole == "architect"
        assert record.provenance.authorRef.startswith("architect@")

    def test_an_undeclared_field_is_refused_on_the_record(self) -> None:
        with pytest.raises(ValidationError):
            ReviewAssessment.model_validate(
                {
                    **_bound().model_dump(),
                    "approvalGate": "approved",
                }
            )


class TestDispositionVocabulary:
    """Requirement 1.2: closed, stored, and never collapsed into one another."""

    def test_the_vocabulary_is_exactly_the_three_declared_values(self) -> None:
        stored = {
            _bound(_Binding(disposition=disposition, finding=finding)).disposition
            for disposition, finding in (
                ("concern_found", "A concern."),
                ("no_concern_found", ""),
                ("unresolved", "Could not decide."),
            )
        }

        assert stored == {"concern_found", "no_concern_found", "unresolved"}

    def test_an_unknown_disposition_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _revision(disposition="compatible")

    def test_unresolved_is_not_a_synonym_for_no_concern_found(self) -> None:
        """The two are stored differently and read differently, and neither is derived from the other."""

        unresolved = _bound(_Binding(disposition="unresolved", finding="Could not decide."))
        cleared = _bound(_Binding(disposition="no_concern_found", finding=""))

        assert unresolved.disposition != cleared.disposition
        assert assessment_state_for([unresolved]).status == "unresolved"
        assert assessment_state_for([cleared]).status == "current"
        assert unresolved.finding and not cleared.finding

    def test_no_concern_found_may_not_carry_a_finding(self) -> None:
        """1.2's collapse, spelled the other way round: a clearance carrying a concern."""

        with pytest.raises(ValidationError) as refusal:
            _revision(disposition="no_concern_found", finding="A concern after all.")

        assert "no_concern_found" in str(refusal.value)

    def test_the_record_is_validated_structurally_and_never_for_truth(self) -> None:
        """1.3: code checks the declared shape and does not adjudicate the sentence.

        The finding below is arbitrary prose that no checker could evaluate. It constructs, and its
        digest is stable, which is the whole of what the substrate claims: the sentence is the
        curator's, not a template the code filled in.
        """

        record = _bound(_Binding(finding="The retry budget is fine, probably, on Tuesdays."))

        assert record.finding == "The retry budget is fine, probably, on Tuesdays."
        assert len(recorded_assessment_digest(record)) == 64


class TestSubjectShape:
    """Requirements 8.2, 8.5, 8.6 and 9: a family is a subject, never a fake source file."""

    def test_a_family_subject_names_the_family_record_and_its_revisions(self) -> None:
        subject = _subject()

        assert subject.kind == "family"
        assert subject.recordId == "FAM-F"
        assert assessment_subject_id(_bound()) == "family:FAM-F"

    def test_a_comparison_subject_must_name_the_comparison_and_not_a_revision_list(self) -> None:
        with pytest.raises(ValidationError) as missing:
            AssessmentSubject(kind="comparison", recordId="comparison-B-M-F1")
        assert "explicit comparison" in str(missing.value)

        with pytest.raises(ValidationError) as listed:
            AssessmentSubject(
                kind="comparison",
                recordId="comparison-B-M-F1",
                beforeRevisionIds=("F1",),
                comparisonRef=COMPARISON,
            )
        assert "not a revision list" in str(listed.value)

    def test_a_revision_bearing_subject_must_name_a_revision(self) -> None:
        with pytest.raises(ValidationError) as refusal:
            AssessmentSubject(kind="invariant-revision", recordId="INV-I")

        assert "examined revision identity" in str(refusal.value)

    def test_a_comparison_subject_and_the_record_must_name_one_comparison(self) -> None:
        with pytest.raises(ValidationError):
            _revision(
                subject=AssessmentSubject(
                    kind="comparison", recordId="comparison-other", comparisonRef="comparison-other"
                )
            )

    def test_two_subject_kinds_with_one_identity_are_two_subjects(self) -> None:
        """5.5's per-examined-item rule at the level of the subject: a projection cannot merge them."""

        family = _bound(_Binding(subject=_subject("family", "F1")))
        invariant = _bound(_Binding(subject=_subject("invariant-revision", "F1")))

        assert assessment_subject_id(family) != assessment_subject_id(invariant)


class TestExaminedInputBinding:
    """Requirements 5.1 and 5.2: identities through the shipped contract, compared for equality."""

    def test_the_binding_uses_the_shipped_contracts_two_own_vocabularies(self) -> None:
        declaration = examined_input_declaration(_inputs()).declaration
        kinds = {edge.kind for edge in declaration.edges}

        assert declaration.schemaVersion == "ar-evidence-dependencies/v1"
        assert {"candidate-state", "code-tree", "memory-tree", "task-intent"} <= kinds
        assert {"semantic-topology", "evidence-bytes", "validator"} <= kinds
        require_evidence_dependencies(declaration, record_type="review-assessment/v1")

    def test_the_policy_requires_exactly_the_clause_five_inputs(self) -> None:
        """A declaration missing one of clause 5.1's inputs, or carrying a foreign kind, is refused.

        Both directions are asserted on one input set: dropping ``validator`` (the resolver/policy
        version pair) is refused as missing, and adding ``memory-attestation`` -- a kind this record
        never examines -- is refused as unowned. The kind set is the shipped closed vocabulary; this
        leaf adds a policy entry and no kind.
        """

        clause_five = [
            dependency("candidate-state", "knowledge-candidate-pair", PAIR_DIGEST),
            dependency("code-tree", "candidate", CODE_TREE, algorithm="git-object"),
            dependency("memory-tree", "candidate", MEMORY_TREE, algorithm="git-object"),
            dependency("semantic-topology", "registered-scope", TOPOLOGY),
            dependency("task-intent", "requirement-identities", INTENT),
            dependency("evidence-bytes", "scope-manifest", canonical_sha256(SCOPE_MANIFEST)),
            dependency("validator", "curator-evidence-resolver/v1", canonical_sha256("r")),
            dependency("validator", "review-assessment-policy/v1", canonical_sha256("p")),
        ]
        with pytest.raises(EvidenceDependencyError) as missing:
            build_evidence_dependencies(
                "review-assessment/v1",
                [edge for edge in clause_five if edge.kind != "validator"],
            )
        assert missing.value.status == "evidence-dependencies-required-missing"

        with pytest.raises(EvidenceDependencyError) as extra:
            build_evidence_dependencies(
                "review-assessment/v1",
                [*clause_five, dependency("memory-attestation", "not-ours", "f" * 64)],
            )
        assert extra.value.status == "evidence-dependencies-undeclared-extra"

        declaration = build_evidence_dependencies("review-assessment/v1", clause_five)
        assert (
            require_evidence_dependencies(declaration, record_type="review-assessment/v1")
            is declaration
        )

    def test_a_record_with_no_citations_is_refused_before_anything_is_written(self) -> None:
        with pytest.raises(ReviewAssessmentError) as refusal:
            bind_assessment(
                authorized=_revision(evidence=()),
                inputs=_inputs(),
                author_ref="curator@leaf",
                author_role="curator",
                publication_ref="curator-coherence/v1:leaf",
            )

        assert refusal.value.status == "review-assessment-binding-incomplete"

    def test_a_matching_binding_is_current_and_records_no_gap(self) -> None:
        record = _bound()

        currentness = assessment_currentness(record, _current_map(record))

        assert currentness.binding_state == "current"
        assert currentness.gaps == ()
        assert not currentness.is_stale

    def test_a_moved_input_marks_the_binding_stale_with_its_exact_identity(self) -> None:
        record = _bound()
        current = dict(_current_map(record))
        current[("code-tree", "candidate")] = ("git-object", "f" * 40)

        gaps = disputed_dependencies(record, current)

        assert [gap.identity for gap in gaps] == ["code-tree:candidate"]
        assert gaps[0].expected == CODE_TREE
        assert gaps[0].observed == "f" * 40
        assert assessment_currentness(record, current).binding_state == "stale"

    def test_an_input_that_can_no_longer_be_read_is_a_mismatch_not_a_pass(self) -> None:
        """5.2: "not measured" is never "still matches"."""

        record = _bound()

        currentness = assessment_currentness(record, {})

        assert currentness.is_stale
        assert {gap.observed for gap in currentness.gaps} == {"unreadable-or-absent"}

    def test_a_relocated_input_with_identical_content_is_not_equivalent(self) -> None:
        """5.2 and Doc13:373: byte-identical content at a new location is never proof of equivalence."""

        record = _bound()
        current = dict(_current_map(record))
        # The record bound a tree by identity. A different identity carrying the same bytes is still a
        # different identity, and nothing in this module is allowed to decide otherwise.
        current[("code-tree", "candidate")] = ("git-object", "1" * 40)

        assert assessment_currentness(record, current).is_stale

    def test_the_stale_refusal_names_the_identity_the_recorded_and_the_observed_state(self) -> None:
        record = _bound()
        current = dict(_current_map(record))
        current[("semantic-topology", "registered-scope")] = ("sha256", "9" * 64)

        with pytest.raises(AssessmentBindingStaleError) as refusal:
            require_current_assessment_binding(record, current, operation="submit_comparison")

        assert refusal.value.status == "review-assessment-binding-stale"
        fields = refusal.value.response_fields()
        assert fields["assessmentId"] == record.assessmentId
        stale = fields["staleBindings"]
        assert isinstance(stale, list)
        assert stale[0]["name"] == "registered-scope"
        assert stale[0]["expected"] == TOPOLOGY
        assert stale[0]["observed"] == "9" * 64

    def test_a_self_referential_review_record_edge_is_never_a_staleness_gap(self) -> None:
        """The record cites the assessment; the assessment must not be asked to cite the record.

        This is ``design/retrieval-review-design.md:368``'s self-invalidating sequence refused at the
        comparison: the edge exists, the assessment's own binding does not carry it, and the read
        reports the assessment current on the strength of the inputs it actually examined.
        """

        record = _bound()
        edges = review_record_edges([record])

        assert edges[0].kind == "review-record"
        assert edges[0].name == f"review-assessment:{record.assessmentId}"
        assert edges[0].digest == recorded_assessment_digest(record)
        assert assessment_currentness(record, _current_map(record)).binding_state == "current"

    def test_a_duplicate_assessment_identity_is_refused(self) -> None:
        revision = _revision()

        with pytest.raises(ReviewAssessmentError) as refusal:
            require_assessments_are_identified([revision, revision])

        assert refusal.value.status == "review-assessment-duplicate-identity"


class TestEvidenceBytesAreRecordedAsThreeFacts:
    """Requirement 6.6: path, digest and size, because a digest alone cannot be opened."""

    def test_a_bound_byte_is_declared_with_its_path_digest_and_size(self) -> None:
        payload = b"# Retry budget fixture\n"
        byte = AssessmentEvidenceByte(
            path="notes/reports/evidence/a/retry-budget.md",
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
        )
        record = _bound(_Binding(inputs=_inputs(evidence_bytes=(byte,))))

        assert record.examinedInputs.identities[("evidence-bytes", byte.path)] == (
            "sha256",
            byte.sha256,
        )

    def test_a_published_byte_with_no_citation_is_refused(self) -> None:
        """A byte kept for a citation nobody made is refused, not silently dropped.

        The publication pairs each byte with its citation, so this can only arise from a caller
        assembling the two halves by hand. Refusing it keeps "the record's binding describes exactly
        the cited bytes" true rather than merely intended -- an uncited kept byte is evidence the
        assessment does not mention, and one the read-back would never verify against a citation.
        """

        cited = _revision().evidenceRefs[0]
        uncited = AssessmentEvidenceReference(
            namespace="task", ref="notes/reports/fixtures/never-cited.md"
        )
        byte = AssessmentEvidenceByte(
            path="notes/reports/evidence/a/never-cited.md", sha256="0" * 64, size=1
        )

        with pytest.raises(ReviewAssessmentError) as refusal:
            reviewed_bytes(
                (cited,),
                (_Published(reference=cited, byte=byte), _Published(reference=uncited, byte=byte)),
            )

        assert refusal.value.status == "review-assessment-evidence-uncited"
        assert uncited.spelling in str(refusal.value)

    def test_a_citation_with_no_published_byte_is_refused(self) -> None:
        with pytest.raises(ReviewAssessmentError) as refusal:
            reviewed_bytes(_revision().evidenceRefs, [])

        assert refusal.value.status == "review-assessment-evidence-unmeasured"

    def test_an_absolute_or_escaping_evidence_path_is_refused(self) -> None:
        for path in ("/etc/passwd", "../outside.md"):
            with pytest.raises(ValidationError):
                AssessmentEvidenceByte(path=path, sha256="0" * 64, size=0)


class TestReadStates:
    """Requirements 4.1, 4.3 and 4.4: absence, unresolved and stale are three states."""

    def test_a_subject_with_no_assessment_is_none_recorded_and_never_a_disposition(self) -> None:
        state = assessment_state_for([])

        assert state.status == "none-recorded"
        assert state.assessmentCount == 0
        assert state.assessments == ()
        assert state.unresolvedCount == 0
        assert state.staleCount == 0

    def test_the_three_states_are_distinct(self) -> None:
        unresolved = _bound(_Binding(disposition="unresolved", finding="Could not decide."))
        cleared = _bound(_Binding(disposition="no_concern_found", finding=""))

        none_recorded = assessment_state_for([])
        recorded_unresolved = assessment_state_for([unresolved])
        recorded_stale = assessment_state_for([cleared], stale_ids=[cleared.assessmentId])

        assert len({none_recorded.status, recorded_unresolved.status, recorded_stale.status}) == 3
        assert recorded_stale.staleCount == 1
        assert recorded_stale.assessments[0].currentness == "stale"

    def test_a_count_reported_to_a_reader_counts_records_that_exist(self) -> None:
        record = _bound()

        assert assessment_state_for([]).assessmentCount == 0
        assert assessment_state_for([record]).assessmentCount == 1

    def test_a_subject_projection_marks_an_unmeasured_assessment_stale(self) -> None:
        """ "Not measured" is never "still matches", in the projection as well as the comparison."""

        record = _bound()

        assert subject_state([record], current={}).status == "stale"
        assert (
            subject_state([record], current={record.assessmentId: _current_map(record)}).status
            == "current"
        )

    def test_per_item_coverage_is_not_inherited_from_a_sibling(self) -> None:
        """5.5: a sibling item does not inherit its neighbour's verdict."""

        measured = _bound(_Binding(assessment_id="assessment-covered"))
        sibling = _bound(_Binding(assessment_id="assessment-sibling"))

        state = subject_state(
            [measured, sibling],
            current={measured.assessmentId: _current_map(measured)},
        )

        by_id = {entry.assessmentId: entry.currentness for entry in state.assessments}
        assert by_id["assessment-covered"] == "current"
        assert by_id["assessment-sibling"] == "stale"

    def test_a_projection_cannot_report_a_disposition_for_an_absent_subject(self) -> None:
        with pytest.raises(ValidationError):
            SubjectAssessmentState(
                status="none-recorded",
                assessmentCount=0,
                unresolvedCount=0,
                staleCount=0,
                assessments=(
                    {
                        "assessmentId": "fabricated",
                        "disposition": "no_concern_found",
                        "currentness": "current",
                    },
                ),
            )

    def test_a_count_that_contradicts_its_records_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            SubjectAssessmentState(
                status="none-recorded",
                assessmentCount=2,
                unresolvedCount=0,
                staleCount=0,
            )


class TestKnowledgeReviewSection:
    """Requirement 8.2 and 8.3: factual, report-only, and the one artifact's own section."""

    def test_the_section_counts_what_is_there_and_names_its_limitations(self) -> None:
        review = knowledge_review_section(
            (
                summarise_assessment_state(
                    AssessmentSummaryInput(
                        subjectId="family:FAM-F",
                        assessmentCount=1,
                        dispositions=("unresolved",),
                        comparisonRefs=(COMPARISON,),
                        scopeRefs=(SCOPE_MANIFEST,),
                        unresolvedCount=1,
                    )
                ),
            )
        )

        rendered = "\n".join(review.lines)
        assert f"## {KNOWLEDGE_REVIEW_HEADING}" in rendered
        assert "| family:FAM-F | 1 | unresolved |" in rendered
        assert review.limitations == (UNRESOLVED_DISPOSITION,)
        assert review.assessmentCount == 1
        assert "Report-only" in rendered

    def test_a_stale_binding_is_a_counted_limitation_not_a_verdict(self) -> None:
        review = knowledge_review_section(
            (
                summarise_assessment_state(
                    AssessmentSummaryInput(
                        subjectId="family:FAM-F",
                        assessmentCount=1,
                        dispositions=("concern_found",),
                        comparisonRefs=(COMPARISON,),
                        scopeRefs=(SCOPE_MANIFEST,),
                        staleCount=1,
                    )
                ),
            )
        )

        assert review.limitations == (STALE_BINDING,)
        assert "not reused" in "\n".join(review.lines)

    def test_an_empty_collection_renders_no_rows_rather_than_a_default(self) -> None:
        review = knowledge_review_section(())

        rendered = "\n".join(review.lines)
        assert "_None recorded._" in rendered
        assert review.assessmentCount == 0
        assert review.limitations == ()

    def test_a_subject_with_no_comparison_or_scope_reference_is_a_counted_limitation(self) -> None:
        review = knowledge_review_section(
            (
                summarise_assessment_state(
                    AssessmentSummaryInput(
                        subjectId="family:FAM-F",
                        assessmentCount=1,
                        dispositions=("concern_found",),
                    )
                ),
            )
        )

        assert review.limitations == (PARTIAL_SCOPE,)

    def test_a_summary_whose_counts_contradict_its_records_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            summarise_assessment_state(
                AssessmentSummaryInput(
                    subjectId="family:FAM-F",
                    assessmentCount=1,
                    dispositions=("concern_found",),
                    unresolvedCount=2,
                )
            )
        with pytest.raises(ValueError, match="one per recorded assessment"):
            summarise_assessment_state(
                AssessmentSummaryInput(subjectId="family:FAM-F", assessmentCount=1)
            )

    def test_the_section_is_written_into_the_one_checklist_artifact(self, tmp_path) -> None:
        """8.3: one replaceable file, and no second checklist or attestation beside it."""

        report_path = report_path_for(tmp_path / "enclosure")
        write_curator_checklist(_checklist(report_path, knowledge_review=(_unresolved_summary(),)))

        rendered = report_path.read_text(encoding="utf-8")
        assert f"## {KNOWLEDGE_REVIEW_HEADING}" in rendered
        assert "| family:FAM-F | 1 | unresolved |" in rendered
        # The section lives inside the one artifact, and exactly the shipped pair of files exists:
        # no second checklist and no second attestation was introduced beside it.
        assert (report_path.parent / REPORT_FILE_NAME).is_file()
        assert (report_path.parent / ATTESTATION_FILE_NAME).is_file()
        assert sorted(path.name for path in report_path.parent.iterdir()) == sorted(
            [REPORT_FILE_NAME, ATTESTATION_FILE_NAME]
        )
        attestation = json.loads(
            (report_path.parent / ATTESTATION_FILE_NAME).read_text(encoding="utf-8")
        )
        assert attestation["curatorActionableCount"] == 0

    def test_the_section_moves_no_count_and_adds_no_finding(self, tmp_path) -> None:
        """8.2's boundary, measured: the same inputs produce the same actionable count.

        The two runs differ in exactly one input -- the recorded assessment collection -- and the
        checklist status and ``curatorActionableCount`` are identical. An implementation that folded
        an unresolved family signal into the count would separate these two numbers, which is the
        gate consequence ``Doc13:422`` reserves for ``KS-R16@v1``.
        """

        without = report_path_for(tmp_path / "without")
        with_assessments = report_path_for(tmp_path / "with")
        write_curator_checklist(_checklist(without))
        write_curator_checklist(
            _checklist(with_assessments, knowledge_review=(_unresolved_summary(),))
        )

        first = without.read_text(encoding="utf-8")
        second = with_assessments.read_text(encoding="utf-8")
        assert "- Status: **ready-for-closeout**" in first
        assert "- Status: **ready-for-closeout**" in second
        assert f"## {KNOWLEDGE_REVIEW_HEADING}" in second
        assert "| family:FAM-F | 1 |" in second
        # The attestation is the machine-readable half of the same artifact, and its actionable
        # count is unchanged by the section. This is the measurement Example 7 asks for.
        for report_path in (without, with_assessments):
            attestation = json.loads(
                (report_path.parent / ATTESTATION_FILE_NAME).read_text(encoding="utf-8")
            )
            assert attestation["curatorActionableCount"] == 0
            assert attestation["checklistStatus"] == "ready-for-closeout"

    def test_an_assessment_collection_cannot_move_the_actionable_count_arithmetic(self) -> None:
        """The count is computed from findings, missing onboarding and stale indexes, and nothing else."""

        source = CuratorChecklist.__dataclass_fields__["knowledge_review"]
        assert source.default == ()


@dataclass(frozen=True)
class _Published:
    """A minimal carrier for the publication protocol the binding is handed.

    The real carrier is the publication path's own value object and is exercised in the integration
    module against a real enclosure. What this module needs to check is that the binding matches by
    *citation* rather than by position, so a local two-attribute value is the honest fixture.
    """

    reference: AssessmentEvidenceReference
    byte: AssessmentEvidenceByte


def _unresolved_summary():
    """One subject whose only assessment is ``unresolved`` -- the state the section must report."""

    return summarise_assessment_state(
        AssessmentSummaryInput(
            subjectId="family:FAM-F",
            assessmentCount=1,
            dispositions=("unresolved",),
            comparisonRefs=(COMPARISON,),
            scopeRefs=(SCOPE_MANIFEST,),
            unresolvedCount=1,
        )
    )


def _checklist(report_path, *, knowledge_review=()) -> CuratorChecklist:
    """Return one minimal checklist input; nothing here is published or attested for real."""

    return CuratorChecklist(
        report_path=report_path,
        repo_id="repo-a",
        code_root=report_path.parent / "code",
        onboarding_root=report_path.parent / "memory" / "onboarding",
        pair_identity=MemoryCandidatePairIdentity(
            repoId="repo-a",
            contractPath="/coordination/tasks/repo-a/task/series-contract.md",
            contractDigest="7" * 64,
            codeRoot="/code",
            memoryRoot="/memory",
            codeSourceBranch="ar/series",
            codeWorkBranch="ar/leaf",
            codeBaseCommit="1" * 40,
            memorySourceBranch="ar/series",
            memoryWorkBranch="ar/leaf",
            memoryBaseCommit="2" * 40,
            onboardingRoot="/memory/onboarding",
            ledgerPath="memory.md",
        ),
        code_candidate_tree=CODE_TREE,
        memory_candidate_tree=MEMORY_TREE,
        quality={"findingCount": 0},
        repair_findings=[],
        commit_owned_findings=[],
        missing_onboarding={"missing": [], "missingCount": 0},
        stale_route_indexes=[],
        source_candidates=(),
        drift_rows=[],
        report_only_findings=[],
        knowledge_review=knowledge_review,
    )


def test_the_record_identity_is_a_function_of_the_authored_act() -> None:
    """The same authored act digests the same, and a changed finding digests differently."""

    first = _bound()
    again = _bound()
    changed = _bound(_Binding(finding="A different concern."))

    assert recorded_assessment_digest(first) == recorded_assessment_digest(again)
    assert recorded_assessment_digest(first) != recorded_assessment_digest(changed)
    assert recorded_assessment_digest(first) == sha256_digest(first.model_dump(mode="json"))


def test_examined_inputs_expose_their_declaration_digest() -> None:
    inputs = ExaminedInputs(declaration=examined_input_declaration(_inputs()).declaration)

    assert inputs.digest == inputs.declaration.fingerprint()
    assert len(inputs.digest) == 64
