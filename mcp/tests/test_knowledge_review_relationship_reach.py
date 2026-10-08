"""The recorded union's reach past the comparison's page (ICR-R08@v1's fix round).

The packet's core cases live beside these in
``mcp/tests/test_knowledge_review_relationship_movement.py``; this module owns the four states the
fix-round ruling turns on, each authored through the public store operations and read back through the
production composition:

* a **member** identity's realization re-authored onto a successor revision -- the comparison's page
  never selects that revision, so the review must read the relationship at the head of the citation's
  authored successor line (ICR-R07@v1's own head rule) and display one movement with both addresses;
* a member line whose head is **not unique** -- the state is unresolved *with its reason*, and no
  sentence may claim that nothing is recorded;
* a citation the candidate **re-records** under a new row with no authored edge -- the withdrawn rows
  name that row instead of denying it, because one payload may not contradict itself about the store;
* a family revision that **keeps its members** -- memberships pair member-wise, so each one is
  displayed once and named a reassignment; and
* a fresh row at the **same address** as a moved realization -- an address resemblance never pairs,
  and the movement that does pair states the recorded relation it used.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.review_rename_inference import (
    RENAME_INFERENCE_COMMAND,
    RenameObservations,
    _with_inference,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
)
from diff_scope_test_support import _git
from knowledge_rows_test_support import (
    FamilyMemberRequest,
    FamilyRevisionRequest,
    RemoveFamilyMemberRequest,
    families,
    memberships,
    open_knowledge_store,
)
from read_scope_test_support import (
    ABSENT_PATH,
    AUXILIARY_PATH,
    BASE_LABEL,
    BATCH_PATH,
    DIRECT_FAMILY_GUARANTEE,
)
from test_knowledge_review_relationship_movement import (
    RENAMED_PATH_TEXT,
    SUCCESSOR_PATH,
    ClaimDraft,
    author_claim,
    author_revision,
    build_endpoint_fixture,
    realization_movements,
    remove_claim,
    review_of,
)
from test_knowledge_review_source_endpoints import EndpointFixture, _place_datasets

pytestmark = pytest.mark.evidence_unit

MEMBER_MOVED_PATH = "src/verify_aux_moved.py"
MEMBER_MOVED_TEXT = "# an unrelated module\nnothing shared with the anchors file\n"
SAME_CITATION_PATH = "src/verify_merge.py"
SAME_CITATION_TEXT = "# merge\nboth predecessors recorded\n"


def build_member_move_fixture(directory: Path) -> EndpointFixture:
    """One *member* identity's realization re-authored onto a successor revision (the ruling's case).

    The reviewed subject is still the retry invariant; the moved association belongs to the auxiliary
    identity, which the comparison reaches through the family but whose *successor* revision the read
    selection never selects. The new address therefore exists only at the head of that revision's
    authored line, and the review must display it there rather than call the association withdrawn.
    """

    endpoints = build_endpoint_fixture(directory / "member-move")
    diff = endpoints.diff
    before = diff.before.fixture
    worktree = endpoints.worktree
    (worktree / MEMBER_MOVED_PATH).write_text(MEMBER_MOVED_TEXT, encoding="utf-8")
    (worktree / AUXILIARY_PATH).unlink(missing_ok=True)
    blob = _git(worktree, ["hash-object", MEMBER_MOVED_PATH])
    revision_id = str(uuid4())
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, before.auxiliary.claim_id)
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
                path=MEMBER_MOVED_PATH,
                blob=blob,
            ),
        )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def build_split_head_fixture(directory: Path) -> EndpointFixture:
    """One *member* identity whose revision was split into two heads, neither with a relationship.

    The candidate authors two successors of the auxiliary revision and records no relationship at
    either. No single head exists, so the review may not assert that nothing is recorded: the state is
    unresolved with its reason.
    """

    endpoints = build_endpoint_fixture(directory / "split-head")
    diff = endpoints.diff
    before = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, before.auxiliary.claim_id)
        for _ in range(2):
            author_revision(
                store,
                diff,
                str(uuid4()),
                predecessors=(before.auxiliary_revision_id,),
                invariant_id=before.auxiliary_invariant_id,
            )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def build_same_citation_fixture(directory: Path) -> EndpointFixture:
    """A withdrawn citation the candidate re-records under a new row, with no authored edge.

    Two baseline claims citing one revision are withdrawn and the candidate records one claim citing
    that *same* revision at a different address. No authored edge connects the rows, so they are three
    separate movements -- and no sentence may deny the relationship the same payload displays.
    """

    endpoints = build_endpoint_fixture(directory / "same-citation")
    diff = endpoints.diff
    before = diff.before.fixture
    worktree = endpoints.worktree
    (worktree / SAME_CITATION_PATH).write_text(SAME_CITATION_TEXT, encoding="utf-8")
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        remove_claim(store, before.batch_primary.claim_id)
        remove_claim(store, before.batch_secondary.claim_id)
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=before.batch_revision_id,
                path=SAME_CITATION_PATH,
                blob="4" * 40,
            ),
        )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def build_family_same_members_fixture(directory: Path) -> tuple[EndpointFixture, str]:
    """A successor family revision that re-records the *same* member revisions.

    Every member stayed and only the family revision moved, so each membership is displayed once,
    paired with the row holding its own member, and named a reassignment.
    """

    endpoints = build_endpoint_fixture(directory / "family-same-members")
    diff = endpoints.diff
    before = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        new_revision_id = str(uuid4())
        for member_revision in (before.subject_revision_id, before.auxiliary_revision_id):
            member = memberships.find_membership_by_pair(
                store, before.direct_family.revision_id, member_revision
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
                    revision_id=new_revision_id,
                    display_version=BASE_LABEL,
                    joint_guarantee=DIRECT_FAMILY_GUARANTEE,
                    predecessors=(before.direct_family.revision_id,),
                    provenance=before.authorship,
                ),
            ),
        )
        assert authored.state == "created", authored.refusal
        for member_revision in (before.subject_revision_id, before.auxiliary_revision_id):
            member = memberships.create_family_member(
                store,
                FamilyMemberRequest(
                    repository_id=store.repository_id,
                    member=FamilyMemberDraft(
                        member_id=str(uuid4()),
                        family_revision_id=new_revision_id,
                        invariant_revision_id=member_revision,
                        provenance=before.authorship,
                    ),
                ),
            )
            assert member.state == "created", member.refusal
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints, new_revision_id


def build_pairing_qualification_fixture(directory: Path) -> EndpointFixture:
    """A new row at the very address the moved realization names, and no authored edge anywhere.

    Two different candidate relationships could be confused with the moved one by address alone: a
    fresh claim at the address the movement's after side names (no predecessor at all), and a fresh
    claim at the address of a row the candidate withdrew. The first must stay its own ``added``
    movement -- pairing it would assert a movement from an address resemblance -- and the second is
    displayed with the withdrawn row only because an authored line connects the citations, which the
    movement's own sentence states.
    """

    endpoints = build_endpoint_fixture(directory / "pairing")
    diff = endpoints.diff
    before = diff.before.fixture
    worktree = endpoints.worktree
    (worktree / SUCCESSOR_PATH).write_text(RENAMED_PATH_TEXT, encoding="utf-8")
    successor_blob = _git(worktree, ["hash-object", SUCCESSOR_PATH])
    same_path_revision = str(uuid4())
    absent_revision = str(uuid4())
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        # the moved realization itself, recorded at the address the review will pair; the revised
        # revision already exists in this fixture and already names the subject revision
        remove_claim(store, diff.added_claim_id)
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=diff.revised_revision_id,
                path=SUCCESSOR_PATH,
                blob=successor_blob,
            ),
        )
        # a fresh row at the *same address*, citing a revision that records no predecessor
        author_revision(store, diff, same_path_revision, predecessors=())
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=same_path_revision,
                path=SUCCESSOR_PATH,
                blob="3" * 40,
            ),
        )
        # the withdrawn absent-path row, re-recorded at its own path with no edge
        remove_claim(store, before.absent_anchor.claim_id)
        author_revision(store, diff, absent_revision, predecessors=())
        author_claim(
            store,
            diff,
            ClaimDraft(
                claim_id=str(uuid4()),
                revision_id=absent_revision,
                path=ABSENT_PATH,
                blob="2" * 40,
            ),
        )
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints


def test_a_member_identities_moved_realization_is_displayed_at_its_own_head(
    tmp_path: Path,
) -> None:
    """A member's realization re-authored onto a successor revision shows BOTH addresses, as one movement.

    The ruling: the recorded relationship union is not bounded by the comparison's selected revision
    page. The auxiliary identity's revision has an authored successor line whose uniquely established
    head records the moved realization, so the review reads it there and displays one movement -- the
    old address from the union and the new address from the head -- under the preserved identity, with
    the head named so a reader knows the row came from outside the comparison's page.
    """

    endpoints = build_member_move_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    moved = [
        movement
        for movement in realization_movements(payload)
        if movement.after is not None
        and movement.after.path == MEMBER_MOVED_PATH
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(moved) == 1, [
        (
            movement.transition,
            [side.path for side in movement.before],
            movement.after and movement.after.path,
        )
        for movement in realization_movements(payload)
    ]
    movement = moved[0]
    assert movement.transition == "moved"
    assert movement.record_id == before.auxiliary_invariant_id
    assert [side.path for side in movement.before] == [AUXILIARY_PATH]
    assert movement.before[0].revision_id == before.auxiliary_revision_id
    assert movement.after is not None
    assert movement.after.revision_id is not None
    assert movement.after.revision_id != before.auxiliary_revision_id
    assert movement.after.item_coverage == "present_outside_selection"
    assert movement.pairing_basis == "authored_successor_head_revision"
    assert "uniquely established head" in movement.statement
    # Both addresses are listed, and both rows carry the one preserved identity.
    located = {
        location.path: location
        for location in payload.source.locations
        if location.path in (AUXILIARY_PATH, MEMBER_MOVED_PATH)
    }
    assert set(located) == {AUXILIARY_PATH, MEMBER_MOVED_PATH}
    assert {location.invariant_id for location in located.values()} == {
        before.auxiliary_invariant_id
    }
    assert located[AUXILIARY_PATH].counterpart_path == MEMBER_MOVED_PATH


def test_a_member_line_with_no_single_head_is_unresolved_and_never_denied(tmp_path: Path) -> None:
    """A split that never rejoins is stated as unresolved, with its reason, and asserts no negative.

    The auxiliary revision has two authored successors and no relationship at either, so no single
    head exists. The withdrawn association is displayed with a ``successor_line_unresolved`` gap naming
    the ends and the reason, and its sentence says the state is unresolved rather than that nothing is
    recorded.
    """

    endpoints = build_split_head_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1
    movement = withdrawn[0]
    gaps = [gap for gap in movement.gaps if gap.code == "successor_line_unresolved"]
    assert len(gaps) == 1, [gap.code for gap in movement.gaps]
    assert "no single head is established" in gaps[0].detail
    assert "read every revision of the line" in gaps[0].detail
    assert "no negative about an unread revision" in gaps[0].detail
    assert "found no relationship for this citation there" in movement.statement
    assert "a relationship for none of them" not in movement.statement
    assert movement.record_id == before.auxiliary_invariant_id
    assert "no relationship citing" not in movement.statement


def test_a_withdrawal_never_denies_a_relationship_the_same_payload_displays(tmp_path: Path) -> None:
    """The same citation recorded under a new row is NAMED by the withdrawn rows, not denied.

    Two baseline claims citing one revision are withdrawn while the candidate records a claim citing
    that same revision, at a different address, with no authored edge. No edge means no pairing, so the
    payload holds three separate movements -- and the withdrawn rows name the candidate's relationship
    instead of asserting that the snapshot records none for that citation.
    """

    endpoints = build_same_citation_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == BATCH_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 2, [movement.transition for movement in realization_movements(payload)]
    added = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "added"
        and movement.after is not None
        and movement.after.path == SAME_CITATION_PATH
    ]
    assert len(added) == 1
    added_after = added[0].after
    assert added_after is not None
    added_id = added_after.relationship_id
    assert added_id is not None
    for movement in withdrawn:
        assert added_id in movement.statement
        assert "same citation" in movement.statement
        assert "no relationship for the same citation" not in movement.statement
        assert "records no relationship under this row" in movement.statement
    # The citation is the same revision on both sides, which is exactly why the negative would be false.
    added_after = added[0].after
    assert added_after is not None
    assert added_after.revision_id == before.batch_revision_id


def test_a_family_revision_that_keeps_its_members_displays_each_membership_once(
    tmp_path: Path,
) -> None:
    """Memberships pair member-wise, so a moved family revision is a reassignment, not a new member.

    Every member stayed and only the family revision moved: each membership is displayed once, with a
    before side holding its OWN member revision, and the transition is the vocabulary's own
    ``reassigned`` rather than ``moved``.
    """

    endpoints, new_revision_id = build_family_same_members_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    moved = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "membership"
        and movement.after is not None
        and movement.after.revision_id == new_revision_id
    ]
    assert len(moved) == 2, [
        (movement.transition, [side.member_revision_id for side in movement.before])
        for movement in moved
    ]
    assert {movement.transition for movement in moved} == {"reassigned"}
    assert {side.member_revision_id for movement in moved for side in movement.before} == {
        before.subject_revision_id,
        before.auxiliary_revision_id,
    }
    for movement in moved:
        assert len(movement.before) == 1
        assert movement.pairing_basis == "same_member_revision"
        assert movement.after is not None
        assert movement.before[0].member_revision_id == movement.after.member_revision_id


def test_a_pairing_states_the_recorded_relation_it_was_made_on(tmp_path: Path) -> None:
    """Every two-sided movement names its pairing basis, and the statement spells it out.

    The addresses alone prove nothing: a moved realization's two rows may be one association only
    because the candidate's revision is a step further along the author's own line. The basis is a
    typed value and the sentence names the lineage it used, so a reader can tell a recorded pairing
    from a resemblance.
    """

    endpoints = build_member_move_fixture(tmp_path)
    diff = endpoints.diff
    payload = review_of(endpoints, diff.retry_invariant_id)
    paired = [
        movement
        for movement in payload.source.relationships
        if movement.after is not None and movement.before
    ]
    assert paired
    assert all(movement.pairing_basis is not None for movement in paired)
    moved = [
        movement
        for movement in paired
        if movement.relationship_kind == "realization"
        and movement.after is not None
        and movement.after.path == MEMBER_MOVED_PATH
    ]
    assert len(moved) == 1
    assert "authored successor line" in moved[0].statement
    assert "not a resemblance between the two addresses" in moved[0].statement


def test_a_pairing_is_qualified_and_an_address_resemblance_never_pairs(tmp_path: Path) -> None:
    """Pairing is a recorded relation, stated as such; a row that merely shares an address is not it.

    A fresh claim at the same address as the moved realization, citing a revision with no predecessor,
    is displayed as its own added movement: no recorded edge connects it, and its address matching is
    not evidence. The movement that does pair names the authored line it used and says in its own
    sentence that the pairing is not a resemblance between the two addresses.
    """

    endpoints = build_pairing_qualification_fixture(tmp_path)
    diff = endpoints.diff
    payload = review_of(endpoints, diff.retry_invariant_id)
    at_the_address = [
        movement
        for movement in realization_movements(payload)
        if (movement.after is not None and movement.after.path == SUCCESSOR_PATH)
        or any(side.path == SUCCESSOR_PATH for side in movement.before)
    ]
    transitions = {movement.transition for movement in at_the_address}
    assert "added" in transitions, [m.transition for m in at_the_address]
    added = [movement for movement in at_the_address if movement.transition == "added"]
    assert all(movement.before == () and movement.pairing_basis is None for movement in added)
    paired = [movement for movement in at_the_address if movement.transition == "moved"]
    assert len(paired) == 1
    statement = paired[0].statement
    assert paired[0].pairing_basis == "authored_successor_revision"
    assert "authored successor line" in statement
    assert "not a resemblance between the two addresses" in statement
    # The absent-path row withdrawn by the candidate is paired only through the authored line, and
    # its movement says which line that is.
    absent = [
        movement
        for movement in realization_movements(payload)
        if any(side.path == ABSENT_PATH for side in movement.before)
    ]
    assert absent
    assert all(
        movement.pairing_basis is not None for movement in absent if movement.after is not None
    )


def test_the_rename_inference_separates_a_measured_absence_from_a_measurement_never_made() -> None:
    """``not_paired`` and ``unavailable`` are different facts, and neither carries a pairing.

    A caller that ran no Git command cannot report "Git found no rename", and a caller whose Git
    command failed cannot report one either: both are stated as the state they are, with the command
    that would (or did) produce the answer, and with no pair of paths.
    """

    movement = ReviewRelationshipMovement(
        relationship_kind="realization",
        record_kind="invariant",
        record_id=str(uuid4()),
        transition="moved",
        before=(
            ReviewRelationshipSide(
                side="before",
                state="recorded",
                relationship_id=str(uuid4()),
                path="src/old.py",
                detail="the before snapshot records this relationship at src/old.py",
            ),
        ),
        after=ReviewRelationshipSide(
            side="after",
            state="recorded",
            relationship_id=str(uuid4()),
            path="src/new.py",
            detail="the after snapshot records this relationship at src/new.py",
        ),
        pairing_basis="authored_successor_revision",
        statement="the association is displayed as moved with both recorded sides",
    )
    command = RENAME_INFERENCE_COMMAND.format(before_tree="a" * 40, after_tree="b" * 40)
    unmeasured = _with_inference(
        movement, RenameObservations(available=False, detail="no inference was measured"), command
    )
    assert unmeasured.rename_inference is not None
    assert unmeasured.rename_inference.state == "unavailable"
    assert unmeasured.rename_inference.before_path is None
    assert unmeasured.rename_inference.similarity is None
    assert "no inference about the source is claimed" in unmeasured.rename_inference.statement

    measured = _with_inference(movement, RenameObservations(available=True, pairs=()), command)
    assert measured.rename_inference is not None
    assert measured.rename_inference.state == "not_paired"
    assert measured.rename_inference.before_path is None
    assert "reported no rename" in measured.rename_inference.statement
    assert "deletion" in measured.rename_inference.statement
