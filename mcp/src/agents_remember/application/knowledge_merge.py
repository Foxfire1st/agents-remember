"""The composition seam between admitted authority and the guarded common-base merge.

This is the third application module the storage design anticipated, and it exists for the same
reason as the snapshot seam: the merge is a different composed operation from a single candidate
write, and keeping it apart leaves each entry point readable as one intent.

Nothing here decides authority and nothing here holds durable state. It resolves a base claim,
merges, and returns the typed result unchanged. Storage ranks below application, so a lower owner --
the worktree or lifecycle package that would call a merge -- receives
:mod:`agents_remember.models.knowledge` values and never an import of this module or of the store.

The adapter this exposes is deliberately *callable rather than wired*: no Git merge driver is
installed, no attribute is configured and no commit is created anywhere on this path. A later,
separately reviewed change is what turns this boundary into a driver, and this module is the exact
seam such a change would call.
"""

from __future__ import annotations

from agents_remember.memory.knowledge.merge import merge_knowledge_datasets
from agents_remember.memory.knowledge.merge_base import resolve_merge_base
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeBaseResolution,
    MergeOutcome,
    MergeRequest,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "merge_resolved_knowledge_datasets",
    "resolve_knowledge_merge_base",
]


def resolve_knowledge_merge_base(
    request: MergeBaseRequest,
) -> MergeBaseResolution | KnowledgeRefusal:
    """Prove the three input identities, their structure and the Git base claim.

    A resolution is returned as the proven value; a failure is returned as the typed refusal, so a
    caller that is composing a tool response branches on one code instead of catching an exception.
    """

    outcome = resolve_merge_base(request)
    if outcome.resolution is not None:
        return outcome.resolution
    if outcome.refusal is None:
        raise KnowledgeMergeSeamDefect(
            "base resolution returned neither a resolution nor a refusal"
        )
    return outcome.refusal


def merge_resolved_knowledge_datasets(request: MergeRequest) -> MergeOutcome:
    """Merge two sides against a proven common base, or return the typed refusal.

    The whole structural outcome is returned, including the coverage of both deltas and the
    publication state when a destination was named. The result carries no compatibility verdict: a
    ``structurally_merged`` outcome is a statement about the candidate's structure and nothing about
    whether the merged knowledge is correct.
    """

    return merge_knowledge_datasets(request)


class KnowledgeMergeSeamDefect(RuntimeError):
    """A seam state the layer below makes unreachable: a refusal and a value both absent."""
