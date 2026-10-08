"""The identities a dataset read is bound to: the snapshot, the resolved context and its lane.

A :class:`SnapshotIdentity` is what a reader compares a dataset against (namespace, schema and
logical digest). A :class:`KnowledgeContext` is the identity an admitted runtime resolved -- which
namespace, which lane, which exact candidate inputs and which logical knowledge digest -- and
:func:`context_digest` seals it for a read's continuation. :class:`CandidateResolution` names a
review candidate's lane and trees.

The candidate-change batch that this module once defined (its command union, expectations and
receipt) was the canonical database's write boundary and was retired with it (MIK-R26).
"""

from __future__ import annotations

from typing import Literal

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

__all__ = [
    "ExactCandidateInput",
    "KnowledgeContext",
    "KnowledgeLane",
    "SnapshotIdentity",
    "context_digest",
]

# The lane a write may address. ``baseline`` is a read-only historical selection: it is a member of
# the vocabulary so a request can *name* it and be refused by name, rather than failing to parse.
KnowledgeLane = Literal["baseline", "draft-candidate", "task-candidate"]


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
