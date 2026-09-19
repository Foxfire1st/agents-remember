"""The snapshot vocabulary: one candidate's local identity, receipt, publication and disposal.

A candidate is a **local working object**: a writable SQLite database plus a small immutable
receipt beside it. The database holds authored work that no other copy of it holds; the receipt
binds that working object to the admission that created it -- namespace, lane, exact code and
memory inputs, schema generation and candidate reference -- without copying task status, seat
ownership or approval.

Two splits carry the contract:

* **Working identity versus published identity.** The database's *logical* identity is read from
  the database, never asserted: the receipt deliberately carries no dataset digest, so a caller
  cannot hand-write the identity its publication will be compared against. What a publication
  installs is a *closed* representation of that identity at one frozen point, and the two are
  compared by digest rather than by bytes.
* **A receipt versus a verdict.** :class:`SnapshotPublicationResult` and :class:`CandidateResult`
  report what was done and what exists. No field here can carry a semantic judgement, an
  acceptance or an approval.

Paths appear only as local operation facts: a destination path is a deliberate local
configuration input, and no portable knowledge record is built from one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import (
    CandidateResolution,
    ExactCandidateInput,
    KnowledgeLane,
    SnapshotIdentity,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The one local layout of a candidate workspace. Both names are fixed here so the admission
# that opens the candidate for writes and the publication that reads it cannot disagree about
# which file is the working database, and so no absolute path has to be stored anywhere.
CANDIDATE_DATABASE_NAME = "knowledge-candidate.sqlite"
CANDIDATE_RECEIPT_NAME = "candidate-receipt.json"
CandidateReceiptVersion = Literal["ar-knowledge-candidate-receipt/v1"]
CANDIDATE_RECEIPT_VERSION: CandidateReceiptVersion = "ar-knowledge-candidate-receipt/v1"


def candidate_database_path(directory: Path) -> Path:
    """Return the working database path inside one admitted candidate directory."""

    return Path(directory) / CANDIDATE_DATABASE_NAME


def candidate_receipt_path(directory: Path) -> Path:
    """Return the receipt path beside one admitted candidate's working database."""

    return Path(directory) / CANDIDATE_RECEIPT_NAME


class AdmittedCandidateDestination(KnowledgeModel):
    """One candidate directory the runtime has already admitted, with its explicit inputs.

    The directory is the local object the admission names; everything inside it is derived from
    :data:`CANDIDATE_DATABASE_NAME` and :data:`CANDIDATE_RECEIPT_NAME`. The resolution supplies
    the lane and the exact code/memory inputs, which the receipt records and a later open checks
    against its own admission -- an existing destination is never re-initialized to make it fit.
    """

    directory: Path
    repository: RepositoryIdentity
    resolution: CandidateResolution

    @property
    def database_path(self) -> Path:
        """Return the working database path this candidate owns."""

        return candidate_database_path(self.directory)

    @property
    def receipt_path(self) -> Path:
        """Return the receipt path beside this candidate's working database."""

        return candidate_receipt_path(self.directory)


class CandidateBaseline(KnowledgeModel):
    """One explicitly selected knowledge database a new candidate is cloned from.

    Both fields are required on purpose. A baseline is identified by *which file* and by the
    exact logical identity the caller admitted for it, so a clone cannot silently start from
    whatever happens to be at that path now -- the identity is re-read and compared before any
    byte is copied.
    """

    database_path: Path
    expected_identity: SnapshotIdentity


class CandidateReceipt(KnowledgeModel):
    """The immutable local receipt binding one candidate database to its admission.

    It records only facts that survive being moved, copied or reopened: the namespace, the lane,
    the exact code and memory inputs, the schema generation and the candidate reference. It
    records no absolute path and no filesystem timestamp, so a candidate remains identifiable
    after its directory moves -- and no durable knowledge row can inherit a machine's layout.

    ``receipt_digest`` seals every other field, so a receipt edited in place is detectable
    without trusting the file that holds it.
    """

    receipt_version: CandidateReceiptVersion = CANDIDATE_RECEIPT_VERSION
    repository_id: str = Field(pattern=UUID_PATTERN)
    lane: KnowledgeLane
    code: ExactCandidateInput
    memory: ExactCandidateInput
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    schema_fingerprint: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    snapshot_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    candidate_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    task_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    receipt_digest: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _require_sealed_receipt(self) -> CandidateReceipt:
        recomputed = receipt_digest(self)
        if recomputed != self.receipt_digest:
            raise ValueError(
                "receipt_digest does not seal this receipt; build one with "
                "models.knowledge.snapshot.receipt_digest instead of asserting a digest"
            )
        return self


def receipt_digest(receipt: CandidateReceipt) -> str:
    """Return the digest sealing every field of ``receipt`` except the digest itself."""

    body = receipt.model_dump(mode="json", exclude={"receipt_digest"})
    return sha256_digest(body)


def build_candidate_receipt(
    *,
    resolution: CandidateResolution,
    repository_id: str,
    schema_version: str,
    schema_fingerprint: str,
) -> CandidateReceipt:
    """Build the sealed receipt for one admitted candidate from its resolution and schema.

    The receipt is derived rather than accepted: every field comes from the admitted resolution
    or from the schema the database actually carries, so a caller cannot present a receipt that
    claims a namespace, a lane or an input set the admission did not establish.

    The returned value is revalidated through the model, so the seal is checked by the same rule
    every reader applies instead of being assumed by the constructor that just computed it.
    """

    body = {
        "repository_id": repository_id,
        "lane": resolution.lane,
        "code": ExactCandidateInput(
            tree_id=resolution.code_tree_id, commit_id=resolution.code_commit_id
        ),
        "memory": ExactCandidateInput(
            tree_id=resolution.memory_tree_id, commit_id=resolution.memory_commit_id
        ),
        "schema_version": schema_version,
        "schema_fingerprint": schema_fingerprint,
        "snapshot_ref": resolution.snapshot_ref,
        "candidate_ref": resolution.candidate_ref,
        "task_ref": resolution.task_ref,
    }
    draft = CandidateReceipt.model_construct(receipt_digest="0" * 64, **body)
    sealed = draft.model_copy(update={"receipt_digest": receipt_digest(draft)})
    return CandidateReceipt.model_validate(sealed.model_dump(mode="json"))


class CandidateResult(KnowledgeModel):
    """The factual outcome of creating, cloning or reopening one candidate.

    ``created`` and ``resumed`` both carry the identity the candidate holds right now and the
    receipt that binds it; a refusal carries neither, because nothing was established about a
    candidate the operation did not admit.
    """

    state: Literal["created", "resumed", "refused"]
    identity: SnapshotIdentity | None = None
    receipt: CandidateReceipt | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_candidate_outcome(self) -> CandidateResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused candidate operation must carry its refusal")
            if self.identity is not None or self.receipt is not None:
                raise ValueError(
                    "a refused candidate operation admits no candidate and reports no identity"
                )
            return self
        if self.refusal is not None:
            raise ValueError("a candidate that was admitted cannot also carry a refusal")
        if self.identity is None or self.receipt is None:
            raise ValueError(
                "an admitted candidate reports the identity it holds and the receipt that binds it"
            )
        if self.identity.repository_id != self.receipt.repository_id:
            raise ValueError(
                "the candidate identity and its receipt must belong to one repository namespace"
            )
        return self


class PreparedKnowledgeSnapshot(KnowledgeModel):
    """One closed snapshot stage: a complete database with no journal dependency left.

    ``identity`` is the logical dataset identity the stage was frozen from, and ``file_digest``
    is the physical digest of the staged bytes. Both are carried because publication compares
    two different things: the logical identity decides whether the destination already holds
    this knowledge, and the physical digest detects a stage that was replaced between freezing
    it and installing it.
    """

    stage_path: Path
    identity: SnapshotIdentity
    file_digest: str = Field(pattern=SHA256_PATTERN)


class SnapshotDestinationRequest(KnowledgeModel):
    """Where one closed snapshot is published, and what the caller admitted is there.

    ``expected_destination`` is either the exact logical identity the caller observed at the
    destination or ``None`` for "the destination is expected to be absent". There is no third
    mode: an unstated destination is not an expectation, so a publication cannot overwrite a
    file the caller never admitted.
    """

    destination_path: Path
    expected_destination: SnapshotIdentity | None = None


class PublishSnapshotRequest(KnowledgeModel):
    """One publication request: the frozen candidate point and the admitted destination."""

    expected_candidate: SnapshotIdentity
    destination: SnapshotDestinationRequest


class SnapshotPublicationResult(KnowledgeModel):
    """The factual outcome of one publication attempt.

    ``published`` installed the staged bytes; ``no_change`` retained the destination's existing
    bytes because they already carry the same logical dataset. Both report the identity the
    destination holds, and ``previous_identity`` is what was there before (``None`` when the
    destination was absent). A refusal reports no identity at all: nothing was established about
    a destination state this operation did not produce.
    """

    state: Literal["published", "no_change", "refused"]
    identity: SnapshotIdentity | None = None
    previous_identity: SnapshotIdentity | None = None
    destination_ref: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_publication_outcome(self) -> SnapshotPublicationResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused publication must carry its refusal")
            if self.identity is not None or self.previous_identity is not None:
                raise ValueError(
                    "a refused publication reports no destination identity, because it did not "
                    "establish one"
                )
            return self
        if self.refusal is not None:
            raise ValueError("a publication that was not refused cannot also carry a refusal")
        if self.identity is None or self.destination_ref is None:
            raise ValueError(
                "a publication that was not refused names the identity and the destination it "
                "reached"
            )
        return self


class PublicationState(KnowledgeModel):
    """Whether a closed snapshot is current with respect to one live candidate.

    The caller compares ``published`` with the identity its own resolved context admitted; this
    value reports the two identities it measured and never guesses which side moved.
    """

    state: Literal["current", "candidate_snapshot_unpublished", "refused"]
    candidate: SnapshotIdentity | None = None
    published: SnapshotIdentity | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_publication_state(self) -> PublicationState:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused publication-state read must carry its refusal")
            return self
        if self.refusal is not None:
            raise ValueError("a publication state that was not refused carries no refusal")
        if self.candidate is None or self.published is None:
            raise ValueError(
                "a publication state reports both the candidate and the published identity it "
                "compared"
            )
        return self


class DiscardCandidate(KnowledgeModel):
    """An explicit authorized discard, naming the candidate identity being discarded.

    ``authorization_ref`` is carried rather than examined. The disposal verdict decides
    *permissibility* -- whether the named identity is the one the candidate holds now, and
    therefore whether discarding it would abandon work that no retained publication covers --
    while the caller owns the authority that made the discard authorized in the first place. A
    reference examined here would be this layer pretending to know an approval chain it does not
    own.
    """

    kind: Literal["discard"] = "discard"
    candidate: SnapshotIdentity
    authorization_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)


class PublishedCandidate(KnowledgeModel):
    """A disposal grounded in an exact published snapshot of the candidate's latest dataset."""

    kind: Literal["published"] = "published"
    candidate: SnapshotIdentity
    published_path: Path


# The closed disposal union. There is no third member: a disposal is either an explicit
# authorized discard or grounded in a retained publication of the exact latest dataset, so
# "it looked published" and "the candidate looked disposable" are not expressible.
CandidateDisposition = Annotated[
    DiscardCandidate | PublishedCandidate,
    Field(discriminator="kind"),
]


class CandidateDisposalResult(KnowledgeModel):
    """The verdict of one disposal authorization, with the identity it was measured against."""

    state: Literal["disposable", "refused"]
    disposition_kind: Literal["discard", "published"]
    observed: SnapshotIdentity | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_disposal_outcome(self) -> CandidateDisposalResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused disposal authorization must carry its refusal")
            return self
        if self.refusal is not None:
            raise ValueError("a disposable verdict cannot also carry a refusal")
        if self.observed is None:
            raise ValueError("a disposable verdict names the candidate identity it verified")
        return self
