"""The frozen baseline every census observation belongs to.

A baseline is **two exact revisions recorded by identity**: the code revision and the memory
revision the corpus was read at. ``KS-R21@v1`` §1.3 makes it frozen and named so that "mechanical
mismatch at the baseline" is a checkable statement rather than a moving target, and it makes two
artifacts examined at two different baselines *two observations* -- which is why the baseline travels
inside every census record's provenance instead of being a parameter of the run that produced it.

The code half is a Git tree: a tree names the exact content of a directory, so a baseline whose code
side is a branch name is not frozen at all. The memory half is a tree for the same reason. Neither is
recovered from ``HEAD``: the baseline is resolved and passed in, never inferred from the working
tree, because a baseline silently read off ``HEAD`` is a baseline that moves when somebody commits.

The two vocabulary constants worth stating are the reason this module exists at all rather than a
tuple of two strings appearing at each call site: a baseline is compared for equality in the census's
own accounting (two baselines are two cohorts, never one), and a comparison a reader cannot trust is
worse than no comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.base import GIT_OBJECT_PATTERN
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The one operation every baseline refusal is filed under. Reading a baseline is not one of the
# candidate write operations, and naming it separately keeps the refusal's own operation honest.
BASELINE_OPERATION: KnowledgeOperation = "read_knowledge_scope"


@dataclass(frozen=True)
class FrozenBaseline:
    """One frozen baseline: the exact code revision and the exact memory revision it names.

    ``code_tree_id`` and ``memory_tree_id`` are Git object ids of *trees*, so a baseline names
    content rather than a ref. ``code_revision`` and ``memory_revision`` are the human-facing
    spellings the report quotes (a described revision is useful to a reader; the tree is what the
    census actually compares against).
    """

    code_tree_id: str
    memory_tree_id: str
    code_revision: str
    memory_revision: str

    def __post_init__(self) -> None:
        """Refuse a baseline whose sides are not exact Git object ids.

        A baseline whose code side is a branch name is not frozen at all: the branch moves when
        somebody commits, and every mismatch reported against it becomes unreadable afterwards. The
        check lives here rather than only in the factory because a caller can construct this value
        directly, and a validation a constructor can bypass is a comment.
        """

        for name, value in (
            ("code_tree_id", self.code_tree_id),
            ("memory_tree_id", self.memory_tree_id),
        ):
            if not re.match(GIT_OBJECT_PATTERN, value):
                raise ValueError(
                    f"{name} must be an exact Git object id; a baseline whose side is a ref name is "
                    "not frozen, because the ref moves when somebody commits"
                )

    def snapshot_identity(self, repository_id: str, schema_version: str) -> SnapshotIdentity:
        """Return this baseline as the knowledge vocabulary's own frozen snapshot identity.

        The logical digest is the **memory tree id**, because the memory tree is the corpus this
        census reads: sealing the code tree there would put a code identity in a field that means
        "which knowledge dataset was read", which is the memory side. The pair is unambiguous because
        both sides are stored on the record's own provenance as well.
        """

        return SnapshotIdentity(
            repository_id=repository_id,
            schema_version=schema_version,
            logical_digest=self.memory_tree_id,
        )

    def key(self) -> tuple[str, str]:
        """Return the pair a census groups its observations by, so two baselines are two cohorts."""

        return (self.code_tree_id, self.memory_tree_id)


def require_frozen_baseline(
    code_tree_id: object,
    memory_tree_id: object,
    *,
    operation: KnowledgeOperation = BASELINE_OPERATION,
) -> FrozenBaseline | KnowledgeRefusal:
    """Return the frozen baseline two revisions name, or the refusal that replaced it.

    A refusal rather than a raised error, because "this run named a baseline that is not an exact
    revision" is an expected input defect a caller branches on -- and because an unfrozen baseline
    silently accepted is the failure mode that makes every later mismatch report unreadable.
    """

    for name, value in (("code_tree_id", code_tree_id), ("memory_tree_id", memory_tree_id)):
        if not isinstance(value, str) or not re.match(GIT_OBJECT_PATTERN, value):
            return refusal(
                "invalid_reference",
                operation,
                f"{name} is not an exact Git object id, so this run names no frozen baseline",
                next_action=(
                    "resolve the code and memory trees from the candidate's recorded revisions and "
                    "pass both object ids; never pass a branch name or a moving ref"
                ),
                facts=RefusalFacts(
                    observed=repr(value), expected="a 40- or 64-character object id"
                ),
            )
    # The revision spellings are the exact object ids because that is what this factory was given:
    # a described revision is the caller's to add, and inventing a name here would put a second,
    # unchecked spelling of the same baseline into the value.
    return FrozenBaseline(
        code_tree_id=str(code_tree_id),
        memory_tree_id=str(memory_tree_id),
        code_revision=str(code_tree_id),
        memory_revision=str(memory_tree_id),
    )
