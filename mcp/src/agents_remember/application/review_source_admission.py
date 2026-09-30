"""Which measurement admits one path into a source-content read, or the refusal that none does (ICR-R03).

The content read opens exactly two populations, and this module is where each is proven:

* **A changed path.** The requested generation's own change set lists it; or, when that pair could not
  be measured, the change set this leaf's review publishes does. Either way the path is a change of a
  measured pair, and its entry's own status travels with it.
* **An attributed unchanged path.** The requested pair *was* measured and does not list the path, and
  a realization recorded in that comparison's own knowledge is anchored at it
  (:mod:`agents_remember.application.review_source_realization_link` answers that). It is opened as
  context for the realization, stated as ``unchanged``, and never becomes an inventory entry or a
  count: this module adds nothing to the inventory it reads.

Every other path is refused by name -- a path no change set lists and no recorded realization of the
comparison links, and any path at all while the requested pair is unmeasured (then "unchanged" is not
a fact this read holds). That is what keeps the route a comparison-bound read rather than a general
file reader over arbitrary trees.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
)
from agents_remember.application.review_source_inventory import (
    review_inventory,
    source_tree_side,
)
from agents_remember.application.review_source_realization_link import (
    RealizationLink,
    recorded_realization_link,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import (
    ReviewChangedFile,
    ReviewRefusal,
    ReviewSourceInventory,
)
from agents_remember.models.knowledge.review_source_content import (
    ReviewSourceAdmission,
    ReviewSourceContentRequest,
    ReviewSourceExpansionStatus,
    ReviewSourcePathBound,
)

__all__ = ["SourceAdmission", "admit_source_path", "bounded_input"]

_CHANGED_DETAIL = "a measured change set lists this path as changed"


@dataclass(frozen=True)
class SourceAdmission:
    """Which measurement admitted one path, and the facts the expansion states about it.

    ``entry`` is the inventory's own entry when the requested generation's change set listed the path,
    and ``None`` otherwise: for a path the leaf's change set bounded (the requested pair's status was
    not measured) and for an attributed unchanged path (the pair was measured and lists no entry).
    """

    entry: ReviewChangedFile | None
    path_bound: ReviewSourcePathBound
    path_bound_detail: str
    admission: ReviewSourceAdmission = "changed"
    admission_detail: str = _CHANGED_DETAIL

    @property
    def status(self) -> ReviewSourceExpansionStatus:
        """The entry's measured status, ``unchanged`` for attributed context, ``unknown`` otherwise.

        ``unknown`` is the unmeasured pair: each side is still read on its own, and the status says the
        change classification was not made rather than guessing one from the bytes that are there.
        """

        if self.admission == "attributed_unchanged":
            return "unchanged"
        return "unknown" if self.entry is None else self.entry.status

    @property
    def mode_change(self) -> bool:
        return False if self.entry is None else self.entry.mode_change


def admit_source_path(
    config: McpRuntimeConfig,
    request: ReviewSourceContentRequest,
    resolved: ReviewCandidateResolution,
    inventory: ReviewSourceInventory,
) -> SourceAdmission | ReviewRefusal:
    """The measurement that admits the requested path, or the refusal that none does.

    The requested generation's own change set is asked first. When it was measured and does not list
    the path, the only remaining admission is a realization the comparison's knowledge records there.
    When it could not be measured, the change set this leaf's review publishes bounds the request
    instead, and no unchanged path is admitted at all.
    """

    entry = _entry_for(inventory, request.path)
    if entry is not None:
        return SourceAdmission(
            entry=entry,
            path_bound="requested_generation",
            path_bound_detail=(
                "the requested generation's own change set is the measurement that lists this path"
            ),
        )
    if inventory.state == "measured":
        return _attributed_or_refused(
            request, inventory, recorded_realization_link(config, request, resolved)
        )
    leaf = review_inventory(
        source_tree_side(resolved.baseline_code_tree_id, resolved.baseline_code_root),
        source_tree_side(resolved.candidate_code_tree_id, resolved.candidate_code_root),
    )
    if leaf.state == "measured" and _entry_for(leaf, request.path) is not None:
        return SourceAdmission(
            entry=None,
            path_bound="leaf_change_set",
            path_bound_detail=(
                f"the requested generation could not be measured ({inventory.detail}), so the "
                f"change set this leaf's review publishes -- its recorded baseline against the "
                f"candidate tree it binds now, {leaf.listed_total} changed path(s) -- is the "
                "measurement that lists this path"
            ),
        )
    return _unconfined(request, inventory, leaf)


def _attributed_or_refused(
    request: ReviewSourceContentRequest,
    inventory: ReviewSourceInventory,
    link: RealizationLink,
) -> SourceAdmission | ReviewRefusal:
    """Admit a measured-unchanged path that the comparison's recorded realization links, or refuse."""

    if not link.linked:
        if not link.determined:
            return _link_undetermined(
                request, inventory, link.detail, never_initialized=link.never_initialized
            )
        return _not_listed(request, inventory, link.detail)
    sides = " and ".join(link.linking_sides)
    return SourceAdmission(
        entry=None,
        path_bound="requested_generation",
        path_bound_detail=(
            f"the requested generation's own change set was measured ({inventory.listed_total} "
            "changed path(s)) and does not list this path, so the path is unchanged between the "
            "requested trees and is not an entry of that inventory"
        ),
        admission="attributed_unchanged",
        admission_detail=(
            f"a realization or proof recorded for the path in the comparison's {sides} knowledge "
            f"is anchored at this unchanged path ({link.detail}); it is opened as that entry's "
            "context at the requested trees and is not counted as a changed file"
        ),
    )


def _unconfined(
    request: ReviewSourceContentRequest,
    requested: ReviewSourceInventory,
    leaf: ReviewSourceInventory,
) -> ReviewRefusal:
    """The refusal for a path no measured change set admits while the requested pair could not be read."""

    bound = (
        "the change set this leaf's review publishes lists "
        f"{leaf.listed_total} changed path(s) and does not list this one"
        if leaf.state == "measured"
        else (
            "the change set this leaf's review publishes could not be measured either "
            f"({leaf.detail}), so no measurement admits this path"
        )
    )
    return refusal(
        "source_content_unresolved",
        (
            f"the requested after generation could not be measured against this leaf's recorded "
            f"baseline ({requested.detail}), and {bound}: no content was read for the requested "
            f"path {request.path!r}. An entry is expanded from a measured change set -- the requested "
            "generation's "
            "or, when that cannot be measured, the one this leaf's review publishes -- and this "
            "route reads no path outside one"
        ),
        next_action=(
            "expand a path the leaf's own inventory listed, or reopen the review so the requested "
            "generation is measured again"
        ),
        offending_input=bounded_input(request.path),
    )


def _not_listed(
    request: ReviewSourceContentRequest, inventory: ReviewSourceInventory, link_detail: str
) -> ReviewRefusal:
    """The refusal for a path the measured pair does not list and no recorded realization links."""

    return refusal(
        "source_content_unresolved",
        (
            f"the requested path {request.path!r} is not one of the {inventory.listed_total} changed "
            "path(s) this surface measured between the requested trees, and no realization recorded "
            f"in the comparison's knowledge links it ({link_detail}), so no content was read for it; "
            "this route reads an inventory entry, or an unchanged path a realization of the same "
            "comparison is anchored at, and no other path"
        ),
        next_action=(
            "expand a path the inventory listed for this generation or one a realization of this "
            "comparison records, or reopen the review if the generation has moved"
        ),
        offending_input=bounded_input(request.path),
    )


def _link_undetermined(
    request: ReviewSourceContentRequest,
    inventory: ReviewSourceInventory,
    link_detail: str,
    *,
    never_initialized: bool = False,
) -> ReviewRefusal:
    """The refusal for an unlisted path whose link could not be determined from unreadable knowledge.

    It never claims the path is unlinked: some knowledge this comparison binds was not read, so the
    only true statements are which part was not read and why, and the remedy is to that cause.
    """

    return refusal(
        "source_content_unresolved",
        (
            f"the requested path {request.path!r} is not one of the {inventory.listed_total} changed "
            "path(s) this surface measured between the requested trees, and whether a realization "
            f"recorded in the comparison's knowledge links it could not be determined ({link_detail}); "
            "no content was read for it, because an unchanged path is opened only on an established "
            "link and an unreadable snapshot establishes neither a link nor its absence"
        ),
        next_action=(_INITIALIZE if never_initialized else _RESTORE),
        offending_input=bounded_input(request.path),
    )


# The remedy for a half that exists but could not be read, and for knowledge that was never created
# (ICR-L43 review R2 O1, routed to MIK-R31 rule 6): the usable step names the cause.
_RESTORE = (
    "restore or repair the knowledge snapshot the detail names as unreadable -- the leaf's "
    "own knowledge halves, or the retained snapshot of the named comparison generation -- "
    "then expand the path again; this generation's changed paths stay expandable meanwhile"
)
_INITIALIZE = (
    "initialize this leaf's knowledge -- the snapshot files the detail names do not exist, so "
    "it was never created -- then expand the path again; this generation's changed paths stay "
    "expandable meanwhile"
)


def _entry_for(inventory: ReviewSourceInventory, path: str) -> ReviewChangedFile | None:
    """The inventory's own entry for one path, matched exactly as the address it is."""

    for entry in inventory.entries:
        if entry.path == path:
            return entry
    return None


def bounded_input(text: str) -> str:
    """One offending input, bounded to the field that carries it.

    A path is bounded by the path limit and an offending input by the shorter reference limit, so a
    very long path is truncated here rather than failing the refusal that names it.
    """

    return text if len(text) <= 512 else f"{text[:508]}..."
