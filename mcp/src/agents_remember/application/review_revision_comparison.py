"""The explicit revision comparison's selection policy (ICR-R07@v1).

This module owns the one rule the packet's Required Behavior states: **a revision head is a
retained revision with no recorded successor for that identity in the selected snapshot revision
population**, where "recorded successor" means an authored predecessor edge -- the successor's own
declaration of which revision it replaced -- and nothing else. No collection order, no presence on
both sides, no timestamp and no text similarity participates, because none of them is a statement
an author made about which revision replaced which.

**What it reads.** The selected populations come from the shipped comparison's own union items
(one item per retained revision, each carrying the side payloads that selected it), and the
authored edges come from the two snapshots' own predecessor tables, read through read-only
connections. Both inputs are recorded facts; this module invents neither.

**What it returns.** One :class:`~agents_remember.models.knowledge.revision_selection.ReviewRevisionSelection`
per reviewed identity: the unique-head pair when each nonempty side establishes exactly one head
(before ``r1`` and after ``r1 -> r2 -> r3`` therefore defaults to ``r1`` versus ``r3``), the
one-sided head under ICR-R06@v1 when a side is known-empty, and an explicit ambiguous or
unresolved selection -- with every head and every retained revision still listed -- when multiple
heads, a successor cycle, or an authored edge the snapshot does not retain prevents a unique
head. Intermediate revisions stay in the union and stay addressable through the comparison's own
per-side explicit revision selector; this module removes none of them.

**Why this is its own module.** The review adapter is over the repository's soft file-size rail,
and the packet's Scope asks a leaf that touches a responsibility inside it to move that
responsibility out before adding behavior. Head selection is that responsibility here: one
implementation, called by :mod:`agents_remember.application.knowledge_review`, which stays the
adapter that resolves, calls and assembles. The adapter re-exports nothing from here because no
module under ``mcp/`` imported the rule this replaces -- the both-sides preference was private
to the adapter -- so there is one implementation and no alias to keep.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import apsw

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.read_queries import (
    family_revision_is_recorded,
    fetch_predecessor_edges,
    invariant_revision_is_recorded,
)
from agents_remember.models.knowledge.diff import KnowledgeDiffItem
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection

__all__ = [
    "SubjectRevisionSelection",
    "read_snapshot_edges",
    "revision_heads",
    "select_subject_revisions",
]

# The union item kinds this policy reads populations from. A claim and a frontier link record no
# authored old/new edge -- a claim's identity is the claim -- so they are never revision heads.
_SELECTABLE_KINDS = ("invariant", "family")


@dataclass(frozen=True)
class SubjectRevisionSelection:
    """One reviewed identity's head selection, with the union items its statements render from.

    ``selection`` is the recorded value: the pair or the explicit non-pair, with every head and
    every retained revision listed. ``before_item``/``after_item`` are the union items whose side
    payloads the pane renders -- the before head's item for the before statement and the after
    head's item for the after statement -- or ``None`` when the selection named no pair and the
    pane must render the explicit ambiguity instead of a winner. A one-sided selection carries
    the nonempty side's head item, which is exactly the item ICR-R06@v1's absent-side rendering
    already reads.
    """

    selection: ReviewRevisionSelection | None
    before_item: KnowledgeDiffItem | None
    after_item: KnowledgeDiffItem | None


def revision_heads(population: Sequence[str], edges: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """Return the heads of one selected revision population, in sorted order.

    A head is a retained revision no other revision of the population names as its predecessor.
    ``edges`` are ``(successor, predecessor)`` pairs; only pairs with both endpoints in the
    population establish a successor relation, because an edge to a revision outside the
    population is not a successor *in the selected population* the packet scopes the rule to.
    The sorted order is a rendering determinism -- the same snapshots always list the same heads
    -- and never a semantic revision rule: no caller may read the first head as the selected one.
    """

    members = set(population)
    succeeded = {predecessor for successor, predecessor in edges if successor in members}
    return tuple(sorted(revision for revision in members if revision not in succeeded))


def read_snapshot_edges(database_path: Path, repository_id: str) -> frozenset[tuple[str, str]]:
    """Return every authored ``(successor, predecessor)`` edge one snapshot records.

    The snapshot is opened read-only and closed before returning, so the read cannot change the
    bytes the selection is made from. Both predecessor tables are read because the policy asks
    the same question of either revision kind, and the caller filters the union to the identity
    it reviews -- an edge is a fact about the snapshot, not about the subject.
    """

    connection = open_read_only_database(Path(database_path))
    try:
        return frozenset(fetch_predecessor_edges(connection, repository_id))
    finally:
        connection.close()


@dataclass(frozen=True)
class _ReviewedPopulations:
    """One reviewed identity's two selected populations and their established heads.

    The four tuples travel together because every decision below -- compared, one-sided,
    ambiguous or unresolved -- reads the same four values, and a helper handed them separately
    could be handed one subject's populations with another's heads.
    """

    kind: str
    record_id: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    before_heads: tuple[str, ...]
    after_heads: tuple[str, ...]


@dataclass(frozen=True)
class _EdgeSources:
    """The two snapshot files one selection reads authored edges from, and their namespace.

    The three travel together because an edge read is meaningless without the namespace that
    scopes it, and a helper handed them separately could read one snapshot under the other's
    namespace.
    """

    repository_id: str
    before_database: Path
    after_database: Path


def select_subject_revisions(
    items: Sequence[KnowledgeDiffItem],
    selector: KnowledgeReadSeed | None,
    *,
    repository_id: str,
    before_database: Path,
    after_database: Path,
) -> SubjectRevisionSelection:
    """Select the reviewed identity's before/after revision pair from authored heads.

    The populations are the shipped comparison's own union items for the selector's identity --
    the before payloads' revisions on the before side and the after payloads' on the after side --
    and the edges are each snapshot's own authored predecessor relations. The combination is the
    packet's Required Behavior: unique heads compare head to head, a known-empty side stays an
    ICR-R06@v1 addition or removal, and anything that prevents a unique head is an explicit
    ambiguous or unresolved selection that still lists every head and every retained revision.
    A selector that names no identity addresses no identity item, exactly as the adapter's rule
    it replaces did.
    """

    identity = _selector_identity(selector)
    if identity is None:
        return SubjectRevisionSelection(selection=None, before_item=None, after_item=None)
    kind, record_id = identity
    sources = _EdgeSources(
        repository_id=repository_id,
        before_database=before_database,
        after_database=after_database,
    )
    populations = _populations(items, kind, record_id, sources)
    if populations is None:
        return _unresolved(
            _ReviewedPopulations(
                kind=kind,
                record_id=record_id,
                before=(),
                after=(),
                before_heads=(),
                after_heads=(),
            ),
            f"no revision of the {kind} {record_id} was selected on either side, so no "
            "pair was established and no statements render as operands",
        )
    return _decide(items, populations, sources)


def _populations(
    items: Sequence[KnowledgeDiffItem], kind: str, record_id: str, sources: _EdgeSources
) -> _ReviewedPopulations | None:
    """Return one identity's two selected populations with their heads, or ``None`` when empty.

    ``None`` means both populations are empty -- there is nothing to decide a head from, so the
    caller records that explicitly rather than deciding. A ``_ReviewedPopulations`` otherwise,
    even when one side is known-empty: a one-sided head is still a head the snapshots establish.
    """

    before_population = _side_population(items, kind, record_id, "before")
    after_population = _side_population(items, kind, record_id, "after")
    if not before_population and not after_population:
        return None
    before_edges = read_snapshot_edges(sources.before_database, sources.repository_id)
    after_edges = read_snapshot_edges(sources.after_database, sources.repository_id)
    return _ReviewedPopulations(
        kind=kind,
        record_id=record_id,
        before=before_population,
        after=after_population,
        before_heads=revision_heads(before_population, _touching(before_edges, before_population)),
        after_heads=revision_heads(after_population, _touching(after_edges, after_population)),
    )


def _decide(
    items: Sequence[KnowledgeDiffItem],
    populations: _ReviewedPopulations,
    sources: _EdgeSources,
) -> SubjectRevisionSelection:
    """Route one identity's established populations to their recorded selection."""

    invalid = _invalid_graph_detail(
        populations.kind,
        sources,
        ("before", populations.before),
        ("after", populations.after),
    )
    if invalid is not None:
        side, reason = invalid
        return _unresolved(populations, f"the {side} snapshot's {reason}")
    if not populations.before or not populations.after:
        return _one_sided(items, populations, added=not populations.before)
    return _combine(items, populations)


def _combine(
    items: Sequence[KnowledgeDiffItem], populations: _ReviewedPopulations
) -> SubjectRevisionSelection:
    """Record the selection for two nonempty populations: compared, ambiguous or unresolved."""

    if not populations.before_heads or not populations.after_heads:
        empty_side = "before" if not populations.before_heads else "after"
        count = len(populations.before) if not populations.before_heads else len(populations.after)
        return _unresolved(
            populations,
            f"the {empty_side} snapshot retains {count} revision(s) but establishes no head -- "
            "every retained revision names a successor, so the authored lineage is cyclic and "
            "no newest version is fabricated",
        )
    if len(populations.before_heads) != 1 or len(populations.after_heads) != 1:
        return _ambiguous(populations)
    return _compared(items, populations)


def _selector_identity(selector: KnowledgeReadSeed | None) -> tuple[str, str] | None:
    """Return ``(union kind, record id)`` for an identity selector, or ``None`` otherwise."""

    kind = getattr(selector, "kind", None)
    if kind == "invariant":
        return ("invariant", str(selector.invariant_id))  # type: ignore[attr-defined]
    if kind == "family":
        return ("family", str(selector.family_id))  # type: ignore[attr-defined]
    return None


def _side_population(
    items: Sequence[KnowledgeDiffItem], kind: str, record_id: str, side: str
) -> tuple[str, ...]:
    """Return one side's selected revision ids for one identity, in sorted order.

    The population is what the shipped comparison selected -- the side payloads the union items
    carry -- and not a second read of either snapshot, so the heads below are heads of the
    population the comparison actually shows. The sorted order is the same rendering determinism
    :func:`revision_heads` states. Only the two supersedable kinds are read: a claim and a
    frontier link are never revision heads.
    """

    if kind not in _SELECTABLE_KINDS:
        return ()
    revisions = {
        str(payload.revision_id)
        for item in items
        if item.kind == kind and item.record_id == record_id
        for payload in (item.before if side == "before" else item.after,)
        if payload is not None and payload.revision_id is not None
    }
    return tuple(sorted(revisions))


def _touching(
    edges: frozenset[tuple[str, str]], population: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    """Return the authored edges with both endpoints in one selected population."""

    members = set(population)
    return tuple(
        sorted(
            (successor, predecessor)
            for successor, predecessor in edges
            if successor in members and predecessor in members
        )
    )


def _invalid_graph_detail(
    kind: str,
    sources: _EdgeSources,
    before: tuple[str, tuple[str, ...]],
    after: tuple[str, tuple[str, ...]],
) -> tuple[str, str] | None:
    """Return ``(side, reason)`` when an authored edge names a revision no snapshot retains.

    An authored predecessor edge is a foreign key into the snapshot that declares it: a snapshot
    that declares an edge naming a revision necessarily holds it. An edge touching the reviewed
    population whose other endpoint is recorded nowhere in that snapshot is therefore an invalid
    graph, and the reason names the missing revision rather than repairing it. Recorded-but-
    unselected endpoints are ignored -- the packet scopes heads to the selected population, so a
    successor relation outside it neither establishes nor breaks one.
    """

    for side, population in (before, after):
        database = sources.before_database if side == "before" else sources.after_database
        missing = _unretained_endpoint(kind, sources.repository_id, database, population)
        if missing is not None:
            return (
                side,
                f"authored lineage names revision {missing} that the snapshot does not "
                "retain, so no head can be established from it",
            )
    return None


def _unretained_endpoint(
    kind: str,
    repository_id: str,
    database_path: Path,
    population: tuple[str, ...],
) -> str | None:
    """Return the first sorted edge endpoint the snapshot does not retain, or ``None``.

    Only edges touching the reviewed population are examined: an edge between two revisions the
    comparison never selected cannot move this subject's heads. The recorded check is one
    existence question per touched endpoint, asked of the snapshot file itself through a
    read-only connection.
    """

    members = set(population)
    edges = read_snapshot_edges(database_path, repository_id)
    candidates = sorted(
        {
            endpoint
            for successor, predecessor in edges
            for endpoint in (successor, predecessor)
            if (successor in members or predecessor in members) and endpoint not in members
        }
    )
    connection = open_read_only_database(Path(database_path))
    try:
        for endpoint in candidates:
            if not _revision_is_recorded(connection, kind, repository_id, endpoint):
                return endpoint
        return None
    finally:
        connection.close()


def _revision_is_recorded(
    connection: apsw.Connection, kind: str, repository_id: str, revision_id: str
) -> bool:
    """Return whether one snapshot retains one revision of the reviewed kind."""

    if kind == "family":
        return family_revision_is_recorded(connection, repository_id, revision_id)
    return invariant_revision_is_recorded(connection, repository_id, revision_id)


def _compared(
    items: Sequence[KnowledgeDiffItem], populations: _ReviewedPopulations
) -> SubjectRevisionSelection:
    """Record the unique-head pair the two snapshots are compared on."""

    before_id = populations.before_heads[0]
    after_id = populations.after_heads[0]
    history = len(populations.before) + len(populations.after) - 2
    kind, record_id = populations.kind, populations.record_id
    return SubjectRevisionSelection(
        selection=ReviewRevisionSelection(
            record_kind=kind,  # type: ignore[arg-type]
            record_id=record_id,
            state="compared",
            before_revision_id=before_id,
            after_revision_id=after_id,
            before_heads=(before_id,),
            after_heads=(after_id,),
            before_retained=populations.before,
            after_retained=populations.after,
            statement=(
                f"the {kind} {record_id} comparison uses the before head {before_id} and "
                f"the after head {after_id}, each the unique retained revision with no "
                f"recorded successor in its snapshot's selected population; {history} other "
                "retained revision(s) remain selectable history"
            ),
        ),
        before_item=_item_with(items, kind, record_id, before_id, "before"),
        after_item=_item_with(items, kind, record_id, after_id, "after"),
    )


def _one_sided(
    items: Sequence[KnowledgeDiffItem], populations: _ReviewedPopulations, *, added: bool
) -> SubjectRevisionSelection:
    """Record a known-empty side as an ICR-R06@v1 addition or removal.

    A known-empty side is a side whose selected population holds no revision of the reviewed
    identity: the nonempty side's unique head is shown and the empty side renders as absent,
    exactly as the one-sided contract already does. A nonempty side with anything but one head
    is not a one-sided side with a head to show -- it is an ambiguity or a lineage failure, and
    it is reported as one rather than as an addition or removal wearing a chosen revision.
    """

    kind, record_id = populations.kind, populations.record_id
    shown = "after" if added else "before"
    heads = populations.after_heads if added else populations.before_heads
    if len(heads) != 1:
        if not heads:
            return _unresolved(
                _ReviewedPopulations(
                    kind=kind,
                    record_id=record_id,
                    before=populations.before,
                    after=populations.after,
                    before_heads=(),
                    after_heads=(),
                ),
                f"the {shown} snapshot retains revisions but establishes no head -- every "
                "retained revision names a successor, so the authored lineage is cyclic and "
                "no newest version is fabricated",
            )
        return _ambiguous(
            populations,
            crowded_side=shown,
            crowded_ids=heads,
        )
    head = heads[0]
    empty = "before" if added else "after"
    head_item = _item_with(items, kind, record_id, head, shown)
    return SubjectRevisionSelection(
        selection=ReviewRevisionSelection(
            record_kind=kind,  # type: ignore[arg-type]
            record_id=record_id,
            state="added" if added else "removed",
            before_revision_id=None if added else head,
            after_revision_id=head if added else None,
            before_heads=() if added else (head,),
            after_heads=(head,) if added else (),
            before_retained=populations.before,
            after_retained=populations.after,
            statement=(
                f"the {empty} snapshot retains no revision of the {kind} {record_id}, so "
                f"the {shown} head {head} is shown as "
                f"{'an addition' if added else 'a removal'} under the one-sided contract"
            ),
        ),
        before_item=None if added else head_item,
        after_item=head_item if added else None,
    )


def _ambiguous(
    populations: _ReviewedPopulations,
    *,
    crowded_side: str | None = None,
    crowded_ids: tuple[str, ...] = (),
) -> SubjectRevisionSelection:
    """Record multiple legitimate heads with no winner chosen."""

    if crowded_side is not None:
        crowded = f"{crowded_side} heads [{', '.join(crowded_ids)}]"
    else:
        named = []
        if len(populations.before_heads) != 1:
            named.append(f"before heads [{', '.join(populations.before_heads)}]")
        if len(populations.after_heads) != 1:
            named.append(f"after heads [{', '.join(populations.after_heads)}]")
        crowded = " and ".join(named) if named else "heads []"
    kind, record_id = populations.kind, populations.record_id
    return SubjectRevisionSelection(
        selection=ReviewRevisionSelection(
            record_kind=kind,  # type: ignore[arg-type]
            record_id=record_id,
            state="ambiguous",
            before_heads=populations.before_heads,
            after_heads=populations.after_heads,
            before_retained=populations.before,
            after_retained=populations.after,
            statement=(
                f"ambiguous revision selection for the {kind} {record_id}: the snapshots "
                f"retain multiple legitimate heads ({crowded}) with no authored successor "
                "ordering between them, so no revision was chosen; select one retained "
                "revision explicitly to compare it"
            ),
        ),
        before_item=None,
        after_item=None,
    )


def _unresolved(populations: _ReviewedPopulations, reason: str) -> SubjectRevisionSelection:
    """Record a lineage failure with the revisions still visible and no pair fabricated."""

    kind, record_id = populations.kind, populations.record_id
    return SubjectRevisionSelection(
        selection=ReviewRevisionSelection(
            record_kind=kind,  # type: ignore[arg-type]
            record_id=record_id,
            state="unresolved",
            before_heads=populations.before_heads,
            after_heads=populations.after_heads,
            before_retained=populations.before,
            after_retained=populations.after,
            statement=f"unresolved revision selection for the {kind} {record_id}: {reason}",
        ),
        before_item=None,
        after_item=None,
    )


def _item_with(
    items: Sequence[KnowledgeDiffItem],
    kind: str,
    record_id: str,
    revision_id: str,
    side: str,
) -> KnowledgeDiffItem | None:
    """Return the union item carrying one selected revision's side payload, or ``None``.

    The item is keyed by the stored revision identity the selection recorded, never by stream
    position or by presence on both sides: a head the snapshots replaced is found by its id
    wherever the union placed it.
    """

    for item in items:
        if item.kind != kind or item.record_id != record_id:
            continue
        payload = item.before if side == "before" else item.after
        if payload is not None and str(payload.revision_id) == revision_id:
            return item
    for item in items:
        if item.kind == kind and item.item_id == revision_id:
            return item
    return None
