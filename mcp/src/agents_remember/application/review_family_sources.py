"""One recorded realization claim of a family member, projected as its inspectable source reference.

The roster owner (:mod:`agents_remember.application.review_family_rosters`) reads a family
revision's members and hands each member's realization claims here. This module owns only the
projection of one claim: the claim's identity, its stored role and rationale, and what the side's own
anchor observation established about the address -- the path and blob identities, the structured
recorded locator, and the line ranges the read's anchor resolver placed that locator on.

Nothing here resolves, searches or re-anchors anything. The ranges are the resolver's own structured
values (:class:`agents_remember.models.knowledge.read.AnchorResolution`); a locator the resolver
could not place on the recorded bytes is stated as unresolved beside the resolver's own resolution,
and the diagnostic ``detail`` sentence is composed *from* the observation and never read back.
"""

from __future__ import annotations

from agents_remember.models.knowledge.read import AnchorResolution, ReadItem
from agents_remember.models.knowledge.review_family_source import (
    ReviewFamilyMemberSource,
    ReviewSourceLocatorState,
    source_locator_state,
)

__all__ = ["member_source"]


def member_source(claim: ReadItem) -> ReviewFamilyMemberSource:
    """One recorded realization claim as an inspectable, side-bound source reference."""

    anchor = claim.anchor
    if claim.role is None or claim.rationale is None:
        # The store's claim columns are NOT NULL, so a realization item without them is not a row
        # this read produced; it is refused rather than given a role or explanation it never had.
        raise RuntimeError(
            f"realization claim {claim.claim_id} was read without its stored role or rationale, "
            "which every stored claim carries; no text is supplied in their place"
        )
    return ReviewFamilyMemberSource(
        claim_id=str(claim.claim_id),
        invariant_revision_id=str(claim.invariant_revision_id),
        role=claim.role,
        rationale=claim.rationale,
        path=None if anchor is None else anchor.path,
        recorded_source_identity=None if anchor is None else anchor.recorded_source_identity,
        observed_source_identity=None if anchor is None else anchor.observed_source_identity,
        resolution=None if anchor is None else anchor.resolution,
        detail=_detail(anchor),
        locator=None if anchor is None else anchor.locator,
        resolved_ranges=() if anchor is None else anchor.resolved_ranges,
        locator_state=_locator_state(anchor),
    )


def _locator_state(anchor: AnchorResolution | None) -> ReviewSourceLocatorState:
    """Which locator fact this side's anchor observation established, by the model's own rule."""

    if anchor is None:
        return source_locator_state(None, exact=False, ranged=False)
    return source_locator_state(
        anchor.locator,
        exact=anchor.resolution == "exact_recorded_blob",
        ranged=bool(anchor.resolved_ranges),
    )


def _detail(anchor: AnchorResolution | None) -> str:
    """The diagnostic sentence one source reference has always carried, unchanged."""

    if anchor is None:
        return (
            "this read observed no address for this recorded realization, so it reports the "
            "claim's own identity and no resolution"
        )
    return (
        f"the author recorded this realization at {anchor.path} ({anchor.resolution}): "
        f"{anchor.detail}"
    )
