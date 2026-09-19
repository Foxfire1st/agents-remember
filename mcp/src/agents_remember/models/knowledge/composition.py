"""Family composition: the authored edge between two exact family revisions, and its policy.

A guarantee family is the substrate's unit of a *joint* obligation, and this module is the
vocabulary of the one relation that says one guarantee stands in a relationship to another
(``KS-R17@v1``). Three rules shape every value here:

* **The endpoints are family revisions, by identity.** Both endpoints are exact family revision
  ids in one repository namespace, and the storage boundary enforces the kind with repository-scoped
  composite foreign keys, so an invariant revision, an anchor, a claim or a facet record is
  unrepresentable as an endpoint rather than merely refused (``Doc13:102``). There is no
  ``target_kind`` / ``target_id`` pair here for an unchecked identity to land in, and there is no
  unrestricted ``related_to`` edge: one authored meaning, one relation.
* **Composition is authored, never inferred.** Nothing in this module derives an edge from a
  display label, a folder or path ancestry, a prefix, a symbol, a shared member or a prose
  sentence. A reader that finds no recorded edge reports absent composition.
* **A policy is declared and versioned, and it defaults to off.** An edge with no declared policy is
  authored, readable and **not traversable**: absence never widens anything. A declared policy names
  a direction, a finite depth bound and the kind of scope it may widen, and a policy that names no
  bound -- or that widens "the scope" without naming which scope -- is refused as malformed rather
  than stored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)

# The closed vocabulary of scopes a declared policy may widen. It is a closed vocabulary rather
# than free text because requirement 3.2 refuses a policy that widens "the scope" without naming
# which scope: a policy is admitted only when it names one of these, and the one member this leaf
# declares is the §8 registered review scope ``Doc13:287`` describes. A later leaf that builds
# another scope adds a member here and states what it widens; nothing else can widen anything,
# because a traversal that reports an unregistered scope is refused by name.
WidenedScope = Literal["registered_review_scope"]

REGISTERED_REVIEW_SCOPE: WidenedScope = "registered_review_scope"

# The one direction vocabulary. A direction is not a hint: it decides which endpoints a traversal
# may step through, and a policy that permits no direction would permit nothing.
FollowDirection = Literal["forward", "reverse", "both"]

FOLLOW_DIRECTIONS: tuple[FollowDirection, ...] = ("forward", "reverse", "both")

# The scope-reporting token every traversal's result carries, so a result constructed under one
# policy version is never readable as one constructed under another (requirement 3.3).
PolicyIdentity = tuple[str, str, str]

# The closed set of candidate command kinds this leaf adds to the operation's union, and the closed
# set of record tables those commands write. Both are published as named constants rather than
# inlined into the union literal, so a case that pins the union's membership can union *this leaf's*
# declaration with the earlier leaves' declarations instead of restating the members: a later leaf
# appends its own named set and nothing has to be re-derived from a count.
COMPOSITION_COMMAND_KINDS: frozenset[str] = frozenset(
    {
        "add_family_composition_policy",
        "add_family_composition",
        "set_family_revision_route",
        "author_family_explanation_context",
    }
)

COMPOSITION_WRITABLE_TABLES: frozenset[str] = frozenset(
    {
        "family_composition",
        "family_composition_policy",
        "family_composition_policy_version",
        "family_revision_route",
        "family_revision_context",
        "family_revision_context_revision",
    }
)


class FamilyCompositionPolicyDraft(KnowledgeModel):
    """One declared traversal policy version exactly as a caller supplies it.

    ``policy_version_id`` is the immutable row identity a composition edge cites, and
    ``declared_version`` is the author's own version spelling. The two are separate because a
    version spelling is authored text -- ``"2026-09-18.1"``, ``"v2"`` -- while the row identity is
    what an edge stores, so re-spelling a version is a new version row rather than a repointed
    reference in every edge that cited it.
    """

    policy_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    policy_version_id: str = Field(pattern=UUID_PATTERN)
    declared_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    direction: FollowDirection
    depth_bound: int = Field(ge=1)
    widened_scope: WidenedScope
    provenance: Authorship

    @field_validator("policy_id", "declared_version")
    @classmethod
    def _require_nonblank_authored_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("a declared policy's identity and version must not be blank")
        return cleaned


class FamilyCompositionPolicyVersion(FamilyCompositionPolicyDraft):
    """One stored, sealed policy version bound to its repository namespace."""

    repository_id: str = Field(pattern=UUID_PATTERN)

    @property
    def identity(self) -> PolicyIdentity:
        """Return the ``(policy_id, policy_version_id, declared_version)`` triple an edge cites."""

        return (self.policy_id, self.policy_version_id, self.declared_version)


class FamilyCompositionDraft(KnowledgeModel):
    """One authored composition edge as a caller supplies it, before storage.

    ``policy`` is absent for an edge that is authored, readable and **not traversable** -- the
    default the requirement's whole safety property rests on. When it is present it names a policy
    *identity and version together*: one without the other is not representable here, because a
    policy that cannot say which version was executed is not a reportable traversal (``Doc13:227``).
    """

    composition_id: str = Field(pattern=UUID_PATTERN)
    from_family_revision_id: str = Field(pattern=UUID_PATTERN)
    to_family_revision_id: str = Field(pattern=UUID_PATTERN)
    policy_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    policy_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provenance: Authorship

    @model_validator(mode="after")
    def _require_a_declared_policy_to_name_both_halves(self) -> FamilyCompositionDraft:
        """Refuse an edge whose policy identity and version do not travel together.

        ``CR17-2`` recorded that the packet's schematic example DDL declared both columns nullable
        and made neither half alone a refusal; §3.2 is what implementation must satisfy, so both
        halves are checked here -- the vocabulary refuses identity-without-version and
        version-without-identity at construction, and the table's own ``CHECK`` refuses the same
        two states structurally so a row arriving through a changeset cannot hold one either.
        """

        if (self.policy_id is None) != (self.policy_version_id is None):
            raise ValueError(
                "a declared traversal policy is an identity *and* a version: one without the other "
                "is not a declared policy, and an edge that carries half of one is refused rather "
                "than stored as not traversable"
            )
        if self.from_family_revision_id == self.to_family_revision_id:
            raise ValueError(
                "a composition edge relates two family revisions; an edge from a revision to "
                "itself is not a relationship between two guarantees"
            )
        return self


class FamilyComposition(FamilyCompositionDraft):
    """One stored composition edge, with the repository namespace it belongs to."""

    repository_id: str = Field(pattern=UUID_PATTERN)

    @property
    def traversable(self) -> bool:
        """Whether a traversal may follow this edge at all.

        An edge that declares no policy is not traversable, and that is the default rather than an
        error state: the edge exists, a reader reports it, and nothing widens.
        """

        return self.policy_id is not None


class FamilyCompositionLink(KnowledgeModel):
    """One composition link as the Family projection reports it, with its direction.

    ``direction`` is relative to the family revision the projection was asked about: ``outgoing``
    when that revision is the edge's ``from`` endpoint and ``incoming`` when it is the ``to``
    endpoint. A projection reports the link; it never follows it, and this value deliberately
    carries no reachable set that could be mistaken for one.
    """

    composition_id: str = Field(pattern=UUID_PATTERN)
    direction: Literal["outgoing", "incoming"]
    from_family_revision_id: str = Field(pattern=UUID_PATTERN)
    to_family_revision_id: str = Field(pattern=UUID_PATTERN)
    policy_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    policy_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    declared_version: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    widened_scope: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    depth_bound: int | None = Field(default=None, ge=1)
    provenance: Authorship

    @property
    def traversable(self) -> bool:
        """Whether the declared policy makes this link followable."""

        return self.policy_id is not None


class FamilyExplanationContext(KnowledgeModel):
    """The authored explanatory context of one exact family revision.

    The context is prose that explains the family's place and purpose. It is separable from the
    joint guarantee, it is immutable in the same sense a revision row is -- a change is a newly
    identified context revision -- and it mints no content address, logical digest or fingerprint of
    its own: the record's identity is its ``(context_id, revision_id)`` pair, which is the identity
    the substrate already gives an authored row.
    """

    context_id: str = Field(pattern=UUID_PATTERN)
    family_id: str = Field(pattern=UUID_PATTERN)
    family_revision_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    predecessor_revision_id: str = Field(pattern=UUID_PATTERN)
    body: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    provenance: Authorship

    @field_validator("body")
    @classmethod
    def _require_nonblank_body(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("authored explanatory context must not be blank")
        return cleaned

    @property
    def is_first_revision(self) -> bool:
        """Whether this is the context's first revision.

        The first revision names *itself* as its predecessor, which is the stored representation of
        "no predecessor": the column is ``NOT NULL`` so that "first" and "the column was left out"
        cannot be confused, and every successor names the exact revision it succeeds.
        """

        return self.predecessor_revision_id == self.revision_id


class FamilyExplanationContextDraft(KnowledgeModel):
    """One authored explanatory-context revision as a caller supplies it, before storage."""

    context_id: str = Field(pattern=UUID_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    family_revision_id: str = Field(pattern=UUID_PATTERN)
    predecessor_revision_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    body: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    provenance: Authorship

    @field_validator("body")
    @classmethod
    def _require_nonblank_body(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("authored explanatory context must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _require_a_successor_to_name_a_different_predecessor(self) -> FamilyExplanationContextDraft:
        """Refuse a revision that declares itself as its own predecessor."""

        if self.predecessor_revision_id == self.revision_id:
            raise ValueError("a context revision must not declare itself as its own predecessor")
        return self


__all__ = [
    "COMPOSITION_COMMAND_KINDS",
    "COMPOSITION_WRITABLE_TABLES",
    "FOLLOW_DIRECTIONS",
    "REGISTERED_REVIEW_SCOPE",
    "FamilyComposition",
    "FamilyCompositionDraft",
    "FamilyCompositionLink",
    "FamilyCompositionPolicyDraft",
    "FamilyCompositionPolicyVersion",
    "FamilyExplanationContext",
    "FamilyExplanationContextDraft",
    "FollowDirection",
    "PolicyIdentity",
    "WidenedScope",
]
