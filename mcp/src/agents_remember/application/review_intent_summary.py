"""The changed-intent summary of one review comparison: how many statements each side alone holds.

The task entry offers the reviewer as one compact ``Intent review +N -N`` control, and its two
numbers must describe the comparison that control opens -- not the change set's line counts and not
the catalogue's identity totals, which enumerate subjects without comparing them. This module is the
small read that answers it, over the same resolved pair the reviewer opens:

* **The head rule is the revision comparison's own.** Each side's current statement of one identity
  is its head revision under :func:`~agents_remember.application.review_revision_comparison.revision_heads`
  -- a retained revision no authored successor in that snapshot replaces. That is the rule the
  reviewer uses to choose which before and after revisions it renders (ICR-R07), so the entry cannot
  count a revision as current that the reviewer would show as superseded.
* **Plus counts heads only the after side holds, minus heads only the before side holds**, for
  invariants and for family joint guarantees. A revised statement is one after-only head and one
  before-only head, so it counts once in each. Identities are counted once whatever families they
  belong to, so a member shared by two families is not counted twice.
* **Every successor revision counts, except one that only carries a relationship change.** A new
  head counts once on each side whether its text, its origin state or acceptance reference changed,
  or nothing but its version (a record-only successor is still a revision). The two exclusions the
  count semantics name are carried as their own typed counts instead: a successor whose text and
  record status equal the superseded head's while an invariant's realizations, or a family's members
  (by canonical invariant identity), differ -- and a head that did not change at all while its
  realizations did.
* **An identity with no single head is not guessed.** Several heads on a side (divergent successors)
  or a retained population with no head at all is ``unresolved``, and the answer is ``partial``.
* **A pair that cannot be read is unavailable, never zero.** The resolution's and the pair's own
  refusals (:mod:`agents_remember.application.review_pair_preflight`, shared with the catalogue) are
  returned as they are, with no counts.

The read is a handful of indexed statements per identity on each snapshot, opened read-only; it runs
no comparison of subjects and loads no subject content beyond each head's own fields.

A tree comparison's summary also carries the unexplained-changes lane's file-level count
(:func:`~agents_remember.application.review_unexplained_lane.lane_summary`), computed over the same
resolved trees in the same request: the entry asks for it exactly when it asks for the intent counts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
    review_namespace,
    unreadable_candidate_refusal,
)
from agents_remember.application.review_pair_preflight import pair_preflight_refusal
from agents_remember.application.review_revision_comparison import revision_heads
from agents_remember.application.review_unexplained_lane import lane_summary
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.read_queries import (
    fetch_family_revisions,
    fetch_identity_rows,
    fetch_invariant_revisions,
    fetch_memberships_of_families_full,
    fetch_predecessor_edges,
    fetch_realizations_for_invariants,
    fetch_revision_ids,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_intent_summary import (
    IntentHeadChanges,
    ReviewIntentCounts,
    ReviewIntentSummaryResult,
)

__all__ = ["intent_summary_of", "read_review_intent_summary"]

# The invariant revision fields compared between two heads: the authored statement and its record
# status. Display version and provenance differ on every successor, so they are not compared; a
# successor that differs in nothing else is still counted (see ``_classify``).
_STATEMENT_FIELDS: tuple[str, ...] = (
    "statement",
    "applicability",
    "conditions",
    "exclusions",
    "state_at_origin",
    "acceptance_ref",
)
# The same for a family revision: its joint guarantee and its record status.
_GUARANTEE_FIELDS: tuple[str, ...] = ("joint_guarantee", "state_at_origin", "acceptance_ref")


@dataclass(frozen=True)
class _KindSide:
    """One snapshot's view of one statement kind: heads per identity and what each head says.

    ``populated`` names the identities that retain at least one revision, so an identity whose
    population has no head (a successor cycle) is told apart from one that was never authored.
    ``content`` is each head's compared text and ``attached`` what hangs off it without being its
    text: an invariant head's realization claims, or a family head's member invariant identities.
    """

    heads: Mapping[str, tuple[str, ...]]
    populated: frozenset[str]
    content: Mapping[str, tuple[Any, ...]]
    attached: Mapping[str, frozenset[Any]]


@dataclass
class _Tally:
    """The running counts of one statement kind across both sides."""

    after_only: int = 0
    before_only: int = 0
    attached_only: int = 0
    unresolved: int = 0


@dataclass(frozen=True)
class _SideIntent:
    """One snapshot's two statement kinds, read from one read-only connection."""

    invariants: _KindSide
    families: _KindSide


def read_review_intent_summary(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> ReviewIntentSummaryResult:
    """Resolve the comparison the task context names and summarize its changed intent.

    The resolution is the reviewer's own (canonical task context; a closed leaf resolves to its
    recorded comparison), so the counts beside the entry describe the comparison the entry opens.
    """

    resolved = resolve_review_candidate(config, repository_id, master, leaf_id)
    if isinstance(resolved, ReviewRefusal):
        return _unavailable(repository_id, master, leaf_id, resolved)
    summary = intent_summary_of(resolved, master)
    if resolved.trees is None:
        return summary
    return summary.model_copy(update={"attribution": lane_summary(resolved.trees)})


def intent_summary_of(
    resolved: ReviewCandidateResolution, master: str
) -> ReviewIntentSummaryResult:
    """Summarize one already-resolved pair, or return the refusal the pair earns as a whole."""

    refused = pair_preflight_refusal(resolved)
    if refused is not None:
        return _unavailable(resolved.repository_id, master, resolved.leaf_id, refused)
    namespace = review_namespace(resolved.repository_id, resolved.candidate_database)
    try:
        before = _read_side(resolved.baseline_database, namespace)
        after = _read_side(resolved.candidate_database, namespace)
    except (KnowledgeStorageError, apsw.Error) as error:
        # The preflight read the same bytes; a half that moves between the two reads is still the
        # typed refusal rather than a traceback.
        return _unavailable(
            resolved.repository_id,
            master,
            resolved.leaf_id,
            unreadable_candidate_refusal(resolved, str(error)),
        )
    counts = _counts(
        _tally(before.invariants, after.invariants), _tally(before.families, after.families)
    )
    return ReviewIntentSummaryResult(
        state="partial" if counts.unresolved else "counted",
        repository_id=resolved.repository_id,
        master=master,
        leaf_id=resolved.leaf_id,
        counts=counts,
    )


def _unavailable(
    repository_id: str, master: str, leaf_id: str, refusal: ReviewRefusal
) -> ReviewIntentSummaryResult:
    return ReviewIntentSummaryResult(
        state="unavailable",
        repository_id=repository_id,
        master=master,
        leaf_id=leaf_id,
        refusal=refusal,
    )


def _counts(invariants: _Tally, families: _Tally) -> ReviewIntentCounts:
    return ReviewIntentCounts(
        added=invariants.after_only + families.after_only,
        removed=invariants.before_only + families.before_only,
        invariants=IntentHeadChanges(
            after_only=invariants.after_only, before_only=invariants.before_only
        ),
        guarantees=IntentHeadChanges(
            after_only=families.after_only, before_only=families.before_only
        ),
        realization_only=invariants.attached_only,
        membership_only=families.attached_only,
        unresolved=invariants.unresolved + families.unresolved,
    )


# --- one snapshot ---------------------------------------------------------------------------------


def _read_side(database: Path, namespace: str) -> _SideIntent:
    """Read one snapshot's heads, their text and what hangs off them, then close it."""

    connection = open_read_only_database(database)
    try:
        edges = fetch_predecessor_edges(connection, namespace)
        invariant_populations = _populations(
            connection, namespace, ("invariant", "invariant_revision", "invariant_id")
        )
        family_populations = _populations(
            connection, namespace, ("family", "family_revision", "family_id")
        )
        invariant_of = {
            revision: identity
            for identity, population in invariant_populations.items()
            for revision in population
        }
        return _SideIntent(
            invariants=_invariant_side(connection, namespace, invariant_populations, edges),
            families=_family_side(connection, namespace, (family_populations, edges), invariant_of),
        )
    finally:
        connection.close()


def _populations(
    connection: apsw.Connection, namespace: str, tables: tuple[str, str, str]
) -> dict[str, tuple[str, ...]]:
    """Every identity of one kind with its retained revision ids, through the shipped readers."""

    identity_table, revision_table, identity_column = tables
    identities = fetch_identity_rows(
        connection, namespace, identity_table, identity_column, "display_label"
    )
    return {
        identity: fetch_revision_ids(
            connection, namespace, revision_table, identity_column, identity
        )
        for identity in identities
    }


def _heads(
    populations: Mapping[str, tuple[str, ...]], edges: Sequence[tuple[str, str]]
) -> dict[str, tuple[str, ...]]:
    return {
        identity: revision_heads(population, edges) for identity, population in populations.items()
    }


def _invariant_side(
    connection: apsw.Connection,
    namespace: str,
    populations: Mapping[str, tuple[str, ...]],
    edges: Sequence[tuple[str, str]],
) -> _KindSide:
    heads = _heads(populations, edges)
    head_ids = _all_heads(heads)
    content = {
        str(row["revision_id"]): tuple(_canonical(row[name]) for name in _STATEMENT_FIELDS)
        for row in fetch_invariant_revisions(connection, namespace, head_ids)
    }
    attached: dict[str, set[tuple[str, ...]]] = {}
    for claim in fetch_realizations_for_invariants(connection, namespace, head_ids):
        attached.setdefault(str(claim["invariant_revision_id"]), set()).add(_claim_key(claim))
    return _KindSide(
        heads=heads,
        populated=frozenset(identity for identity, revisions in populations.items() if revisions),
        content=content,
        attached={revision: frozenset(keys) for revision, keys in attached.items()},
    )


def _family_side(
    connection: apsw.Connection,
    namespace: str,
    lineage: tuple[Mapping[str, tuple[str, ...]], Sequence[tuple[str, str]]],
    invariant_of: Mapping[str, str],
) -> _KindSide:
    populations, edges = lineage
    heads = _heads(populations, edges)
    head_ids = _all_heads(heads)
    content = {
        str(row["revision_id"]): tuple(_canonical(row[name]) for name in _GUARANTEE_FIELDS)
        for row in fetch_family_revisions(connection, namespace, head_ids)
    }
    members: dict[str, set[str]] = {}
    for (
        _member_id,
        family_revision,
        invariant_revision,
        _provenance,
    ) in fetch_memberships_of_families_full(connection, namespace, head_ids):
        # Membership is compared by canonical invariant identity: a member whose invariant moved to
        # a successor revision is the same membership, and the revision change is the invariant's.
        members.setdefault(family_revision, set()).add(
            invariant_of.get(invariant_revision, invariant_revision)
        )
    return _KindSide(
        heads=heads,
        populated=frozenset(identity for identity, revisions in populations.items() if revisions),
        content=content,
        attached={revision: frozenset(ids) for revision, ids in members.items()},
    )


def _all_heads(heads: Mapping[str, tuple[str, ...]]) -> list[str]:
    return sorted({revision for revisions in heads.values() for revision in revisions})


def _canonical(value: object) -> str:
    """One stored cell as comparable text; JSON cells compare by their canonical spelling."""

    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _claim_key(claim: Mapping[str, Any]) -> tuple[str, ...]:
    """What one realization claim asserts, independent of the claim's own record identity."""

    return (
        str(claim["path"]),
        _canonical(claim["locator"]),
        _canonical(claim["source_identity"]),
        str(claim["role"]),
        str(claim["rationale"]),
    )


# --- both snapshots -------------------------------------------------------------------------------


def _tally(before: _KindSide, after: _KindSide) -> _Tally:
    tally = _Tally()
    for identity in sorted(set(before.heads) | set(after.heads)):
        _classify(identity, before, after, tally)
    return tally


def _classify(identity: str, before: _KindSide, after: _KindSide, tally: _Tally) -> None:
    """Add one identity's change to the tally: plus/minus, attached-only, unresolved, or nothing."""

    before_heads = before.heads.get(identity, ())
    after_heads = after.heads.get(identity, ())
    if _no_single_head(identity, before, before_heads) or _no_single_head(
        identity, after, after_heads
    ):
        if set(before_heads) != set(after_heads):
            tally.unresolved += 1
        return
    if not before_heads and not after_heads:
        return
    if not before_heads:
        tally.after_only += 1
        return
    if not after_heads:
        tally.before_only += 1
        return
    before_head, after_head = before_heads[0], after_heads[0]
    same_content = before.content.get(before_head) == after.content.get(after_head)
    same_attached = before.attached.get(before_head, frozenset()) == after.attached.get(
        after_head, frozenset()
    )
    if before_head == after_head or (same_content and not same_attached):
        # The head is unchanged, or a successor only carries a realization/membership change.
        tally.attached_only += 0 if same_attached else 1
        return
    # A successor revision: new text or record status, or a record-only successor.
    tally.after_only += 1
    tally.before_only += 1


def _no_single_head(identity: str, side: _KindSide, heads: tuple[str, ...]) -> bool:
    """Several heads, or a retained population with none: the side's statement is not one head."""

    return len(heads) > 1 or (not heads and identity in side.populated)
