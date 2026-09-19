"""Building, checking and reading the coherence authority's typed assessment collection.

``KS-R15@v1`` §8.1 makes this leaf the owner of the collection
``design/retrieval-review-design.md:350`` asks for: a typed family/invariant assessment collection on
the *existing* curator-coherence authority, with that authority's source-candidate exact-coverage
obligation left intact. The two obligations live in two different modules on purpose --
``_judgments_cover_candidates_exactly`` stays in the model and is not touched, while everything this
module adds is about the separate collection beside it.

Three rules shape what is here, and each of them is a refusal rather than a convention:

* **Authorship comes from the publication path.** :func:`bind_assessment` takes the authenticated
  author and role as arguments and stamps them onto the record; no function in this module accepts an
  author from a caller's submission text, because the submission shape has no such field to accept
  (requirement 2.1).
* **The binding is the exact examined inputs, and nothing decides equivalence.** The declaration is
  built from the observation the publication path already captured, so an assessment binds the same
  identities the coherence record binds. Currentness is compared for equality by
  :mod:`…review_assessment_binding`; nothing here reinterprets a moved input.
* **Persisting endorses nothing.** No function here reads a disposition to decide an outcome, and the
  collection cannot advance, gate or approve anything (requirements 2.2, 2.3, 8.4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from agents_remember.models.lifecycles.evidence_dependencies import (
    EvidenceDependency,
    EvidenceDependencyError,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceByte,
    AssessmentEvidenceReference,
    AssessmentProvenance,
    ExaminedInputs,
    ReviewAssessment,
    ReviewAssessmentRevision,
    recorded_assessment_digest,
)
from agents_remember.models.lifecycles.review_assessment_binding import (
    AssessmentBindingStaleError,
    AssessmentCurrentness,
    assessment_currentness,
    require_assessment_dependencies,
)


class PublishedEvidenceByte(Protocol):
    """One published evidence byte together with the citation it was published for.

    Declared as a protocol rather than imported as a class because the concrete carrier is the
    publication path's own value object, which lives above this module. What this module needs from it
    is exactly these two attributes, and stating that as a protocol keeps the dependency pointing the
    right way: the binding knows what it must be handed, not where the hand came from.
    """

    @property
    def reference(self) -> AssessmentEvidenceReference: ...

    @property
    def byte(self) -> AssessmentEvidenceByte: ...


class ReviewAssessmentError(ValueError):
    """One assessment's structural contract is unmet, with the exact code a caller branches on."""

    def __init__(self, status: str, detail: str, *, next_action: str = "publish") -> None:
        self.status = status
        self.detail = detail
        self.next_action = next_action
        super().__init__(f"{status}: {detail}")

    def response_fields(self) -> dict[str, object]:
        return {
            "status": self.status,
            "detail": self.detail,
            "nextAction": self.next_action,
        }


@dataclass(frozen=True)
class AssessmentInputs:
    """The exact inputs one assessment examined, as the publication path observed them.

    Every field is an identity the coherence observation already captured, which is what makes the
    assessment's binding and the coherence record's binding two statements about *the same* candidate
    rather than two statements that happen to agree. ``scopeManifestRef`` is the registered-scope
    manifest the assessment examined; it is a recorded fact passed in by the caller and never derived
    from a name or a path, because route scope is a recorded scope and not an inference
    (``KS-R10@v1`` §4.5).
    """

    scopeManifestRef: str
    comparisonRef: str
    codeCandidateTree: str
    memoryCandidateTree: str | None
    pairIdentityDigest: str
    taskTopologyFingerprint: str
    taskIntentDigest: str
    resolverVersion: str
    policyVersion: str
    evidenceBytes: tuple[AssessmentEvidenceByte, ...] = ()


def examined_input_declaration(inputs: AssessmentInputs) -> ExaminedInputs:
    """Declare the exact examined inputs as an ``ar-evidence-dependencies/v1`` binding.

    The kinds are the ones the shipped contract already names and its ``review-assessment/v1`` policy
    requires; ``KS-R15@v1`` §5.1 lists the inputs and explicitly says this leaf uses that contract
    rather than inventing a second one. ``evidence-bytes`` edges are derived from the recorded bytes,
    so each cited byte's path and digest travel together and the declaration a validator re-checks is
    the same one a reader resolves the bytes through.
    """

    edges = [
        dependency("candidate-state", "knowledge-candidate-pair", inputs.pairIdentityDigest),
        dependency(
            "code-tree",
            "candidate",
            inputs.codeCandidateTree,
            algorithm="git-object",
        ),
        dependency(
            "semantic-topology",
            "registered-scope",
            inputs.taskTopologyFingerprint,
        ),
        dependency("task-intent", "requirement-identities", inputs.taskIntentDigest),
        dependency("evidence-bytes", "scope-manifest", canonical_sha256(inputs.scopeManifestRef)),
        dependency("evidence-bytes", "comparison", canonical_sha256(inputs.comparisonRef)),
        dependency("validator", inputs.resolverVersion, canonical_sha256(inputs.resolverVersion)),
        dependency("validator", inputs.policyVersion, canonical_sha256(inputs.policyVersion)),
    ]
    if inputs.memoryCandidateTree is not None:
        edges.append(
            dependency(
                "memory-tree",
                "candidate",
                inputs.memoryCandidateTree,
                algorithm="git-object",
            )
        )
    edges.extend(
        dependency("evidence-bytes", byte.path, byte.sha256) for byte in inputs.evidenceBytes
    )
    try:
        declaration = build_evidence_dependencies("review-assessment/v1", edges)
    except EvidenceDependencyError as error:
        raise ReviewAssessmentError(error.status, error.detail) from error
    return ExaminedInputs(declaration=declaration)


def bind_assessment(
    *,
    authorized: ReviewAssessmentRevision,
    inputs: AssessmentInputs,
    author_ref: str,
    author_role: str,
    publication_ref: str,
) -> ReviewAssessment:
    """Stamp one authored revision with its provenance and its binding to the exact inputs.

    ``author_ref``, ``author_role`` and ``inputs`` are **arguments supplied by the publication
    path**, and ``authorized`` is a :class:`ReviewAssessmentRevision`, which has no field to carry
    any of them. That is requirement 2.1 as a type boundary rather than a check: a caller of the
    publication cannot state who authored the assessment, cannot state its role, and cannot state
    which inputs it examined, because the submission shape has nowhere to put any of the three.
    """

    if not authorized.evidenceRefs:
        raise ReviewAssessmentError(
            "review-assessment-binding-incomplete",
            f"assessment {authorized.assessmentId} has no recorded evidence-references; a record "
            "without it is indistinguishable from an absent assessment and is refused with nothing "
            "written",
        )
    declaration = examined_input_declaration(inputs)
    record = ReviewAssessment(
        **authorized.model_dump(),
        examinedInputs=declaration,
        provenance=AssessmentProvenance(
            authorRef=author_ref,
            authorRole=author_role,
            publicationRef=publication_ref,
        ),
    )
    require_assessment_dependencies(record)
    return record


def require_assessments_are_identified(assessments: Iterable[ReviewAssessmentRevision]) -> None:
    """Refuse a collection whose assessment identities are not unique.

    The model enforces uniqueness on a stored record; this runs earlier, on the *submission*, so a
    duplicate is refused with a typed code naming the identity rather than surfacing as a pydantic
    error from a document the caller has not built yet.
    """

    seen: set[str] = set()
    for assessment in assessments:
        if assessment.assessmentId in seen:
            raise ReviewAssessmentError(
                "review-assessment-duplicate-identity",
                f"assessment {assessment.assessmentId} appears more than once in one publication",
            )
        seen.add(assessment.assessmentId)


def assessment_currentness_for_record(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]],
) -> tuple[AssessmentCurrentness, ...]:
    """Report each stored assessment's currentness without failing the read.

    A stale assessment is reported, not raised: requirement 5.4 keeps it visible, and a read that
    refused on the first stale record could not report the others at all.
    """

    return tuple(
        assessment_currentness(assessment, current.get(assessment.assessmentId, {}))
        for assessment in assessments
    )


def require_current_assessments(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]],
    *,
    operation: str,
) -> None:
    """Refuse an operation that may only proceed over assessments still bound to current inputs."""

    for assessment in assessments:
        currentness = assessment_currentness(assessment, current.get(assessment.assessmentId, {}))
        if currentness.is_stale:
            first = currentness.gaps[0]
            raise AssessmentBindingStaleError(
                "review-assessment-binding-stale",
                f"{operation} refuses assessment {assessment.assessmentId}: its recorded binding no "
                f"longer matches the current inputs ({first.identity})",
                assessment_id=assessment.assessmentId,
                operation=operation,
                gaps=currentness.gaps,
            )


def reviewed_bytes(
    references: Sequence[AssessmentEvidenceReference],
    published: Iterable[PublishedEvidenceByte],
) -> tuple[AssessmentEvidenceByte, ...]:
    """Return the per-byte facts a binding declares for citations whose bytes are already published.

    Each published byte carries three facts that must travel together (the task-root-relative path a
    later reader opens, the digest it verifies against, and the size) because a digest alone cannot be
    opened and a path alone cannot be verified. Each value in ``published`` carries its own citation
    as a field, so this function never reads a file and never assumes a positional correspondence: it
    matches by citation spelling, refuses a citation with no published byte, and refuses a byte with no
    citation. The bytes bound here are the bytes the publication wrote *and read back*; measuring them
    again later would let the record describe bytes nobody verified.
    """

    measured = {item.reference.spelling: item.byte for item in published}
    facts: list[AssessmentEvidenceByte] = []
    for reference in references:
        byte = measured.pop(reference.spelling, None)
        if byte is None:
            raise ReviewAssessmentError(
                "review-assessment-evidence-unmeasured",
                f"cited evidence {reference.spelling} has no published byte to bind",
            )
        facts.append(byte)
    if measured:
        raise ReviewAssessmentError(
            "review-assessment-evidence-uncited",
            "a published evidence byte has no citation in the assessment it was published for: "
            + ", ".join(sorted(measured)),
        )
    return tuple(facts)


def review_record_edges(
    assessments: Iterable[ReviewAssessment],
) -> tuple[EvidenceDependency, ...]:
    """Declare the coherence record's ``review-record`` edge to each stored assessment.

    One direction only, and this is the direction that owns the kind. The assessment declares the
    inputs it examined; the *record* declares an edge to each assessment it stores, so a reader can
    resolve an assessment from the coherence record without parsing a payload. Requiring
    ``review-record`` inside an assessment's own binding would be a record citing itself -- the
    self-invalidating shape ``design/retrieval-review-design.md:368`` exists to avoid -- which is
    exactly why :data:`SELF_REFERENTIAL_KINDS` excludes it from the staleness comparison.
    """

    return tuple(
        dependency(
            "review-record",
            assessment_edge_name(assessment.assessmentId),
            recorded_assessment_digest(assessment),
        )
        for assessment in assessments
    )


def assessment_edge_name(assessment_id: str) -> str:
    """Return the one spelling a stored assessment is addressed by in a record's dependency edges."""

    return f"review-assessment:{assessment_id}"


__all__ = [
    "AssessmentInputs",
    "ReviewAssessmentError",
    "assessment_currentness_for_record",
    "assessment_edge_name",
    "bind_assessment",
    "examined_input_declaration",
    "require_assessments_are_identified",
    "require_current_assessments",
    "review_record_edges",
    "reviewed_bytes",
]
