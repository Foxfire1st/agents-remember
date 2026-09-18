"""Structured authority contracts for leaf curator-coherence publication."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout.source import EvidenceFact
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.evidence_dependencies import (
    EVIDENCE_DEPENDENCY_VALIDATOR,
    EvidenceDependencies,
    EvidenceDependencyError,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
    require_evidence_dependencies,
)
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    ReviewAssessmentRevision,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity, TaskIntentState

Digest = str
CuratorCoherenceAction = Literal["status", "prepare", "publish", "validate"]
CuratorDisposition = Literal[
    "reconciled",
    "preserved",
    "extended",
    "superseded",
    "contradicted",
    "no-content-impact",
    "no-route-impact",
    "capture-candidate",
]
MAX_CURATOR_SOURCE_CANDIDATES = 2048
# The assessment collection's own bound. It is deliberately far below the candidate bound: an
# assessment is an authored act a human writes, not a machine-produced row per changed file, so a
# thousand of them on one leaf is already a defect rather than a workload.
MAX_CURATOR_REVIEW_ASSESSMENTS = 256
MEMORY_QUALITY_ATTESTATION_VALIDATOR = "curator-memory-quality-attestation/v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CuratorSourceCandidate(_StrictModel):
    sourceFile: str = Field(min_length=1, max_length=8192)
    onboardingFile: str = Field(min_length=1, max_length=8192)
    classification: str = Field(min_length=1, max_length=256)

    @field_validator("sourceFile", "onboardingFile", "classification")
    @classmethod
    def _strip_identity(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("curator source-candidate identity fields must not be blank")
        return cleaned

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.sourceFile, self.onboardingFile, self.classification


class CuratorQualityAttestation(_StrictModel):
    """Exact enclosure-local memory-quality source consumed by coherence."""

    schema_: Literal["ar-curator-memory-quality/v1"] = Field(alias="schema")
    checklistStatus: Literal["action-required", "ready-for-closeout"]
    curatorActionableCount: int = Field(ge=0)
    memoryRepairCount: int = Field(ge=0)
    missingOnboardingCount: int = Field(ge=0)
    staleRouteIndexCount: int = Field(ge=0)
    sourceChangeCandidateCount: int = Field(ge=0, le=MAX_CURATOR_SOURCE_CANDIDATES)
    sourceChangeCandidates: list[CuratorSourceCandidate] = Field(
        max_length=MAX_CURATOR_SOURCE_CANDIDATES
    )
    pairIdentity: MemoryCandidatePairIdentity
    onboardingRoot: str = Field(min_length=1, max_length=8192)
    reportPath: str = Field(min_length=1, max_length=8192)
    reportSha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    dependencies: EvidenceDependencies | None = None

    @model_validator(mode="after")
    def _candidate_collection_is_exact(self) -> Self:
        identities = [candidate.identity for candidate in self.sourceChangeCandidates]
        if self.sourceChangeCandidateCount != len(identities):
            raise ValueError("sourceChangeCandidateCount does not match its candidate list")
        if len(identities) != len(set(identities)):
            raise ValueError("source-change candidate identities must be unique")
        return self


def memory_quality_attestation_dependencies(
    *,
    pair_identity: MemoryCandidatePairIdentity,
    code_candidate_tree: str,
    memory_candidate_tree: str,
    report_sha256: str,
) -> EvidenceDependencies:
    """Bind the memory-quality result to exactly the candidate pair it inspected."""

    return build_evidence_dependencies(
        "memory-quality-attestation/v1",
        [
            dependency("candidate-state", "memory-candidate-pair", pair_identity.contractDigest),
            dependency(
                "code-tree",
                "candidate",
                code_candidate_tree,
                algorithm="git-object",
            ),
            dependency(
                "memory-tree",
                "candidate",
                memory_candidate_tree,
                algorithm="git-object",
            ),
            dependency("evidence-bytes", "rendered-checklist", report_sha256),
            dependency(
                "validator",
                MEMORY_QUALITY_ATTESTATION_VALIDATOR,
                canonical_sha256(MEMORY_QUALITY_ATTESTATION_VALIDATOR),
            ),
            dependency(
                "validator",
                EVIDENCE_DEPENDENCY_VALIDATOR,
                canonical_sha256(EVIDENCE_DEPENDENCY_VALIDATOR),
            ),
        ],
    )


def require_memory_quality_attestation_dependencies(
    attestation: CuratorQualityAttestation,
    *,
    code_candidate_tree: str,
    memory_candidate_tree: str,
) -> EvidenceDependencies:
    """Refuse an attestation whose declared inputs differ from its current source facts."""

    expected = memory_quality_attestation_dependencies(
        pair_identity=attestation.pairIdentity,
        code_candidate_tree=code_candidate_tree,
        memory_candidate_tree=memory_candidate_tree,
        report_sha256=attestation.reportSha256,
    )
    observed = require_evidence_dependencies(
        attestation.dependencies,
        record_type="memory-quality-attestation/v1",
    )
    if observed != expected:
        raise EvidenceDependencyError(
            "memory-quality-attestation-dependencies-stale",
            "memory-quality attestation dependencies do not match its canonical inputs",
        )
    return observed


class CuratorCoherenceJudgment(_StrictModel):
    """One agent-owned semantic judgment; the lifecycle never invents these fields."""

    sourceFile: str = Field(min_length=1, max_length=8192)
    onboardingFile: str = Field(min_length=1, max_length=8192)
    classification: str = Field(min_length=1, max_length=256)
    disposition: CuratorDisposition
    rationale: str = Field(min_length=1, max_length=8192)
    evidenceRef: str = Field(min_length=1, max_length=8192)

    @field_validator("sourceFile", "onboardingFile", "classification", "rationale", "evidenceRef")
    @classmethod
    def _strip_judgment_fields(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("curator judgment fields must not be blank")
        return cleaned

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.sourceFile, self.onboardingFile, self.classification


class CuratorCoherenceRecordedJudgment(CuratorCoherenceJudgment):
    """Published judgment with the lifecycle-captured evidence digest."""

    evidenceSha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")


class CuratorCoherenceRecord(_StrictModel):
    """Immutable content-bound generation selected by the stable authority manifest."""

    schemaVersion: Literal["ar-curator-coherence-record/v1"] = "ar-curator-coherence-record/v1"
    leafId: str = Field(min_length=1, max_length=4096)
    contractPath: str = Field(min_length=1, max_length=8192)
    taskDocumentRef: TaskDocumentRef
    semanticRequirementRevision: str = Field(min_length=1, max_length=1024)
    deliveryAttempt: str = Field(min_length=1, max_length=256)
    pairIdentity: MemoryCandidatePairIdentity
    codeCandidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryCandidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    taskTopologyFingerprint: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    taskIntent: TaskIntentState
    attestationPath: str = Field(min_length=1, max_length=8192)
    attestationSha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    attestationReportSha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    # The durable copy of the bytes ``attestationSha256`` commits to: task-root-relative, in the same
    # surviving tree as the record. ``attestationPath`` names the enclosure's own file, which
    # ``lifecycle_finalize_task`` reclaims -- without this copy the record's digest names bytes that
    # no longer exist anywhere, and a reader cannot tell a candidate-empty publication from one whose
    # attestation listed candidates. Optional because authorities published before this field existed
    # do not carry it.
    attestationCopyPath: str | None = Field(default=None, max_length=8192)
    sourceCandidates: list[CuratorSourceCandidate] = Field(max_length=MAX_CURATOR_SOURCE_CANDIDATES)
    judgments: list[CuratorCoherenceRecordedJudgment] = Field(
        max_length=MAX_CURATOR_SOURCE_CANDIDATES
    )
    # ``KS-R15@v1`` §8.1's extension: the typed assessment collection on this same authority. It is a
    # SEPARATE collection from ``judgments`` and that separation is the whole point. A judgment's
    # identity is the ``(sourceFile, onboardingFile, classification)`` triple at
    # ``CuratorCoherenceJudgment.identity`` and ``_judgments_cover_candidates_exactly`` refuses a
    # record whose judgment set is not exactly its source-candidate set; a knowledge review's subject
    # is a family, an invariant revision or a comparison, none of which is a source-file pair.
    # Appending one to ``judgments`` would therefore either break exact coverage or fabricate a
    # source-candidate tuple for a family, and ``design/retrieval-review-design.md:30`` forbids
    # exactly that. The coverage obligation below is untouched by this field, and the default keeps
    # every already-published generation valid: an assessment is optional content, and its ABSENCE is
    # reported as the ``none-recorded`` state rather than as an empty favourable disposition.
    assessments: list[ReviewAssessment] = Field(
        default_factory=list, max_length=MAX_CURATOR_REVIEW_ASSESSMENTS
    )
    dependencies: EvidenceDependencies | None = None
    predecessorAuthorityDigest: Digest = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    publicationFingerprint: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    publishedBy: str = Field(min_length=1, max_length=8192)
    reportSha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def _decode_legacy_missing_intent(cls, value: Any) -> Any:
        if isinstance(value, dict) and "taskIntent" not in value:
            return {**value, "taskIntent": {"state": "missing-intent"}}
        return value

    @model_validator(mode="after")
    def _judgments_cover_candidates_exactly(self) -> Self:
        candidates = [candidate.identity for candidate in self.sourceCandidates]
        judgments = [judgment.identity for judgment in self.judgments]
        if len(candidates) != len(set(candidates)):
            raise ValueError("coherence record source candidates must be unique")
        if len(judgments) != len(set(judgments)):
            raise ValueError("coherence record judgments must be unique")
        if set(candidates) != set(judgments):
            raise ValueError("coherence record judgments must exactly cover source candidates")
        return self

    @model_validator(mode="after")
    def _assessments_are_uniquely_identified(self) -> Self:
        """One assessment identity may appear once, and the collection stays outside coverage.

        The uniqueness rule is the collection's own; it says nothing about the source candidates,
        which is what keeps ``_judgments_cover_candidates_exactly`` above the only rule relating
        judgments to candidates. An assessment whose identity repeated would make "which assessment
        covers this item" ambiguous, and requirement 5.5's per-examined-item rule has no meaning once
        coverage is ambiguous.
        """

        identities = [assessment.assessmentId for assessment in self.assessments]
        if len(identities) != len(set(identities)):
            raise ValueError("coherence record assessment identities must be unique")
        return self


class CuratorCoherenceAuthority(_StrictModel):
    """The only stable live pointer; historical generations never compete with it."""

    schemaVersion: Literal["ar-curator-coherence-authority/v1"] = (
        "ar-curator-coherence-authority/v1"
    )
    leafId: str = Field(min_length=1, max_length=4096)
    contractPath: str = Field(min_length=1, max_length=8192)
    currentRecordDigest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    recordPath: str = Field(min_length=1, max_length=8192)
    reportPath: str = Field(min_length=1, max_length=8192)
    reportSha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")


class CuratorCoherenceSnapshot(_StrictModel):
    schemaVersion: Literal["ar-curator-coherence-snapshot/v1"] = "ar-curator-coherence-snapshot/v1"
    semanticRequirementRevision: str = Field(min_length=1, max_length=1024)
    deliveryAttempt: str = Field(min_length=1, max_length=256)
    recordDigest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    recordPath: str = Field(min_length=1, max_length=8192)
    reportPath: str = Field(min_length=1, max_length=8192)


@dataclass(frozen=True)
class PublicationMember:
    """One request member ``publish`` requires, under its caller-written field name.

    ``caller_supplied`` marks the members ``prepare`` does not derive from the observation it
    returns: a statement about the delivery attempt that stays the caller's to author. The
    publication validator, the ``publish`` refusal and the ``prepare`` summary all read
    ``PUBLICATION_MEMBERS``, so no reader can name a member the others do not know about, and a
    member appended there needs no second edit anywhere -- which is the whole defect this removes.
    """

    name: str
    caller_supplied: bool = False


# The publication members, in the request model's declaration order. This single declaration is
# the authority for what `publish` requires: the validator refuses on it, the refusal names what
# is missing from it, and the `prepare` text states all of it.
PUBLICATION_MEMBERS: tuple[PublicationMember, ...] = (
    PublicationMember("semantic_requirement_revision", caller_supplied=True),
    PublicationMember("delivery_attempt", caller_supplied=True),
    PublicationMember("expected_predecessor_digest"),
    PublicationMember("expected_code_candidate_tree"),
    PublicationMember("expected_memory_candidate_tree"),
    PublicationMember("expected_task_topology_fingerprint"),
    PublicationMember("expected_task_intent"),
    PublicationMember("expected_attestation_sha256"),
    PublicationMember("caller"),
)

# A publication input too, but not one of the nine the `None` check covers: a leaf with no source
# candidates publishes with an empty judgment list.
JUDGMENTS_MEMBER = "judgments"

# And likewise for the assessment collection: a leaf with nothing to review publishes with an empty
# list. It is named here rather than added to `PUBLICATION_MEMBERS` so the nine-member refusal keeps
# its exact meaning -- every one of those nine is an identity `publish` must be told, while this one
# is content a leaf may legitimately have none of.
REVIEW_ASSESSMENTS_MEMBER = "review_assessments"


def _declared_publication_members(
    members: tuple[PublicationMember, ...] | None,
) -> tuple[PublicationMember, ...]:
    """Read the declaration at call time so one extended declaration drives every reader."""

    return PUBLICATION_MEMBERS if members is None else members


def _names_in_english(names: Sequence[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def publication_refusal(missing: Sequence[str]) -> str:
    """Name every missing publication member, in declaration order.

    The shipped opening sentence stays, so a reader who already knows the rule still recognizes
    it. The callout names only the *missing* members that are the caller's own delivery
    identities: a refusal that named a member the caller did supply would be the very defect this
    text exists to remove.
    """

    text = "publish requires every identity, predecessor, and caller field; missing " + ", ".join(
        missing
    )
    caller_supplied = {member.name for member in PUBLICATION_MEMBERS if member.caller_supplied}
    identities = [name for name in missing if name in caller_supplied]
    if len(identities) == 1:
        text += (
            f"; {identities[0]} is a caller-supplied delivery identity prepare does not derive"
            " -- supply it in the request"
        )
    elif identities:
        text += (
            f"; {_names_in_english(identities)} are caller-supplied delivery identities prepare"
            " does not derive -- supply them in the request"
        )
    return text + "."


def forbidden_publication_refusal(supplied: Sequence[str]) -> str:
    """Name the publication-only request fields a ``status``/``prepare``/``validate`` received."""

    return "status/prepare/validate forbid publication-only fields; supplied " + ", ".join(supplied)


def publication_input_statement(
    members: tuple[PublicationMember, ...] | None = None,
) -> str:
    """State every input ``publish`` requires, derived from the one member declaration.

    A member appended to the declaration appears here with no edit to this text, and the members
    ``prepare`` does not derive are called out as caller-supplied delivery identities.
    """

    declared = _declared_publication_members(members)
    statement = (
        "to publish, supply one agent-owned judgment per source candidate and every request "
        "member publish requires: " + ", ".join(member.name for member in declared)
    )
    identities = [member.name for member in declared if member.caller_supplied]
    if len(identities) == 1:
        statement += (
            f"; {identities[0]} is a caller-supplied delivery identity prepare does not derive"
            " from the observation it returns"
        )
    elif identities:
        statement += (
            f"; {_names_in_english(identities)} are caller-supplied delivery identities prepare"
            " does not derive from the observation it returns"
        )
    return statement + "."


class CuratorCoherenceRequest(_StrictModel):
    action: CuratorCoherenceAction
    contract_path: str = Field(min_length=1, max_length=8192)
    semantic_requirement_revision: str | None = Field(default=None, max_length=1024)
    delivery_attempt: str | None = Field(default=None, max_length=256)
    judgments: list[CuratorCoherenceJudgment] = Field(
        default_factory=list, max_length=MAX_CURATOR_SOURCE_CANDIDATES
    )
    # ``KS-R15@v1`` §8.1's collection, as a publish input. It is deliberately *not* a member of
    # ``PUBLICATION_MEMBERS``: a leaf with nothing to review publishes no assessment, and making the
    # field required would force every existing caller to supply an empty list to say so. Like
    # ``judgments`` it is refused on ``status``/``prepare``/``validate`` by the shape validator below,
    # so the four actions keep one input shape each.
    #
    # The element type is ``ReviewAssessmentRevision``, not ``ReviewAssessment``, and the difference
    # is the point: the revision carries only what a caller authors -- the finding, its rationale, its
    # assumptions, its disposition and its citations. It has no ``provenance``, no ``authorRef``, no
    # ``authorRole`` and no ``examinedInputs``, so a caller cannot author an assessment under another
    # identity and cannot claim inputs it did not examine (requirement 2.1). The publication path
    # stamps those fields from its own authenticated caller and its own observation.
    review_assessments: list[ReviewAssessmentRevision] = Field(
        default_factory=list, max_length=MAX_CURATOR_REVIEW_ASSESSMENTS
    )
    expected_predecessor_digest: str | None = Field(default=None, pattern=r"^$|^[0-9a-f]{64}$")
    expected_code_candidate_tree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    expected_memory_candidate_tree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    expected_task_topology_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_task_intent: TaskIntentIdentity | None = None
    expected_attestation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    freeze_snapshot: bool = False
    caller: DeclaredCaller | None = None

    @field_validator("contract_path")
    @classmethod
    def _strip_contract_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("contract_path must not be blank")
        return cleaned

    @field_validator("semantic_requirement_revision", "delivery_attempt")
    @classmethod
    def _strip_optional_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("semantic requirement revision and delivery attempt must not be blank")
        return cleaned

    def _publication_inputs_supplied(self) -> tuple[str, ...]:
        """The publication-only request fields this request actually supplied, in model order.

        ``judgments`` and ``review_assessments`` are included when non-empty so the sibling refusal
        can name them too; neither is one of the nine members ``publish`` requires to be non-``None``.
        """

        declared = {member.name for member in PUBLICATION_MEMBERS}
        content = (JUDGMENTS_MEMBER, REVIEW_ASSESSMENTS_MEMBER)
        return tuple(
            name
            for name in type(self).model_fields
            if (name in declared and getattr(self, name) is not None)
            or (name in content and bool(getattr(self, name)))
        )

    @model_validator(mode="after")
    def _action_has_one_input_shape(self) -> Self:
        supplied = self._publication_inputs_supplied()
        if self.action == "publish":
            missing = tuple(
                member.name for member in PUBLICATION_MEMBERS if member.name not in supplied
            )
            if missing:
                raise ValueError(publication_refusal(missing))
            return self
        if supplied:
            raise ValueError(forbidden_publication_refusal(supplied))
        if self.freeze_snapshot:
            raise ValueError("only publish may freeze an immutable attempt snapshot")
        return self


class CuratorCoherenceValidationResult(_StrictModel):
    state: Literal["valid"] = "valid"
    candidateCount: int = Field(ge=0)
    checked: list[str] = Field(default_factory=list, max_length=32)


class CuratorCoherenceResponse(ToolResponse):
    operation: Literal["curator_coherence"] = "curator_coherence"
    action: CuratorCoherenceAction
    state: str = Field(max_length=256)
    summary: str = Field(max_length=8192)
    contractPath: str = Field(max_length=8192)
    canonicalPath: str | None = Field(default=None, max_length=8192)
    recordPath: str | None = Field(default=None, max_length=8192)
    reportPath: str | None = Field(default=None, max_length=8192)
    snapshotPath: str | None = Field(default=None, max_length=8192)
    semanticRequirementRevision: str | None = Field(default=None, max_length=1024)
    deliveryAttempt: str | None = Field(default=None, max_length=256)
    pairIdentity: MemoryCandidatePairIdentity | None = None
    codeCandidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    memoryCandidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    taskTopologyFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    taskIntent: TaskIntentIdentity | None = None
    currentnessStatus: str | None = Field(default=None, max_length=256)
    attestationPath: str | None = Field(default=None, max_length=8192)
    attestationSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attestationReportSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    predecessorAuthorityDigest: str | None = Field(default=None, pattern=r"^$|^[0-9a-f]{64}$")
    recordDigest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reportDigest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidateCount: int | None = Field(default=None, ge=0)
    candidates: list[CuratorSourceCandidate] | None = Field(
        default=None, max_length=MAX_CURATOR_SOURCE_CANDIDATES
    )
    reviewAssessments: list[ReviewAssessment] | None = Field(
        default=None, max_length=MAX_CURATOR_REVIEW_ASSESSMENTS
    )
    validationResult: CuratorCoherenceValidationResult | None = None
    status: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=8192)
    pairField: str | None = Field(default=None, max_length=256)
    expected: dict[str, Any] | None = Field(default=None, max_length=32)
    observed: dict[str, Any] | None = Field(default=None, max_length=32)
    nextAction: str | None = Field(default=None, max_length=8192)
    nextArgs: dict[str, Any] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _failure_shape_is_coherent(self) -> Self:
        if self.ok:
            if self.status is not None or self.detail is not None:
                raise ValueError("successful coherence response cannot carry refusal fields")
        elif not self.status or not self.detail or self.state != "refused":
            raise ValueError("failed coherence response requires typed refusal fields")
        return self


@dataclass(frozen=True)
class ValidatedCuratorCoherence:
    """One validated coherence authority.

    Shelved with its model, not with the plane that first consumed it: the
    record, its paths and its evidence are model facts, and both the memory
    quality certification lane and the closeout lane read them.
    """

    authority: CuratorCoherenceAuthority
    record: CuratorCoherenceRecord
    record_path: Path
    report_path: Path
    record_digest: str
    evidence: list[EvidenceFact]
