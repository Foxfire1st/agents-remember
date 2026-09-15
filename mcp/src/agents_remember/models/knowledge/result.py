"""Typed operation requests, refusal codes and mutation results.

Every refusal names the exact code, operation, offending record and a concrete next action.
The codes here are the vocabulary the storage layer maps onto; a storage failure that has no
code here is a defect, not a new code invented at the raise site.
"""

from __future__ import annotations

from typing import Literal

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
from agents_remember.models.knowledge.repository import RepositoryIdentity

KnowledgeOperation = Literal[
    "create_repository",
    "create_invariant",
    "create_invariant_revision",
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
    "duplicate_identity",
    "immutable_revision",
    "invalid_reference",
    "lineage_cycle",
    "relationship_constraint",
    "unknown_invariant",
    "no_change",
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
        if self.state == "created":
            if not self.stored or self.payload_digest is None or self.refusal is not None:
                raise ValueError("a created result carries a digest, stored=True and no refusal")
            return self
        if self.state == "no_change":
            if self.stored or self.refusal is not None:
                raise ValueError("a no_change result stored nothing and carries no refusal")
            return self
        if self.refusal is None:
            raise ValueError("a refused result must carry its refusal")
        if self.stored:
            raise ValueError("a refusal that stored a row is not a refusal")
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
        if self.state == "created":
            if not self.stored or self.refusal is not None:
                raise ValueError("a created result carries stored=True and no refusal")
            return self
        if self.state == "no_change":
            if self.stored or self.refusal is not None:
                raise ValueError("a no_change result stored nothing and carries no refusal")
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
