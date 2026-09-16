"""Typed operation requests, refusal codes and mutation results.

Every refusal names the exact code, operation, offending record and a concrete next action.
The codes here are the vocabulary the storage layer maps onto; a storage failure that has no
code here is a defect, not a new code invented at the raise site.

The result models enforce the outcome they report: a ``created`` result must carry ``stored=True``
and no refusal, a ``refused`` result must carry its refusal, and a revision result must carry the
digest the store recomputed. A caller therefore reads one consistent value instead of re-deriving
whether an operation succeeded.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.authorship import Authorship, KnowledgeState
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
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
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.source import SourceAnchorDraft

KnowledgeOperation = Literal[
    "create_repository",
    "create_invariant",
    "create_invariant_revision",
    "create_family",
    "create_family_revision",
    "create_source_anchor",
    "remove_source_anchor",
    "create_family_member",
    "remove_family_member",
    "create_realization_claim",
    "remove_realization_claim",
    "set_invariant_label",
    "set_family_label",
    "change_candidate",
    "create_candidate",
    "clone_candidate",
    "open_candidate",
    "publish_snapshot",
    "read_published_snapshot",
    "dispose_candidate",
    # Common-base merge. Two operations rather than one because base resolution is a separate,
    # separately-refusable step: a caller that cannot even establish which dataset is the common
    # base has not attempted a merge, and the refusal has to say so in its own name.
    "resolve_merge_base",
    "merge_knowledge_datasets",
]

# The exact refusal vocabulary of the storage contract. Each member names a distinct
# observable failure, so a caller branches on the code rather than on message text.
KnowledgeRefusalCode = Literal[
    "invalid_payload",
    "unauthorized_scope",
    "unsupported_schema",
    "destination_occupied",
    "candidate_busy",
    "lock_capability_unavailable",
    "missing_expected_row",
    "stale_precondition",
    "duplicate_identity",
    "immutable_revision",
    "invalid_reference",
    "lineage_cycle",
    "relationship_constraint",
    "unknown_invariant",
    "unknown_family",
    "target_not_candidate",
    "promotion_not_supported",
    "no_change",
    # Snapshot publication. One member per observable failure point of the publication and
    # candidate-lifecycle contract, so a caller branches on the code rather than on prose.
    "selected_input_unavailable",
    "candidate_binding_changed",
    "candidate_snapshot_unpublished",
    "snapshot_incomplete",
    "destination_stale",
    "publication_failed",
    "publication_durability_unconfirmed",
    # Common-base merge. One member per observable failure point of the merge contract, so a
    # caller branches on the code rather than on prose. ``missing_expected_row``,
    # ``duplicate_identity``, ``relationship_constraint``, ``immutable_revision`` and
    # ``changeset_incomplete`` are shared with the write path where the failure is the same
    # fact; the members here are the ones that only a merge can observe.
    "common_base_unavailable",
    "common_base_ambiguous",
    "common_base_mismatch",
    "schema_mismatch",
    "missing_required_table",
    "conflicting_values",
    "duplicate_relationship",
    "delete_reference_conflict",
    "immutable_revision_changed",
    "session_unavailable",
    "changeset_incomplete",
    "changeset_postcondition_failed",
]


class KnowledgeRefusal(KnowledgeModel):
    """One refusal with its code, offending record and concrete next action."""

    code: KnowledgeRefusalCode
    operation: KnowledgeOperation
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    table: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expected: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


def require_stored_outcome(*, state: str, stored: bool, refusal: KnowledgeRefusal | None) -> None:
    """Refuse a creation result whose state, stored flag and refusal disagree."""

    if state == "created":
        if not stored or refusal is not None:
            raise ValueError("a created result carries stored=True and no refusal")
        return
    if state == "no_change":
        if stored or refusal is not None:
            raise ValueError("a no_change result stored nothing and carries no refusal")
        return
    if refusal is None:
        raise ValueError("a refused result must carry its refusal")
    if stored:
        raise ValueError("a refusal that stored a row is not a refusal")


def require_removal_outcome(*, state: str, refusal: KnowledgeRefusal | None) -> None:
    """Refuse a removal result whose state and refusal disagree."""

    if state == "removed":
        if refusal is not None:
            raise ValueError("a removed result cannot also carry a refusal")
        return
    if refusal is None:
        raise ValueError("a refused result must carry its refusal")


class RevisionDraft(KnowledgeModel):
    """One authored revision aggregate as a caller supplies it, before sealing.

    The digest is absent by design: the store recomputes and stores it, so a caller cannot
    present a payload whose seal belongs to different content.
    """

    revision_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    applicability: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    predecessors: tuple[str, ...] = ()
    state_at_origin: KnowledgeState = "proposed"
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: Authorship

    @model_validator(mode="after")
    def _refuse_self_predecessor(self) -> RevisionDraft:
        if self.revision_id in self.predecessors:
            raise ValueError("a revision must not declare itself as its own predecessor")
        return self


class RevisionRequest(KnowledgeModel):
    """One atomic request to insert a whole revision aggregate into a namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    revision: RevisionDraft


class InvariantRequest(KnowledgeModel):
    """One request to record an invariant identity in a namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    provenance: Authorship


class FamilyRequest(KnowledgeModel):
    """One request to record a family identity in a namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    provenance: Authorship


class FamilyRevisionRequest(KnowledgeModel):
    """One atomic request to insert a whole family revision aggregate into a namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    revision: FamilyRevisionDraft


class SourceAnchorRequest(KnowledgeModel):
    """One request to record a source anchor.

    The anchor is stored as authored: no resolver, parser or Git object is consulted, so a
    location whose target does not exist in the selected snapshot is still representable.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    anchor: SourceAnchorDraft
    provenance: Authorship


class FamilyMemberRequest(KnowledgeModel):
    """One request to record a membership of an exact invariant revision in a family revision."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    member: FamilyMemberDraft


class AnchorReference(KnowledgeModel):
    """A realization whose anchor is already stored in this namespace."""

    kind: Literal["existing"] = "existing"
    anchor_id: str = Field(pattern=UUID_PATTERN)


class NewAnchor(KnowledgeModel):
    """A realization that records its anchor in the same transaction as the claim itself.

    A newly authored location and the claim about it are one act of authorship, so a failure
    after the anchor was written must not leave the anchor behind as an orphan row.
    """

    kind: Literal["new"] = "new"
    anchor: SourceAnchorDraft


AnchorEndpoint = Annotated[
    AnchorReference | NewAnchor,
    Field(discriminator="kind"),
]


class RealizationClaimRequest(KnowledgeModel):
    """One request to record a realization claim on an exact invariant revision."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    claim: RealizationClaimDraft
    anchor: AnchorEndpoint
    provenance: Authorship


class RemoveSourceAnchorRequest(KnowledgeModel):
    """One explicit request to remove an unreferenced source anchor."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    anchor_id: str = Field(pattern=UUID_PATTERN)


class SetInvariantLabelRequest(KnowledgeModel):
    """One label edit naming the exact invariant row the caller read.

    The expected digest is the row's ``row_digest`` as a read returned it. A label is the only
    mutable field of the identity row, so this is the only identity edit the store exposes, and
    the expectation is what keeps it from overwriting a row that changed under the caller.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class SetFamilyLabelRequest(KnowledgeModel):
    """One label edit naming the exact family row the caller read."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class RemoveFamilyMemberRequest(KnowledgeModel):
    """One explicit request to remove a membership by its identity and expected row digest."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    member_id: str = Field(pattern=UUID_PATTERN)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class RemoveRealizationClaimRequest(KnowledgeModel):
    """One explicit request to remove a realization claim by identity and expected row digest."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    expected_row_digest: str = Field(pattern=SHA256_PATTERN)


class CreateRevisionResult(KnowledgeModel):
    """The typed outcome of one revision-creation attempt."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_invariant_revision"
    repository_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    payload_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateRevisionResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        if self.state == "created" and self.payload_digest is None:
            raise ValueError("a created result carries a digest, stored=True and no refusal")
        return self


class CreateInvariantResult(KnowledgeModel):
    """The typed outcome of one invariant-identity insert."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_invariant"
    repository_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateInvariantResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        return self


class CreateFamilyResult(KnowledgeModel):
    """The typed outcome of one family-identity insert."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_family"
    repository_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateFamilyResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        return self


class CreateFamilyRevisionResult(KnowledgeModel):
    """The typed outcome of one family-revision insert."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_family_revision"
    repository_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    payload_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateFamilyRevisionResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        if self.state == "created" and self.payload_digest is None:
            raise ValueError("a created result carries a digest, stored=True and no refusal")
        return self


class CreateSourceAnchorResult(KnowledgeModel):
    """The typed outcome of one source-anchor insert."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_source_anchor"
    repository_id: str = Field(pattern=UUID_PATTERN)
    anchor_id: str = Field(pattern=UUID_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateSourceAnchorResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        return self


class CreateFamilyMemberResult(KnowledgeModel):
    """The typed outcome of one membership insert."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_family_member"
    repository_id: str = Field(pattern=UUID_PATTERN)
    member_id: str = Field(pattern=UUID_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateFamilyMemberResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        return self


class CreateRealizationClaimResult(KnowledgeModel):
    """The typed outcome of one realization-claim insert and any anchor recorded with it."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_realization_claim"
    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    anchor_id: str = Field(pattern=UUID_PATTERN)
    stored: bool
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> CreateRealizationClaimResult:
        require_stored_outcome(state=self.state, stored=self.stored, refusal=self.refusal)
        return self


class RemoveSourceAnchorResult(KnowledgeModel):
    """The typed outcome of one explicit anchor removal."""

    state: Literal["removed", "refused"]
    operation: KnowledgeOperation = "remove_source_anchor"
    repository_id: str = Field(pattern=UUID_PATTERN)
    anchor_id: str = Field(pattern=UUID_PATTERN)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> RemoveSourceAnchorResult:
        require_removal_outcome(state=self.state, refusal=self.refusal)
        return self


class RemoveFamilyMemberResult(KnowledgeModel):
    """The typed outcome of one explicit membership removal."""

    state: Literal["removed", "refused"]
    operation: KnowledgeOperation = "remove_family_member"
    repository_id: str = Field(pattern=UUID_PATTERN)
    member_id: str = Field(pattern=UUID_PATTERN)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> RemoveFamilyMemberResult:
        require_removal_outcome(state=self.state, refusal=self.refusal)
        return self


class RemoveRealizationClaimResult(KnowledgeModel):
    """The typed outcome of one explicit realization-claim removal."""

    state: Literal["removed", "refused"]
    operation: KnowledgeOperation = "remove_realization_claim"
    repository_id: str = Field(pattern=UUID_PATTERN)
    claim_id: str = Field(pattern=UUID_PATTERN)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> RemoveRealizationClaimResult:
        require_removal_outcome(state=self.state, refusal=self.refusal)
        return self


class SetInvariantLabelResult(KnowledgeModel):
    """The typed outcome of one invariant label edit."""

    state: Literal["labeled", "refused"]
    operation: KnowledgeOperation = "set_invariant_label"
    repository_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> SetInvariantLabelResult:
        if self.state == "labeled":
            if self.refusal is not None:
                raise ValueError("a labeled result cannot also carry a refusal")
            if self.display_label is None:
                raise ValueError("a labeled result carries the label it stored")
            return self
        if self.refusal is None:
            raise ValueError("a refused result must carry its refusal")
        return self


class SetFamilyLabelResult(KnowledgeModel):
    """The typed outcome of one family label edit."""

    state: Literal["labeled", "refused"]
    operation: KnowledgeOperation = "set_family_label"
    repository_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    display_label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> SetFamilyLabelResult:
        if self.state == "labeled":
            if self.refusal is not None:
                raise ValueError("a labeled result cannot also carry a refusal")
            if self.display_label is None:
                raise ValueError("a labeled result carries the label it stored")
            return self
        if self.refusal is None:
            raise ValueError("a refused result must carry its refusal")
        return self


class RepositoryCreationResult(KnowledgeModel):
    """The typed outcome of initializing one repository namespace."""

    state: Literal["created", "no_change", "refused"]
    operation: KnowledgeOperation = "create_repository"
    repository: RepositoryIdentity
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> RepositoryCreationResult:
        if self.state != "created" and self.refusal is None:
            raise ValueError(f"a {self.state} result must carry its refusal")
        if self.state == "created" and self.refusal is not None:
            raise ValueError("a created result cannot also carry a refusal")
        return self
