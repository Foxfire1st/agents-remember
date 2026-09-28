"""The family-context values' own construction rules, exercised as values (ICR-R31@v1).

``ICR-R31@v1``'s composition is measured over the production entry in
``mcp/tests/test_review_family_context.py``. These cases measure the other half of the same
deliverable: the rules the *values* enforce on any caller, so that the sentences the composition
publishes cannot be built from numbers that contradict them.

Each case mutates one internally consistent value and shows the construction refusing it: a family
remainder with no way to reach it, a membership-row total that is not the sum of the rosters beside
it, a unique member total inflated by counting rows, a truncated roster page presented as a complete
one, and a side that read nothing carrying a roster. The honest value they mutate is a *value*
fixture -- it is never evidence about the store, and no case here claims to be one.
"""

from __future__ import annotations

import pytest
from agents_remember.models.knowledge.read import KnowledgeReadCounts
from agents_remember.models.knowledge.review_family_context import (
    ReviewFamilyContext,
    ReviewFamilyMemberSource,
    ReviewFamilyRevisionContext,
    ReviewFamilyRosterPage,
)
from pydantic import ValidationError

pytestmark = pytest.mark.evidence_unit


def test_a_context_whose_counts_do_not_describe_its_roster_is_refused() -> None:
    """The counts are the composition's own facts: a context claiming others cannot be built.

    Four mutations of one honest value, each of which a rendering would otherwise show as a true
    sentence: a family remainder with no way to reach it, a membership row total that is not the sum
    of the rosters beside it, a unique member total inflated by counting rows instead of revisions,
    and a returned-family count that is not the list beside it.
    """

    context = _honest_context()

    for update in (
        {"families_remaining": 1},
        {"membership_rows_total": context.membership_rows_total + 1},
        {"unique_member_revision_total": context.unique_member_revision_total + 1},
        {"families_returned": context.families_returned - 1},
    ):
        with pytest.raises(ValidationError):
            ReviewFamilyContext(**{**context.model_dump(), **update})


def test_a_roster_page_may_not_present_a_truncated_roster_as_a_complete_one() -> None:
    """The page's completeness, its cursor and the owner's own remainder are one statement."""

    side = _honest_context().entries[0].after
    assert side.page is not None
    page = side.page.model_dump()

    # A page that reports items ahead of the walk is incomplete by construction and carries the
    # cursor that reaches them; calling it complete is the truncation this guard refuses.
    counts = dict(page["counts"])
    counts.update(primary_items_total=2, primary_items_returned=1, primary_items_remaining=1)
    truncated = {**page, "counts": counts, "complete": True, "continuation": "cursor"}
    with pytest.raises(ValidationError):
        ReviewFamilyRosterPage.model_validate(truncated)

    # The same walk, honestly stated, is constructible -- so the refusal above is about the claim.
    honest = {**page, "counts": counts, "complete": False, "continuation": "cursor"}
    assert ReviewFamilyRosterPage.model_validate(honest).complete is False

    incomplete = dict(page)
    incomplete["complete"] = False
    with pytest.raises(ValidationError):
        ReviewFamilyRosterPage.model_validate(incomplete)

    wrong_total = dict(page)
    wrong_total["members_total"] = int(wrong_total["members_total"]) + 1
    with pytest.raises(ValidationError):
        ReviewFamilyRosterPage.model_validate(wrong_total)


def test_a_side_that_read_nothing_may_not_carry_a_roster() -> None:
    """A stated non-recording is not an empty roster: the two cannot be built from one value."""

    context = _honest_context()
    side = context.entries[0].before.model_dump()
    side["state"] = "not_recorded"
    with pytest.raises(ValidationError):
        ReviewFamilyRevisionContext.model_validate(side)

    recorded_without_page = context.entries[0].after.model_dump()
    recorded_without_page["page"] = None
    with pytest.raises(ValidationError):
        ReviewFamilyRevisionContext.model_validate(recorded_without_page)


def test_a_recorded_side_may_not_name_a_revision_its_family_does_not_record() -> None:
    """A recorded side's selected revision is one the family owner lists for that snapshot.

    This is the guard the fix round added: the published context carries the family's own revision
    list beside the revision it selected, so a selected revision outside that list -- a revision the
    snapshot does not hold, or one a population derivation invented -- cannot be built.
    """

    context = _honest_context()
    side = context.entries[0].after.model_dump()
    side["recorded_revision_ids"] = ["99999999-9999-4999-8999-999999999999"]
    with pytest.raises(ValidationError):
        ReviewFamilyRevisionContext.model_validate(side)

    side["recorded_revision_ids"] = []
    with pytest.raises(ValidationError):
        ReviewFamilyRevisionContext.model_validate(side)


def test_a_complete_walk_that_is_one_page_must_carry_the_whole_roster() -> None:
    """A walk the read took in one page IS the roster, so it may not carry fewer rows than it counts.

    This is the guard's real subject, and the correction in ICR-L24's fix round 3 kept it exactly
    here: a page that is complete AND the walk's first page is the whole recorded roster, so a page
    carrying none of the rows it counts would present a truncated roster as the complete one.
    """

    side = _honest_context().entries[0].after.model_dump()
    side["members"] = []
    with pytest.raises(ValidationError):
        ReviewFamilyRevisionContext.model_validate(side)


def test_a_final_page_of_a_multi_page_walk_may_carry_only_its_own_share() -> None:
    """A CONTINUED page that completes the walk carried that page's share, not every recorded row.

    ``complete`` is the walk's flag: it becomes true on the final page, and that page carries only its
    own part of the selection while the pages before it carried the rest. Requiring such a page to
    carry every recorded membership compared a page-scoped list against a revision-wide count, and on
    an ordinary multi-page roster it raised an unhandled ``ValidationError`` -- the route answered the
    reader's own continuation request with HTTP 500 (ICR-L24 fix round 3, V9). Both directions are
    pinned here: the continued page is accepted, and the SAME numbers on a first page are refused by
    the case above.
    """

    context = _honest_context()
    side = context.entries[0].after.model_dump()
    page = dict(side["page"])
    page["state"] = "continued"
    page["continued_from"] = "the cursor this walk published"
    side["page"] = page
    side["members"] = []

    built = ReviewFamilyRevisionContext.model_validate(side)
    assert built.page is not None
    assert built.page.complete is True
    assert built.page.continuation is None
    assert built.members == ()
    assert built.members_total == 1

    # The same carried rows on a walk's FIRST page are the truncation the guard refuses: this is what
    # keeps the relaxation above from becoming a hole.
    first = dict(side)
    first_page = dict(page)
    first_page.pop("continued_from")
    first_page["state"] = "first_page"
    first["page"] = first_page
    with pytest.raises(ValidationError):
        ReviewFamilyRevisionContext.model_validate(first)


def test_a_member_source_may_not_state_a_region_its_observation_does_not_support() -> None:
    """A source's locator state, locator, ranges and resolution are one fact; no range is invented.

    The honest resolved source is accepted, and each mutation is refused: ranges beside a resolution
    other than the exact recorded blob, a resolved state with no range, a whole file claimed for a
    symbol locator, ranges presented as unresolved, and a locator carried with no observed address.
    A reference without the claim's stored role or rationale is refused too.
    """

    honest = {
        "claim_id": "claim",
        "invariant_revision_id": "revision",
        "role": "enforcement",
        "rationale": "the authored explanation",
        "path": "src/shared.py",
        "recorded_source_identity": "a" * 40,
        "observed_source_identity": "a" * 40,
        "resolution": "exact_recorded_blob",
        "detail": "the requested tree holds the exact recorded blob",
        "locator": {"kind": "symbol", "language": "python", "qualified_name": "alpha"},
        "resolved_ranges": [{"kind": "line_range", "start_line": 1, "end_line": 2}],
        "locator_state": "resolved",
    }
    assert ReviewFamilyMemberSource.model_validate(honest).locator_state == "resolved"
    unobserved = {
        "claim_id": "claim",
        "invariant_revision_id": "revision",
        "role": "enforcement",
        "rationale": "the authored explanation",
        "detail": "no address was observed",
        "locator_state": "not_observed",
    }
    assert ReviewFamilyMemberSource.model_validate(unobserved).locator is None
    # The store holds a role and a rationale for every claim, so a reference without either is
    # refused rather than published with an absent explanation.
    for stored in ("role", "rationale"):
        with pytest.raises(ValidationError):
            ReviewFamilyMemberSource.model_validate(
                {key: value for key, value in unobserved.items() if key != stored}
            )

    for mutation in (
        {"resolution": "recorded_blob_mismatch"},
        {"resolved_ranges": []},
        {"resolved_ranges": [], "locator_state": "whole_file"},
        {"locator_state": "unresolved"},
        {"path": None, "resolution": None, "resolved_ranges": [], "locator_state": "unresolved"},
    ):
        with pytest.raises(ValidationError):
            ReviewFamilyMemberSource.model_validate({**honest, **mutation})


def _honest_context() -> ReviewFamilyContext:
    """One hand-built, internally consistent context used only as the base of a mutation.

    It is a *value* fixture for the model's own construction rules and nothing else: every claim
    about the composition is measured over the production entry above, never against this value.
    """

    digest = "0" * 64
    family_id = "11111111-1111-4111-8111-111111111111"
    revision_id = "22222222-2222-4222-8222-222222222222"
    member_revision_id = "33333333-3333-4333-8333-333333333333"
    member_id = "44444444-4444-4444-8444-444444444444"
    counts = KnowledgeReadCounts(
        invariant_revisions_total=1,
        family_revisions_total=1,
        memberships_total=1,
        realization_claims_total=0,
        advertised_expansions_total=0,
        primary_items_total=2,
        primary_items_returned=2,
        primary_items_remaining=0,
        distinct_source_locations_total=0,
        distinct_source_paths_total=0,
        unresolved_anchor_total=0,
    )
    page = ReviewFamilyRosterPage(
        state="first_page",
        counts=counts,
        complete=True,
        members_total=1,
        continuation=None,
    )
    guarantee = {
        "family_id": family_id,
        "revision_id": revision_id,
        "display_version": "v1",
        "joint_guarantee": "One authored guarantee.",
        "state_at_origin": "proposed",
        "provenance": {},
        "payload_digest": digest,
    }
    member = {
        "member_id": member_id,
        "invariant_revision_id": member_revision_id,
        "state": "recorded",
        "statement": "One recorded member statement.",
        "detail": "the before snapshot records this membership",
    }
    sides = {
        side: {
            "side": side,
            "family_id": family_id,
            "state": "recorded",
            "family_revision_id": revision_id,
            "recorded_revision_ids": [revision_id],
            "guarantee": guarantee,
            "members": [member],
            "members_total": 1,
            "page": page.model_dump(),
            "detail": "the roster was read whole",
        }
        for side in ("before", "after")
    }
    return ReviewFamilyContext.model_validate(
        {
            "state": "recorded",
            "detail": "every recorded family is composed",
            "entries": [
                {
                    "family_id": family_id,
                    "display_label": "one-family",
                    "label_side": "after",
                    "selection": {
                        "record_kind": "family",
                        "record_id": family_id,
                        "state": "compared",
                        "before_revision_id": revision_id,
                        "after_revision_id": revision_id,
                        "before_heads": [revision_id],
                        "after_heads": [revision_id],
                        "before_retained": [revision_id],
                        "after_retained": [revision_id],
                        "statement": "the family is read at its one recorded revision",
                    },
                    "before": sides["before"],
                    "after": sides["after"],
                    "state": "recorded",
                    "detail": "the family context is complete",
                }
            ],
            "families_total": 1,
            "families_returned": 1,
            "families_remaining": 0,
            "membership_rows_total": 2,
            "unique_member_revision_total": 1,
        }
    )
