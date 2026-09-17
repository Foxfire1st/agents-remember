"""The recorded-scope selection: the packet's finite selection policy, executed once over one snapshot.

This module owns one function that answers *what is the selected set* for one seed, and one that
answers *which page of it fits*. Selection and paging are deliberately separate, because they are
separate claims: the selection is a fact about the recorded graph and does not depend on a budget,
while the page is a presentation of it that must never be readable as a different scope.

The policy is the requirement's own table, and the two subtleties it states are the ones this
module has to get exactly right:

* **A membership is added for the families the seed reached, not for the families it discovered.**
  The directly containing families of the seed's own invariant revisions are frozen *before* their
  members join the invariant set, so a member's membership in some further family is an advertised
  expansion rather than a reason to keep walking. That is what makes the traversal finite, and it
  is why ``frontier`` is computed against the frozen family set rather than against the invariants.
* **An identity seed is not a revision choice.** Every retained revision of the named identity is
  selected, grouped under its identity, and the response carries no ordering, version comparison or
  insertion time that could be read as "this one is current". The stream's order is by stable
  identifier, so re-authoring the same graph in another insertion order produces the same pages.

Nothing here writes. Every statement is a ``SELECT`` on a connection the caller opened read-only,
and the one bounded integer this module adds to any statement counts rows; a refusal therefore
leaves the file byte-identical because there is no statement that could change it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import apsw

from agents_remember.kernel.canonical_json import canonical_json_bytes, sha256_digest
from agents_remember.memory.knowledge.read_queries import (
    fetch_family_ids_for_revisions,
    fetch_family_revisions,
    fetch_identity_rows,
    fetch_invariant_revisions,
    fetch_memberships_of_families,
    fetch_memberships_of_families_full,
    fetch_memberships_of_invariants,
    fetch_realizations_at_path,
    fetch_realizations_for_invariants,
    fetch_revision_ids,
)
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import (
    SELECTION_ITEM_LIMIT,
    AdvertisedExpansion,
    AnchorResolution,
    DirectlyContainingFamily,
    FamilyIdentitySeed,
    FamilyRevisionSeed,
    InvariantIdentitySeed,
    InvariantRevisionSeed,
    KnowledgeReadContext,
    KnowledgeReadCounts,
    KnowledgeReadPage,
    KnowledgeReadSeed,
    PathSeed,
    ReadItem,
    ReadRevisionGroup,
    ReadStage,
    SelectionReason,
    cursor_for,
)

# The source-resolution seam: one decoded realization row in, one anchor observation out. It is a
# caller-supplied callable rather than a model, which is why it is a ``Callable`` and not ``Any``:
# the selection layer decides *which* anchors a seed exposes and never how a path is resolved.
AnchorResolver = Callable[[dict[str, Any]], "AnchorResolution | None"]

# The declared order of the item stream: invariant revisions, family revisions, memberships,
# realization claims, then the advertised frontier links. Nothing about a row's insertion order,
# an authored display label or a paging budget can change it.
_KIND_ORDER: Mapping[str, int] = {
    "invariant_revision": 1,
    "family_revision": 2,
    "family_membership": 3,
    "realization_claim": 4,
    "advertised_family": 5,
}

# The item kinds whose identifying text is a stable identifier rather than a relation key. Their
# second sort component is the exact revision the item names.
_IDENTITY_FIRST_KEY: Mapping[str, str] = {
    "invariant_revision": "invariant_id",
    "family_revision": "family_id",
    "family_membership": "family_revision_id",
    "realization_claim": "invariant_revision_id",
    "advertised_family": "family_revision_id",
}

_IDENTITY_SECOND_KEY: Mapping[str, str] = {
    "invariant_revision": "revision_id",
    "family_revision": "revision_id",
    "family_membership": "member_id",
    "realization_claim": "claim_id",
    "advertised_family": "member_id",
}

# Named so a refusal can say which bound the selection reached.
ITEM_LIMIT_REASON = (
    f"the selected set exceeded the declared execution bound of {SELECTION_ITEM_LIMIT} items"
)


class SelectionIncomplete(ValueError):
    """The selected set reached the declared execution bound before it was enumerated.

    It is an exception rather than a returned refusal because the caller is the read operation,
    which knows the operation name and the context this refusal belongs to. The detail names the
    exact bound so a caller is never told "too large" without being told what was too large.
    """

    def __init__(self, item_count: int, bound: int) -> None:
        super().__init__(
            f"{ITEM_LIMIT_REASON}; the bound is {bound} and the count reached {item_count}"
        )
        self.item_count = item_count
        self.bound = bound


@dataclass(frozen=True)
class SelectedScope:
    """One complete selected set: its ordered items, its counts and its selection reasons.

    ``items`` is the whole declared set, ordered deterministically and never filtered by a page
    budget. A page is a slice of this, so "the union of all pages equals the declared set" is a
    property of construction rather than something a caller has to re-derive.
    """

    items: tuple[ReadItem, ...]
    counts: KnowledgeReadCounts
    directly_containing_families: tuple[DirectlyContainingFamily, ...]
    revision_groups: tuple[ReadRevisionGroup, ...]
    selected_invariant_revision_ids: frozenset[str]
    selected_family_revision_ids: frozenset[str]
    advertised: tuple[AdvertisedExpansion, ...]
    manifest_digest: str

    def item_ids(self) -> tuple[str, ...]:
        """Return the exact identity of every primary item, for the union-versus-set comparison."""

        return tuple(item.item_id for item in self.items)


@dataclass(frozen=True)
class SelectionQuery:
    """One namespace, one seed and the source-resolution seam, as one value.

    The three travel together because a selection is meaningless without its namespace, and the
    anchor resolver is the only part of source resolution this layer owns: it observes a recorded
    anchor against the tree the caller named, and everything else about the source stays outside.

    ``seed_override`` is the one field a two-snapshot comparison adds, and it exists so that a
    comparison does **not** need a second selection rule. A comparison runs this same policy twice,
    once per snapshot, and the two sides may name *different exact revisions* of one identity; the
    override lets a side address its own revision without the policy learning a diff-shaped branch.
    It replaces ``seed`` for this one selection and is otherwise invisible: every step below --
    the seed's own revisions, the frozen directly-containing families, the closed member union, the
    advertised frontier -- is computed from the effective seed exactly as it is for a plain read.
    """

    repository_id: str
    seed: KnowledgeReadSeed
    resolve_anchor: AnchorResolver | None = None
    seed_override: KnowledgeReadSeed | None = None

    @property
    def effective_seed(self) -> KnowledgeReadSeed:
        """Return the seed this selection actually uses."""

        return self.seed if self.seed_override is None else self.seed_override


@dataclass(frozen=True)
class SelectionSets:
    """The three frozen sets one selection is built from, kept together as one value.

    ``seed_revisions`` is what the seed itself named, ``families`` is the family set frozen before
    membership expansion, and ``selected_invariants`` is the closed union. Keeping them distinct is
    the policy: the stopping rule is a statement about which of these three a lookup may use.
    """

    seed: KnowledgeReadSeed
    seed_revisions: Mapping[str, str]
    families: Mapping[str, str]
    selected_invariants: frozenset[str]


def select_recorded_scope(connection: apsw.Connection, query: SelectionQuery) -> SelectedScope:
    """Select the exact recorded scope one seed names, inside the caller's snapshot.

    Every statement runs on the connection the caller holds open, so all of them observe one
    snapshot. ``query.resolve_anchor`` is the source-resolution seam: the caller supplies the
    function that observes a recorded anchor against the requested code tree, and this function
    only decides *which* anchors the selection exposes.

    ``query.effective_seed`` is what is selected. It is ``query.seed`` unless the caller named a
    ``seed_override``, which is how one comparison addresses a different exact revision on each of
    its two snapshots while running this one policy on both.
    """

    seed = query.effective_seed
    seed_revisions = _seed_invariant_revisions(connection, query.repository_id, seed)
    families = _directly_containing_families(connection, query.repository_id, seed, seed_revisions)
    if isinstance(seed, PathSeed) and not seed_revisions:
        # An unregistered path is a typed absence with zero recorded claims and no selection: the
        # caller reports it as ``registration_absent`` rather than as a scope that happens to be
        # empty, because "nothing is registered here" and "the registered set is empty" are
        # different facts about the repository.
        return _empty_scope()
    selected = frozenset(seed_revisions) | frozenset(
        _member_revision_ids(connection, query.repository_id, families)
    )
    sets = SelectionSets(
        seed=seed,
        seed_revisions=seed_revisions,
        families=families,
        selected_invariants=selected,
    )
    frontier = _frontier_expansions(connection, query.repository_id, sorted(selected), families)
    items = _item_stream(
        connection,
        repository_id=query.repository_id,
        sets=sets,
        frontier=frontier,
        resolve_anchor=query.resolve_anchor,
    )
    return _assembled_scope(
        connection,
        repository_id=query.repository_id,
        sets=sets,
        frontier=frontier,
        items=items,
    )


def _empty_scope() -> SelectedScope:
    counts = KnowledgeReadCounts(
        invariant_revisions_total=0,
        family_revisions_total=0,
        memberships_total=0,
        realization_claims_total=0,
        advertised_expansions_total=0,
        primary_items_total=0,
        primary_items_returned=0,
        primary_items_remaining=0,
        distinct_source_locations_total=0,
        distinct_source_paths_total=0,
        unresolved_anchor_total=0,
    )
    return SelectedScope(
        items=(),
        counts=counts,
        directly_containing_families=(),
        revision_groups=(),
        selected_invariant_revision_ids=frozenset(),
        selected_family_revision_ids=frozenset(),
        advertised=(),
        manifest_digest=_manifest_digest(()),
    )


# --- seed selection ------------------------------------------------------------------------


def _seed_invariant_revisions(
    connection: apsw.Connection, repository_id: str, seed: KnowledgeReadSeed
) -> dict[str, str]:
    """Return ``revision_id -> invariant_id`` for the invariant revisions the seed selects.

    A path seed selects the invariant revisions its recorded claims cite; an invariant identity
    seed selects every retained revision of that identity; an explicit revision seed selects that
    one revision. A family seed selects no invariant revisions here -- its members are added from
    the frozen family set, which is what stops the traversal after one family hop.
    """

    if isinstance(seed, PathSeed):
        # A path seed selects through the recorded claim: the claim names the invariant revision,
        # and the anchor beside it names the location. The invariant identity is read from the
        # revision row rather than assumed, because a revision is keyed by its own id and nothing
        # in the claim tells us which identity authored it.
        rows = fetch_realizations_at_path(connection, repository_id, seed.path)
        return {row["invariant_revision_id"]: row["invariant_id"] for row in rows}
    if isinstance(seed, InvariantRevisionSeed):
        return {
            str(row[0]): seed.invariant_id
            for row in connection.execute(
                "SELECT revision_id FROM invariant_revision "
                "WHERE repository_id = ? AND invariant_id = ? AND revision_id = ?",
                (repository_id, seed.invariant_id, seed.revision_id),
            )
        }
    if isinstance(seed, InvariantIdentitySeed):
        return {
            str(revision_id): seed.invariant_id
            for revision_id in fetch_revision_ids(
                connection, repository_id, "invariant_revision", "invariant_id", seed.invariant_id
            )
        }
    return {}


def _directly_containing_families(
    connection: apsw.Connection,
    repository_id: str,
    seed: KnowledgeReadSeed,
    seed_revisions: Mapping[str, str],
) -> dict[str, str]:
    """Return ``family_revision_id -> family_id`` for the families the seed reaches directly.

    For a path or invariant seed these are the families whose *recorded membership rows* cite one
    of the seed's own invariant revisions. For a family seed they are the family revisions the seed
    names, and nothing else: a family seed does not walk onward from its members.
    """

    if isinstance(seed, (FamilyIdentitySeed, FamilyRevisionSeed)):
        if isinstance(seed, FamilyRevisionSeed):
            rows = connection.execute(
                "SELECT revision_id, family_id FROM family_revision "
                "WHERE repository_id = ? AND family_id = ? AND revision_id = ?",
                (repository_id, seed.family_id, seed.revision_id),
            )
        else:
            rows = connection.execute(
                "SELECT revision_id, family_id FROM family_revision "
                "WHERE repository_id = ? AND family_id = ?",
                (repository_id, seed.family_id),
            )
        return {str(row[0]): str(row[1]) for row in rows}
    # The families that directly contain one of the seed's own revisions are read from the same
    # membership rows a reader would: the edge names both endpoints, so the family set is derived
    # from recorded memberships rather than from a second, possibly disagreeing index.
    memberships = fetch_memberships_of_invariants(connection, repository_id, sorted(seed_revisions))
    family_revisions = sorted({family_revision_id for _, family_revision_id, _ in memberships})
    return fetch_family_ids_for_revisions(connection, repository_id, family_revisions)


def _member_revision_ids(
    connection: apsw.Connection, repository_id: str, families: Mapping[str, str]
) -> set[str]:
    """Return the exact member invariant revisions of the frozen family set.

    The family set is passed in already frozen, and that is the whole stopping rule: this reads the
    memberships *of those family revisions* and never asks which other families a member belongs
    to, so a member's membership elsewhere stays an advertised expansion.
    """

    return set(fetch_memberships_of_families(connection, repository_id, sorted(families)))


def _frontier_expansions(
    connection: apsw.Connection,
    repository_id: str,
    selected_invariants: Sequence[str],
    families: Mapping[str, str],
) -> tuple[AdvertisedExpansion, ...]:
    """Return the sibling memberships that are advertised and never traversed.

    A membership is a frontier link when it cites a selected invariant revision but belongs to a
    family the seed did not reach. It is reported so the caller can select that family explicitly
    in a further request, and it adds nothing to this selection's items or counts beyond itself.
    """

    if not selected_invariants:
        return ()
    rows = fetch_memberships_of_invariants(connection, repository_id, list(selected_invariants))
    advertised: list[AdvertisedExpansion] = []
    for member_id, family_revision_id, invariant_revision_id in rows:
        if family_revision_id in families:
            continue
        family_id = _family_identity_of_revision(connection, repository_id, family_revision_id)
        if family_id is None:
            # A membership whose family revision is absent is a dangling canonical endpoint; the
            # structural validation refuses that state before a read is served, so this branch is
            # unreachable for a validated snapshot and is a defensive skip rather than a policy.
            continue
        advertised.append(
            AdvertisedExpansion(
                via_invariant_revision_id=invariant_revision_id,
                family_id=family_id,
                family_revision_id=family_revision_id,
                member_id=member_id,
            )
        )
    advertised.sort(key=lambda entry: (entry.family_revision_id, entry.member_id))
    return tuple(advertised)


def _family_identity_of_revision(
    connection: apsw.Connection, repository_id: str, family_revision_id: str
) -> str | None:
    row = next(
        iter(
            connection.execute(
                "SELECT family_id FROM family_revision WHERE repository_id = ? AND revision_id = ?",
                (repository_id, family_revision_id),
            )
        ),
        None,
    )
    return None if row is None else str(row[0])


# --- item stream ---------------------------------------------------------------------------


def _item_stream(
    connection: apsw.Connection,
    *,
    repository_id: str,
    sets: SelectionSets,
    frontier: tuple[AdvertisedExpansion, ...],
    resolve_anchor: AnchorResolver | None,
) -> tuple[ReadItem, ...]:
    """Build the complete ordered item stream for one selection."""

    items: list[ReadItem] = []
    items.extend(_invariant_revision_items(connection, repository_id, sets))
    items.extend(_family_revision_items(connection, repository_id, sets))
    items.extend(_membership_items(connection, repository_id, sets))
    items.extend(_realization_items(connection, repository_id, sets, resolve_anchor))
    items.extend(
        ReadItem(
            kind="advertised_family",
            item_id=entry.member_id,
            family_id=entry.family_id,
            family_revision_id=entry.family_revision_id,
            member_id=entry.member_id,
            invariant_revision_id=entry.via_invariant_revision_id,
            record_id=entry.family_revision_id,
            selection_reasons=(
                SelectionReason(
                    stage="sibling_membership_in_another_family",
                    via_id=entry.via_invariant_revision_id,
                ),
            ),
        )
        for entry in frontier
    )
    items.sort(key=_sort_key)
    if len(items) > SELECTION_ITEM_LIMIT:
        raise SelectionIncomplete(len(items), SELECTION_ITEM_LIMIT)
    return tuple(items)


# Which stage a selection reason names, per seed kind. A path seed selects the records it matched
# at that path; an identity seed selects an identity's retained revisions; an exact revision seed
# selects one revision. The keys are the seed union's own discriminators, so a new seed kind cannot
# silently fall through to a neighbouring stage.
_SEED_STAGES: Mapping[str, ReadStage] = {
    "path": "seed_selected",
    "invariant": "invariant_identity",
    "invariant_revision": "invariant_revision",
    "family": "family_identity",
    "family_revision": "family_revision",
}


def _seed_stage(seed: KnowledgeReadSeed) -> ReadStage:
    """Return the selection stage one seed's own records carry."""

    return _SEED_STAGES[seed.kind]


def _sort_key(item: ReadItem) -> tuple[int, str, str, str]:
    """Return the documented lexical order of one item.

    The key is ``(kind order, stable identity, exact revision/relation key, item id)``. It is made
    of stored identifiers only: no authored label, no display version and no insertion counter
    participates, so the same graph authored in another order pages identically.
    """

    dumped = item.model_dump()
    first = dumped.get(_IDENTITY_FIRST_KEY[item.kind]) or ""
    second = dumped.get(_IDENTITY_SECOND_KEY[item.kind]) or ""
    return (_KIND_ORDER[item.kind], str(first), str(second), item.item_id)


def _invariant_revision_items(
    connection: apsw.Connection, repository_id: str, sets: SelectionSets
) -> list[ReadItem]:
    selected_invariants = sets.selected_invariants
    seed_revisions = sets.seed_revisions
    seed_stage = _seed_stage(sets.seed)
    if not selected_invariants:
        return []
    identities = fetch_identity_rows(
        connection, repository_id, "invariant", "invariant_id", "display_label"
    )
    items: list[ReadItem] = []
    for row in fetch_invariant_revisions(connection, repository_id, sorted(selected_invariants)):
        revision_id = str(row["revision_id"])
        invariant_id = str(row["invariant_id"])
        from_seed = revision_id in seed_revisions
        stage: ReadStage = seed_stage if from_seed else "member_of_selected_family"
        via = invariant_id if from_seed else None
        items.append(
            ReadItem(
                kind="invariant_revision",
                item_id=revision_id,
                invariant_id=invariant_id,
                record_id=invariant_id,
                revision_id=revision_id,
                display_label=identities.get(invariant_id),
                display_version=str(row["display_version"]),
                statement=str(row["statement"]),
                applicability=str(row["applicability"]),
                essential_conditions=tuple(row["conditions"]),
                exclusions=tuple(row["exclusions"]),
                lifecycle=str(row["state_at_origin"]),
                acceptance_ref=None
                if row["acceptance_ref"] is None
                else str(row["acceptance_ref"]),
                provenance=row["provenance"],
                payload_digest=str(row["payload_digest"]),
                selection_reasons=(SelectionReason(stage=stage, via_id=via),),
            )
        )
    return items


def _family_revision_items(
    connection: apsw.Connection, repository_id: str, sets: SelectionSets
) -> list[ReadItem]:
    families = sets.families
    seed = sets.seed
    seed_revisions = sets.seed_revisions
    seed_stage = _seed_stage(seed)
    if not families:
        return []
    identities = fetch_identity_rows(
        connection, repository_id, "family", "family_id", "display_label"
    )
    items: list[ReadItem] = []
    for row in fetch_family_revisions(connection, repository_id, sorted(families)):
        revision_id = str(row["revision_id"])
        family_id = str(row["family_id"])
        from_seed = seed.kind.startswith("family")
        stage: ReadStage = seed_stage if from_seed else "member_of_selected_family"
        via = None if from_seed else _first_seed_revision(seed_revisions)
        items.append(
            ReadItem(
                kind="family_revision",
                item_id=revision_id,
                family_id=family_id,
                record_id=family_id,
                revision_id=revision_id,
                display_label=identities.get(family_id),
                display_version=str(row["display_version"]),
                joint_guarantee=str(row["joint_guarantee"]),
                lifecycle=str(row["state_at_origin"]),
                acceptance_ref=None
                if row["acceptance_ref"] is None
                else str(row["acceptance_ref"]),
                provenance=row["provenance"],
                payload_digest=str(row["payload_digest"]),
                selection_reasons=(SelectionReason(stage=stage, via_id=via),),
            )
        )
    return items


def _membership_items(
    connection: apsw.Connection, repository_id: str, sets: SelectionSets
) -> list[ReadItem]:
    families = sets.families
    seed = sets.seed
    if not families:
        return []
    staged: ReadStage = (
        _seed_stage(seed) if seed.kind.startswith("family") else "member_of_selected_family"
    )
    rows = fetch_memberships_of_families_full(connection, repository_id, sorted(families))
    return [
        ReadItem(
            kind="family_membership",
            item_id=row[0],
            member_id=row[0],
            family_revision_id=row[1],
            invariant_revision_id=row[2],
            record_id=row[1],
            family_id=families.get(row[1]),
            provenance=row[3],
            selection_reasons=(SelectionReason(stage=staged, via_id=row[1]),),
        )
        for row in rows
    ]


def _realization_items(
    connection: apsw.Connection,
    repository_id: str,
    sets: SelectionSets,
    resolve_anchor: AnchorResolver | None,
) -> list[ReadItem]:
    selected_invariants = sets.selected_invariants
    seed_revisions = sets.seed_revisions
    seed_stage = _seed_stage(sets.seed)
    if not selected_invariants:
        return []
    items: list[ReadItem] = []
    for row in fetch_realizations_for_invariants(
        connection, repository_id, sorted(selected_invariants)
    ):
        revision_id = str(row["invariant_revision_id"])
        from_seed = revision_id in seed_revisions
        stage: ReadStage = seed_stage if from_seed else "member_of_selected_family"
        items.append(
            ReadItem(
                kind="realization_claim",
                item_id=str(row["claim_id"]),
                claim_id=str(row["claim_id"]),
                invariant_revision_id=revision_id,
                record_id=revision_id,
                role=cast("RealizationRole", str(row["role"])),
                rationale=str(row["rationale"]),
                provenance=row["provenance"],
                anchor=None if resolve_anchor is None else resolve_anchor(row),
                selection_reasons=(SelectionReason(stage=stage, via_id=revision_id),),
            )
        )
    return items


# --- assembly -----------------------------------------------------------------------------


def _assembled_scope(
    connection: apsw.Connection,
    *,
    repository_id: str,
    sets: SelectionSets,
    frontier: tuple[AdvertisedExpansion, ...],
    items: tuple[ReadItem, ...],
) -> SelectedScope:
    locations = {
        (item.anchor.path, item.anchor.locator.model_dump_json())
        for item in items
        if item.anchor is not None
    }
    paths = {item.anchor.path for item in items if item.anchor is not None}
    unresolved = sum(
        1
        for item in items
        if item.anchor is not None and item.anchor.resolution != "exact_recorded_blob"
    )
    counts = KnowledgeReadCounts(
        invariant_revisions_total=sum(1 for item in items if item.kind == "invariant_revision"),
        family_revisions_total=sum(1 for item in items if item.kind == "family_revision"),
        memberships_total=sum(1 for item in items if item.kind == "family_membership"),
        realization_claims_total=sum(1 for item in items if item.kind == "realization_claim"),
        advertised_expansions_total=len(frontier),
        primary_items_total=len(items),
        primary_items_returned=0,
        primary_items_remaining=len(items),
        distinct_source_locations_total=len(locations),
        distinct_source_paths_total=len(paths),
        unresolved_anchor_total=unresolved,
    )
    return SelectedScope(
        items=items,
        counts=counts,
        directly_containing_families=_containing_family_rows(
            connection, repository_id, sets.families
        ),
        revision_groups=_revision_groups(items),
        selected_invariant_revision_ids=sets.selected_invariants,
        selected_family_revision_ids=frozenset(sets.families),
        advertised=frontier,
        manifest_digest=_manifest_digest(items),
    )


def _containing_family_rows(
    connection: apsw.Connection, repository_id: str, families: Mapping[str, str]
) -> tuple[DirectlyContainingFamily, ...]:
    rows: list[DirectlyContainingFamily] = []
    for family_revision_id, family_id in sorted(families.items()):
        for row in connection.execute(
            "SELECT invariant_revision_id FROM family_member "
            "WHERE repository_id = ? AND family_revision_id = ?",
            (repository_id, family_revision_id),
        ):
            rows.append(
                DirectlyContainingFamily(
                    family_id=family_id,
                    family_revision_id=family_revision_id,
                    via_invariant_revision_id=str(row[0]),
                )
            )
    return tuple(rows)


def _first_seed_revision(seed_revisions: Mapping[str, str]) -> str | None:
    """Return one seed revision that reached a family, for that family's own reason row.

    A reason names one exact recorded edge rather than a summary of several, and the sorted-first
    choice is deterministic: re-authoring the same graph in another insertion order names the same
    edge, because the seed identity set does not depend on insertion order.
    """

    ordered = sorted(seed_revisions)
    return ordered[0] if ordered else None


def _revision_groups(items: Iterable[ReadItem]) -> tuple[ReadRevisionGroup, ...]:
    """Count the selected revisions per stable identity, for both revision kinds."""

    invariant_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for item in items:
        if item.kind == "invariant_revision" and item.record_id is not None:
            invariant_counts[item.record_id] = invariant_counts.get(item.record_id, 0) + 1
        elif item.kind == "family_revision" and item.record_id is not None:
            family_counts[item.record_id] = family_counts.get(item.record_id, 0) + 1
    groups = [
        ReadRevisionGroup(record_id=record_id, selected_revision_count=count)
        for record_id, count in sorted(invariant_counts.items())
    ]
    groups.extend(
        ReadRevisionGroup(record_id=record_id, selected_revision_count=count)
        for record_id, count in sorted(family_counts.items())
    )
    return tuple(groups)


def _manifest_digest(items: Sequence[ReadItem]) -> str:
    """Return the digest of the selected set's identity stream.

    It covers every primary item's kind, identity and selection reasons, and nothing about how a
    page happened to be cut: two runs that select the same records produce the same manifest
    digest whatever budget they were paged with.
    """

    return sha256_digest(
        [
            {
                "kind": item.kind,
                "item_id": item.item_id,
                "reasons": [reason.model_dump(mode="json") for reason in item.selection_reasons],
            }
            for item in items
        ]
    )


@dataclass(frozen=True)
class PageRequest:
    """Where in a selection to page from, how much fits, and the identity each page declares."""

    context: KnowledgeReadContext
    seed: KnowledgeReadSeed
    max_items: int
    max_utf8_bytes: int
    position: int = 0


def page_of_scope(scope: SelectedScope, request: PageRequest) -> KnowledgeReadPage:
    """Cut one whole-item page from ``position`` of an already-selected scope.

    Items are added while the complete serialized page still fits, so a governing statement is
    never separated from its conditions. If even the first remaining item cannot fit, the page is
    empty, ``minimum_utf8_bytes`` names what the item actually needs, and the continuation is
    unchanged -- an empty page with ``has_more`` set is not a state this can produce.
    """

    context = request.context
    seed = request.seed
    position = request.position
    remaining = scope.items[position:]
    selected: list[ReadItem] = []
    for item in remaining[: request.max_items]:
        candidate = [*selected, item]
        # The continuation a page would carry is part of the page a caller receives, so it is
        # measured with it rather than estimated: a page must not pass the byte ceiling on the
        # size of its own cursor.
        cursor = cursor_for(
            context=context,
            seed=seed,
            manifest_digest=scope.manifest_digest,
            position=position + len(candidate),
        )
        if (
            _page_bytes(candidate, scope=scope, position=position, continuation=cursor)
            > request.max_utf8_bytes
        ):
            break
        selected.append(item)
    returned = len(selected)
    total = len(scope.items)
    leftover = total - position - returned
    if returned == 0 and remaining:
        return _too_small_page(scope=scope, request=request)
    has_more = leftover > 0
    counts = _page_counts(scope.counts, remaining=leftover)
    return KnowledgeReadPage(
        items=tuple(selected),
        counts=counts,
        has_more=has_more,
        enumeration_complete=not has_more,
        continuation=(
            cursor_for(
                context=context,
                seed=seed,
                manifest_digest=scope.manifest_digest,
                position=position + returned,
            )
            if has_more
            else None
        ),
        minimum_utf8_bytes=None,
    )


def _too_small_page(*, scope: SelectedScope, request: PageRequest) -> KnowledgeReadPage:
    """Return the empty page that reports one indivisible item the budget cannot hold."""

    position = request.position
    cursor = cursor_for(
        context=request.context,
        seed=request.seed,
        manifest_digest=scope.manifest_digest,
        position=position,
    )
    needed = _page_bytes(
        [scope.items[position]], scope=scope, position=position, continuation=cursor
    )
    return KnowledgeReadPage(
        items=(),
        counts=_page_counts(scope.counts, remaining=len(scope.items) - position),
        has_more=True,
        enumeration_complete=False,
        continuation=cursor,
        minimum_utf8_bytes=needed,
    )


def _page_counts(selected: KnowledgeReadCounts, *, remaining: int) -> KnowledgeReadCounts:
    """Return the selection's counts with this page's own arithmetic applied.

    ``primary_items_total`` keeps the **declared selection total** and is never restated as the
    tail ahead of the cursor. The three fields are one statement about one walk: the selection
    holds ``total`` items, ``returned`` of them have been emitted by the pages up to and including
    this one, and ``remaining`` are still ahead. ``returned`` is therefore the walk's figure rather
    than this page's item count; the slice size is ``len(page.items)``, which the page already
    carries, so nothing is lost by not repeating it here.

    Restating the total as "this page's items plus the tail" is what makes a continuation page lie:
    page 2 of a 17-item walk would say 16 and the last page would say 1, while the same counts
    object still reports every kind total over all 17 -- the state the packet names as
    non-conforming, "a one-item page implies that the invariant has only one implementation".
    Keeping the declared total is also what makes the model's own arithmetic invariant hold at
    every position rather than only at position 0.
    """

    return selected.model_copy(
        update={
            "primary_items_total": selected.primary_items_total,
            "primary_items_returned": selected.primary_items_total - remaining,
            "primary_items_remaining": remaining,
        }
    )


def _page_bytes(
    items: Sequence[ReadItem], *, scope: SelectedScope, position: int, continuation: str | None
) -> int:
    """Return the serialized size of a prospective page, with its exact continuation included."""

    remaining = len(scope.items) - position - len(items)
    # The measured page is the page a caller receives, so its counts are the same restated counts
    # the page will carry rather than the selection's totals.
    payload = {
        "items": [item.model_dump(mode="json") for item in items],
        "counts": _page_counts(scope.counts, remaining=remaining).model_dump(mode="json"),
        "has_more": remaining > 0,
        "enumeration_complete": remaining <= 0,
        "continuation": continuation if remaining > 0 else None,
        "minimum_utf8_bytes": None,
    }
    return len(canonical_json_bytes(payload))
