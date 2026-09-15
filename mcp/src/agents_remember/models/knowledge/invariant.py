"""Invariant identity and the immutable invariant revision aggregate.

An invariant identity is the stable subject; a revision is one authored statement of it. The
two are separate identifiers, and a friendly display version belongs to the revision's label
rather than to its identity: two successors of one revision may both display ``v2``, and that
is a supported state rather than an identity conflict.

A revision is immutable by construction. Its ``payload_digest`` seals the whole semantic
payload *including the sorted predecessor set*, so a stored revision identifies exactly one
authored aggregate and no later write can be mistaken for it.
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from agents_remember.models.knowledge.authorship import (
    ACCEPTED_STATE,
    PROPOSED_STATE,
    Authorship,
    KnowledgeState,
)
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)


class InvariantDraft(KnowledgeModel):
    """An invariant identity proposed for a repository namespace."""

    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    label_provenance: Authorship

    @field_validator("display_label")
    @classmethod
    def _require_nonblank_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_label must not be blank")
        return cleaned


class InvariantIdentity(InvariantDraft):
    """A stored invariant identity bound to its repository namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    row_digest: str = Field(min_length=1, max_length=128)


class InvariantRevision(KnowledgeModel):
    """One complete immutable revision aggregate.

    ``predecessors`` is the declared predecessor set within the same invariant. It is stored
    sorted and unique, because it is part of the sealed payload: an unsorted or
    duplicate-bearing tuple would make the same authored set digest differently depending on
    how the caller happened to write it.
    """

    repository_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    display_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    applicability: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    state_at_origin: KnowledgeState = PROPOSED_STATE
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: Authorship
    predecessors: tuple[str, ...] = ()
    payload_digest: str = Field(pattern=SHA256_PATTERN)

    @field_validator("display_version", "statement", "applicability")
    @classmethod
    def _require_nonblank_authored_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("authored revision text must not be blank")
        return cleaned

    @field_validator("conditions", "exclusions")
    @classmethod
    def _require_nonblank_clauses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep authored clause order and refuse an empty clause.

        Order is preserved because it is the author's, and the digest covers it. An empty
        array is a recorded "none", never a missing value.
        """

        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("condition and exclusion clauses must not be blank")
        return cleaned

    @field_validator("predecessors")
    @classmethod
    def _require_unique_predecessors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("the predecessor set must not repeat a revision identity")
        return value

    @model_validator(mode="after")
    def _require_self_consistent_acceptance(self) -> InvariantRevision:
        if self.state_at_origin == ACCEPTED_STATE:
            if not (self.acceptance_ref or "").strip():
                raise ValueError("accepted origin data requires a nonempty acceptance_ref")
        elif self.acceptance_ref is not None:
            raise ValueError("a proposed revision must not carry an acceptance_ref")
        if self.revision_id in self.predecessors:
            raise ValueError("a revision must not declare itself as its own predecessor")
        return self


class StoredInvariantRevision(KnowledgeModel):
    """A revision as read back from the store, with its decoded predecessor set."""

    revision: InvariantRevision
    predecessors_sorted: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_sorted_predecessors(self) -> StoredInvariantRevision:
        expected = tuple(sorted(self.revision.predecessors))
        if self.predecessors_sorted != expected:
            raise ValueError("predecessors_sorted must be the sorted predecessor set")
        return self
