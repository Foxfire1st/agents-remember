"""The declared-policy traversal: the read-side successor of requirement 7.3.

This module owns one act -- follow declared composition edges from one family revision under one
declared policy version, bounded by that version's declared depth bound -- and it is deliberately
*not* a flag on ``KS-R07@v1``'s retrieval selection. The two are different operations on different
axes:

* **axis A** is the retrieval selection: what one snapshot read selects for a seed, its counts, its
  ordering and the frontier it advertises. Nothing here touches it, and the shipped read does not
  consult the composition table at all.
* **axis B** is the §8 registered review scope ``Doc13:287`` describes: declared edges followed under
  a versioned traversal policy. ``L16`` owns the construction of that scope; this module supplies the
  traversal semantics and the policy checks the construction consumes.

Four refusals are structural rather than incidental, and each names the policy identity, its version
and the edge or bound it reached:

* an unknown policy identity or version -- never resolved to a default or to the only version stored;
* a policy that widens a scope this build does not register -- a policy may add scope to a *named*
  scope and nothing else;
* an edge encountered under a policy that does not permit following it -- an edge whose own declared
  policy is absent, of another identity, or of another version is not followable, and a traversal
  that meets one refuses rather than stepping around it silently;
* a traversal that would exceed its declared bound -- refused, never truncated, because a truncated
  traversal reported as a scope would be a false statement about what was reached.

Widening is monotone and additive by construction: the walk only ever *adds* revisions to the
reached set, in the declared direction, and it never removes, reorders or reinterprets anything a
caller already held.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import compositions, families
from agents_remember.memory.knowledge.composition_policies import get_policy_version
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    RefusalFacts,
    composition_policy_refusal,
    composition_traversal_refusal,
    refusal,
)
from agents_remember.models.knowledge.composition import (
    REGISTERED_REVIEW_SCOPE,
    FamilyComposition,
    FamilyCompositionPolicyVersion,
    FollowDirection,
)
from agents_remember.models.knowledge.result import KnowledgeOperation

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The one operation name the traversal carries. It is its own member of the operation vocabulary
# because following declared edges under a versioned policy is not a retrieval selection.
TRAVERSAL_OPERATION: KnowledgeOperation = "follow_family_composition"


@dataclass(frozen=True)
class CompositionScope:
    """The scope one declared-policy traversal constructed, with the policy version it ran under.

    ``policy_identity`` is ``(policy_id, policy_version_id, declared_version)`` and travels with
    every result, which is requirement 3.3: a scope constructed under one policy version is never
    readable as one constructed under another. ``depth_reached`` is the deepest step the traversal
    actually took, so a caller can tell a scope that used its whole bound from one that stopped
    early, and ``widened_scope`` names the one scope the policy was permitted to widen.
    """

    seed_family_revision_id: str
    policy_identity: tuple[str, str, str]
    direction: FollowDirection
    depth_bound: int
    depth_reached: int
    widened_scope: str
    reached_family_revision_ids: tuple[str, ...]
    followed_composition_ids: tuple[str, ...]


def follow_composition_scope(
    store: OpenedKnowledgeStore,
    seed_family_revision_id: str,
    policy_id: str,
    policy_version_id: str,
) -> CompositionScope:
    """Follow declared composition edges from one family revision under one declared policy version.

    This is the ``Doc13:287`` traversal: opt-in by policy, bounded by the policy's declared depth
    bound, reported together with the policy identity and version it executed under. It is **not** a
    modification of ``KS-R07@v1``'s retrieval selection and it does not widen it: the shipped read
    does not consult the composition table at all, and nothing here changes a selected set, a count
    or an advertised frontier.

    Four refusals are structural rather than incidental, and each names the policy identity, its
    version and the edge or bound it reached:

    * an **unknown policy identity or version** -- never resolved to a default or to the only
      version stored;
    * a policy that widens a scope this build does not register -- a policy may add scope to a named
      scope and nothing else;
    * an **edge encountered under a policy that does not permit following it** -- an edge whose own
      declared policy is absent, of another identity, or of another version is not followable, and a
      traversal that meets one refuses rather than stepping around it silently;
    * a traversal that would **exceed its declared bound** -- refused, never truncated, because a
      truncated traversal reported as a scope would be a false statement about what was reached.

    Widening is monotone and additive by construction: this function only ever *adds* revisions to
    the reached set, in the policy's declared direction, and it never removes, reorders or
    reinterprets anything a caller already held.
    """

    seed = families.get_family_revision(store, seed_family_revision_id)
    if seed is None:
        raise KnowledgeRefused(
            refusal(
                "missing_expected_row",
                TRAVERSAL_OPERATION,
                "the traversal's seed is not a stored family revision in this namespace",
                facts=RefusalFacts(
                    table="family_revision",
                    record_id=seed_family_revision_id,
                    expected="a stored family revision",
                    observed="<absent>",
                ),
                next_action="Seed the traversal with a stored family revision identity.",
            )
        )
    declared = get_policy_version(store, policy_id, policy_version_id)
    if declared is None:
        raise KnowledgeRefused(
            composition_policy_refusal(
                TRAVERSAL_OPERATION,
                f"the traversal names policy {policy_id!r} version {policy_version_id!r}, which is "
                "not a declared policy version in this namespace",
                record_id=policy_version_id,
                expected="a declared policy version",
                observed=f"{policy_id}/{policy_version_id}",
            )
        )
    if declared.widened_scope != REGISTERED_REVIEW_SCOPE:
        raise KnowledgeRefused(
            composition_policy_refusal(
                TRAVERSAL_OPERATION,
                "the declared policy widens a scope this build does not register, so no traversal "
                "under it can construct a scope",
                record_id=policy_version_id,
                expected=REGISTERED_REVIEW_SCOPE,
                observed=declared.widened_scope,
            )
        )
    reached, followed, depth_reached, beyond_bound = _bounded_walk(
        store, seed_family_revision_id, declared
    )
    if beyond_bound is not None:
        raise KnowledgeRefused(
            composition_traversal_refusal(
                TRAVERSAL_OPERATION,
                "the traversal would step past the bound its declared policy names, so it is "
                "refused rather than reported as a complete scope",
                record_id=beyond_bound.composition_id,
                expected=f"at most {declared.depth_bound} steps",
                observed=f"a further step from {beyond_bound.from_family_revision_id} to "
                f"{beyond_bound.to_family_revision_id}",
            )
        )
    return CompositionScope(
        seed_family_revision_id=seed_family_revision_id,
        policy_identity=(
            declared.policy_id,
            declared.policy_version_id,
            declared.declared_version,
        ),
        direction=declared.direction,
        depth_bound=declared.depth_bound,
        depth_reached=depth_reached,
        widened_scope=declared.widened_scope,
        reached_family_revision_ids=tuple(sorted(reached)),
        followed_composition_ids=tuple(sorted(followed)),
    )


def _bounded_walk(
    store: OpenedKnowledgeStore,
    seed_family_revision_id: str,
    declared: FamilyCompositionPolicyVersion,
) -> tuple[list[str], list[str], int, FamilyComposition | None]:
    """Walk the declared edges breadth-first, and report the first edge the bound forbids.

    The walk visits each revision at most once, so a cycle among recorded edges terminates here the
    way it terminates in a traversal: the cycle itself is refused earlier, by the shared rule, but a
    reader must not be able to make this function loop even on a graph that reached the store another
    way. ``beyond_bound`` is the edge a bounded traversal could not take, or ``None`` when the walk
    finished inside its bound -- a *complete* scope is one where nothing remained to follow.
    """

    reached = [seed_family_revision_id]
    seen = {seed_family_revision_id}
    followed: list[str] = []
    frontier = [seed_family_revision_id]
    depth_reached = 0
    for depth in range(1, declared.depth_bound + 1):
        next_frontier: list[str] = []
        for revision_id in frontier:
            for edge in _followable_edges(store, revision_id, declared):
                candidate = (
                    edge.to_family_revision_id
                    if edge.from_family_revision_id == revision_id
                    else edge.from_family_revision_id
                )
                if candidate in seen:
                    continue
                seen.add(candidate)
                reached.append(candidate)
                followed.append(edge.composition_id)
                next_frontier.append(candidate)
        if not next_frontier:
            return (reached, followed, depth_reached, None)
        depth_reached = depth
        frontier = next_frontier
    return (reached, followed, depth_reached, _first_exceeding_edge(store, frontier, declared))


def _followable_edges(
    store: OpenedKnowledgeStore, revision_id: str, declared: FamilyCompositionPolicyVersion
) -> tuple[FamilyComposition, ...]:
    """Return the edges one traversal may step through from this revision, or refuse.

    An edge is followable only when its own declared policy is *this exact* policy version and its
    direction admits the step. An edge that declares no policy, another policy identity or another
    version of this policy is not followable, and the traversal refuses by naming it rather than
    stepping around it: silently ignoring an edge would report a scope that depends on which
    decisions the reader happened to honour.
    """

    followable: list[FamilyComposition] = []
    for edge in _edges_touching(store, revision_id):
        outgoing = edge.from_family_revision_id == revision_id
        if not _direction_admits(declared.direction, outgoing=outgoing):
            continue
        if (
            edge.policy_id == declared.policy_id
            and edge.policy_version_id == declared.policy_version_id
        ):
            followable.append(edge)
            continue
        raise KnowledgeRefused(
            composition_traversal_refusal(
                TRAVERSAL_OPERATION,
                "this edge is recorded but is not followable under the policy the traversal named, "
                "and an edge an executed policy does not admit is refused rather than skipped",
                record_id=edge.composition_id,
                expected=f"{declared.policy_id}/{declared.policy_version_id}",
                observed=(
                    "no declared policy"
                    if edge.policy_id is None
                    else f"{edge.policy_id}/{edge.policy_version_id}"
                ),
            )
        )
    return tuple(followable)


def _first_exceeding_edge(
    store: OpenedKnowledgeStore,
    frontier: Sequence[str],
    declared: FamilyCompositionPolicyVersion,
) -> FamilyComposition | None:
    """Return one edge a bounded traversal could not take, or ``None`` when none remains."""

    for revision_id in sorted(frontier):
        for edge in _edges_touching(store, revision_id):
            outgoing = edge.from_family_revision_id == revision_id
            if not _direction_admits(declared.direction, outgoing=outgoing):
                continue
            candidate = edge.to_family_revision_id if outgoing else edge.from_family_revision_id
            if edge.from_family_revision_id == candidate:
                continue
            return edge
    return None


def _edges_touching(
    store: OpenedKnowledgeStore, family_revision_id: str
) -> tuple[FamilyComposition, ...]:
    """Return every recorded edge with this revision at either endpoint, in declared order."""

    rows = store.connection.execute(
        f"SELECT {compositions.COMPOSITION_COLUMNS} FROM family_composition "
        "WHERE repository_id = ? AND (from_family_revision_id = ? OR to_family_revision_id = ?) "
        "ORDER BY composition_id",
        (store.repository_id, family_revision_id, family_revision_id),
    )
    return tuple(compositions.composition_of(row) for row in rows)


def _direction_admits(direction: FollowDirection, *, outgoing: bool) -> bool:
    """Whether a declared direction admits stepping through an edge in this orientation."""

    if direction == "both":
        return True
    return direction == ("forward" if outgoing else "reverse")


__all__ = [
    "TRAVERSAL_OPERATION",
    "CompositionScope",
    "follow_composition_scope",
]
