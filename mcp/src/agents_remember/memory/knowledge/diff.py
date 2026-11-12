"""The two-snapshot union: what the baseline selected, what the candidate selected, and how they differ.

This module owns exactly one claim, and it owns it because the packet's hard parts are all
consequences of it: **a comparison is the union of two independently selected sets, with each item
retaining the snapshot it came from.** Everything else in the comparison follows from that:

* The selection on each side is KS-R07's policy, run by its own owner on that side's connection.
  Nothing here re-decides relevance, re-walks a family or widens a frontier; this module is handed
  two already-selected sets, and a second selection rule is absent rather than merely discouraged.
* A relationship only the baseline reached stays in the union, because the union is built from the
  two selected sets and never from a walk of the candidate. That is why deleting a realization link
  on the candidate cannot make the baseline's code disappear from the comparison.
* A record one snapshot holds and the other side's selection did not reach is
  ``present_outside_selection`` -- a fact about the selection, reported with the path that does reach
  it -- while a record the other snapshot does not hold at all is ``absent_from_snapshot``.
  Conflating the two is the design's own named misreading, so the coverage is decided by one exact
  existence question asked of the other snapshot, and by nothing else.
* Record changes and source changes are two separate collections. ``changed_fields`` is a statement
  about the *record*; :class:`~agents_remember.models.knowledge.diff.KnowledgeDiffSourceChange` is a
  statement about the *source observation*. A source-only change therefore cannot be rendered as a
  changed obligation, because no record field changed -- and a statement-only change still carries
  both sides' attributed source, because a source comparison is built for every claim in the union
  rather than only for the claims whose source moved.

Nothing here writes: both connections are the caller's, opened read-only by the application seam, and
every statement this module adds is a ``SELECT``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import apsw

from agents_remember.memory.knowledge.read import SelectedScope
from agents_remember.memory.knowledge.read_queries import (
    family_revision_is_recorded,
    invariant_revision_is_recorded,
    membership_is_recorded,
    realization_claim_is_recorded,
)
from agents_remember.models.knowledge.diff import (
    KNOWLEDGE_DIFF_FIELD_NAMES,
    DiffCoverage,
    DiffItemKind,
    DiffRecordTransition,
    KnowledgeDiffSourceChange,
)
from agents_remember.models.knowledge.read import ReadItem

__all__ = [
    "FIELD_PROJECTION",
    "DiffComparison",
    "DiffItemComparison",
    "advertised_item_id",
    "compare_selected_scopes",
]

# Which ``ReadItem`` field carries each compared record field. The two revision kinds store the same
# seven fields plus the statement or the joint guarantee; a claim stores none of them, because a
# claim's content is its role, its rationale and the source it attributes -- and the source half is
# compared as an observation rather than as text.
FIELD_PROJECTION: Mapping[str, str] = {
    "acceptance_ref": "acceptance_ref",
    "applicability": "applicability",
    "essential_conditions": "essential_conditions",
    "exclusions": "exclusions",
    "joint_guarantee": "joint_guarantee",
    "lifecycle": "lifecycle",
    "payload_digest": "payload_digest",
    "provenance": "provenance",
    "statement": "statement",
}

# The anchor fields an observation is compared on. They are the *source* facts: where the author
# recorded the claim, what identity they recorded for it, what identity the requested tree actually
# holds, how the observation resolved, and which locator was recorded. ``detail`` is deliberately
# absent: it is prose written for a reader, and two sides' prose differing is not a source change.
_ANCHOR_FIELDS: tuple[str, ...] = (
    "path",
    "recorded_source_identity",
    "observed_source_identity",
    "resolution",
    "locator",
)

# The declared order of the union stream. It is the read stream's own kind order, so a caller sees
# revisions together, then families, then memberships, then realizations, then the advertised
# frontier -- and within a kind, the same stable identity order the read uses.
_KIND_ORDER: Mapping[str, int] = {
    "invariant": 1,
    "family": 2,
    "membership": 3,
    "realization": 4,
    "advertised_family": 5,
}

_KIND_BY_READ_ITEM: Mapping[str, DiffItemKind] = {
    "invariant_revision": "invariant",
    "family_revision": "family",
    "family_membership": "membership",
    "realization_claim": "realization",
    "advertised_family": "advertised_family",
}

# One advertised frontier link is one *pair* -- a family revision and the member it holds -- and not
# a row of its own. ``::`` joins the pair because neither component can contain it: both are UUIDs.
ADVERTISED_ID_SEPARATOR = "::"


def advertised_item_id(*, family_revision_id: str, member_id: str) -> str:
    """Return the union identity of one advertised frontier link."""

    return f"{family_revision_id}{ADVERTISED_ID_SEPARATOR}{member_id}"


@dataclass(frozen=True)
class SideView:
    """One snapshot as this comparison sees it: its connection, its selected scope, its claims.

    The three travel together because every per-side question the union asks -- is this record
    recorded here, which claims did this side select, what does this side's payload say -- is a
    question about one snapshot, and a function that had to be handed them separately could be
    handed them crossed.
    """

    connection: apsw.Connection
    scope: SelectedScope
    claims: Mapping[str, ReadItem]


@dataclass(frozen=True)
class ComparisonView:
    """Everything one union comparison reads: the two sides."""

    before: SideView
    after: SideView


@dataclass(frozen=True)
class DiffItemComparison:
    """One union item: both sides' payloads, its coverage, and its two independent change statements."""

    item_id: str
    kind: DiffItemKind
    coverage: DiffCoverage
    record_transition: DiffRecordTransition
    before: ReadItem | None
    after: ReadItem | None
    before_selected: bool
    after_selected: bool
    record_id: str | None
    revision_id: str | None
    changed_fields: tuple[str, ...]
    reached_via: tuple[str, ...]
    source_change: KnowledgeDiffSourceChange | None
    # The projected record values behind ``changed_fields``, kept so the typed layer can render each
    # changed field's before and after text without re-deriving which fields changed.
    before_projection: Mapping[str, Any]
    after_projection: Mapping[str, Any]


@dataclass(frozen=True)
class DiffComparison:
    """One complete comparison: the ordered union, its change statements and its selected sets."""

    items: tuple[DiffItemComparison, ...]
    changed_field_count: int
    changed_source_observation_count: int
    source_change_only_item_ids: tuple[str, ...]
    record_change_only_item_ids: tuple[str, ...]
    before_selected_invariant_revision_ids: frozenset[str]
    after_selected_invariant_revision_ids: frozenset[str]
    before_selected_family_revision_ids: frozenset[str]
    after_selected_family_revision_ids: frozenset[str]

    def item_ids(self) -> tuple[str, ...]:
        """Return the union's item identities, in stream order."""

        return tuple(item.item_id for item in self.items)

    def items_with_coverage(self, coverage: DiffCoverage) -> tuple[DiffItemComparison, ...]:
        """Return the union items carrying one coverage value."""

        return tuple(item for item in self.items if item.coverage == coverage)


def compare_selected_scopes(
    *,
    before_connection: apsw.Connection,
    after_connection: apsw.Connection,
    repository_id: str,
    before_scope: SelectedScope,
    after_scope: SelectedScope,
) -> DiffComparison:
    """Compare two already-selected recorded scopes and return their origin-tagged union.

    Both scopes were selected by :func:`agents_remember.memory.knowledge.read.select_recorded_scope`
    on the caller's own connections, one per snapshot. This function reads nothing else from either
    database except the existence questions the union's coverage needs, which is what lets it tell
    "the other snapshot does not hold this record" apart from "the other side's selection did not
    reach it".
    """

    before = SideView(
        connection=before_connection,
        scope=before_scope,
        claims=_claims_by_id(before_scope),
    )
    after = SideView(
        connection=after_connection,
        scope=after_scope,
        claims=_claims_by_id(after_scope),
    )
    view = ComparisonView(
        before=before,
        after=after,
    )
    comparisons = [
        _compare_one(key, payloads, view, repository_id)
        for key, payloads in _union_items(before_scope, after_scope).items()
    ]
    items = _ordered(comparisons)
    return DiffComparison(
        items=items,
        changed_field_count=sum(len(item.changed_fields) for item in items),
        changed_source_observation_count=sum(
            1
            for item in items
            if item.source_change is not None and item.source_change.source_observation_changed
        ),
        source_change_only_item_ids=tuple(
            item.item_id
            for item in items
            if item.source_change is not None and item.source_change.source_change_only
        ),
        record_change_only_item_ids=tuple(
            item.item_id
            for item in items
            if item.source_change is not None and item.source_change.record_change_only
        ),
        before_selected_invariant_revision_ids=before_scope.selected_invariant_revision_ids,
        after_selected_invariant_revision_ids=after_scope.selected_invariant_revision_ids,
        before_selected_family_revision_ids=before_scope.selected_family_revision_ids,
        after_selected_family_revision_ids=after_scope.selected_family_revision_ids,
    )


def _union_items(
    before_scope: SelectedScope, after_scope: SelectedScope
) -> dict[tuple[DiffItemKind, str], tuple[ReadItem | None, ReadItem | None]]:
    """Return ``(kind, union identity) -> (before payload, after payload)`` for the whole union.

    A record both sides selected is **one** entry with two payloads, keyed by a stored identity: a
    revision id, a family revision id, a membership id, a claim id, or the family-revision/member
    pair that an advertised frontier link asserts.
    """

    union: dict[tuple[DiffItemKind, str], list[ReadItem | None]] = {}
    for scope, index in ((before_scope, 0), (after_scope, 1)):
        for item in scope.items:
            key = _union_key(item)
            union.setdefault(key, [None, None])[index] = item
    return {key: (payloads[0], payloads[1]) for key, payloads in union.items()}


def _union_key(item: ReadItem) -> tuple[DiffItemKind, str]:
    """Return the union identity of one selected item."""

    kind = _KIND_BY_READ_ITEM[item.kind]
    if kind == "advertised_family":
        return (
            kind,
            advertised_item_id(
                family_revision_id=str(item.family_revision_id), member_id=str(item.member_id)
            ),
        )
    return (kind, item.item_id)


def _claims_by_id(scope: SelectedScope) -> dict[str, ReadItem]:
    """Return the claims of one selected set, keyed by claim identity."""

    return {
        str(item.claim_id): item
        for item in scope.items
        if item.kind == "realization_claim" and item.claim_id is not None
    }


def _compare_one(
    key: tuple[DiffItemKind, str],
    payloads: tuple[ReadItem | None, ReadItem | None],
    view: ComparisonView,
    repository_id: str,
) -> DiffItemComparison:
    """Compare one union key: both payloads, its coverage, its fields and its source observation."""

    kind, item_id = key
    before_item, after_item = payloads
    coverage = _coverage(key, payloads, view, repository_id)
    before_projection = _project(before_item)
    after_projection = _project(after_item)
    changed = (
        _claim_changed_fields(before_item, after_item)
        if kind == "realization"
        else _changed_fields(
            before_projection,
            after_projection,
            present_on_both_sides=before_item is not None and after_item is not None,
        )
    )
    record_transition = _record_transition(payloads, changed)
    return DiffItemComparison(
        item_id=item_id,
        kind=kind,
        coverage=coverage,
        record_transition=record_transition,
        before=before_item,
        after=after_item,
        before_selected=before_item is not None,
        after_selected=after_item is not None,
        record_id=_first_present(before_item, after_item, "record_id"),
        revision_id=_first_present(before_item, after_item, "revision_id"),
        changed_fields=changed,
        reached_via=_reached_via(before_item) + _reached_via(after_item),
        source_change=(
            None
            if kind != "realization"
            else _source_change(
                claim_id=item_id,
                before=view.before.claims.get(item_id),
                after=view.after.claims.get(item_id),
                record_changed=bool(changed),
            )
        ),
        before_projection=before_projection,
        after_projection=after_projection,
    )


def _record_transition(
    payloads: tuple[ReadItem | None, ReadItem | None],
    changed: tuple[str, ...],
) -> DiffRecordTransition:
    """Return how one union item's record moved between the two snapshots.

    Four states. A revised statement is a *pair* of records -- the old revision only the baseline
    holds and the new revision only the candidate holds -- and text records hold no predecessor
    edge naming one as the other's replacement (MIK-R74 rule 1), so the pair is reported as it
    reads: a removal beside an unrelated addition, never a succession the author did not record.
    """

    before_item, after_item = payloads
    if before_item is not None and after_item is not None:
        return "changed" if changed else "unchanged"
    return "removed" if before_item is not None else "added"


# The kinds whose records can hold another selected revision of the same identity: a claim and
# a frontier link have no such relation -- a claim's identity is the claim -- so they are never
# reported as present-but-unselected on that ground.
_REVISABLE_KINDS: frozenset[DiffItemKind] = frozenset({"invariant", "family"})


def _selected_another_revision_of(side: SideView, present: ReadItem | None) -> bool:
    """Return whether one side selected another exact revision of one record identity.

    The identity is the record the item belongs to, and the question is only whether that side's
    selected set holds a *different* exact revision of it. Nothing about authored labels, display
    versions or insertion order participates: a revision is recognised by identity alone, never by
    which version looks newer.
    """

    if present is None or present.record_id is None or present.revision_id is None:
        return False
    return any(
        _KIND_BY_READ_ITEM[item.kind] == _KIND_BY_READ_ITEM[present.kind]
        and item.record_id == present.record_id
        and item.revision_id is not None
        and item.revision_id != present.revision_id
        for item in side.scope.items
    )


def _coverage(
    key: tuple[DiffItemKind, str],
    payloads: tuple[ReadItem | None, ReadItem | None],
    view: ComparisonView,
    repository_id: str,
) -> DiffCoverage:
    """Return what each side has of one union item: selected, present but unselected, or absent.

    Three states, and the third must not absorb the second. A record the other snapshot holds but
    whose declared selection did not reach it is ``present_outside_selection``, which the design
    states is *not* deletion; only a record the other snapshot does not hold at all is
    ``absent_from_snapshot``.
    """

    kind, item_id = key
    before_item, after_item = payloads
    if before_item is not None and after_item is not None:
        return "selected_both"
    other = view.after if before_item is not None else view.before
    present = before_item if before_item is not None else after_item
    # Two ways to establish that the other snapshot holds the record. **They are not two
    # independent deciders, and the difference was measured rather than argued** (fix round 1's F2,
    # re-measured in fix round 2 after the rule-3 ablation it cited for the wrong variant):
    #
    # * the other side *selected* another exact revision of the same identity. This one is
    #   load-bearing on its own: removing it alone turns a missing selection into a real absence on
    #   the fixture's `unselected_revision_id` (`absent_from_snapshot` where
    #   `present_outside_selection` is owed -- variant A of the fix round 2 ablation, measured);
    # * the other side's tables hold the record, asked by this item's own key. This is the rule that
    #   reports a real absence *and* the one that keeps a record the other snapshot holds but did not
    #   select out of that absence. It is load-bearing in the direction that matters: forcing its
    #   answer *present* turns a genuinely deleted realization into a missing selection
    #   (`present_outside_selection` where `absent_from_snapshot` is owed -- variant C' of the fix
    #   round 2 ablation, two kills, and `M24` kills the same node by forcing the realization probe
    #   true). Forcing its answer *absent* changes no asserted state on this population (variant C,
    #   all 28 nodes survive), because the three items whose absence-answer it gives are all really
    #   absent -- so the direction is measured, not assumed. A candidate whose copy of a revision was
    #   removed answers no under all three.
    #
    # A snapshot and a selection are different objects, which is why the second rule cannot be
    # replaced by the first: the probe asks the snapshot, and the first reads what the other
    # side's *selection* already established.
    if kind in _REVISABLE_KINDS and _selected_another_revision_of(other, present):
        return "present_outside_selection"
    return (
        "present_outside_selection"
        if _recorded(kind, item_id, repository_id, other.connection)
        else "absent_from_snapshot"
    )


def _recorded(
    kind: DiffItemKind, item_id: str, repository_id: str, connection: apsw.Connection
) -> bool:
    """Return whether one snapshot holds the record one union item names.

    Each kind asks its own table by its own key. A membership is keyed by its ``member_id``, which is
    the identity the read stream already uses for that item, and an advertised link is keyed by the
    family revision it belongs to -- the row whose presence or absence the question is really about.
    """

    if kind == "advertised_family":
        family_revision_id, _separator, _member_id = item_id.partition(ADVERTISED_ID_SEPARATOR)
        return family_revision_is_recorded(connection, repository_id, family_revision_id)
    if kind == "invariant":
        return invariant_revision_is_recorded(connection, repository_id, item_id)
    if kind == "family":
        return family_revision_is_recorded(connection, repository_id, item_id)
    if kind == "membership":
        return membership_is_recorded(connection, repository_id, item_id)
    return realization_claim_is_recorded(connection, repository_id, item_id)


def _project(item: ReadItem | None) -> dict[str, Any]:
    """Return one item's compared record fields, keyed by their declared names, or empty."""

    if item is None:
        return {}
    dumped = item.model_dump()
    return {field: dumped.get(attribute) for field, attribute in FIELD_PROJECTION.items()}


def _changed_fields(
    before: Mapping[str, Any], after: Mapping[str, Any], *, present_on_both_sides: bool
) -> tuple[str, ...]:
    """Return the record fields whose values differ, in the declared field order.

    A field is compared only when both sides hold the record: a record that only one snapshot holds
    is reported by its ``coverage``, and reporting all nine of its fields as "changed" would state
    nine differences where there is one absence (or, for a removal, one relationship).
    """

    if not present_on_both_sides:
        return ()
    return tuple(
        field
        for field in KNOWLEDGE_DIFF_FIELD_NAMES
        if _comparable(before.get(field)) != _comparable(after.get(field))
    )


def _claim_changed_fields(
    before_item: ReadItem | None, after_item: ReadItem | None
) -> tuple[str, ...]:
    """Return the authored fields of one claim that differ between the two sides.

    A claim's content is the relationship's own authored words: the role the author recorded and the
    rationale they wrote. That is why a claim is compared on those two fields rather than on
    ``FIELD_PROJECTION``: a claim's substantive difference *is* its authored statement about the
    relationship, while a claim that only moved its anchor has changed its source, not its words.
    """

    if before_item is None or after_item is None:
        return ()
    changed: list[str] = []
    if before_item.role != after_item.role:
        changed.append("role")
    if before_item.rationale != after_item.rationale:
        changed.append("rationale")
    return tuple(changed)


def _comparable(value: Any) -> Any:
    """Return one projected value in a shape that compares by content rather than by identity."""

    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _comparable(inner)) for key, inner in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_comparable(inner) for inner in value)
    return value


def _anchor_signature(item: ReadItem | None) -> tuple[Any, ...] | None:
    """Return the source facts one side's claim observation carries, or ``None`` without an anchor."""

    if item is None or item.anchor is None:
        return None
    dumped = item.anchor.model_dump()
    return tuple(_comparable(dumped.get(field)) for field in _ANCHOR_FIELDS)


def _source_change(
    *,
    claim_id: str,
    before: ReadItem | None,
    after: ReadItem | None,
    record_changed: bool,
) -> KnowledgeDiffSourceChange:
    """Return one claim's typed source comparison.

    Each boolean is a statement about a different object: the claim's authored fields, the source
    observation, and whether exactly one of the two moved. Nothing here names *what* a source change
    means, because the model has no field for a meaning -- the packet's second non-conforming
    example is unrepresentable rather than avoided.
    """

    before_signature = _anchor_signature(before)
    after_signature = _anchor_signature(after)
    if before is None or after is None:
        # A claim only one snapshot holds has no second observation, so nothing about its source
        # *moved*: one side is simply not there. Reporting that as a source change would be a
        # statement the comparison did not make, and the absent side's own observation is still
        # carried beside this so the earlier code stays inspectable.
        return KnowledgeDiffSourceChange(
            claim_id=claim_id,
            before_observation=before,
            after_observation=after,
            record_field_changed=False,
            source_observation_changed=False,
            source_change_only=False,
            record_change_only=False,
            missing_side="after" if after is None else "before",
        )
    source_changed = before_signature != after_signature
    return KnowledgeDiffSourceChange(
        claim_id=claim_id,
        before_observation=before,
        after_observation=after,
        record_field_changed=record_changed,
        source_observation_changed=source_changed,
        source_change_only=source_changed and not record_changed,
        record_change_only=record_changed and not source_changed,
    )


def _first_present(
    before_item: ReadItem | None, after_item: ReadItem | None, attribute: str
) -> str | None:
    """Return one identity field, preferring whichever side holds it."""

    for item in (before_item, after_item):
        if item is not None:
            value = getattr(item, attribute)
            if value is not None:
                return str(value)
    return None


def _reached_via(item: ReadItem | None) -> tuple[str, ...]:
    """Return the selection paths that reached one item, as ``stage:via`` strings."""

    if item is None:
        return ()
    return tuple(
        reason.stage if reason.via_id is None else f"{reason.stage}:{reason.via_id}"
        for reason in item.selection_reasons
    )


def _ordered(items: Sequence[DiffItemComparison]) -> tuple[DiffItemComparison, ...]:
    """Return the union in its declared stream order."""

    return tuple(sorted(items, key=lambda item: (_KIND_ORDER[item.kind], item.item_id)))
