"""Family identity and the immutable family revision aggregate.

A family is a stable subject whose revision carries an authored **joint guarantee**: a statement
about a set of invariant revisions that hold together. The guarantee is the family's own text and
is never assembled from its members -- a member keeps its exact own obligation, and the two are
separate authored claims that a reader must not derive from one another.

Like an invariant revision, a family revision is immutable by construction. Its ``payload_digest``
seals the whole authored payload *including the sorted predecessor set*, so a stored family
revision identifies exactly one authored aggregate: changing the guarantee means authoring a
separately identified successor, and an earlier membership citing the original still resolves the
original text.
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from agents_remember.models.knowledge.authorship import (
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
    require_consistent_acceptance,
)


class FamilyDraft(KnowledgeModel):
    """A family identity proposed for a repository namespace."""

    family_id: str = Field(pattern=UUID_PATTERN)
    display_label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    label_provenance: Authorship

    @field_validator("display_label")
    @classmethod
    def _require_nonblank_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_label must not be blank")
        return cleaned


class FamilyIdentity(FamilyDraft):
    """A stored family identity bound to its repository namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    row_digest: str = Field(min_length=1, max_length=128)


class FamilyRevisionDraft(KnowledgeModel):
    """One authored family revision aggregate as a caller supplies it, before sealing.

    The digest is absent by design: the store recomputes and stores it, so a caller cannot
    present a payload whose seal belongs to different content. ``predecessors`` is the declared
    predecessor set within the same family; the guarantee text carries no member obligation.
    """

    family_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    display_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    joint_guarantee: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    predecessors: tuple[str, ...] = ()
    state_at_origin: KnowledgeState = PROPOSED_STATE
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: Authorship

    @field_validator("display_version", "joint_guarantee")
    @classmethod
    def _require_nonblank_authored_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("authored family revision text must not be blank")
        return cleaned

    @field_validator("predecessors")
    @classmethod
    def _require_unique_predecessors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("the predecessor set must not repeat a revision identity")
        return value

    @model_validator(mode="after")
    def _require_self_consistent_aggregate(self) -> FamilyRevisionDraft:
        """Refuse an aggregate whose own parts contradict each other.

        Both rules are properties of the authored value rather than of the database, so they are
        refused at construction: a revision that declares itself as its own predecessor, and an
        origin state whose acceptance reference is missing or belongs to a proposed revision.
        """

        require_consistent_acceptance(self.state_at_origin, self.acceptance_ref)
        if self.revision_id in self.predecessors:
            raise ValueError("a revision must not declare itself as its own predecessor")
        return self


class FamilyRevision(FamilyRevisionDraft):
    """One complete immutable family revision aggregate."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    payload_digest: str = Field(pattern=SHA256_PATTERN)


class StoredFamilyRevision(KnowledgeModel):
    """A family revision as read back from the store, with its decoded predecessor set."""

    revision: FamilyRevision
    predecessors_sorted: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_sorted_predecessors(self) -> StoredFamilyRevision:
        expected = tuple(sorted(self.revision.predecessors))
        if self.predecessors_sorted != expected:
            raise ValueError("predecessors_sorted must be the sorted predecessor set")
        return self
