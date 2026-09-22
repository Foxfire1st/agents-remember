"""What the comparison shows, what it leaves out, and the reference to the whole of it.

Three jobs, and they are one job: a response must never be readable as more than it is.

* **The display filter selects only recorded vocabulary.** Roles an author recorded on a realization
  claim, and nothing computed. A filter cannot select by size, recency, churn or inferred impact,
  because none of those is a recorded fact -- so nothing here can invent a relevance rule even by
  accident: the filter model it consumes has no field for one.
* **Every suppression is counted with its reason.** A filter reduces the display and never the
  comparison, and the counts travel beside the shown items so a filtered response states its own
  limits. The two omissions the packet names explicitly are here: a record the other snapshot holds
  but the declared selection did not reach, and a path that changed between the two code trees that
  no recorded realization attributes. Neither is dropped, and neither is described as harmless.
* **The expansion is a value, not a promise.** :class:`KnowledgeDiffExpansion` names both trees, the
  command that reproduces the full source diff, the paths the two trees differ at, and which of those
  paths carry no recorded attribution. A caller that was shown a partial view can therefore reach the
  whole comparison without asking this operation for anything else -- and the operation never dumps
  source text, because reporting *attribution* is this increment's contract.
* **A changed path is an address and a status.** :class:`TreeChange` carries the raw filename exactly
  as Git recorded it plus what happened to it, so a tab or a newline inside a name stays part of the
  address a caller expands the same file with, and an addition, a deletion, a mode or type change and
  a path whose content cannot be rendered are all still *listed* rather than dropped from the set.
* **Attribution is one partition of one population.** :func:`partition_attribution` -- re-exported
  here from :mod:`agents_remember.memory.knowledge.diff_attribution`, which owns it -- divides the
  *measured* changed paths, the source measurement's own denominator, into attributed, confirmed
  unregistered and undetermined. The three buckets are disjoint and exhaustive by construction, several
  links to one path count it once, and a purported mapping that did not resolve is listed with that fact
  rather than promoted. This module's own job is to carry that partition onto the expansion and to state
  the two omissions it establishes.
* **One name left and is deliberately not replaced.** The module used to define
  ``attributed_paths(comparison)``: every path a *selected* claim named, with no intersection against the
  measured change set, which is the calculation ``ICR-R04`` corrects. It is deleted rather than
  re-exported -- an unchanged mapped path is context and never a change -- and what replaces it is
  :attr:`SourceAttribution.attributed_paths`, the partition value's own bucket. No module in this
  repository imported the old name, and this note is here so a reader comparing the two versions does not
  have to guess whether it moved or went.

The source observation itself is delegated through :data:`TreeDifferenceProbe` rather than performed
here: Git is the application layer's seam
(:mod:`agents_remember.memory.knowledge.read_anchors` observes anchors the same way), and a storage
module that shelled out would put a subprocess on a read path whose whole persistence argument is that
it only ever issues a ``SELECT``. The observation vocabulary it produces travels for the same reason it
is shared -- :mod:`agents_remember.memory.knowledge.tree_observation` -- and is re-exported here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents_remember.memory.knowledge.diff import DiffComparison, DiffItemComparison
from agents_remember.memory.knowledge.diff_attribution import (
    AttributionReader,
    MappingFact,
    SideInspection,
    licenses_absence,
    partition_attribution,
    unavailable_attribution,
)
from agents_remember.memory.knowledge.tree_observation import (
    TreeChange,
    TreeDifferenceProbe,
    TreePaths,
    TreeSide,
    no_tree_difference_probe,
)
from agents_remember.models.knowledge.diff import (
    DiffItemKind,
    DiffLimitation,
    DiffOmissionReason,
    DisplayFilter,
    KnowledgeDiffExpansion,
    KnowledgeDiffItem,
    OmittedChanges,
    SourceAttribution,
)

__all__ = [
    "DIFF_EXPANSION_REFERENCE",
    "DIFF_LIMITATION_ORDER",
    "AttributionReader",
    "DiffDisplay",
    "MappingFact",
    "SideInspection",
    "SourceObservation",
    "TreeChange",
    "TreeDifferenceProbe",
    "TreePaths",
    "build_display",
    "licenses_absence",
    "no_tree_difference_probe",
    "partition_attribution",
    "unavailable_attribution",
]

# The reference a response publishes for the whole comparison. It names the operation and the
# binding it belongs to rather than a filesystem location, because the expansion is a *request* a
# caller can act on and not a cached artifact whose freshness would have to be trusted.
DIFF_EXPANSION_REFERENCE = "diff_knowledge_scope:full-selected-candidate-source-diff"

# The command this operation's expansion reference describes. It is stated in full so the reference
# is reproducible without reading this module: the two tree objects are substituted, never a branch,
# a working tree or ``HEAD``, because those name whatever is checked out now rather than the two
# snapshots the comparison was between.
#
# It is the **same delimiter-safe interface the measurement itself reads** (``--raw -z``), and that is
# not a detail: the line-oriented ``--name-only`` form quotes and escapes a pathname containing a tab
# or a newline, so a caller who ran the advertised command would hold a different string from the
# address this response lists and from the address the same file is expanded by. Advertising an
# interface that loses the identity the response just preserved would make the boundary example --
# "a tab/newline filename remains the same address used for file expansion" -- false in the one place
# a reader acts on it.
TREE_DIFF_COMMAND = "git diff --raw -z --no-renames {before_tree} {after_tree}"

# The declared order limitations are reported in. It is fixed so two responses that established the
# same limits present them identically, whatever order their items happened to be built in.
DIFF_LIMITATION_ORDER: tuple[DiffLimitation, ...] = (
    "display_filtered",
    "records_present_outside_the_selection",
    "unattributed_changed_paths",
    "unknown_attribution_changed_paths",
    "no_semantic_assessment_performed",
)


@dataclass(frozen=True)
class SourceObservation:
    """The source half one display is built from: the two bound sides and the seams that read them.

    They travel together because they are one measurement. The probe observes the two trees once, and
    the attribution reader is handed exactly that observation, so the partition's denominator and the
    expansion's own path lists cannot come from two different measurements of the same pair.

    ``attribution`` is required rather than defaulted: a comparison that reports source changes has
    always measured *whether* they are attributed, and a display built without that measurement would
    have to render its absence as one of the buckets. A reader whose own measurement was not made
    returns :func:`unavailable_attribution`, which is the state that says exactly that.
    """

    probe: TreeDifferenceProbe
    before: TreeSide
    after: TreeSide
    attribution: AttributionReader


@dataclass(frozen=True)
class DiffDisplay:
    """One built display: what is shown, what was left out, what the expansion points at."""

    items: tuple[KnowledgeDiffItem, ...]
    omissions: tuple[OmittedChanges, ...]
    limitations: tuple[DiffLimitation, ...]
    expansion: KnowledgeDiffExpansion
    attribution: SourceAttribution


def build_display(
    comparison: DiffComparison,
    *,
    display_filter: DisplayFilter | None,
    source: SourceObservation,
) -> DiffDisplay:
    """Build one display of a comparison: the shown items, the omissions and the expansion.

    The filter is applied to the union and never to the comparison: ``comparison.items`` is the whole
    selected union and stays that way, which is what keeps the raw and displayed totals two different
    numbers a caller can compare.

    The source is observed **once**, here, and the same :class:`TreePaths` is handed to the
    attribution reader the caller supplied. That is what makes the partition's denominator and the
    expansion's own path list two renderings of one measurement rather than two measurements that
    could disagree.
    """

    shown, filtered_out = _apply_filter(comparison, display_filter)
    observed = source.probe(source.before, source.after)
    partition = source.attribution(observed)
    omissions: tuple[OmittedChanges, ...] = (
        *_unselected_omissions(comparison),
        *_attribution_omissions(partition),
        *filtered_out,
    )
    limitations: tuple[DiffLimitation, ...] = tuple(
        limitation
        for limitation in DIFF_LIMITATION_ORDER
        if _declared(limitation, omissions, partition)
    )
    return DiffDisplay(
        items=shown,
        omissions=omissions,
        limitations=limitations,
        expansion=_expansion(
            observed=observed,
            before=source.before,
            after=source.after,
            attribution=partition,
        ),
        attribution=partition,
    )


def _declared(
    limitation: DiffLimitation,
    omissions: Sequence[OmittedChanges],
    attribution: SourceAttribution,
) -> bool:
    """Return whether one limitation is established by the omissions and partition beside it.

    ``no_semantic_assessment_performed`` is unconditional: it is not established by an omission but
    by the operation's contract, and it is always declared. ``unknown_attribution_changed_paths`` has
    two producers and either one establishes it: an omission counting the paths a completed
    measurement could not attribute, or an attribution measurement that was never made -- where there
    is no population to count and the limit is the missing measurement itself.
    """

    if limitation == "unknown_attribution_changed_paths":
        return attribution.state != "measured" or any(
            omission.reason == "attribution_not_determined" for omission in omissions
        )
    reason = _LIMITATION_REASONS.get(limitation)
    if reason is None:
        return True
    return any(omission.reason == reason for omission in omissions)


# The omission each limitation advertises, as one table. Three of the five limitations are
# established by an omission and the other two are not: ``no_semantic_assessment_performed`` is a
# statement about the operation's own contract, and ``unknown_attribution_changed_paths`` has a second
# producer (a measurement that was not made at all, which counts nothing), so both are decided by
# ``_declared`` rather than by a reason row here. A limitation with no reason row that had no other
# producer would be one this response declared without having established it -- which the result model
# refuses at construction, so the absence of a row here is never a quiet pass.
_LIMITATION_REASONS: Mapping[DiffLimitation, DiffOmissionReason] = {
    "display_filtered": "outside_the_display_filter",
    "records_present_outside_the_selection": "present_outside_the_declared_selection",
    "unattributed_changed_paths": "change_not_attributed_to_a_recorded_realization",
}


# --- the display filter ---------------------------------------------------------------------


def _apply_filter(
    comparison: DiffComparison, display_filter: DisplayFilter | None
) -> tuple[tuple[KnowledgeDiffItem, ...], tuple[OmittedChanges, ...]]:
    """Return the items the filter displays, and the one omission that filter produced.

    The guard below is *"was a display decision declared at all"* and not *"what does the display
    decision do"*: an absent filter and a filter that named no roles are the same state, so both are
    returned whole and neither produces an omission. What actually narrows the display is the
    suppression branch beneath the guard. Both halves were measured in fix round 1: inverting the
    guard raises ``AttributeError`` on the unfiltered comparison, where ``display_filter`` is
    ``None``; neutering the suppression branch instead leaves every displayed total equal to its
    comparison total, which is the state the role-filter case kills on
    ``displayed_total < items_total``.
    """

    roles = frozenset(() if display_filter is None else display_filter.realization_roles)
    if not roles:
        return tuple(_item(entry) for entry in comparison.items), ()
    suppressed = [
        entry
        for entry in comparison.items
        if entry.kind == "realization" and _authored_role(entry) not in roles
    ]
    shown = tuple(
        _item(entry)
        for entry in comparison.items
        if entry.kind != "realization" or _authored_role(entry) in roles
    )
    if not suppressed:
        return shown, ()
    return (
        shown,
        (
            OmittedChanges(
                reason="outside_the_display_filter",
                item_kind="realization",
                omitted_count=len(suppressed),
                detail=(
                    f"{_count(len(suppressed), 'realization claim')} whose authored role is outside "
                    "the declared display filter; the comparison selected them and this display does "
                    "not show them, so the filter narrows the display and not the comparison"
                ),
            ),
        ),
    )


def _authored_role(entry: DiffItemComparison) -> str | None:
    """Return the role one realization item was authored with, from whichever side holds it.

    A removed claim's role is the role its author recorded on the baseline; an added claim's is the
    role its author recorded on the candidate. Reading it from the present side is what lets one
    filter rule apply to both directions of a change without a second rule for removals.
    """

    for item in (entry.after, entry.before):
        if item is not None and item.role is not None:
            return str(item.role)
    return None


# --- omissions ------------------------------------------------------------------------------


def _unselected_omissions(comparison: DiffComparison) -> tuple[OmittedChanges, ...]:
    """Return one omission per kind for records the other side held but did not select."""

    unselected = comparison.items_with_coverage("present_outside_selection")
    per_kind: dict[DiffItemKind, int] = {}
    for entry in unselected:
        per_kind[entry.kind] = per_kind.get(entry.kind, 0) + 1
    return tuple(
        OmittedChanges(
            reason="present_outside_the_declared_selection",
            item_kind=kind,
            omitted_count=count,
            detail=(
                f"{_count(count, _KIND_NOUNS[kind])} the other snapshot holds and its declared "
                "selection did not reach; this is a fact about the two selections and not a "
                "deletion, and selecting the path that reaches it brings the record into the union"
            ),
        )
        for kind, count in sorted(per_kind.items(), key=lambda entry: _kind_order(entry[0]))
    )


def _attribution_omissions(attribution: SourceAttribution) -> tuple[OmittedChanges, ...]:
    """Return one omission per attribution bucket that holds any measured changed path.

    Both buckets are omissions *from the knowledge half* and neither is a judgement about the change:
    the confirmed-unregistered bucket is a conclusion this measurement reached -- nobody registered
    this path -- and the undetermined bucket is the statement that no conclusion was available. They
    are two omissions with two reasons rather than one count, because a reader who saw them summed
    could not tell "nobody registered this" from "nobody looked".
    """

    unregistered = attribution.confirmed_unregistered_paths
    undetermined = attribution.unknown_attribution_paths
    omissions: list[OmittedChanges] = []
    if unregistered:
        omissions.append(
            OmittedChanges(
                reason="change_not_attributed_to_a_recorded_realization",
                item_kind=None,
                omitted_count=len(unregistered),
                detail=(
                    f"{_count(len(unregistered), 'changed path')} between the two code trees that no "
                    "registered realization claim resolves, in snapshots that were completely "
                    "inspected: this response has no knowledge half for them, and they are listed in "
                    "the expansion rather than dropped. No assessment of their consequence is made or "
                    "implied here"
                ),
            )
        )
    if undetermined:
        omissions.append(
            OmittedChanges(
                reason="attribution_not_determined",
                item_kind=None,
                omitted_count=len(undetermined),
                detail=(
                    f"{_count(len(undetermined), 'changed path')} whose attribution could not be "
                    "determined, because a required snapshot or scope was not completely inspected "
                    "and cannot be read as one that registered nothing: the paths are listed in the "
                    "expansion, the snapshot that was not inspected is named beside this response, "
                    "and no conclusion is drawn about them either way"
                ),
            )
        )
    return tuple(omissions)


# The noun each item kind is named by in a prose omission. It is a table rather than an f-string so
# a kind added later is named by its own word instead of a neighbouring kind's.
_KIND_NOUNS: dict[DiffItemKind, str] = {
    "invariant": "invariant revision",
    "family": "family revision",
    "membership": "family membership",
    "realization": "realization claim",
    "advertised_family": "advertised family link",
}

_KIND_ORDER: dict[DiffItemKind, int] = {
    "invariant": 1,
    "family": 2,
    "membership": 3,
    "realization": 4,
    "advertised_family": 5,
}


def _kind_order(kind: DiffItemKind) -> int:
    return _KIND_ORDER[kind]


def _count(count: int, noun: str) -> str:
    """Return one counted noun phrase, so a detail reads as a measurement and not as a template."""

    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --- the attribution partition, and the reader seam the display is handed ---------------------
#
# The partition itself lives next door, in `agents_remember.memory.knowledge.diff_attribution`, and
# every name below is re-exported from it: this module's file-size budget is better spent on the
# display, the partition is a responsibility of its own, and an importer that has always read these
# names from here keeps working. There is one implementation and it is that module's.

# --- the expansion --------------------------------------------------------------------------


def _expansion(
    *,
    observed: TreePaths,
    before: TreeSide,
    after: TreeSide,
    attribution: SourceAttribution,
) -> KnowledgeDiffExpansion:
    """Return the reference to the full selected-candidate source diff.

    The changed-path set comes from the one observation the caller passed in, so the omission counts
    and the expansion's own path lists are two renderings of the same measurement rather than two
    measurements that could disagree. The command is always published, with the two tree ids
    substituted: a caller can reproduce the diff itself even when this operation did not observe it,
    and a command naming the two requested trees is what "never substitute another HEAD" means in
    practice.

    The three path lists are the partition's own buckets and nothing else, so a path that a recorded
    claim names but the two trees agree at is listed in none of them -- it is context, not a change.
    """

    return KnowledgeDiffExpansion(
        reference=DIFF_EXPANSION_REFERENCE,
        command=TREE_DIFF_COMMAND.format(
            before_tree=before.tree_id or "<no baseline tree requested>",
            after_tree=after.tree_id or "<no candidate tree requested>",
        ),
        before_root=before.root,
        after_root=after.root,
        before_code_tree_id=before.tree_id,
        after_code_tree_id=after.tree_id,
        attributed_changed_paths=attribution.attributed_paths,
        unattributed_changed_paths=attribution.confirmed_unregistered_paths,
        unknown_attribution_changed_paths=attribution.unknown_attribution_paths,
        attribution=attribution,
        detail=_expansion_detail(observed, attribution),
    )


def _expansion_detail(observed: TreePaths, attribution: SourceAttribution) -> str:
    """Return what the expansion states about itself: the measurement, its limits, and nothing more.

    A partial observation states its limit *here* as well as counting less, because the counts beside
    this sentence describe only the paths that could be carried: a reader who is not told that two
    changed paths could not be named would read a smaller change set as the whole one. The unavailable
    case reports the observation's own reason verbatim, as it always has.
    """

    if not observed.available:
        return observed.detail
    stated = _stated_attribution(observed, attribution)
    if not observed.partial:
        return stated
    return f"{stated} This observation is partial: {observed.detail}"


def _stated_attribution(observed: TreePaths, attribution: SourceAttribution) -> str:
    """Return the sentence the expansion publishes about its own attribution accounting."""

    if attribution.state != "measured":
        return (
            f"the two code trees differ at {len(observed.paths)} path(s); this comparison measured no "
            "attribution for them, so none of them is reported as attributed, confirmed unregistered "
            f"or undetermined rather than as an empty set. {attribution.detail}"
        )
    return (
        f"the two code trees differ at {len(observed.paths)} path(s); "
        f"{attribution.attributed_total} are attributed by a registered realization claim, "
        f"{attribution.confirmed_unregistered_total} are confirmed to have no valid registered "
        f"attribution, and {attribution.unknown_attribution_total} are of undetermined attribution. "
        "This reference names the whole comparison so a filtered or partial response can be expanded "
        "rather than trusted"
    )


# --- one union item, typed ------------------------------------------------------------------


def _item(entry: DiffItemComparison) -> KnowledgeDiffItem:
    """Return one union item as the typed model a caller reads."""

    return KnowledgeDiffItem(
        item_id=entry.item_id,
        kind=entry.kind,
        coverage=entry.coverage,
        record_transition=entry.record_transition,
        before=entry.before,
        after=entry.after,
        before_selected=entry.before_selected,
        after_selected=entry.after_selected,
        record_id=entry.record_id,
        revision_id=entry.revision_id,
        changed_fields=entry.changed_fields,
        reached_via=entry.reached_via,
        source_change=entry.source_change,
    )
