"""The relation half of the knowledge graph: memberships, realizations and their reads.

Two relations carry the graph, and each is stored exactly once:

* a **family membership** relates one exact family revision to one exact invariant revision. It
  cites revisions rather than identities, so nobody has to guess which statement a membership was
  authored against, and a newer family revision needs its own explicitly authored membership
  instead of inheriting one through a moving ``current`` pointer.
* a **realization claim** relates one exact invariant revision to one source anchor and carries
  the author's role and rationale. The role is authored vocabulary, never a machine observation:
  the closed set below exists so a reader can render a role without inventing one, and
  ``unclassified`` exists so an author who does not know the role can say so rather than guess.

The read models at the bottom return the very rows the write operations stored. There is no
second, separately maintained list of semantic facts: a forward query (from the invariant
revision) and a reverse query (from the anchor or the family revision) both select from the same
relation table, which is why the two directions can be compared by identity.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    UUID_PATTERN,
    KnowledgeModel,
)

# The authored relationship roles. The words come from the design's small explicit list; the
# leading and trailing members are ours: a role is a claim about this relationship, so a missing
# role must be representable as "not classified" rather than silently defaulted to a real one.
RealizationRole = Literal[
    "primary-authority",
    "enforcement",
    "propagation-persistence",
    "support",
    "presentation",
    "incidental",
    "unclassified",
]

UNCLASSIFIED_ROLE: RealizationRole = "unclassified"


class FamilyMemberDraft(KnowledgeModel):
    """One authored membership of an exact invariant revision in an exact family revision."""

    member_id: str = Field(pattern=UUID_PATTERN)
    family_revision_id: str = Field(pattern=UUID_PATTERN)
    invariant_revision_id: str = Field(pattern=UUID_PATTERN)
    provenance: Authorship


class FamilyMember(FamilyMemberDraft):
    """One stored membership, bound to its repository namespace with its expected-row digest."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    row_digest: str = Field(min_length=1, max_length=128)


class InvariantFamilies(KnowledgeModel):
    """The memberships that place one exact invariant revision in families, in stable order."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    invariant_revision_id: str = Field(pattern=UUID_PATTERN)
    members: tuple[FamilyMember, ...] = ()
