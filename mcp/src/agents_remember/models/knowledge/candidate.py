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

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
    KnowledgeState,
    require_consistent_acceptance,
)
from agents_remember.models.knowledge.composition import (
    FamilyCompositionPolicyDraft,
    FamilyExplanationContextDraft,
)
from agents_remember.models.knowledge.facet import (
    AddExplanationRevision,
    AddFacet,
    AttachFacet,
    AuthorExplanation,
    DesignateExplanation,
    RemoveFacetAttachment,
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
    "AddEvidenceClaim",
    "AddExplanationRevision",
    "AddFacet",
    "AddFamily",
    "AddFamilyComposition",
    "AddFamilyCompositionPolicy",
    "AddFamilyMember",
    "AddFamilyRevision",
    "AddInvariant",
    "AddInvariantEffectClaim",
    "AddInvariantRevision",
    "AddPreservationClaim",
    "AddRealizationClaim",
    "AddSemanticChangeSet",
    "AddSourceAnchor",
    "AddUnresolvedQuestion",
    "AddVerificationObservation",
    "AnchorEndpoint",
    "AnchorReference",
    "AttachFacet",
    "AuthorExplanation",
    "AuthorFamilyExplanationContext",
    "CandidateResolution",
    "ChangeBatch",
    "ChangeCommand",
    "DesignateExplanation",
    "EffectCommand",
    "ExactCandidateInput",
    "ExpectedRecord",
    "KnowledgeContext",
    "KnowledgeLane",
    "MutableRecordTable",
    "MutationResult",
    "NewAnchor",
    "ProposedCommand",
    "RecordIdentity",
    "RemoveFacetAttachment",
    "RemoveFamilyMember",
    "RemoveRealizationClaim",
    "RemoveSourceAnchor",
    "SetFamilyLabel",
    "SetFamilyRevisionRoute",
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
# repository row, the two predecessor-edge tables and the decision-supersession edge's own parent
# column are written only as part of the aggregate that owns them, so an expectation about them
# would name a state no command could produce.
#
# The facet generation's six tables are here because facet commands write them: an expectation, a
# duplicate check and a receipt all address one of these rows by its own primary key, and every
# facet table has a single-column identity for exactly that reason. A case asserts this literal is
# the shipped seven plus the facet module's own declared list, so the two cannot drift.
MutableRecordTable = Literal[
    "invariant",
    "invariant_revision",
    "family",
    "family_revision",
    "source_anchor",
    "family_member",
    "realization_claim",
    "knowledge_record",
    "record_revision",
    "facet_attachment",
    "facet_decision_supersession",
    "explanation",
    "explanation_revision",
    # The composition generation's six tables. They are here for the same reason the facet
    # generation's six are: a composition, a declared policy version, a family revision's owning
    # route and its explanatory-context record and revisions are all written by a batch command, so
    # an expectation, a duplicate check and a receipt all address one of these rows by its own
    # primary key.
    "family_composition",
    "family_composition_policy",
    "family_composition_policy_version",
    "family_revision_route",
    "family_revision_context",
    "family_revision_context_revision",
    # The supporting-record generation's five tables, here for the same reason: a claim, its two
    # subject join tables, its claimed coverage and an observation are each written by a batch
    # command, so an expectation, a duplicate check and a receipt all address one of these rows by its
    # own primary key.
    "evidence_claim",
    "evidence_claim_invariant_subject",
    "evidence_claim_facet_subject",
    "evidence_claim_coverage",
    "verification_observation",
    # The authored-effect generation adds **no** table here, and that is the group's declared shape
    # rather than an omission: its four commands write the two envelope tables above -- one
    # ``knowledge_record`` row and its one sealed ``record_revision`` -- exactly as the facet and
    # detection commands do, and the succession edge a change set declares is written only as part of
    # the aggregate that owns it, so an expectation about it would name a state no command could
    # produce. ``models.knowledge.effect``'s own ``EFFECT_WRITABLE_TABLES`` declares that set beside
    # the commands, and ``candidate_records.EFFECT_ONLY_WRITABLE_TABLES`` is its envelope-subtracted
    # remainder -- empty, and asserted empty rather than left to a reader to notice.
    # The census generation's six tables, here for the same reason the supporting-record generation's
    # five are: an inventory row, an assessment-free claim, a migration disposition and the three
    # relations they resolve through are each written by a batch command, so an expectation, a
    # duplicate check and a receipt all address one of these rows by its own primary key.
    "census_inventory_row",
    "census_claim",
    "census_disposition",
    "census_claim_evidence",
    "census_claim_realization",
    "census_disposition_link",
]


class SnapshotIdentity(KnowledgeModel):
    """The logical identity of one knowledge dataset, independent of its SQLite page layout.

    Two databases holding the same records compare equal here whatever their files, journals or
    mtimes are, which is what makes "did this batch change anything?" a question about content.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    logical_digest: str = Field(pattern=SHA256_PATTERN)


# The two supporting-record commands are imported here rather than at the top of the module because
# one of their payloads records a :class:`SnapshotIdentity`, which is declared below: the pair is a
# genuine two-way reference between the operation's own vocabulary and the record kinds it writes,
# and an import at this position resolves it in one direction while the other side's own annotation
# resolves it in the other. Nothing else is imported late -- every other command module is a leaf
# that does not reach back into this one.
from agents_remember.models.knowledge.census import (  # noqa: E402
    CensusClaimCommand,
    CensusDispositionCommand,
    CensusInventoryRowCommand,
)
from agents_remember.models.knowledge.evidence import (  # noqa: E402
    AddEvidenceClaim,
    AddVerificationObservation,
)


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


class AddFamilyCompositionPolicy(KnowledgeModel):
    """Declare one immutable version of a traversal policy an edge may cite.

    The command carries an identity *and* a version, because a declared policy that cannot say which
    version was executed is not a reportable traversal (``Doc13:227``); a policy that names no finite
    bound, or that widens a scope the build does not register, is refused by the value's own
    construction rather than stored as a malformed policy.
    """

    kind: Literal["add_family_composition_policy"] = "add_family_composition_policy"
    policy: FamilyCompositionPolicyDraft


class AddFamilyComposition(KnowledgeModel):
    """Author one composition edge between two exact family revisions.

    ``policy_id``/``policy_version_id`` are absent for an edge that is authored, readable and not
    traversable -- the default. When present, both must name a *declared* policy version, which the
    batch checks before any row is written.
    """

    kind: Literal["add_family_composition"] = "add_family_composition"
    composition_id: str = Field(pattern=UUID_PATTERN)
    from_family_revision_id: str = Field(pattern=UUID_PATTERN)
    to_family_revision_id: str = Field(pattern=UUID_PATTERN)
    policy_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    policy_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)

    @model_validator(mode="after")
    def _require_a_declared_policy_to_name_both_halves(self) -> AddFamilyComposition:
        """Refuse an edge that carries half of a declared policy.

        ``CR17-2`` recorded that the packet's schematic example made neither half alone a refusal;
        §3.2 is what implementation satisfies, so identity-without-version and
        version-without-identity are refused at construction *and* by the table's own ``CHECK``.
        """

        if (self.policy_id is None) != (self.policy_version_id is None):
            raise ValueError(
                "a declared traversal policy is an identity *and* a version; an edge that carries "
                "one without the other is refused rather than stored as not traversable"
            )
        if self.from_family_revision_id == self.to_family_revision_id:
            raise ValueError(
                "a composition edge relates two family revisions; an edge from a revision to itself "
                "is not a relationship between two guarantees"
            )
        return self


class _AuthoredEffectCommand(KnowledgeModel):
    """What every authored-effect command shares: the payload seam, the route and the origin state.

    The payload arrives as the authored mapping and is validated **at the envelope seam**, which is
    the one place any write path decides whether a payload is admissible. It is deliberately not
    pre-validated here: a second validation in the command model would be a second decision point,
    and it would turn an out-of-vocabulary label or an inadmissible cardinality into a parse error
    instead of the typed ``invalid_payload`` refusal a caller has to branch on.

    ``state_at_origin`` and ``acceptance_ref`` exist so that "this operation stores proposed origin
    data only" is checkable in both directions: the accepted-origin consistency rule is inherited
    from the shipped vocabulary base, and a command that would store accepted origin data is refused
    by the batch with the shipped ``promotion_not_supported``. Every row this record group writes is
    the shipped ``proposed`` lifecycle state, surfaced by the read projection as ``lifecycle`` --
    never a third authored status, and never an answer to "is this claim true".
    """

    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    payload: Mapping[str, Any]
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    state_at_origin: KnowledgeState = "proposed"
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_consistent_origin(self) -> _AuthoredEffectCommand:
        require_consistent_acceptance(self.state_at_origin, self.acceptance_ref)
        return self


class AddInvariantEffectClaim(_AuthoredEffectCommand):
    """Record one authored invariant effect claim as one envelope record and its sealed revision.

    The command fixes the identity pair the seam resolves with -- the record and the revision the
    payload is written under -- so a refusal can name both. It carries no author: provenance comes
    from the admission, so no part of a submitted payload can become the claim's author.
    """

    kind: Literal["add_invariant_effect_claim"] = "add_invariant_effect_claim"


class AddPreservationClaim(_AuthoredEffectCommand):
    """Record one authored preservation claim as one envelope record and its sealed revision."""

    kind: Literal["add_preservation_claim"] = "add_preservation_claim"


class AddUnresolvedQuestion(_AuthoredEffectCommand):
    """Record one authored open question as one envelope record and its sealed revision."""

    kind: Literal["add_unresolved_question"] = "add_unresolved_question"


class AddSemanticChangeSet(_AuthoredEffectCommand):
    """Record one authored change set, with the succession edge it declares, in one batch.

    ``predecessor_change_set_ids`` is the edge requirement 4.8 requires: a revised change set is a new
    record naming its exact predecessor, and the edge is inserted inside this successor's own creation
    batch -- there is no standalone predecessor-append operation, exactly as the shipped ``store``
    rule states. The default is the empty tuple, which is the record that supersedes nothing, and it is
    an explicit declaration rather than an assumed one.
    """

    kind: Literal["add_semantic_change_set"] = "add_semantic_change_set"
    predecessor_change_set_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_distinct_predecessors(self) -> AddSemanticChangeSet:
        if len(set(self.predecessor_change_set_ids)) != len(self.predecessor_change_set_ids):
            raise ValueError(
                "a predecessor change set is named twice; a lineage is a set of exact ancestors, so "
                "declare each one once"
            )
        return self


class SetFamilyRevisionRoute(KnowledgeModel):
    """Record the canonical owning route of one exact family revision.

    Nothing here is derived: the route is named, never inferred from a member's anchor, a
    realization's path, a display label or a common path prefix. A family revision with no rows
    recording one is the explicit ungoverned state.
    """

    kind: Literal["set_family_revision_route"] = "set_family_revision_route"
    family_revision_id: str = Field(pattern=UUID_PATTERN)
    route_id: str = Field(pattern=UUID_PATTERN)


class AuthorFamilyExplanationContext(KnowledgeModel):
    """Author one revision of a family revision's explanatory context.

    The first revision of a context carries no predecessor; a later one names the exact revision it
    succeeds, so a change is a newly identified revision rather than an in-place edit. The command
    carries no field that could hold the joint guarantee, which is what makes "explanations are
    never written through the statement column" a property of the vocabulary rather than a rule the
    write path remembers.
    """

    kind: Literal["author_family_explanation_context"] = "author_family_explanation_context"
    context: FamilyExplanationContextDraft


# The closed command union, and the ONE declaration of its membership: this annotation is the
# count, so no prose here (or anywhere else in ``mcp/src``) restates it -- the docstring in
# ``memory/knowledge/candidate.py`` and ``_refuse_unreachable``'s both said "twelve" while this
# union carried thirty-one members (D-40). The generations below keep their exact discriminators
# and shapes: the shipped authored commands, the facet commands, the composition commands --
# declare-a-policy-version, author-an-edge, record-a-revision's-owning-route and author-a-context-
# revision -- the supporting-record commands (record an evidence claim, record a verification
# observation) and the authored-effect commands (record an effect claim, record a preservation
# claim, record an open question, and record a change set together with the succession edge it
# declares). There is still no free-form member and still no member that could
# promote, approve, judge or execute a statement the caller wrote, and deliberately **no** removal
# member: a composition edge is immutable in the same sense a revision row is, so a correction is a
# new edge with its own identity rather than a deletion of an earlier dataset's recorded relationship.
# Each widening added exactly the acts one record kind needs, and each of those acts has a typed shape
# of its own: an evidence claim's subject, evidence anchor and claimed coverage are typed endpoint
# values the write path must resolve, an observation's payload is a frozen shape whose execution
# result is a closed vocabulary, and an authored-effect command carries its payload as the authored
# mapping so the envelope seam stays the one place a payload is decided. Nothing here can address an
# arbitrary table or column, and no member can infer anything the caller did not write -- in
# particular there is **no** member that derives an effect label, converts an unchanged row into a
# preservation claim, or summarises a change set, so those acts are absent from the vocabulary rather
# than refused by it.
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
    | RemoveRealizationClaim
    | AddFacet
    | AttachFacet
    | RemoveFacetAttachment
    | AuthorExplanation
    | AddExplanationRevision
    | DesignateExplanation
    | AddFamilyCompositionPolicy
    | AddFamilyComposition
    | SetFamilyRevisionRoute
    | AuthorFamilyExplanationContext
    | AddEvidenceClaim
    | AddVerificationObservation
    | AddInvariantEffectClaim
    | AddPreservationClaim
    | AddUnresolvedQuestion
    | AddSemanticChangeSet
    | CensusInventoryRowCommand
    | CensusClaimCommand
    | CensusDispositionCommand,
    Field(discriminator="kind"),
]

ChangeCommand = ProposedCommand

# The four candidate commands that write the authored-effect record group. The alias exists so the
# batch's dispatch, its preconditions and the record group's own write path all name one type rather
# than four, and so a fifth command added to the union without a handler is a type error here.
EffectCommand = (
    AddInvariantEffectClaim | AddPreservationClaim | AddUnresolvedQuestion | AddSemanticChangeSet
)


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
