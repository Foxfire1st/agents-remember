"""The ``ReviewAssessment`` record: one authored judgment and the exact inputs it examined.

Three kinds of record meet on this branch and ``Doc13:104`` forbids promoting one into another:

* a **worker claim** -- an agent-authored assertion;
* a **machine signal** -- a detector's factual output (``KS-R14@v1``, whose payload models live in
  :mod:`agents_remember.models.knowledge.detection`);
* a **reviewer conclusion** -- this record.

The separation is a property of the *type*, not of a convention: nothing in this module can be
constructed from a detection-signal payload or a facet payload, and there is no conversion operation
in either direction. Where an assessment cites a signal it does so through an
:class:`AssessmentEvidenceReference`, which is a citation that keeps both records and both types
(``Doc13:359``).

**Validation here is structural only.** A record missing an author, a role, a disposition, a subject
or an examined-input binding does not construct. Nothing in this module -- or in the publication path
that calls it -- ever reads the finding to decide whether it is *true*; ``Doc13:357`` makes the
finding a curator's authored sentence rather than a checker response template, and the separation
rule would be meaningless if code adjudicated the conclusion. What the model *does* enforce is that
the three dispositions are not interchangeable: a "found nothing" assessment carries no concern text
and a "found a concern" assessment carries one, so neither can be spelled with the other's shape,
and ``unresolved`` -- which carries the open question -- is the only one of the three whose text may
be a question rather than a conclusion.

**Missing stays missing.** This module owns no default and no ``compatible`` verdict. An absent
assessment is represented by the *absence* of a record (:class:`SubjectAssessmentState`), never by a
record with a favourable disposition, and :func:`assessment_state_for` is the one place that decides
which distinct state a subject is in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
)
from agents_remember.models.lifecycles.evidence_dependencies import EvidenceDependencies

# The one closed disposition vocabulary. ``design/retrieval-review-design.md:366`` fixes the three
# allowed values, and ``:376`` makes the third a visible outcome rather than a failure: an
# inconclusive review must be representable, or a curator who could not decide has to lie.
ReviewAssessmentDisposition = Literal["concern_found", "no_concern_found", "unresolved"]
REVIEW_ASSESSMENT_DISPOSITIONS: tuple[ReviewAssessmentDisposition, ...] = (
    "concern_found",
    "no_concern_found",
    "unresolved",
)

# The subject kinds the submission shape carries (``design/retrieval-review-design.md:352-364``). A
# family is a subject kind here, which is exactly why an assessment cannot be expressed by appending
# to ``CuratorCoherenceJudgment``: that identity is a ``(sourceFile, onboardingFile, classification)``
# triple, and encoding FAM-F as a fake ``sourceFile`` is what ``:350`` forbids.
AssessmentSubjectKind = Literal["knowledge-record", "family", "invariant-revision", "comparison"]
ASSESSMENT_SUBJECT_KINDS: tuple[AssessmentSubjectKind, ...] = (
    "knowledge-record",
    "family",
    "invariant-revision",
    "comparison",
)

# The evidence namespaces ``resolve_curator_evidence_ref`` already confines. Restated as a closed
# literal so an assessment's citations are typed at the model boundary and a fourth namespace cannot
# appear without a decision, rather than being discovered by the resolver at read time.
AssessmentEvidenceNamespace = Literal["code", "memory", "task"]

# The states the read layer must report distinctly (requirement 4.3). ``none-recorded`` is a state of
# the *subject*, not a disposition: it exists precisely so absence never has to be spelled as a
# fourth disposition value.
SubjectAssessmentStatus = Literal["none-recorded", "unresolved", "stale", "current"]
SUBJECT_ASSESSMENT_STATUSES: tuple[SubjectAssessmentStatus, ...] = (
    "none-recorded",
    "unresolved",
    "stale",
    "current",
)


class _StrictAssessmentModel(BaseModel):
    """Strict, frozen, no undeclared field -- the same seam every other durable record uses."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_text(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("assessment text fields must not be blank")
    return cleaned


class AssessmentSubject(_StrictAssessmentModel):
    """What one assessment judges: an identity, a revision pair, or an explicit comparison.

    ``recordId`` carries the judged identity under every kind, so a family assessment names the
    family's own record identity rather than a manufactured source file. ``comparisonRef`` is the
    explicit comparison a ``comparison`` subject judges, and the record carries the same comparison
    in its own ``comparisonRef``; the two are one fact and are required to agree.

    The revision lists are required for a revision-bearing kind because a family assessment that
    named no revision would be a judgment about nothing in particular -- the design's own contrast is
    a family *revision* and an invariant *revision*, and "the family, in general" is not one of the
    examined inputs a binding can store. They are refused for ``comparison``, whose examined inputs
    are the comparison's two sides rather than a revision list.
    """

    kind: AssessmentSubjectKind
    recordId: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    beforeRevisionIds: tuple[str, ...] = ()
    afterRevisionIds: tuple[str, ...] = ()
    comparisonRef: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @field_validator("recordId")
    @classmethod
    def _strip_record_id(cls, value: str) -> str:
        return _canonical_text(value)

    @field_validator("beforeRevisionIds", "afterRevisionIds")
    @classmethod
    def _strip_revisions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_canonical_text(item) for item in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("an assessment subject must not repeat a revision identity")
        return cleaned

    @field_validator("comparisonRef")
    @classmethod
    def _strip_comparison(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_text(value)

    @model_validator(mode="after")
    def _subject_shape_matches_its_kind(self) -> Self:
        revisions = self.beforeRevisionIds or self.afterRevisionIds
        if self.kind == "comparison":
            if self.comparisonRef is None:
                raise ValueError("a comparison subject must name the explicit comparison it judges")
            if revisions:
                raise ValueError(
                    "a comparison subject judges the comparison's sides, not a revision list"
                )
            return self
        if not revisions:
            raise ValueError(
                f"a {self.kind} subject must name at least one examined revision identity"
            )
        return self


class AssessmentEvidenceReference(_StrictAssessmentModel):
    """One citation, under the namespace the shipped resolver already confines.

    A citation is a *reference*, not a promotion (requirement 3.2): an assessment may cite a
    detection signal, a source file, a manifest or a task document here, and doing so neither changes
    the cited record's type nor turns it into a finding.
    """

    namespace: AssessmentEvidenceNamespace
    ref: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)

    @field_validator("ref")
    @classmethod
    def _ref_is_namespace_relative(cls, value: str) -> str:
        cleaned = _canonical_text(value).replace("\\", "/")
        if cleaned.startswith("/") or cleaned == "." or ".." in cleaned.split("/"):
            raise ValueError(
                "an assessment evidence reference must be relative to its own namespace root"
            )
        return cleaned

    @property
    def spelling(self) -> str:
        """Return the one canonical ``namespace:relative`` spelling."""

        return f"{self.namespace}:{self.ref}"


class AssessmentEvidenceByte(_StrictAssessmentModel):
    """One published evidence byte, recorded as the three facts a later reader can act on.

    ``KS-R12-v1`` §9.3 requires the publication reference to live in the record itself so a later
    reader does not have to guess, and ``design/retrieval-review-design.md:370`` requires the bytes to
    survive enclosure cleanup. A digest alone satisfies neither: a digest cannot be opened, so the
    task-root-relative path travels with it, and the size is recorded because a digest over bytes of
    the right length is a different fact from a digest over bytes that are not.

    The path is relative to ``task_root`` and never absolute: an absolute path would make the record a
    statement about one machine's filesystem rather than about the task's published tree.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _path_is_task_relative(cls, value: str) -> str:
        cleaned = _canonical_text(value).replace("\\", "/")
        if cleaned.startswith("/") or ".." in cleaned.split("/"):
            raise ValueError(
                "a published evidence byte must be recorded by a task-root-relative path"
            )
        return cleaned


class AssessmentProvenance(_StrictAssessmentModel):
    """The authenticated publication path's own two facts, plus the publication it came from.

    ``authorRef`` and ``authorRole`` are supplied by the publication path, never by caller text
    (``design/retrieval-review-design.md:366``). They live on the record rather than in the caller's
    submission shape at all, so "a caller cannot author an assessment under another identity" is a
    property of the shape: there is no field for a caller to fill in, and a request that tries to
    supply one is refused as an undeclared field before any row is written.
    """

    authorRef: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    authorRole: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    publicationRef: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)

    @field_validator("authorRef", "authorRole", "publicationRef")
    @classmethod
    def _strip_provenance(cls, value: str) -> str:
        return _canonical_text(value)


class ExaminedInputs(_StrictAssessmentModel):
    """The declaration of exactly which inputs one assessment examined.

    This is the typed binding ``design/retrieval-review-design.md:370`` enumerates -- the exact
    knowledge snapshot(s), source trees, registered-scope manifest, task/requirement identities,
    resolver/policy versions and cited evidence bytes -- expressed through the shipped
    ``ar-evidence-dependencies/v1`` contract rather than through a second vocabulary.

    The *identities* are stored, never a description of them (requirement 5.1): each edge carries a
    kind, a name and a digest under a named algorithm, so validation can ask "does this still match"
    without ever asking "is this equivalent" (requirement 5.2).
    """

    declaration: EvidenceDependencies

    @property
    def digest(self) -> str:
        """Return the content address of the declaration, for the record's own provenance."""

        return self.declaration.fingerprint()

    @property
    def identities(self) -> Mapping[tuple[str, str], tuple[str, str]]:
        """Return ``(kind, name) -> (algorithm, digest)`` for every declared direct input."""

        return {edge.identity: (edge.algorithm, edge.digest) for edge in self.declaration.edges}


class _AuthoredAssessmentFields(_StrictAssessmentModel):
    """The fields a caller authors, declared once for both shapes that carry them.

    ``ReviewAssessmentRevision`` is what a caller submits and ``ReviewAssessment`` is what gets
    stored; they carry the same authored content and differ only in the two fields the publication
    path stamps. Declaring the authored half once is what keeps the submission sentence and the
    record sentence from being two spellings of the same rule that can drift: the ``no_concern_found``
    / finding separation and the comparison-subject agreement below therefore hold on the way in *and*
    on the way out, rather than only on the shape a caller happens to build.
    """

    assessmentId: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    subject: AssessmentSubject
    disposition: ReviewAssessmentDisposition
    finding: str = Field(max_length=PROSE_MAX_LENGTH)
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    assumptions: tuple[str, ...] = ()
    evidenceRefs: tuple[AssessmentEvidenceReference, ...] = ()
    comparisonRef: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    scopeManifestRef: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)

    @field_validator("assessmentId", "rationale", "comparisonRef", "scopeManifestRef")
    @classmethod
    def _strip_authored_text(cls, value: str) -> str:
        return _canonical_text(value)

    @field_validator("finding")
    @classmethod
    def _finding_is_trimmed_but_may_be_empty(cls, value: str) -> str:
        return value.strip()

    @field_validator("assumptions")
    @classmethod
    def _strip_authored_assumptions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_canonical_text(item) for item in value)

    @field_validator("evidenceRefs")
    @classmethod
    def _authored_references_are_unique(
        cls, value: tuple[AssessmentEvidenceReference, ...]
    ) -> tuple:
        spellings = [reference.spelling for reference in value]
        if len(set(spellings)) != len(spellings):
            raise ValueError("an assessment must not cite the same evidence reference twice")
        return value

    @model_validator(mode="after")
    def _disposition_and_subject_agree(self) -> Self:
        if self.subject.kind == "comparison" and self.subject.comparisonRef != self.comparisonRef:
            raise ValueError(
                "a comparison subject and the record's comparison_ref must name one comparison"
            )
        # ``no_concern_found`` is a claim that nothing was found. Carrying a finding with it is the
        # collapse requirement 1.2 forbids, spelled the other way round: a reader would see a concern
        # in the text and a clearance in the field. The two dispositions are structurally
        # distinguishable, and this is the only structural fact that separates them without reading
        # the sentence for meaning -- so it is enforced here rather than left to reader discipline.
        if self.disposition == "no_concern_found" and self.finding:
            raise ValueError(
                "a no_concern_found assessment records that its examination produced no finding; "
                "carrying a finding text would make it indistinguishable from concern_found"
            )
        return self


class ReviewAssessmentRevision(_AuthoredAssessmentFields):
    """What a caller may *author*: the finding, its rationale, and its citations.

    This is deliberately a **different type** from the record that gets stored, and the difference is
    the whole of requirement 2.1 made structural rather than procedural. The submission shape carries
    no ``provenance``, no ``authorRef`` and no ``authorRole`` -- there is no field here for a caller to
    fill in, so "a caller cannot author an assessment under another identity" is a property of the
    shape rather than of a check somebody has to remember to run. ``examinedInputs`` is absent for the
    same reason: the binding is built from the publication path's own observation, and a caller who
    could submit it could claim inputs that were never examined.

    ``design/retrieval-review-design.md:366`` states the split exactly: "The authenticated existing
    publication path supplies author and authority provenance; caller text supplies the finding,
    rationale, assumptions, disposition, and citations."
    """


class ReviewAssessment(_AuthoredAssessmentFields):
    """One stored assessment: the authored judgment, its exact examined inputs, and its provenance.

    Every field ``Doc13:100`` names is required. ``assumptions`` and ``evidenceRefs`` may be empty,
    and their emptiness is a recorded fact rather than an omission: an assessment resting on no
    assumption says so by carrying none, and one with no citation is refused by
    :func:`assessment_binding_gaps` at the publication boundary.

    The examined-input binding is :attr:`examinedInputs`, an ``ar-evidence-dependencies/v1``
    declaration. It is required for every disposition, **including ``no_concern_found``**
    (requirement 1.4): a "nothing found" assessment with no recorded examined inputs is
    indistinguishable from an absent assessment, and the model refuses it at construction rather
    than at some later read.

    This record is produced by the publication path (:func:`bind_assessment`), never assembled by a
    caller: the caller authors a :class:`ReviewAssessmentRevision` and the path stamps the two fields
    that carry authority. The pair of types is what makes that a structural fact.
    """

    schemaVersion: Literal["ar-review-assessment/v1"] = "ar-review-assessment/v1"
    examinedInputs: ExaminedInputs
    provenance: AssessmentProvenance


def assessment_subject_id(assessment: ReviewAssessment) -> str:
    """Return the one spelling a subject is addressed by in a read projection.

    ``kind:recordId`` rather than the record id alone, because a family and an invariant may
    legitimately share a spelling in different namespaces, and a projection that merged them would
    report one subject's assessment under another's identity -- requirement 5.5's per-examined-item
    rule, at the level of the subject. A writer and a reader both come through here, so the spelling
    cannot drift between the two.
    """

    return f"{assessment.subject.kind}:{assessment.subject.recordId}"


def assessment_binding_gaps(assessment: ReviewAssessment) -> tuple[str, ...]:
    """Return the binding obligations one assessment left unmet, in a stable order.

    Structural only, and deliberately narrow: it names what is *absent*, never whether what is
    present is right. The publication path refuses a non-empty result with no row written, which is
    requirement 1.3's shape -- and the reason ``no_concern_found`` is not exempt is requirement 1.4.
    """

    gaps: list[str] = []
    if not assessment.examinedInputs.identities:
        gaps.append("examined-inputs")
    if not assessment.evidenceRefs:
        gaps.append("evidence-references")
    return tuple(gaps)


def recorded_assessment_digest(assessment: ReviewAssessment) -> str:
    """Return the content address of one assessment's authored act.

    The digest covers the judgment, the binding and the provenance, so two assessments with the same
    digest are the same authored act. It is deliberately *not* stored on the record: an assessment
    that carried its own digest would have to be re-addressed whenever its binding was recomputed,
    and the binding is exactly the thing that must stay still while currentness moves.
    """

    return sha256_digest(assessment.model_dump(mode="json", by_alias=True))


class AssessmentEntry(_StrictAssessmentModel):
    """One assessment as a projection reports it: identity, disposition and currentness."""

    assessmentId: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    disposition: ReviewAssessmentDisposition
    currentness: Literal["current", "stale"]


class SubjectAssessmentState(_StrictAssessmentModel):
    """The read projection of one subject's assessment history.

    ``status`` is one of :data:`SubjectAssessmentStatus`. ``none-recorded`` carries an empty
    ``assessments`` tuple and a zero count, and there is no path through this model that produces a
    disposition for a subject with no stored assessment.
    """

    status: SubjectAssessmentStatus
    assessmentCount: int = Field(ge=0)
    unresolvedCount: int = Field(ge=0)
    staleCount: int = Field(ge=0)
    assessments: tuple[AssessmentEntry, ...] = ()

    @model_validator(mode="after")
    def _counts_describe_the_records_that_exist(self) -> Self:
        if self.assessmentCount != len(self.assessments):
            raise ValueError("the assessment count must be the number of stored records")
        if self.status == "none-recorded" and self.assessments:
            raise ValueError("a subject with no recorded assessment cannot report one")
        if self.status != "none-recorded" and not self.assessments:
            raise ValueError("a subject with a recorded assessment cannot report none")
        return self


def assessment_state_for(
    assessments: Sequence[ReviewAssessment],
    *,
    stale_ids: Sequence[str] = (),
) -> SubjectAssessmentState:
    """Project stored assessments onto the distinct states the read layer must report.

    The states are ``none-recorded`` (no stored record for the subject), ``unresolved`` (a stored
    record whose disposition is ``unresolved``), ``stale`` (a stored record no longer bound to
    current inputs) and ``current``. They are not one "not compatible" state, and this function never
    manufactures a disposition: ``assessmentCount`` counts records that exist, so a subject with no
    stored assessment answers zero and ``none-recorded`` -- requirement 4.4's rule, expressed as the
    only place this package produces such a count.
    """

    stale = frozenset(stale_ids)
    entries = tuple(
        AssessmentEntry(
            assessmentId=assessment.assessmentId,
            disposition=assessment.disposition,
            currentness="stale" if assessment.assessmentId in stale else "current",
        )
        for assessment in assessments
    )
    if not entries:
        status: SubjectAssessmentStatus = "none-recorded"
    elif any(entry.currentness == "stale" for entry in entries):
        status = "stale"
    elif any(entry.disposition == "unresolved" for entry in entries):
        status = "unresolved"
    else:
        status = "current"
    return SubjectAssessmentState(
        status=status,
        assessmentCount=len(entries),
        unresolvedCount=sum(1 for entry in entries if entry.disposition == "unresolved"),
        staleCount=sum(1 for entry in entries if entry.currentness == "stale"),
        assessments=entries,
    )


__all__ = [
    "ASSESSMENT_SUBJECT_KINDS",
    "REVIEW_ASSESSMENT_DISPOSITIONS",
    "SUBJECT_ASSESSMENT_STATUSES",
    "AssessmentEntry",
    "AssessmentEvidenceByte",
    "AssessmentEvidenceNamespace",
    "AssessmentEvidenceReference",
    "AssessmentProvenance",
    "AssessmentSubject",
    "AssessmentSubjectKind",
    "ExaminedInputs",
    "ReviewAssessment",
    "ReviewAssessmentDisposition",
    "SubjectAssessmentState",
    "SubjectAssessmentStatus",
    "assessment_binding_gaps",
    "assessment_state_for",
    "assessment_subject_id",
    "recorded_assessment_digest",
]
