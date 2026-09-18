"""``SemanticChangeSet``: the composed record of authored work, and the views it serves.

This module owns the change set's frozen payload shape and the derived views the record group reads
back. The record is an envelope record like the members it composes: ``KS-R10@v1`` already built the
envelope, so this module adds no table for the change set itself, no second identity mechanism and
no second revision aggregate.

Five properties are load-bearing and none is incidental:

* **The declared composition is ``Doc13:97``'s list, and every part of it is recorded.** ``baseline``
  and ``candidate`` are exact :class:`SnapshotIdentity` values copied from the comparison the author
  worked against -- naming both sides is this packet's conservative addition, recorded as such,
  because a record that named one side would be unreadable as a comparison and a change set must not
  name a moving head. ``requirement_revision_refs`` are the explicit stored references of
  requirement 5. ``candidate_realization_claim_ids`` name the shipped ``realization_claim`` rows by
  exact claim identity: the change set records them and does not author a second copy, endorse them
  or widen them. The remaining two parts -- proposed effects and preservation claims, plus
  unresolved questions -- are members that declare their own ``change_set_id``, so the composition is
  complete without the change set holding a second copy of an identity one of its members already
  declares.
* **A change set is superseded by a new record with a predecessor edge.** There is no in-place
  revision, no mutable "current version" pointer and no separately typed successor record: the
  successor is another ``SemanticChangeSet`` and the edge that names the superseded one is inserted
  inside the successor's own creation batch.
* **There is no field that could hold an inference.** No generated summary, no generated narrative,
  no computed effect list, no severity, no display ordering computed from meaning. ``extra="forbid"``
  is what refuses a payload arriving with one.
* **A change set is not a decision.** It carries no acceptance, no promotion and no merge verdict;
  it is authored work awaiting a curator's assessment and the Intent Reviewer's display.
* **Every view here is derived and disposable.** A view is a pure function of rows the caller already
  read, so deleting one changes nothing and a rebuild over unchanged rows reproduces it byte for
  byte. The views are also where the record group's *unresolved* states become readable: an
  unresolved assessment reference, an unresolved requirement-revision reference and an unresolved
  preservation subject are all reported verbatim, together with the record that holds them, and none
  is suppressed, substituted, resolved or re-pointed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
    KnowledgeState,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.effect import (
    EffectLabel,
    EffectReference,
    PreservationSubject,
    RevisionReference,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The one stable value this record group declares in the envelope's typed kind vocabulary, and the
# one frozen shape it resolves to. The pair is the registry's key, like every other record group's.
SEMANTIC_CHANGE_SET_KIND = "semantic_change_set"
SEMANTIC_CHANGE_SET_SCHEMA = "semantic-change-set/v1"

# The one operation this record group serves. Recording authored work is the candidate batch's own
# operation (``change_candidate``), because the packet requires these records to be written through
# that one write path; reading the authored-effect scope is this record group's, and it is named
# once so the result and the refusal cannot disagree about which act ran.
EffectReadOperation = Literal["read_effect_scope"]


class SemanticChangeSetPayload(KnowledgeModel):
    """One authored change set, as the envelope stores it.

    ``baseline`` and ``candidate`` are the shipped :class:`SnapshotIdentity` (namespace, schema
    version, logical digest) copied from the comparison the author worked against. They are stored
    rather than resolved on read, so the record says what it was authored over and not what happens
    to be current when it is read.

    ``requirement_revision_refs`` are the opaque references of requirement 5: bounded, nonblank,
    stored verbatim, and never parsed, canonicalised or resolved against prose. A reference that
    resolves to nothing is a representable state, reported as an unresolved reference.

    ``candidate_realization_claim_ids`` are exact stored realization-claim identities. The change
    set records which shipped claims it proposes and authors nothing about them.
    """

    change_set_kind: Literal["semantic_change_set"] = "semantic_change_set"
    baseline: SnapshotIdentity
    candidate: SnapshotIdentity
    requirement_revision_refs: tuple[EffectReference, ...] = ()
    candidate_realization_claim_ids: tuple[RevisionReference, ...] = ()

    @model_validator(mode="after")
    def _require_one_namespace(self) -> SemanticChangeSetPayload:
        """Both snapshot identities name the same namespace, and it is one namespace.

        A baseline and a candidate from two namespaces are not two sides of one comparison, so this
        is refused at construction rather than stored as a change set nobody could read.
        """

        baseline, candidate = self.baseline, self.candidate
        if baseline.repository_id != candidate.repository_id:
            raise ValueError(
                "the baseline and candidate snapshots name different namespaces, so they are not "
                "two sides of one comparison"
            )
        return self

    @model_validator(mode="after")
    def _require_distinct_references(self) -> SemanticChangeSetPayload:
        """A reference declared twice is one reference; the stored form says so."""

        if len(set(self.requirement_revision_refs)) != len(self.requirement_revision_refs):
            raise ValueError(
                "a requirement-revision reference is declared twice; a repeated reference is one "
                "reference stored once, so declare it once"
            )
        if len(set(self.candidate_realization_claim_ids)) != len(
            self.candidate_realization_claim_ids
        ):
            raise ValueError(
                "a candidate realization claim is named twice; the change set addresses each "
                "proposed claim by its identity exactly once"
            )
        return self


# The pair the envelope registry resolves for the change-set kind. Declared here, beside the model.
CHANGE_SET_PAYLOAD_MODELS: Mapping[tuple[str, str], type[KnowledgeModel]] = {
    (SEMANTIC_CHANGE_SET_KIND, SEMANTIC_CHANGE_SET_SCHEMA): SemanticChangeSetPayload,
}


class UnresolvedReference(KnowledgeModel):
    """One stored reference that resolves to nothing, reported verbatim with its holder.

    ``field`` names where the reference is stored, ``reference`` is the stored text exactly as it
    was written, and ``holder_record_id`` / ``holder_revision_id`` name the claim or change set that
    holds it. There is deliberately no field for a resolved value: this record group reports the
    absence and resolves nothing, and a caller that wants the reference's meaning waits for the leaf
    that owns it.
    """

    holder_record_id: str = Field(pattern=UUID_PATTERN)
    holder_revision_id: str = Field(pattern=UUID_PATTERN)
    field: Literal[
        "assessment_refs",
        "requirement_revision_refs",
        "preservation_subject",
        "candidate_realization_claim_ids",
    ]
    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class InvariantEffectClaimView(KnowledgeModel):
    """One stored effect claim, as a derived view reads it back.

    ``lifecycle`` is the shipped lifecycle state the envelope's ``knowledge_record`` row carries --
    ``proposed`` for every row this record group writes -- and it answers one question only: was this
    stored as a proposal or as an accepted record. It is never "is this effect true".
    """

    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: KnowledgeState
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship
    content_digest: str = Field(pattern=SHA256_PATTERN)
    change_set_id: str = Field(pattern=UUID_PATTERN)
    effect: EffectLabel
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    assessment_refs: tuple[str, ...]
    unresolved_references: tuple[UnresolvedReference, ...] = ()


class PreservationClaimView(KnowledgeModel):
    """One stored preservation claim, as a derived view reads it back."""

    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: KnowledgeState
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship
    content_digest: str = Field(pattern=SHA256_PATTERN)
    change_set_id: str = Field(pattern=UUID_PATTERN)
    subject: PreservationSubject
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    unresolved_references: tuple[UnresolvedReference, ...] = ()


class UnresolvedQuestionView(KnowledgeModel):
    """One stored open question, as a derived view reads it back."""

    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: KnowledgeState
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship
    content_digest: str = Field(pattern=SHA256_PATTERN)
    change_set_id: str = Field(pattern=UUID_PATTERN)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ChangeSetMembership(KnowledgeModel):
    """One change set's composed membership, computed from the members' own declarations.

    This is the membership *fact* requirement 4.6 allows code to compute: which records belong to
    which change set. It is not an inference about meaning -- nothing here is derived from a payload's
    content, and an empty list means exactly that no member declares this change set.
    """

    effect_claim_ids: tuple[str, ...] = ()
    preservation_claim_ids: tuple[str, ...] = ()
    unresolved_question_ids: tuple[str, ...] = ()


class SemanticChangeSetView(KnowledgeModel):
    """One stored change set, with all six declared parts readable.

    ``members`` carries the three parts whose membership the members declare; the other three --
    baseline, requirement revisions and candidate realization claims -- are the change set's own
    stored fields. ``predecessor_change_set_ids`` are the stored successor-to-predecessor edges, so a
    superseded change set is addressed rather than overwritten.
    """

    record_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    lifecycle: KnowledgeState
    governing_route_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship
    content_digest: str = Field(pattern=SHA256_PATTERN)
    baseline: SnapshotIdentity
    candidate: SnapshotIdentity
    requirement_revision_refs: tuple[str, ...]
    candidate_realization_claim_ids: tuple[str, ...]
    members: ChangeSetMembership
    predecessor_change_set_ids: tuple[str, ...] = ()
    unresolved_references: tuple[UnresolvedReference, ...] = ()


class AuthoredEffectScope(KnowledgeModel):
    """Everything this record group makes readable about one namespace's authored work.

    The whole scope is derived: it is computed on read from the stored rows passed in, and nothing in
    it is stored. Deleting it changes no claim, no question and no change set, and a rebuild from the
    same rows reproduces it byte for byte, because there is no second place for a value to live.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    change_sets: tuple[SemanticChangeSetView, ...] = ()
    effect_claims: tuple[InvariantEffectClaimView, ...] = ()
    preservation_claims: tuple[PreservationClaimView, ...] = ()
    unresolved_questions: tuple[UnresolvedQuestionView, ...] = ()
    unresolved_references: tuple[UnresolvedReference, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class EffectReadResult(KnowledgeModel):
    """The typed outcome of one authored-effect read: the derived scope, or one refusal."""

    state: Literal["read", "refused"]
    operation: EffectReadOperation
    repository_id: str = Field(pattern=UUID_PATTERN)
    scope: AuthoredEffectScope | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> EffectReadResult:
        if self.state == "refused":
            if self.refusal is None or self.scope is not None:
                raise ValueError("a refused effect read carries its refusal and no scope")
            return self
        if self.scope is None or self.refusal is not None:
            raise ValueError("a served effect read carries its scope and no refusal")
        return self
