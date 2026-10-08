"""Reopen a leaf from its exact tree record, or preserve its historical source identities.

Legacy generation JSON still records source and evidence identities. Its canonical dataset sides
are unavailable; no database snapshot or current memory tree substitutes for those sides.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    recorded_leaf_contract,
    refusal,
)
from agents_remember.application.review_comparison_generation import (
    ComparisonGenerationManifest,
)
from agents_remember.application.review_comparison_reopen import (
    ComparisonReopen,
    reopen_comparison_generation,
)
from agents_remember.application.review_legacy_comparison import (
    LEGACY_UNAVAILABLE,
    legacy_comparison_resolution,
)
from agents_remember.application.review_tree_comparison import reopen_review_trees, tree_resolution
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import ReviewRefusal


@dataclass(frozen=True)
class ClosedLeafReview:
    """An existing historical JSON record and its source/evidence availability."""

    reopened: ComparisonReopen
    sentence: str

    @property
    def manifest(self) -> ComparisonGenerationManifest | None:
        return self.reopened.manifest

    @property
    def state(self) -> str:
        return "recorded-comparison" if self.manifest is not None else "recorded-source-range"

    @property
    def generation_id(self) -> str | None:
        generation = self.reopened.generation
        return None if generation is None else generation.generation_id


def resolve_committed_leaf_review(
    config: McpRuntimeConfig,
    repository_id: str,
    master: str,
    leaf_id: str,
    *,
    generation_id: str | None = None,
) -> ReviewCandidateResolution | ReviewRefusal:
    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    if contract is None:
        return refusal(
            "candidate_unresolved",
            "no readable enclosure records this leaf",
            next_action="name the leaf's existing enclosure",
            offending_input=leaf_id,
        )
    if generation_id is None:
        trees = reopen_review_trees(config.coordination_root, contract)
        if isinstance(trees, ReviewRefusal):
            return trees
        if trees is not None:
            return tree_resolution(repository_id, contract, trees)
    reopened = reopen_comparison_generation(
        config, repository_id, master, leaf_id, generation_id=generation_id
    )
    if reopened.refusal is not None and (generation_id is not None or reopened.state != "absent"):
        return reopened.refusal
    resolved = legacy_comparison_resolution(config, contract, generation_id=generation_id)
    if isinstance(resolved, ReviewRefusal) or reopened.manifest is None:
        return resolved
    return replace(
        resolved,
        closed_leaf=ClosedLeafReview(
            reopened=reopened,
            sentence="This historical comparison preserves its recorded source and evidence; its canonical knowledge datasets are legacy-unavailable.",
        ),
    )


def closed_leaf_limitations(resolved: ReviewCandidateResolution) -> tuple[str, ...]:
    closed = resolved.closed_leaf
    if closed is None:
        return ()
    return (
        "history:recorded-comparison",
        f"history:comparison-generation:{closed.generation_id}",
        *(f"history:intent:{side}:legacy-unavailable" for side in ("before", "after")),
    )


def closed_leaf_intent_detail(resolved: ReviewCandidateResolution) -> str | None:
    return None if resolved.closed_leaf is None else resolved.closed_leaf.sentence


def closed_leaf_dataset_refusal(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    if resolved.closed_leaf is None:
        return None
    return refusal(
        "candidate_dataset_absent",
        "the historical comparison's knowledge is legacy-unavailable; canonical datasets are retired",
        next_action="read its recorded source comparison; no current knowledge is substituted",
        offending_input=LEGACY_UNAVAILABLE,
    )
