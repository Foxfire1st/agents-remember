"""No authored line: retractions state what was read, and moved-to sides outside the union stay out.

Reach cases beside these read a relationship the reviewed union holds. This module owns the
shapes the old line search used to follow past the union's page -- each authored through the
public store operations and read back through the production composition -- and pins what the
rule-1 display does with them instead:

* two further revisions each recording a realization: the withdrawn association displays as a
  retraction stating that no relationship citing its revision was read, and neither moved-to
  address (which cites a revision outside the reviewed union) displays as a movement;
* a claim at an intermediate further revision: the same retraction, with the same sentence;
* a membership recorded again under another family revision: the withdrawn baseline side
  displays as its own retraction, and no sibling member is presented as this member's record.

Text records hold no predecessor edge (MIK-R74 rule 1), so no line is read, no head is
established, and no succession, split or merge is stated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from diff_scope_test_support import rebuild_after_tree, replace_after_tree
from read_scope_test_support import AUXILIARY_PATH, SYNCHRONIZATION_PATH
from test_knowledge_review_relationship_movement import (
    build_endpoint_fixture,
    realization_movements,
    review_of,
)
from test_knowledge_review_relationship_reach import build_split_head_fixture
from test_knowledge_review_source_endpoints import EndpointFixture

pytestmark = pytest.mark.evidence_unit

SPLIT_ONE_PATH = "src/verify_aux_one.py"
SPLIT_ONE_TEXT = "# anchors\nfirst authored successor\n"
SPLIT_TWO_PATH = "src/verify_aux_two.py"
SPLIT_TWO_TEXT = "# anchors\nsecond authored successor\n"
INTERMEDIATE_PATH = "src/verify_aux_mid.py"
INTERMEDIATE_TEXT = "# anchors\nintermediate authored successor\n"


def build_split_with_relationships_fixture(directory: Path) -> EndpointFixture:
    """One member revision's realization withdrawn, with no further row the union can read.

    The candidate drops the auxiliary claim and records nothing in its place. With no
    predecessor edge (MIK-R74 rule 1) no line is read past the withdrawn association's
    citation -- the retraction states that no relationship citing its revision was read, and no
    sentence denies a relationship the same payload displays.
    """

    endpoints = build_endpoint_fixture(directory / "split-with-relationships")
    after = rebuild_after_tree(
        directory / "split-with-relationships-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, AUXILIARY_PATH),
    )
    return replace_after_tree(endpoints, after)


def build_intermediate_descendant_fixture(directory: Path) -> EndpointFixture:
    """One member revision's realization withdrawn, with no intermediate row to stand on.

    The candidate drops the auxiliary claim; with no predecessor edge no line is read and no
    intermediate revision is established, so the association reads as a retraction stating its
    searched citation -- never a negative about an unread revision.
    """

    endpoints = build_endpoint_fixture(directory / "intermediate")
    after = rebuild_after_tree(
        directory / "intermediate-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, AUXILIARY_PATH),
    )
    return replace_after_tree(endpoints, after)


def build_family_head_membership_fixture(directory: Path) -> EndpointFixture:
    """One membership withdrawn from the direct family, with no other family recording it.

    The candidate's direct family no longer holds the auxiliary member's row. Pairing needs the
    same recorded row, so the withdrawn baseline side displays as its own retraction stating
    that no relationship citing its member revision was read -- and no sibling member of the
    old family revision is presented as this member's record.
    """

    endpoints = build_endpoint_fixture(directory / "family-head")
    after = rebuild_after_tree(
        directory / "family-head-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH,),
        omit_members=(("direct_family", "auxiliary_revision_id"),),
    )
    return replace_after_tree(endpoints, after)


def test_two_further_revisions_that_record_relationships_are_a_retraction_and_never_denied(
    tmp_path: Path,
) -> None:
    """No line is read: no moved-to claim exists in the union, the retraction is exact.

    The auxiliary claim is withdrawn and the candidate records nothing in its place. The
    withdrawn association displays as a retraction stating that no relationship citing its
    revision was read. No sentence anywhere denies a relationship the same payload displays.
    """

    endpoints = build_split_with_relationships_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1, [m.statement for m in realization_movements(payload)]
    movement = withdrawn[0]
    assert movement.after is None
    assert movement.record_id == before.auxiliary_invariant_id
    assert "no relationship citing" in movement.statement
    assert before.auxiliary_revision_id in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement
    # No sentence anywhere denies a relationship the same payload displays.
    assert not [
        movement.statement
        for movement in payload.source.relationships
        if "a relationship for none of them" in movement.statement
        or "no relationship for the same citation" in movement.statement
    ]


def test_two_unpaired_rows_are_two_absences_and_never_a_unique_head(tmp_path: Path) -> None:
    """The withdrawn association stays out, and no sentence claims a head that does not exist.

    The auxiliary claim is withdrawn and the candidate records nothing in its place, so the
    payload holds no movement for the withdrawn address -- and no sentence claims a uniquely
    established head.
    """

    endpoints = build_split_with_relationships_fixture(tmp_path)
    diff = endpoints.diff
    payload = review_of(endpoints, diff.retry_invariant_id)
    rows = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == AUXILIARY_PATH
    ]
    assert rows == []
    assert not [
        movement.statement
        for movement in payload.source.relationships
        if "uniquely established head" in movement.statement
    ]


def test_a_further_revision_outside_the_union_states_only_what_was_read(
    tmp_path: Path,
) -> None:
    """The split-only case: the retraction states its searched citation and nothing else."""

    endpoints = build_split_head_fixture(tmp_path)
    diff = endpoints.diff
    payload = review_of(endpoints, diff.retry_invariant_id)
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1
    movement = withdrawn[0]
    assert movement.after is None
    assert "no relationship citing" in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement


def test_a_relationship_at_a_further_revision_outside_the_union_is_not_displayed(
    tmp_path: Path,
) -> None:
    """No line is read past the citation: nothing is recorded where the claim was withdrawn.

    The auxiliary claim is withdrawn and the candidate records nothing in its place, so it
    displays nowhere -- and the withdrawn association reads as a retraction stating that no
    relationship citing its revision was read, under the preserved identity.
    """

    endpoints = build_intermediate_descendant_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    assert not [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path == AUXILIARY_PATH
    ], [m.statement for m in realization_movements(payload)]
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1
    movement = withdrawn[0]
    assert movement.record_id == before.auxiliary_invariant_id
    assert "no relationship citing" in movement.statement
    assert before.auxiliary_revision_id in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement
    assert not any(
        location.path == INTERMEDIATE_PATH for location in payload.source.locations
    )


def test_a_membership_recorded_again_under_another_family_revision_is_a_retraction(
    tmp_path: Path,
) -> None:
    """The family half of the no-line rule: the withdrawn side displays alone, exactly.

    The auxiliary member's row was withdrawn from the candidate's direct family and no other
    family records it. Pairing needs the same recorded row, so the baseline side displays as a
    retraction stating that no relationship citing its member revision was read, under the
    preserved family identity -- and no sibling member of the old family revision is named as
    this member's record.
    """

    endpoints = build_family_head_membership_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    withdrawn = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "membership"
        and movement.transition == "retracted"
        and any(side.member_revision_id == before.auxiliary_revision_id for side in movement.before)
    ]
    assert len(withdrawn) == 1, [
        (
            m.transition,
            [s.member_revision_id for s in m.before],
            m.after and m.after.member_revision_id,
        )
        for m in payload.source.relationships
        if m.relationship_kind == "membership"
    ]
    movement = withdrawn[0]
    assert movement.after is None
    assert movement.pairing_basis is None
    assert movement.record_id == before.direct_family.family_id
    assert movement.before[0].revision_id == before.direct_family.revision_id
    assert "no relationship citing" in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement
    # The candidate's record for this member is not this movement, and no sibling member is
    # named for it: no movement pairs this member revision with another row.
    assert not [
        other
        for other in payload.source.relationships
        if other is not movement
        and any(side.member_revision_id == before.auxiliary_revision_id for side in other.before)
        and other.after is not None
    ]
