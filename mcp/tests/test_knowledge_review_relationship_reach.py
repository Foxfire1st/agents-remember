"""The union's edge past the comparison's page, and what the rule-1 display does instead.

The packet's core cases live beside these in
``mcp/tests/test_knowledge_review_relationship_movement.py``; this module owns the shapes the old
line search used to follow past the union's page -- each built as a text tree and read back
through the production composition. Text records hold no predecessor edge (MIK-R74 rule 1), so no
line is read past a withdrawn association's citation: what the old reach displayed as a movement
reads as a retraction stating what was searched, and the moved-to rows outside the union stay
out -- except where the candidate re-records under a row the union holds, which pairs by that
row.
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
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
)
from diff_scope_test_support import _git, rebuild_after_tree, replace_after_tree
from read_scope_test_support import (
    ABSENT_PATH,
    AUXILIARY_PATH,
    BATCH_PATH,
    BATCH_REVISION,
    SUBJECT_REVISION,
    SYNCHRONIZATION_PATH,
    FileLocator,
    ReadFixtureRealization,
    RealizationSeed,
)
from test_knowledge_review_relationship_movement import (
    RENAMED_PATH_TEXT,
    SUCCESSOR_PATH,
    _extra_claim,
    build_endpoint_fixture,
    realization_movements,
    review_of,
)
from test_knowledge_review_source_endpoints import EndpointFixture

pytestmark = pytest.mark.evidence_unit

MEMBER_MOVED_PATH = "src/verify_aux_moved.py"
MEMBER_MOVED_TEXT = "# an unrelated module\nnothing shared with the anchors file\n"
SAME_CITATION_PATH = "src/verify_merge.py"
SAME_CITATION_TEXT = "# merge\nboth predecessors recorded\n"


def build_member_move_fixture(directory: Path) -> EndpointFixture:
    """One *member* identity's realization withdrawn, with no continuation the union can read.

    The reviewed subject is still the retry invariant; the auxiliary claim is gone from the
    candidate tree. Text records hold no predecessor edge (MIK-R74 rule 1), so no search follows
    the association past its citation: the retraction states that no relationship citing the old
    revision was read, and the moved-to address cites a revision outside the reviewed union.
    """

    endpoints = build_endpoint_fixture(directory / "member-move")
    worktree = endpoints.worktree
    (worktree / AUXILIARY_PATH).unlink(missing_ok=True)
    after = rebuild_after_tree(
        directory / "member-move-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, AUXILIARY_PATH),
    )
    return replace_after_tree(endpoints, after)


def build_split_head_fixture(directory: Path) -> EndpointFixture:
    """One *member* identity's realization withdrawn, with no further revision to read.

    The candidate drops the auxiliary claim and records nothing in its place. With no predecessor
    edge no line is read and no head is established, so the withdrawn association reads as a
    retraction stating its searched citation -- never a negative about an unread revision.
    """

    endpoints = build_endpoint_fixture(directory / "split-head")
    after = rebuild_after_tree(
        directory / "split-head-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, AUXILIARY_PATH),
    )
    return replace_after_tree(endpoints, after)


def build_same_citation_fixture(directory: Path) -> EndpointFixture:
    """A withdrawn citation the candidate re-records under a new row, with no authored edge.

    Two baseline claims citing the batch revision are withdrawn and the candidate records one
    claim citing that *same* revision at a different address. No authored edge connects the rows
    -- text records hold none -- so they are three separate movements, and no sentence may deny
    the relationship the same payload displays.
    """

    endpoints = build_endpoint_fixture(directory / "same-citation")
    worktree = endpoints.worktree
    (worktree / SAME_CITATION_PATH).write_text(SAME_CITATION_TEXT, encoding="utf-8")
    after = rebuild_after_tree(
        directory / "same-citation-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, BATCH_PATH),
        extra_claims=(
            RealizationSeed(
                realization=ReadFixtureRealization(
                    anchor_id=str(uuid4()), claim_id=str(uuid4()), path=SAME_CITATION_PATH
                ),
                revision_id=BATCH_REVISION,
                role="enforcement",
                locator=FileLocator(),
                recorded_blob="4" * 40,
            ),
        ),
    )
    return replace_after_tree(endpoints, after)


def build_family_same_members_fixture(directory: Path) -> EndpointFixture:
    """A moved direct-family revision that keeps the *same* member revisions.

    The candidate's direct-family record is at another revision number, holding the same two
    member revisions. The membership rows keep their (family, invariant) identities, so each one
    pairs by row: the member whose cited revision changed displays as moved, and the member whose
    cited revision stayed displays as reassigned under the moved family revision.
    """

    endpoints = build_endpoint_fixture(directory / "family-same-members")
    after = rebuild_after_tree(
        directory / "family-same-members-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH,),
        direct_family_revision_number=2,
    )
    return replace_after_tree(endpoints, after)


def build_pairing_qualification_fixture(directory: Path) -> EndpointFixture:
    """New rows at the very addresses the withdrawn rows named, and no pairing across rows.

    The candidate re-records the successor claim with the worktree's bytes, records a second
    claim at the same address under another role, and re-records the absent-path row with
    another blob. Pairing needs the same recorded row, so the withdrawn baseline rows stay
    one-sided retractions and the new rows stay additions -- an address resemblance pairs
    nothing, and the payload states the recorded row every pairing used.
    """

    endpoints = build_endpoint_fixture(directory / "pairing")
    worktree = endpoints.worktree
    (worktree / SUCCESSOR_PATH).write_text(RENAMED_PATH_TEXT, encoding="utf-8")
    successor_blob = _git(worktree, ["hash-object", SUCCESSOR_PATH])
    after = rebuild_after_tree(
        directory / "pairing-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH, SUCCESSOR_PATH, ABSENT_PATH),
        extra_claims=(
            # the moved realization itself, re-recorded with the worktree's bytes
            _extra_claim(
                claim_id=str(uuid4()),
                anchor_id=str(uuid4()),
                path=SUCCESSOR_PATH,
                blob=successor_blob,
            ),
            # a fresh row at the *same address* under another role: its own addition
            RealizationSeed(
                realization=ReadFixtureRealization(
                    anchor_id=str(uuid4()), claim_id=str(uuid4()), path=SUCCESSOR_PATH
                ),
                revision_id=SUBJECT_REVISION,
                role="support",
                locator=FileLocator(),
                recorded_blob="3" * 40,
            ),
            # the withdrawn absent-path row, re-recorded at its own path with another blob
            RealizationSeed(
                realization=ReadFixtureRealization(
                    anchor_id=str(uuid4()), claim_id=str(uuid4()), path=ABSENT_PATH
                ),
                revision_id=SUBJECT_REVISION,
                role="enforcement",
                locator=FileLocator(),
                recorded_blob="2" * 40,
            ),
        ),
    )
    return replace_after_tree(endpoints, after)


def test_a_member_identities_moved_realization_is_a_retraction_with_no_read_continuation(
    tmp_path: Path,
) -> None:
    """A member's realization re-authored onto another revision is a retraction, and says so.

    The ruling: the recorded relationship union is the comparison's selected page. The
    auxiliary identity's old revision keeps its withdrawn association, and the candidate's new
    claim cites another revision under another row -- with no predecessor edge (MIK-R74 rule 1)
    no search follows the association past its citation. The retraction therefore states that
    no relationship citing the old revision was read, rather than denying that the snapshot
    records anything elsewhere, and the moved-to address (which cites a revision outside the
    reviewed union) is not displayed as a movement at all.
    """

    endpoints = build_member_move_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    withdrawn = [
        movement
        for movement in realization_movements(payload)
        if movement.transition == "retracted"
        and any(side.path == AUXILIARY_PATH for side in movement.before)
    ]
    assert len(withdrawn) == 1, [
        (
            movement.transition,
            [side.path for side in movement.before],
            movement.after and movement.after.path,
        )
        for movement in realization_movements(payload)
    ]
    movement = withdrawn[0]
    assert movement.after is None
    assert movement.record_id == before.auxiliary_invariant_id
    assert [side.path for side in movement.before] == [AUXILIARY_PATH]
    assert movement.before[0].revision_id == before.auxiliary_revision_id
    assert movement.pairing_basis is None
    assert "no relationship citing" in movement.statement
    assert before.auxiliary_revision_id in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement
    # The moved-to claim cites a revision outside the reviewed union, so no movement carries
    # its address and the pane lists only the withdrawn side under the preserved identity.
    assert MEMBER_MOVED_PATH not in {
        movement.after.path
        for movement in realization_movements(payload)
        if movement.after is not None
    }
    located = {
        location.path: location
        for location in payload.source.locations
        if location.path in (AUXILIARY_PATH, MEMBER_MOVED_PATH)
    }
    assert set(located) == {AUXILIARY_PATH}
    assert located[AUXILIARY_PATH].invariant_id == before.auxiliary_invariant_id
    assert located[AUXILIARY_PATH].counterpart_path is None


def test_a_member_line_with_two_unpaired_revisions_is_a_retraction_and_never_denied(
    tmp_path: Path,
) -> None:
    """Two revisions with no edge between them are stated as a retraction, with its reason.

    The auxiliary revision has two further revisions authored beside it and no relationship at
    either, and text records hold no predecessor edge (MIK-R74 rule 1), so no line is read and
    no head is established. The withdrawn association is displayed as a retraction stating that
    no relationship citing its revision was read -- an assertion about the searched citation,
    never a negative about an unread revision.
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
    assert movement.gaps == (), [gap.code for gap in movement.gaps]
    assert movement.record_id == before.auxiliary_invariant_id
    assert "no relationship citing" in movement.statement
    assert "withdrawn association rather than a movement" in movement.statement


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
    """Memberships pair by row, so a moved family revision is one movement per member.

    The candidate's direct-family record is at another revision number, holding the same two
    member revisions; the rows keep their (family, invariant) identities, so each pairs with its
    own baseline row. The member whose cited revision changed displays as moved; the member
    whose cited revision stayed displays as reassigned under the moved family revision. Every
    movement states the recorded row it was paired on -- never an address, role, status, or
    predecessor, because text records hold no authored line to difference.
    """

    endpoints = build_family_same_members_fixture(tmp_path)
    diff = endpoints.diff
    before = diff.before.fixture
    payload = review_of(endpoints, diff.retry_invariant_id)
    paired = [
        movement
        for movement in payload.source.relationships
        if movement.relationship_kind == "membership"
        and movement.after is not None
        and movement.after.revision_id == diff.after.fixture.direct_family.revision_id
        and len(movement.before) == 1
    ]
    assert len(paired) == 2, [
        (movement.transition, [side.member_revision_id for side in movement.before])
        for movement in paired
    ]
    assert {movement.pairing_basis for movement in paired} == {"same_recorded_relationship"}
    moved = [movement for movement in paired if movement.transition == "moved"]
    assert len(moved) == 1
    assert moved[0].before[0].member_revision_id == before.subject_revision_id
    assert moved[0].after is not None
    assert moved[0].after.member_revision_id == diff.revised_revision_id
    assert "recorded relation and not a resemblance" in moved[0].statement
    reassigned = [movement for movement in paired if movement.transition == "reassigned"]
    assert len(reassigned) == 1
    assert reassigned[0].before[0].member_revision_id == before.auxiliary_revision_id
    assert reassigned[0].after is not None
    assert reassigned[0].after.member_revision_id == before.auxiliary_revision_id
    for movement in paired:
        assert "recorded relation and not a resemblance" in movement.statement


def test_every_two_sided_movement_names_the_recorded_row_it_was_paired_on(
    tmp_path: Path,
) -> None:
    """Every two-sided movement names its pairing basis, and the statement spells it out.

    Pairing is the same recorded relationship row on both sides -- the only pairing the
    display performs. A moved realization's two rows are therefore two movements, never one:
    no movement in this payload pairs across rows, and every movement that does pair states
    the recorded row it paired on, so a reader can tell a recorded pairing from a resemblance.
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
    assert {movement.pairing_basis for movement in paired} <= {
        "same_recorded_relationship",
        "same_governed_identity",
    }
    for movement in paired:
        assert movement.pairing_basis is not None
        if movement.relationship_kind != "governing_route":
            assert "recorded relation and not a resemblance" in movement.statement
    assert not any(
        movement.after is not None and movement.after.path == MEMBER_MOVED_PATH
        for movement in paired
    ), "the re-authored realization is not a two-sided movement"


def test_a_row_at_a_matching_address_with_no_recorded_relation_is_only_an_addition(
    tmp_path: Path,
) -> None:
    """Pairing is the same recorded row, stated as such; a row at a matching address is not it.

    A fresh claim at the same address as the moved realization, citing a revision with no
    relation to the withdrawn row, is displayed as its own added movement: nothing pairs it,
    and its address matching is not evidence. No movement in the payload pairs across rows,
    and the absent-path row withdrawn by the candidate displays as its own one-sided
    retraction.
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
    assert "moved" not in transitions, [m.transition for m in at_the_address]
    added = [movement for movement in at_the_address if movement.transition == "added"]
    assert all(movement.before == () and movement.pairing_basis is None for movement in added)
    # The absent-path row withdrawn by the candidate is its own one-sided retraction.
    absent = [
        movement
        for movement in realization_movements(payload)
        if any(side.path == ABSENT_PATH for side in movement.before)
    ]
    assert absent
    assert all(movement.after is None and movement.pairing_basis is None for movement in absent)


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
        pairing_basis="same_recorded_relationship",
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
