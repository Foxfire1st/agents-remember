"""The candidate-change write boundary: resolved context, command union, batch and receipt.

One operation writes the knowledge graph, and this module is its whole vocabulary. A
:class:`ChangeBatch` names the context it was authored against, the exact record states it
expects, and a **closed** sequence of domain commands. Nothing here can express arbitrary SQL,
request an approval, or promote a proposal: the union is the operation's entire reach.

Three splits are load-bearing:

* **Resolved context versus proposed content.** A :class:`KnowledgeContext` is the identity the
  admitted runtime resolved -- which namespace, which lane, which exact candidate inputs and which
  logical knowledge digest. It is compared against the database, never trusted. Everything a caller
  *authored* travels in the commands.
* **Expected state versus assumed state.** A :class:`ExpectedRecord` says "this record is exactly
  this value, or exactly absent". A caller that omits an expectation is not thereby entitled to
  overwrite whatever is there; the operation fails closed instead (see
  :mod:`agents_remember.memory.knowledge.candidate`).
* **Receipt versus verdict.** :class:`MutationResult` reports what was stored and nothing else. It
  has no field that could carry a semantic judgement, an approval or an acceptance.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import (
    FamilyMemberDraft,
    RealizationClaimDraft,
)
from agents_remember.models.knowledge.result import (
    AnchorEndpoint,
    AnchorReference,
    KnowledgeRefusal,
    NewAnchor,
    RevisionDraft,
)
from agents_remember.models.knowledge.source import SourceAnchorDraft

__all__ = [
    "CANDIDATE_LANES",
    "AddFamily",
    "AddFamilyMember",
    "AddFamilyRevision",
    "AddInvariant",
    "AddInvariantRevision",
    "AddRealizationClaim",
    "AddSourceAnchor",
    "AnchorEndpoint",
    "AnchorReference",
    "CandidateResolution",
    "ChangeBatch",
    "ChangeCommand",
    "ExactCandidateInput",
    "ExpectedRecord",
    "KnowledgeContext",
    "KnowledgeLane",
    "MutableRecordTable",
    "MutationResult",
    "NewAnchor",
    "ProposedCommand",
    "RecordIdentity",
    "RemoveFamilyMember",
    "RemoveRealizationClaim",
    "RemoveSourceAnchor",
    "SetFamilyLabel",
    "SetInvariantLabel",
    "SnapshotIdentity",
    "context_digest",
]

# The lane a write may address. ``baseline`` is a read-only historical selection: it is a member of
# the vocabulary so a request can *name* it and be refused by name, rather than failing to parse.
KnowledgeLane = Literal["baseline", "draft-candidate", "task-candidate"]

CANDIDATE_LANES: tuple[KnowledgeLane, ...] = ("draft-candidate", "task-candidate")

_MUTABLE_TABLES: tuple[str, ...] = (
    "invariant",
    "invariant_revision",
    "family",
    "family_revision",
    "source_anchor",
    "family_member",
    "realization_claim",
)

# The tables an expectation may address. They are the tables a batch command can write; the
# repository row and the two predecessor-edge tables are written only as part of the aggregate
# that owns them, so an expectation about them would name a state no command could produce.
MutableRecordTable = Literal[
    "invariant",
    "invariant_revision",
    "family",
    "family_revision",
    "source_anchor",
    "family_member",
    "realization_claim",
]


class SnapshotIdentity(KnowledgeModel):
    """The logical identity of one knowledge dataset, independent of its SQLite page layout.

    Two databases holding the same records compare equal here whatever their files, journals or
    mtimes are, which is what makes "did this batch change anything?" a question about content.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    logical_digest: str = Field(pattern=SHA256_PATTERN)


class ExactCandidateInput(KnowledgeModel):
    """One exact Git tree (and its enclosing commit, when there is one) for a candidate side.

    A dirty candidate has no enclosing commit, so ``commit_id`` is optional rather than a
    placeholder. A reference name, a branch or ``HEAD`` is not a candidate identity and is not
    representable here.
    """

    tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    commit_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)


class KnowledgeContext(KnowledgeModel):
    """The resolved candidate context a batch was authored against.

    The operation verifies three of these fields against the database it holds open -- the
    namespace, the schema version and the logical digest -- and carries the rest as the admitted
    resolution they are. ``lane`` selects the authority context; it does not declare every stored
    revision accepted, and it never selects an accepted historical snapshot for writing.

    ``candidate_ref`` and ``task_ref`` are opaque references to admission facts owned elsewhere
    (the candidate binding and, for a task candidate, the existing contract owner). They are
    carried, not re-derived: this operation invents no candidate registry and no task authority.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    lane: KnowledgeLane
    code: ExactCandidateInput
    memory: ExactCandidateInput
    knowledge: SnapshotIdentity
    snapshot_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    context_digest: str = Field(pattern=SHA256_PATTERN)
    candidate_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    task_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_namespace(self) -> KnowledgeContext:
        """The context's namespace and its knowledge snapshot must be the same namespace."""

        if self.knowledge.repository_id != self.repository_id:
            raise ValueError(
                "the context names a repository the knowledge snapshot does not belong to"
            )
        return self

    @model_validator(mode="after")
    def _require_sealed_context(self) -> KnowledgeContext:
        recomputed = context_digest(self)
        if recomputed != self.context_digest:
            raise ValueError(
                "context_digest does not seal this context; build one with "
                "models.knowledge.candidate.context_digest instead of asserting a digest"
            )
        return self


def context_digest(context: KnowledgeContext) -> str:
    """Return the digest sealing every field of ``context`` except the digest itself.

    Resolving a context twice with the same inputs must produce the same value, so this covers the
    whole resolved identity including the candidate reference and both exact tree inputs. A
    context whose digest does not match is a tampered or stale resolution and is refused before any
    statement runs.
    """

    body = context.model_dump(mode="json", exclude={"context_digest"})
    return sha256_digest(body)


class RecordIdentity(KnowledgeModel):
    """One record the batch touched, named exactly, with what it did to it.

    Two kinds of entry reach a receipt, and both of them describe a change the batch made:

    * ``written`` -- a row the batch wrote, carrying the digest the store computed for it;
    * ``removed`` -- a row the batch deleted, carrying that row's identity and the digest it had
      when it was deleted, because the caller has to be able to name what is gone.

    A command whose requested effect was already stored changes nothing and contributes **no**
    entry: it ran no statement, the dataset did not move for it, and reporting it as a write would
    overstate the receipt.

    The digest is always the value the read operations expose as ``row_digest`` (or, for a
    revision, its sealed ``payload_digest``), so a caller carries one straight from a read instead
    of inventing a second identity scheme.
    """

    state: Literal["written", "removed"] = "written"
    table: MutableRecordTable
    record_id: str = Field(pattern=UUID_PATTERN)
    digest: str = Field(pattern=SHA256_PATTERN)


class ExpectedRecord(KnowledgeModel):
    """One record state the batch requires before it may run.

    ``present`` with a digest means "this record is stored with exactly this value"; ``absent``
    means "this identity is not stored". There is no third mode: "I did not say" is deliberately
    not an expectation, so a caller cannot leave a mutable record unnamed and have the batch
    proceed on the strength of its silence.
    """

    state: Literal["present", "absent"]
    table: MutableRecordTable
    record_id: str = Field(pattern=UUID_PATTERN)
    digest: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _require_digest_exactly_when_present(self) -> ExpectedRecord:
        if self.state == "present":
            if self.digest is None:
                raise ValueError("an expectation that a record is present must name its digest")
            return self
        if self.digest is not None:
            raise ValueError("an expectation that a record is absent must not carry a digest")
        return self


class AddInvariant(KnowledgeModel):
    """Record one invariant identity in the namespace."""

    kind: Literal["add_invariant"] = "add_invariant"
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)


class AddInvariantRevision(KnowledgeModel):
    """Record one whole invariant revision aggregate, predecessors included."""

    kind: Literal["add_invariant_revision"] = "add_invariant_revision"
    revision: RevisionDraft


class SetInvariantLabel(KnowledgeModel):
    """Change an invariant's friendly display label, naming the row it expects.

    A label is the one mutable field of an identity row, so it carries the digest of the row the
    author read. Nothing about the identity itself -- its ID or its namespace -- is editable.
    """

    kind: Literal["set_invariant_label"] = "set_invariant_label"
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class AddFamily(KnowledgeModel):
    """Record one family identity in the namespace."""

    kind: Literal["add_family"] = "add_family"
    family_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)


class AddFamilyRevision(KnowledgeModel):
    """Record one whole family revision aggregate, predecessors included."""

    kind: Literal["add_family_revision"] = "add_family_revision"
    revision: FamilyRevisionDraft


class SetFamilyLabel(KnowledgeModel):
    """Change a family's friendly display label, naming the row it expects."""

    kind: Literal["set_family_label"] = "set_family_label"
    family_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class AddSourceAnchor(KnowledgeModel):
    """Record one source anchor exactly as authored, resolving nothing."""

    kind: Literal["add_source_anchor"] = "add_source_anchor"
    anchor: SourceAnchorDraft


class RemoveSourceAnchor(KnowledgeModel):
    """Remove one anchor that no stored realization claim cites."""

    kind: Literal["remove_source_anchor"] = "remove_source_anchor"
    anchor_id: str = Field(pattern=UUID_PATTERN)


class AddFamilyMember(KnowledgeModel):
    """Relate one exact invariant revision to one exact family revision."""

    kind: Literal["add_family_member"] = "add_family_member"
    member: FamilyMemberDraft


class RemoveFamilyMember(KnowledgeModel):
    """Remove one membership by identity and expected row digest."""

    kind: Literal["remove_family_member"] = "remove_family_member"
    member_id: str = Field(pattern=UUID_PATTERN)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class AddRealizationClaim(KnowledgeModel):
    """Record one realization claim, plus its anchor when the endpoint records a new one."""

    kind: Literal["add_realization_claim"] = "add_realization_claim"
    claim: RealizationClaimDraft
    anchor: AnchorEndpoint


class RemoveRealizationClaim(KnowledgeModel):
    """Remove one realization claim by identity and expected row digest."""

    kind: Literal["remove_realization_claim"] = "remove_realization_claim"
    claim_id: str = Field(pattern=UUID_PATTERN)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


# The closed command union. Twelve authored commands, no thirteenth variant, no free-form field,
# and no member that could promote, approve or execute a statement the caller wrote.
ProposedCommand = Annotated[
    AddInvariant
    | AddInvariantRevision
    | SetInvariantLabel
    | AddFamily
    | AddFamilyRevision
    | SetFamilyLabel
    | AddSourceAnchor
    | RemoveSourceAnchor
    | AddFamilyMember
    | RemoveFamilyMember
    | AddRealizationClaim
    | RemoveRealizationClaim,
    Field(discriminator="kind"),
]

ChangeCommand = ProposedCommand


class CandidateResolution(KnowledgeModel):
    """Everything an admitted resolution contributes to a context, minus the dataset identity.

    The dataset identity is read from the candidate rather than supplied, so it is deliberately not
    a field here: a caller describes *which* candidate it has admitted and *which* exact tree inputs
    it resolved, and the application reads the identity those inputs currently hold. Bundling these
    one-place values also keeps the resolution call from becoming a long positional signature whose
    argument order a caller has to remember.
    """

    lane: KnowledgeLane
    code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    memory_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    snapshot_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    candidate_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    code_commit_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    memory_commit_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    task_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)


class ChangeBatch(KnowledgeModel):
    """One all-or-nothing candidate change: its context, its expectations and its commands.

    ``expected_records`` and ``commands`` are both ordered. The expectations are checked as a set
    against the database as it stands; the commands are applied in the order given, so a batch may
    author an anchor and cite it in a later command of the same batch.
    """

    expected: KnowledgeContext
    expected_records: tuple[ExpectedRecord, ...] = ()
    commands: tuple[ProposedCommand, ...] = ()

    @model_validator(mode="after")
    def _require_unique_expectations(self) -> ChangeBatch:
        """One record may be expected once; two expectations for it could contradict."""

        seen: set[tuple[str, str]] = set()
        for expected in self.expected_records:
            key = (expected.table, expected.record_id)
            if key in seen:
                raise ValueError(
                    f"the batch states two expectations for {expected.table} "
                    f"{expected.record_id}; state it once"
                )
            seen.add(key)
        return self


class MutationResult(KnowledgeModel):
    """The factual receipt of one candidate-change attempt.

    ``changed`` reports every record the batch touched, in command order: an entry with
    ``state="written"`` per row it wrote and ``state="removed"`` per row it deleted. A removal-only
    batch is therefore a ``changed`` result whose every entry is a removal. A command whose
    requested effect was already stored contributes no entry at all, because it ran no statement.

    ``before`` and ``after`` are the logical identities of the whole dataset; for a refusal they are
    equal, because the transaction rolled back. A refusal never reports a partially applied batch,
    and a success never reports a semantic judgement.
    """

    state: Literal["changed", "no_change", "refused"]
    before: SnapshotIdentity
    after: SnapshotIdentity
    changed: tuple[RecordIdentity, ...] = ()
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_receipt(self) -> MutationResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused batch must carry its refusal")
            if self.changed:
                raise ValueError("a refused batch changed nothing and must report no record")
            if self.after != self.before:
                raise ValueError(
                    "a refused batch leaves the dataset unchanged, so after equals before"
                )
            return self
        if self.refusal is not None:
            raise ValueError("a batch that was not refused cannot also carry a refusal")
        if self.state == "no_change" and self.changed:
            raise ValueError("a no_change batch reported touched records")
        if self.state == "changed" and not self.changed:
            raise ValueError(
                "a changed batch reports at least one touched record: a batch whose result differs "
                "from the dataset it started on either wrote a row or removed one, and a receipt "
                "that names neither cannot be reconciled with the two identities it carries"
            )
        if self.state == "changed" and self.after == self.before:
            raise ValueError("a changed batch must not report an unchanged logical identity")
        return self
