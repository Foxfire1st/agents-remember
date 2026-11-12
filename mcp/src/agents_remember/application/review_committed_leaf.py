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
    resolve_review_candidate,
    review_context_refusal,
)
from agents_remember.application.review_comparison_generation import (
    ComparisonGenerationManifest,
)
from agents_remember.application.review_comparison_reopen import (
    ComparisonReopen,
    reopen_comparison_generation,
)
from agents_remember.application.review_delivered_comparison import delivered_default
from agents_remember.application.review_legacy_comparison import (
    LEGACY_UNAVAILABLE,
    legacy_comparison_resolution,
)
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    reopen_review_trees,
    tree_resolution,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.worktrees.worktree_contract import WorktreeContract


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


def resolve_review_comparison(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str, number: int | None
) -> ReviewCandidateResolution | ReviewRefusal:
    """The review one read names: the leaf's own resolution, or its tree comparison ``number``.

    A number always reopens that record (MIK-R89 v2 rule 10), live leaf or not, as the tree view's
    numbered reads do: the read is a read of the record, never a capture of the open leaf's
    worktrees, and the legacy generation path is never read in its place. A number the leaf did not
    record is refused by name.
    """

    if number is None:
        return resolve_review_candidate(config, repository_id, master, leaf_id)
    invalid = review_context_refusal(repository_id, master, leaf_id)
    if invalid is not None:
        return invalid
    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    if contract is None:
        return _no_enclosure(leaf_id)
    trees = reopen_review_trees(config.coordination_root, contract, number)
    if isinstance(trees, ReviewTrees):
        return tree_resolution(repository_id, contract, trees)
    return trees or refusal(
        "candidate_unresolved",
        f"leaf {leaf_id} records no tree comparison {number}",
        next_action="name a comparison this leaf recorded, or open the live review",
        offending_input=str(number),
    )


def _no_enclosure(leaf_id: str) -> ReviewRefusal:
    return refusal(
        "candidate_unresolved",
        "no readable enclosure records this leaf",
        next_action="name the leaf's existing enclosure",
        offending_input=leaf_id,
    )


def resolve_committed_leaf_review(
    config: McpRuntimeConfig,
    repository_id: str,
    master: str,
    leaf_id: str,
    *,
    generation_id: str | None = None,
) -> ReviewCandidateResolution | ReviewRefusal:
    """A closed leaf's review: the pair it delivered, or a named record of it (MIK-R195 rule 2).

    Opening a landed leaf's review without an explicit historical selection opens the delivered
    comparison, not the last numbered record, which may predate curation. A named generation, and
    :func:`resolve_recorded_leaf_review`, address the leaf's recorded comparison instead. A leaf that
    records no delivery keeps the recorded default it had; a delivery the enclosure claims and this
    read cannot establish is answered by the delivered resolution's own refusal, never by that
    default.
    """

    return _committed_read(
        config,
        _Address(repository_id, master, leaf_id),
        LeafReviewRead(generation_id=generation_id),
    )


def resolve_recorded_leaf_review(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> ReviewCandidateResolution | ReviewRefusal:
    """The leaf's published comparison record: the historical read ICR-R12 addresses.

    ``recorded`` here is the caller's own statement of *which record it is reading*. It never opens
    the delivered pair in place of that record, because the two are different facts about the leaf.
    """

    return _committed_read(
        config, _Address(repository_id, master, leaf_id), LeafReviewRead(delivered=False)
    )


@dataclass(frozen=True)
class _Address:
    """The canonical task context one closed-leaf read names."""

    repository_id: str
    master: str
    leaf_id: str


@dataclass(frozen=True)
class LeafReviewRead:
    """Which comparison a closed leaf's read addresses: the delivered pair, or a recorded one."""

    delivered: bool = True
    generation_id: str | None = None


def _committed_read(
    config: McpRuntimeConfig, address: _Address, read: LeafReviewRead
) -> ReviewCandidateResolution | ReviewRefusal:
    contract = recorded_leaf_contract(
        config, address.repository_id, address.master, address.leaf_id
    )
    if contract is None:
        return _no_enclosure(address.leaf_id)
    if read.generation_id is None:
        default = _default_read(config, address.repository_id, contract, read)
        if default is not None:
            return default
    return _generation_read(config, address, contract, read.generation_id)


def _default_read(
    config: McpRuntimeConfig,
    repository_id: str,
    contract: WorktreeContract,
    read: LeafReviewRead,
) -> ReviewCandidateResolution | ReviewRefusal | None:
    """The delivered comparison, or -- for a leaf with none, or a recorded read -- its own record."""

    if read.delivered:
        landed = delivered_default(repository_id, contract)
        if landed is not None:
            return landed
    trees = reopen_review_trees(config.coordination_root, contract)
    if isinstance(trees, ReviewRefusal) or trees is None:
        return trees
    return tree_resolution(repository_id, contract, trees)


def _generation_read(
    config: McpRuntimeConfig,
    address: _Address,
    contract: WorktreeContract,
    generation_id: str | None,
) -> ReviewCandidateResolution | ReviewRefusal:
    """The recorded generation's own comparison, or the source range the enclosure recorded."""

    reopened = reopen_comparison_generation(
        config, address.repository_id, address.master, address.leaf_id, generation_id=generation_id
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
