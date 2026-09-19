"""The supporting-record read projection's declared contract: stored values, seeds, items, page.

``KS-R07@v1`` owns the recorded-scope selection and ``KS-R11@v1`` owns the facet selection; neither
is touched here. This is a **third** selection with its own seeds, its own item kinds, its own counts
and its own declared policy name, and nothing here is reachable from either of the other two -- so a
shipped seed's page stays byte-identical not by a guard but because no code path is shared.

What the projection is for, in one sentence: an evidence claim must be readable *with its
limitations visible*, and a verification observation must be readable as the facts a run produced and
nothing more. Three consequences shape every model here.

* **Limitations are part of the claim, and an empty one is a fact.** ``limitations`` is a required
  field of the claim's payload and a required field of the item that serves it, so a summary that
  drops it is not constructible rather than merely discouraged. The empty string is served as the
  empty string: a reader is told "this author declared no limitations", never "limitations unknown"
  and never an unqualified endorsement.
* **Absence is served as absence.** A claim with no recorded assessment is served with
  ``assessed=False`` and an empty reference list -- the explicit *unassessed* state -- never with a
  defaulted "compatible" or "complete". An observation whose artifact could not be checked is served
  with the resolution state that says so, and the observation's own content is unchanged either way:
  a missing artifact never erases the record that referenced it.
* **No field could carry a verdict.** There is no status, grade, score, confidence, severity or
  aggregate in any model here, and both item models are frozen and extra-forbidden, so a page cannot
  be extended with one. The execution result is served verbatim as the member that was recorded, and
  a passing run is served exactly as a failing one is: as a fact about what happened.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.evidence import (
    CoverageEndpoint,
    EvidenceClaimPayload,
    EvidenceSubject,
    VerificationObservationPayload,
)
from agents_remember.models.knowledge.read import KnowledgeReadSnapshot
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The declared policy this selection's pages were produced by. Its own name rather than the shipped
# ``recorded-family-frontier/v1`` or the facet selection's: a policy name is a claim about which
# selection produced a page, and three selections that select different things are three policies
# whatever modules they live in.
EVIDENCE_SELECTION_POLICY_VERSION = "supporting-evidence-records/v1"

# The declared execution bound of one evidence selection, and the reason it refuses past it. It is a
# bound on enumeration, not a page size: a selection either fits whole or is refused, so a caller is
# never handed a page it could read as the complete set.
EVIDENCE_SELECTION_ITEM_LIMIT = 5000

EVIDENCE_ITEM_LIMIT_REASON = (
    "the evidence selection exceeded the declared execution bound of "
    f"{EVIDENCE_SELECTION_ITEM_LIMIT} items"
)


# ---------------------------------------------------------------------------
# Stored values. Each is the exact shape one stored row serves, with the digest the read operations
# expose so a caller carries an expectation straight from a read.


class EvidenceClaimRecord(KnowledgeModel):
    """One stored evidence claim: its frozen payload, its envelope facts and its author.

    ``state_at_origin`` is the envelope's recorded lifecycle served under the same field name the
    shipped ``ReadItem`` uses for a displayed lifecycle, because it is the same fact: the state the
    record was written with. Persisting a claim endorses nothing, and this field is where a reader
    sees that a faithfully stored proposal is still a proposal.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    record_schema: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    authority_home: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    state_at_origin: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    payload: EvidenceClaimPayload
    provenance: Authorship
    row_digest: str = Field(pattern=SHA256_PATTERN)


class EvidenceClaimRevision(KnowledgeModel):
    """One sealed claim revision, with the payload exactly as it was validated and stored."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    record_schema: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    payload: EvidenceClaimPayload
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    content_digest: str = Field(pattern=SHA256_PATTERN)
    provenance: Authorship


class ClaimSubject(KnowledgeModel):
    """The claim's subject: one exact revision of one of the two declared subject kinds.

    The kind is the typed model, exactly as it is in the table that stores it, so a reader can never
    receive an anchor identity where a revision belongs.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    subject: EvidenceSubject
    row_digest: str = Field(pattern=SHA256_PATTERN)


class ClaimCoverage(KnowledgeModel):
    """One claimed-coverage edge: an endpoint the author asserted this evidence covers.

    It carries no ``covers`` verdict and no resolution state beyond the fact that it is stored: the
    coverage list is what the author asserts, and whether the endpoint still resolves is a property
    of the endpoint row, not of the claim.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    endpoint: CoverageEndpoint
    row_digest: str = Field(pattern=SHA256_PATTERN)


class VerificationObservationRecord(KnowledgeModel):
    """One stored verification observation: the recorded facts of a run that already happened."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    observation_id: str = Field(pattern=UUID_PATTERN)
    payload: VerificationObservationPayload
    row_digest: str = Field(pattern=SHA256_PATTERN)


class VerificationObservationRevision(KnowledgeModel):
    """One sealed observation revision, with the payload exactly as it was validated and stored."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    observation_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    record_schema: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    payload: VerificationObservationPayload
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    content_digest: str = Field(pattern=SHA256_PATTERN)
    provenance: Authorship


# ---------------------------------------------------------------------------
# The artifact resolution fact. Four states, and the fourth is not a failure.
#
# ``equal`` requires bytes to compare against, and bytes are only reachable when a caller supplied a
# root to resolve the repository-relative path against. Without one the honest state is
# ``not_attempted`` -- which is a *different statement* from ``equal`` and from ``absent``, and the
# whole point of serving resolution as data is that a reader can tell the three apart.

ArtifactResolutionState = Literal["equal", "absent", "digest_mismatch", "not_attempted"]


class ArtifactResolution(KnowledgeModel):
    """What one read observed about a recorded artifact reference.

    ``observed_sha256`` and ``observed_size_bytes`` are populated only when bytes were actually read,
    so a reader can never mistake a not-attempted state for a verified one. ``detail`` says what was
    checked, in the same idiom the shipped anchor resolution uses.
    """

    state: ArtifactResolutionState
    path: str = Field(min_length=1, max_length=4096)
    recorded_sha256: str = Field(pattern=SHA256_PATTERN)
    recorded_size_bytes: int = Field(ge=0)
    digest_checked_at_write: bool
    observed_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    observed_size_bytes: int | None = Field(default=None, ge=0)
    detail: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _require_observation_matches_state(self) -> ArtifactResolution:
        """Refuse a resolution that reports bytes it did not read, or a state without evidence."""

        if self.state in {"equal", "digest_mismatch"}:
            if self.observed_sha256 is None or self.observed_size_bytes is None:
                raise ValueError(
                    f"an artifact resolution reported as {self.state} names the bytes it observed"
                )
            return self
        if self.observed_sha256 is not None or self.observed_size_bytes is not None:
            raise ValueError(
                f"an artifact resolution reported as {self.state} observed no bytes, so it must "
                "not carry an observed digest or size"
            )
        return self


# ---------------------------------------------------------------------------
# The assessment state. This leaf does not implement an assessment and does not pre-shape one; it
# reports the *absence* of one as absence, which is the only honest thing it can report until
# ``KS-R15@v1`` lands and the reference clause becomes a resolved association.


class AssessmentReferenceState(KnowledgeModel):
    """The claim's recorded assessment references, served as references and nothing else.

    ``assessed`` is derived from the stored list being non-empty -- it is the recorded fact "the
    author named an assessment", never a judgement about the assessment. ``resolved`` is ``False``
    for every reference in this leaf because the referent is owned by ``KS-R15@v1`` and is not
    checked here; it is a declared field rather than an omission so that the state is visible and so
    that the later leaf changes this field's behaviour without changing the stored meaning of a
    sealed claim.
    """

    assessed: bool
    references: tuple[str, ...] = ()
    resolved: bool

    @model_validator(mode="after")
    def _require_state_to_describe_the_references(self) -> AssessmentReferenceState:
        if self.assessed != bool(self.references):
            raise ValueError(
                "the assessed state is the recorded fact that the author named an assessment, so it "
                f"is exactly whether a reference is listed: {self.assessed} against "
                f"{len(self.references)} reference(s)"
            )
        return self


# ---------------------------------------------------------------------------
# Seeds. Two kinds, because the selection answers two questions: what is this claim made of, and
# which observations recorded a run against this exact candidate.

ClaimIdSeedKind = Literal["evidence_claim"]
ObservationCandidateSeedKind = Literal["observation_candidate"]


class EvidenceClaimSeed(KnowledgeModel):
    """Select one evidence claim's whole aggregate by its exact claim identity."""

    kind: ClaimIdSeedKind = "evidence_claim"
    claim_id: str = Field(pattern=UUID_PATTERN)


class ObservationCandidateSeed(KnowledgeModel):
    """Select every observation whose recorded tested candidate is this exact candidate.

    The candidate is named the same way the record names it: the knowledge snapshot's logical digest
    and/or the code candidate's tree identity. Both are optional so that either half can be selected
    on its own -- a caller asking "what was run against this knowledge dataset" and a caller asking
    "what was run against this code tree" are two questions, and a seed that required both would
    answer neither.
    """

    kind: ObservationCandidateSeedKind = "observation_candidate"
    knowledge_logical_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    code_candidate_tree_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def _require_a_candidate(self) -> ObservationCandidateSeed:
        if self.knowledge_logical_digest is None and self.code_candidate_tree_id is None:
            raise ValueError(
                "an observation-candidate seed names the candidate it selects: the knowledge "
                "snapshot's logical digest, the code candidate's tree identity, or both. A seed "
                "that names neither selects every observation, which is a different question."
            )
        return self


EvidenceReadSeed = Annotated[
    EvidenceClaimSeed | ObservationCandidateSeed,
    Field(discriminator="kind"),
]


def evidence_seed_digest(seed: EvidenceReadSeed) -> str:
    """Return the digest sealing one evidence seed, on the shipped rule for a read selector."""

    return sha256_digest(seed.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Items. One model per item kind, so an item carries exactly the fields its kind has.


class EvidenceClaimItem(KnowledgeModel):
    """The evidence claim itself: its subject, anchor, coverage, author, lifecycle and content.

    ``limitations`` and ``explanation`` are non-optional fields of the served payload, so no
    projection of a claim can drop them.
    """

    kind: Literal["evidence_claim"] = "evidence_claim"
    claim: EvidenceClaimRecord
    subject: ClaimSubject
    evidence_anchor_id: str = Field(pattern=UUID_PATTERN)
    coverage: tuple[ClaimCoverage, ...]
    assessments: AssessmentReferenceState


class EvidenceClaimRevisionItem(KnowledgeModel):
    """One retained claim revision, carrying the same payload and its own seal."""

    kind: Literal["evidence_claim_revision"] = "evidence_claim_revision"
    revision: EvidenceClaimRevision


class VerificationObservationItem(KnowledgeModel):
    """One recorded observation: the run's facts, its artifact reference and its resolution state.

    ``execution_result`` is the member that was recorded, served verbatim. There is no companion
    field here that could turn it into a verdict, and an observation that recorded ``not_run`` is
    served as ``not_run``.
    """

    kind: Literal["verification_observation"] = "verification_observation"
    observation: VerificationObservationRecord
    artifact_resolution: ArtifactResolution | None = None


class VerificationObservationRevisionItem(KnowledgeModel):
    """One retained observation revision."""

    kind: Literal["verification_observation_revision"] = "verification_observation_revision"
    revision: VerificationObservationRevision


EvidenceReadItem = Annotated[
    EvidenceClaimItem
    | EvidenceClaimRevisionItem
    | VerificationObservationItem
    | VerificationObservationRevisionItem,
    Field(discriminator="kind"),
]

EvidenceReadItemKind = Literal[
    "evidence_claim",
    "evidence_claim_revision",
    "verification_observation",
    "verification_observation_revision",
]

# The declared order of the item stream. Fixed over item kinds and then over stable identifiers, so
# two runs over one snapshot produce the same page and no authored label, insertion order or
# timestamp can move an item.
_EVIDENCE_KIND_ORDER: dict[str, int] = {
    "evidence_claim": 1,
    "evidence_claim_revision": 2,
    "verification_observation": 3,
    "verification_observation_revision": 4,
}


def evidence_item_sort_key(item: EvidenceReadItem) -> tuple[int, str, str]:
    """Return the declared sort key of one item: item kind, then two stable identifiers."""

    return (_EVIDENCE_KIND_ORDER[item.kind], *_evidence_item_identifiers(item))


def _evidence_item_identifiers(item: EvidenceReadItem) -> tuple[str, str]:
    """Return the two stable identifiers one item is ordered by."""

    if isinstance(item, EvidenceClaimItem):
        return (item.claim.claim_id, item.claim.claim_id)
    if isinstance(item, EvidenceClaimRevisionItem):
        return (item.revision.claim_id, item.revision.revision_id)
    if isinstance(item, VerificationObservationItem):
        return (item.observation.observation_id, item.observation.observation_id)
    return (item.revision.observation_id, item.revision.revision_id)


def evidence_item_id(item: EvidenceReadItem) -> str:
    """Return the identity one item is addressed by inside its selection."""

    first, second = _evidence_item_identifiers(item)
    return f"{item.kind}:{first}/{second}"


class EvidenceReadCounts(KnowledgeModel):
    """What one evidence selection selected, by item kind.

    The counts are the selected set's own arithmetic -- derived from the same item tuple the page is
    built from -- so a page cannot report a total its items do not add up to. There is no ``has_more``
    and no remaining count, because a selection that does not fit whole is refused rather than paged.
    """

    evidence_claims: int = Field(ge=0)
    evidence_claim_revisions: int = Field(ge=0)
    verification_observations: int = Field(ge=0)
    verification_observation_revisions: int = Field(ge=0)

    @property
    def items_total(self) -> int:
        """Return the number of items the counts describe."""

        return (
            self.evidence_claims
            + self.evidence_claim_revisions
            + self.verification_observations
            + self.verification_observation_revisions
        )


class EvidenceReadPage(KnowledgeModel):
    """One complete evidence page: every item the seed selected, in the declared order."""

    items: tuple[EvidenceReadItem, ...]
    counts: EvidenceReadCounts
    # ``Literal[True]`` rather than a flag: this selection serves a page only when it holds the whole
    # selected set, so the completeness claim is not something a builder can set to False.
    enumeration_complete: Literal[True] = True

    @model_validator(mode="after")
    def _require_counts_to_describe_the_items(self) -> EvidenceReadPage:
        if self.counts.items_total != len(self.items):
            raise ValueError(
                "an evidence page reports the counts of the items it carries: "
                f"{self.counts.items_total} counted against {len(self.items)} item(s)"
            )
        expected = tuple(sorted(self.items, key=evidence_item_sort_key))
        if self.items != expected:
            raise ValueError(
                "an evidence page carries its items in the declared order; an order derived from "
                "insertion time or an authored label is not this selection's"
            )
        return self


class EvidenceReadRequest(KnowledgeModel):
    """One evidence-specific selection request: exactly one seed and an optional artifact root.

    ``artifact_root`` is the local checkout a recorded repository-relative artifact path is resolved
    against so a resolution *fact* can be reported. It is environment configuration, never identity:
    it is not stored, it is not part of any digest, and a read that is given no root reports
    ``not_attempted`` rather than guessing.
    """

    seed: EvidenceReadSeed
    artifact_root: str | None = Field(default=None, max_length=4096)


class EvidenceReadResult(KnowledgeModel):
    """The typed outcome of one evidence selection: a complete page, or one typed refusal."""

    state: Literal["page", "refused"]
    operation: Literal["read_evidence_scope"] = "read_evidence_scope"
    repository_id: str = Field(pattern=UUID_PATTERN)
    snapshot: KnowledgeReadSnapshot | None = None
    context_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    seed: EvidenceReadSeed | None = None
    seed_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    manifest_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    policy_version: str = Field(
        default=EVIDENCE_SELECTION_POLICY_VERSION, max_length=LABEL_MAX_LENGTH
    )
    page: EvidenceReadPage | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> EvidenceReadResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused evidence selection must carry its refusal")
            if self.page is not None:
                raise ValueError("a refused evidence selection selected no page")
            return self
        if self.refusal is not None:
            raise ValueError(
                "an evidence selection that was not refused cannot also carry a refusal"
            )
        if self.page is None:
            raise ValueError("a served evidence selection carries its page")
        return self


__all__ = [
    "EVIDENCE_ITEM_LIMIT_REASON",
    "EVIDENCE_SELECTION_ITEM_LIMIT",
    "EVIDENCE_SELECTION_POLICY_VERSION",
    "ArtifactResolution",
    "ArtifactResolutionState",
    "AssessmentReferenceState",
    "ClaimCoverage",
    "ClaimSubject",
    "EvidenceClaimItem",
    "EvidenceClaimRecord",
    "EvidenceClaimRevision",
    "EvidenceClaimRevisionItem",
    "EvidenceClaimSeed",
    "EvidenceReadCounts",
    "EvidenceReadItem",
    "EvidenceReadItemKind",
    "EvidenceReadPage",
    "EvidenceReadRequest",
    "EvidenceReadResult",
    "EvidenceReadSeed",
    "ObservationCandidateSeed",
    "VerificationObservationItem",
    "VerificationObservationRecord",
    "VerificationObservationRevision",
    "VerificationObservationRevisionItem",
    "evidence_item_id",
    "evidence_item_sort_key",
    "evidence_seed_digest",
]
