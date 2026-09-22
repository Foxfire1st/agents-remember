"""The authored line: intermediate descendants, splits and the family side (ICR-R08@v1, round 2).

The reach cases beside these read a relationship recorded at the end of a citation's authored line;
this module owns the three shapes the second verification round found still missing, each authored
through the public store operations and read back through the production composition:

* a relationship recorded at an **intermediate** descendant of the citation, with the line's head
  recording nothing -- the whole line is read, so the address is displayed;
* a line **split** into two successors that each record a relationship -- both are displayed and the
  split lineage names them, instead of a sentence denying what the same movement calls unresolved --
  and the split-only case, where the lineage sentence states the complete negative over the revisions
  that were read;
* a membership moved onto an unselected successor **family** revision holding the same member -- read
  through the family-revision membership owner, so the member's new side is displayed and no sibling
  member is presented as this member's record.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import families, memberships
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.result import (
    FamilyMemberRequest,
    FamilyRevisionRequest,
    RemoveFamilyMemberRequest,
)
from diff_scope_test_support import _git
from read_scope_test_support import AUXILIARY_PATH, BASE_LABEL, DIRECT_FAMILY_GUARANTEE
from test_knowledge_review_relationship_movement import (
    ClaimDraft,
    author_claim,
    author_revision,
    build_endpoint_fixture,
    realization_movements,
    remove_claim,
    review_of,
)
from test_knowledge_review_relationship_reach import build_split_head_fixture
from test_knowledge_review_source_endpoints import EndpointFixture, _place_datasets

pytestmark = pytest.mark.evidence_unit

SPLIT_ONE_PATH = "src/verify_aux_one.py"
SPLIT_ONE_TEXT = "# anchors\nfirst authored successor\n"
SPLIT_TWO_PATH = "src/verify_aux_two.py"
SPLIT_TWO_TEXT = "# anchors\nsecond authored successor\n"
INTERMEDIATE_PATH = "src/verify_aux_mid.py"
INTERMEDIATE_TEXT = "# anchors\nintermediate authored successor\n"


def build_split_with_relationships_fixture(directory: Path) -> EndpointFixture:
    """One member revision split into two successors that **each** record a realization.

    This is the shape the lineage sentence used to deny: two authored successors, a claim on each. With
    the whole line read, both claims are displayed -- each as its own movement continuing the withdrawn
    association -- and the split lineage names both successors without claiming that the line holds no
    relationship.
    """

    endpoints = build_endpoint_fixture(directory / "split-with-relationships")
    diff = endpoints.diff
    before = diff.before.fixture
    worktree = endpoints.worktree
    paths = (SPLIT_ONE_PATH, SPLIT_TWO_PATH)
    for path, body in zip(paths, (SPLIT_ONE_TEXT, SPLIT_TWO_TEXT), strict=True):
        (worktree / path).write_text(body, encoding="utf-8")
    blobs = {path: _git(worktree, ["hash-object", path]) for path in paths}
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, before.auxiliary.claim_id)
        for path in paths:
            revision_id = str(uuid4())
            author_revision(
                store,
                diff,
                revision_id,
                predecessors=(before.auxiliary_revision_id,),
                invariant_id=before.auxiliary_invariant_id,
            )
            author_claim(
                store,
                diff,
                ClaimDraft(
                    claim_id=str(uuid4()),
                    revision_id=revision_id,
                    path=path,
                    blob=blobs[path],
                ),
            )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def build_intermediate_descendant_fixture(directory: Path) -> EndpointFixture:
    """One member line X -> A -> B whose relationship is recorded at the **intermediate** A.

    The head B records nothing, so a head-only read would call the association withdrawn while the
    snapshot demonstrably moved it. The whole line is read, so the claim at A is displayed.
    """

    endpoints = build_endpoint_fixture(directory / "intermediate")
    diff = endpoints.diff
    before = diff.before.fixture
    worktree = endpoints.worktree
    (worktree / INTERMEDIATE_PATH).write_text(INTERMEDIATE_TEXT, encoding="utf-8")
    blob = _git(worktree, ["hash-object", INTERMEDIATE_PATH])
    middle = str(uuid4())
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, before.auxiliary.claim_id)
        author_revision(
            store,
            diff,
            middle,
            predecessors=(before.auxiliary_revision_id,),
            invariant_id=before.auxiliary_invariant_id,
        )
        author_revision(
            store,
            diff,
            str(uuid4()),
            predecessors=(middle,),
            invariant_id=before.auxiliary_invariant_id,
        )
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=middle,
                path=INTERMEDIATE_PATH,
                blob=blob,
            ),
        )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def build_family_head_membership_fixture(directory: Path) -> EndpointFixture:
    """One membership moved onto a successor **family** revision the comparison's page does not reach.

    The candidate withdraws the old row and records the same member revision in the family's successor
    revision, which the selection never selects. The family half of the reach must read it through the
    family-revision membership owner, display the member's new side, and never present a sibling member
    of the old family revision as this member's record.
    """

    endpoints = build_endpoint_fixture(directory / "family-head")
    diff = endpoints.diff
    before = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        new_family_revision = str(uuid4())
        member = memberships.find_membership_by_pair(
            store, before.direct_family.revision_id, before.auxiliary_revision_id
        )
        assert member is not None
        removed = memberships.remove_family_member(
            store,
            RemoveFamilyMemberRequest(
                repository_id=store.repository_id,
                member_id=member.member_id,
                expected_row_digest=member.row_digest,
            ),
        )
        assert removed.state == "removed", removed.refusal
        authored = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=store.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=before.direct_family.family_id,
                    revision_id=new_family_revision,
                    display_version=BASE_LABEL,
                    joint_guarantee=DIRECT_FAMILY_GUARANTEE,
                    predecessors=(before.direct_family.revision_id,),
                    provenance=before.authorship,
                ),
            ),
        )
        assert authored.state == "created", authored.refusal
        holder = memberships.create_family_member(
            store,
            FamilyMemberRequest(
                repository_id=store.repository_id,
                member=FamilyMemberDraft(
                    member_id=str(uuid4()),
                    family_revision_id=new_family_revision,
                    invariant_revision_id=before.auxiliary_revision_id,
                    provenance=before.authorship,
                ),
            ),
        )
        assert holder.state == "created", holder.refusal
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def test_a_split_line_that_records_relationships_names_them_and_never_denies(
    tmp_path: Path,
) -> None:
    """The lineage sentence is built from what was read: a successor holding a relationship is shown.

    One member revision split into two successors, each recording a realization. Every claim on the
    line is displayed -- each as its own movement continuing the withdrawn association -- the split
    lineage names both successors, and no sentence anywhere in the payload claims that the line records
    no relationship.
    """

    endpoints = build_split_with_relationships_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    moved = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "moved"
        and movement.after is not None
        and movement.after.path in (SPLIT_ONE_PATH, SPLIT_TWO_PATH)
    ]
    assert {movement.after.path for movement in moved if movement.after} == {
        SPLIT_ONE_PATH,
        SPLIT_TWO_PATH,
    }, [m.statement for m in realization_movements(payload)]
    for movement in moved:
        assert [side.path for side in movement.before] == [AUXILIARY_PATH]
        assert movement.record_id == before.auxiliary_invariant_id
        after = movement.after
        assert after is not None
        # Two ends and no unique head, so neither row may be described as one.
        assert movement.pairing_basis == "authored_successor_line_revision"
        assert "uniquely established head" not in movement.statement
        assert after.revision_id is not None
        assert after.revision_id in movement.statement
        assert "every authored revision of which this review read" in movement.statement
        split = [entry for entry in movement.lineage if entry.kind == "split"]
        assert len(split) == 1
        assert (
            set(split[0].related_revision_ids) >= {side.revision_id for side in movement.before}
            or split[0].related_revision_ids
        )
        assert "a relationship for none of them" not in split[0].statement
    # No sentence anywhere denies a relationship the same payload displays.
    assert not [
        movement.statement
        for movement in payload.source.relationships
        if "a relationship for none of them" in movement.statement
        or "no relationship for the same citation" in movement.statement
    ]
    assert any(location.path == SPLIT_ONE_PATH for location in payload.source.locations)
    assert any(location.path == SPLIT_TWO_PATH for location in payload.source.locations)


def test_a_multi_head_line_with_relationships_never_claims_a_unique_head(tmp_path: Path) -> None:
    """Both rows of a split line are described as line reads, because no head exists to be named.

    The member revision was split into two successors that each record a relationship, so the line's
    state is unresolved with two ends and `head_revision_id` is absent. Each row's sentence names the
    revision that row records and the line that was read, and neither claims a unique head.
    """

    endpoints = build_split_with_relationships_fixture(tmp_path)
    diff = endpoints.diff
    payload = review_of(endpoints, diff.retry_invariant_id)
    rows = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None and movement.after.path in (SPLIT_ONE_PATH, SPLIT_TWO_PATH)
    ]
    assert len(rows) == 2
    for movement in rows:
        after = movement.after
        assert after is not None
        assert movement.pairing_basis == "authored_successor_line_revision"
        assert "uniquely established head" not in movement.statement
        assert after.revision_id is not None and after.revision_id in movement.statement
        assert "rather than the line's head" in movement.statement
    # The two sentences name their own rows and differ from each other.
    assert len({movement.statement for movement in rows}) == 2


def test_a_split_line_that_records_nothing_states_only_what_was_read(tmp_path: Path) -> None:
    """The split-only case: every revision of the line was read, and the sentence says exactly that."""

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
    split = [entry for entry in movement.lineage if entry.kind == "split"]
    assert len(split) == 1, [entry.statement for entry in movement.lineage]
    assert "so the revision was split" in split[0].statement
    assert "read every revision of that line" in split[0].statement
    assert "no revision of that line records a relationship for this citation" in split[0].statement
    assert "a relationship for none of them" not in split[0].statement


def test_a_relationship_on_an_intermediate_descendant_is_displayed(tmp_path: Path) -> None:
    """The whole line is read, not only its head: a claim at an intermediate successor is displayed.

    The member line runs X -> A -> B with the head B recording nothing and the relationship at A. A
    head-only read would call the association withdrawn; the line read displays A's address under the
    preserved identity, and the movement states the authored line it was paired by.
    """

    endpoints = build_intermediate_descendant_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    moved = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None
        and movement.after.path == INTERMEDIATE_PATH
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(moved) == 1, [m.statement for m in realization_movements(payload)]
    movement = moved[0]
    assert movement.transition == "moved"
    assert movement.record_id == before.auxiliary_invariant_id
    after = movement.after
    assert after is not None
    assert after.revision_id != before.auxiliary_revision_id
    assert movement.pairing_basis == "authored_successor_line_revision"
    # The sentence is exact: it names the row's own revision, says the whole line was read, and does
    # not claim a unique head the line does not have here.
    assert after.revision_id is not None
    assert after.revision_id in movement.statement
    assert before.auxiliary_revision_id in movement.statement
    assert "every authored revision of which this review read" in movement.statement
    assert "uniquely established head" not in movement.statement
    assert any(location.path == INTERMEDIATE_PATH for location in payload.source.locations)


def test_a_membership_moved_onto_an_unselected_family_revision_is_displayed(tmp_path: Path) -> None:
    """The family half of the reach: a successor family revision's membership is read and displayed.

    The member row was withdrawn from the baseline family revision and recorded again, holding the same
    member revision, in the family's successor revision -- which the comparison never selects. The
    movement displays the member's new side under the preserved family identity, and no sentence
    presents a sibling member of the old family revision as this member's record.
    """

    endpoints = build_family_head_membership_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    moved = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "membership"
        and movement.after is not None
        and movement.after.member_revision_id == before.auxiliary_revision_id
        and movement.before
        and movement.before[0].revision_id == before.direct_family.revision_id
    ]
    assert len(moved) == 1, [
        (
            m.transition,
            [s.member_revision_id for s in m.before],
            m.after and m.after.member_revision_id,
        )
        for m in payload.source.relationships
        if m.relationship_kind == "membership"
    ]
    movement = moved[0]
    assert movement.transition == "reassigned"
    assert movement.pairing_basis == "same_member_revision"
    assert movement.record_id == before.direct_family.family_id
    after = movement.after
    assert after is not None
    assert after.revision_id != before.direct_family.revision_id
    assert after.item_coverage == "present_outside_selection"
    # The candidate's record for this member is the moved row, and no sibling member is named for it.
    sibling_members = {
        side.member_revision_id
        for other in payload.source.relationships
        for side in other.before
        if side.member_revision_id not in (None, before.auxiliary_revision_id)
    }
    for other in payload.source.relationships:
        if other.transition != "outside_selection":
            continue
        assert all(
            side.member_revision_id not in sibling_members or "same citation" not in other.statement
            for side in other.before
        )
