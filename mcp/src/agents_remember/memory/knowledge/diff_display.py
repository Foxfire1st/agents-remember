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

The unattributed-path computation is delegated through :data:`TreeDifferenceProbe` rather than
performed here: Git is the application layer's seam (:mod:`agents_remember.memory.knowledge.read_anchors`
observes anchors the same way), and a storage module that shelled out would put a subprocess on a
read path whose whole persistence argument is that it only ever issues a ``SELECT``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from agents_remember.memory.knowledge.diff import DiffComparison, DiffItemComparison
from agents_remember.models.knowledge.diff import (
    DiffItemKind,
    DiffLimitation,
    DiffOmissionReason,
    DisplayFilter,
    KnowledgeDiffExpansion,
    KnowledgeDiffItem,
    OmittedChanges,
)

__all__ = [
    "DIFF_EXPANSION_REFERENCE",
    "DIFF_LIMITATION_ORDER",
    "DiffDisplay",
    "TreeDifferenceProbe",
    "TreePaths",
    "attributed_paths",
    "build_display",
    "no_tree_difference_probe",
]

# The reference a response publishes for the whole comparison. It names the operation and the
# binding it belongs to rather than a filesystem location, because the expansion is a *request* a
# caller can act on and not a cached artifact whose freshness would have to be trusted.
DIFF_EXPANSION_REFERENCE = "diff_knowledge_scope:full-selected-candidate-source-diff"

# The command this operation's expansion reference describes. It is stated in full so the reference
# is reproducible without reading this module: the two tree objects are substituted, never a branch,
# a working tree or ``HEAD``, because those name whatever is checked out now rather than the two
# snapshots the comparison was between.
TREE_DIFF_COMMAND = "git diff --name-only --no-renames {before_tree} {after_tree}"

# The declared order limitations are reported in. It is fixed so two responses that established the
# same limits present them identically, whatever order their items happened to be built in.
DIFF_LIMITATION_ORDER: tuple[DiffLimitation, ...] = (
    "display_filtered",
    "records_present_outside_the_selection",
    "unattributed_changed_paths",
    "no_semantic_assessment_performed",
)


@dataclass(frozen=True)
class TreeSide:
    """One side's source binding: the exact code tree, and the repository root it lives in.

    Both are carried because they answer different questions: the tree id is the *identity* the
    comparison resolved against, and the root is *where* a caller runs the command the expansion
    publishes. ``tree_id`` is ``None`` when the side requested no source resolution, which is a
    supported state and not a failure -- the record half of the comparison is complete without it.
    """

    tree_id: str | None
    root: str | None


@dataclass(frozen=True)
class TreePaths:
    """The paths two code trees differ at, as the probe observed them.

    ``available`` is separate from ``paths`` on purpose: a probe that could not run (an absent root,
    a tree this repository does not hold) has not observed *no changes*, and reporting its silence
    as "nothing changed between the trees" would be a fabricated fact. An unavailable probe
    therefore contributes no expansion at all and says so through its ``detail``.
    """

    available: bool
    paths: tuple[str, ...] = ()
    detail: str = ""


# The Git seam, narrowed to one question. It is a callable rather than a class so the application
# layer can pass the same kind of seam the anchor resolver is, and so a case can substitute an
# observation without a repository.
TreeDifferenceProbe = Callable[[TreeSide, TreeSide], TreePaths]


def no_tree_difference_probe(before: TreeSide, after: TreeSide) -> TreePaths:
    """Return the probe that observes nothing, for a comparison whose sides named no code tree.

    It is the honest answer for a comparison with no source half: there is no pair of trees to
    compare, so nothing was observed, and the expansion says exactly that instead of reporting an
    empty change set as though it had been measured.
    """

    if before.tree_id is None or after.tree_id is None:
        return TreePaths(
            available=False,
            detail=(
                "at least one side named no exact code tree, so no source expansion was observed; "
                "the record half of this comparison is complete and the source half was not "
                "requested for every side"
            ),
        )
    return TreePaths(available=True)


@dataclass(frozen=True)
class DiffDisplay:
    """One built display: what is shown, what was left out, what the expansion points at."""

    items: tuple[KnowledgeDiffItem, ...]
    omissions: tuple[OmittedChanges, ...]
    limitations: tuple[DiffLimitation, ...]
    expansion: KnowledgeDiffExpansion


def build_display(
    comparison: DiffComparison,
    *,
    display_filter: DisplayFilter | None,
    probe: TreeDifferenceProbe,
    before: TreeSide,
    after: TreeSide,
) -> DiffDisplay:
    """Build one display of a comparison: the shown items, the omissions and the expansion.

    The filter is applied to the union and never to the comparison: ``comparison.items`` is the whole
    selected union and stays that way, which is what keeps the raw and displayed totals two different
    numbers a caller can compare.
    """

    shown, filtered_out = _apply_filter(comparison, display_filter)
    observed = probe(before, after)
    omissions: tuple[OmittedChanges, ...] = (
        *_unselected_omissions(comparison),
        *_unattributed_omission(comparison, observed),
        *filtered_out,
    )
    limitations: tuple[DiffLimitation, ...] = tuple(
        limitation for limitation in DIFF_LIMITATION_ORDER if _declared(limitation, omissions)
    )
    return DiffDisplay(
        items=shown,
        omissions=omissions,
        limitations=limitations,
        expansion=_expansion(comparison, observed=observed, before=before, after=after),
    )


def _declared(limitation: DiffLimitation, omissions: Sequence[OmittedChanges]) -> bool:
    """Return whether one limitation is established by the omissions beside it.

    ``no_semantic_assessment_performed`` is unconditional: it is not established by an omission but
    by the operation's contract, and it is always declared.
    """

    reason = _LIMITATION_REASONS.get(limitation)
    if reason is None:
        return True
    return any(omission.reason == reason for omission in omissions)


# The omission each limitation advertises, as one table. Two of the four limitations are established
# by an omission and the other two are not: ``no_semantic_assessment_performed`` is a statement about
# the operation's own contract, and a limitation with no reason row would be one this response
# declared without having established it -- which the result model refuses at construction, so the
# absence of a row here is never a quiet pass.
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


def _unattributed_omission(
    comparison: DiffComparison, observed: TreePaths
) -> tuple[OmittedChanges, ...]:
    """Return the omission for changed paths no recorded realization attributes, if any exist."""

    if not observed.available:
        return ()
    unattributed = _unattributed_paths(comparison, observed.paths)
    if not unattributed:
        return ()
    return (
        OmittedChanges(
            reason="change_not_attributed_to_a_recorded_realization",
            item_kind=None,
            omitted_count=len(unattributed),
            detail=(
                f"{_count(len(unattributed), 'changed path')} between the two code trees that no "
                "recorded realization claim attributes: this response has no knowledge half for "
                "them, and they are listed in the expansion rather than dropped. No assessment of "
                "their consequence is made or implied here"
            ),
        ),
    )


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


# --- the expansion --------------------------------------------------------------------------


def attributed_paths(comparison: DiffComparison) -> tuple[str, ...]:
    """Return every path either selected set attributes, sorted and deduplicated.

    A path is attributed when a realization claim the comparison selected names it, on **either**
    side. The two sides are unioned rather than intersected, and that is the exact question the
    packet's own omission is about: ``unattributed_changed_path`` means a change no recorded
    realization attributes at all. A path the baseline's claim names and the candidate's selection no
    longer reaches is attributed -- the relationship's removal is its own item in the union, reported
    with its before-side source -- so counting it as unattributed would tell a reviewer that the
    earlier code had no recorded attribution when it had exactly that.
    """

    before_paths = _claim_paths(comparison, side="before")
    after_paths = _claim_paths(comparison, side="after")
    return tuple(sorted(before_paths | after_paths))


def _claim_paths(comparison: DiffComparison, *, side: str) -> frozenset[str]:
    paths: set[str] = set()
    for entry in comparison.items:
        if entry.kind != "realization":
            continue
        item = entry.before if side == "before" else entry.after
        if item is not None and item.anchor is not None:
            paths.add(item.anchor.path)
    return frozenset(paths)


def _unattributed_paths(
    comparison: DiffComparison, changed_paths: Sequence[str]
) -> tuple[str, ...]:
    """Return the changed paths no selected realization claim attributes."""

    attributed = set(attributed_paths(comparison))
    return tuple(sorted(path for path in changed_paths if path not in attributed))


def _expansion(
    comparison: DiffComparison,
    *,
    observed: TreePaths,
    before: TreeSide,
    after: TreeSide,
) -> KnowledgeDiffExpansion:
    """Return the reference to the full selected-candidate source diff.

    The changed-path set comes from the one observation the caller passed in, so the omission count
    and the expansion's own path list are two renderings of the same measurement rather than two
    measurements that could disagree. The command is always published, with the two tree ids
    substituted: a caller can reproduce the diff itself even when this operation did not observe it,
    and a command naming the two requested trees is what "never substitute another HEAD" means in
    practice.
    """

    attributed = attributed_paths(comparison)
    unattributed = _unattributed_paths(comparison, observed.paths) if observed.available else ()
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
        attributed_changed_paths=attributed,
        unattributed_changed_paths=unattributed,
        detail=(
            observed.detail
            if not observed.available
            else (
                f"the two code trees differ at {len(observed.paths)} path(s); "
                f"{len(attributed)} are attributed by a realization claim both sides selected and "
                f"{len(unattributed)} are not. This reference names the whole comparison so a "
                "filtered or partial response can be expanded rather than trusted"
            )
        ),
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
