"""Focused behaviour of family identity and the immutable family revision aggregate.

Each case protects one consequential operation or failure: a guarantee that must not change behind
an existing revision, a lineage that must refuse a cross-family or dangling predecessor, the wider
lineage rule that refuses a candidate beneath a stored cycle, and the payload seal that makes a
stored guarantee the one its identity names. Constructor validation is not re-tested here.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.memory.knowledge import families, memberships
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    family_lineage_cycle_refusal,
)
from agents_remember.models.knowledge.result import FamilyRevisionRequest
from knowledge_fixture_test_support import (
    FAMILY_GUARANTEE,
    FAMILY_SIBLING_GUARANTEE,
    FAMILY_SUCCESSOR_GUARANTEE,
    SHARED_SUCCESSOR_DISPLAY_VERSION,
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
)
from knowledge_graph_test_support import (
    FamilySeed,
    family_draft,
    insert_raw_family_edge,
    insert_raw_family_revision,
    sealed_family,
    table_counts,
)


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "graph")


def _revision_request(
    fixture: BranchingKnowledgeFixture, family_id: str, seed: FamilySeed
) -> FamilyRevisionRequest:
    return FamilyRevisionRequest(
        repository_id=fixture.repository_id, revision=family_draft(fixture, family_id, seed)
    )


def test_a_family_membership_keeps_resolving_the_guarantee_it_was_authored_against(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """An earlier membership still resolves the first revision's guarantee after successors exist.

    Both later revisions display the same ``v2``. A store that treated the display version as a
    pointer, kept only the newest revision, or re-pointed an existing membership at its successor
    would answer this read with a guarantee the membership never cited.
    """

    with fixture.reopen() as store:
        original = families.get_family_revision(store, fixture.family.revision_id)
        successor = families.get_family_revision(store, fixture.family_successor_revision_id)
        sibling = families.get_family_revision(store, fixture.family_sibling_revision_id)
        membership = memberships.get_family_member(store, fixture.family.member_ids[0])
        successor_members = memberships.list_members(store, fixture.family_successor_revision_id)
    assert original is not None and successor is not None and sibling is not None
    assert original.revision.joint_guarantee == FAMILY_GUARANTEE
    assert successor.revision.joint_guarantee == FAMILY_SUCCESSOR_GUARANTEE
    assert sibling.revision.joint_guarantee == FAMILY_SIBLING_GUARANTEE
    assert successor.revision.display_version == SHARED_SUCCESSOR_DISPLAY_VERSION
    assert sibling.revision.display_version == SHARED_SUCCESSOR_DISPLAY_VERSION
    assert successor.revision.revision_id != sibling.revision.revision_id
    assert (
        successor.predecessors_sorted
        == sibling.predecessors_sorted
        == (fixture.family.revision_id,)
    )
    assert successor.revision.payload_digest != sibling.revision.payload_digest
    assert membership is not None
    assert membership.family_revision_id == fixture.family.revision_id
    assert membership.invariant_revision_id == fixture.base_revision_id
    # A newer family revision composes nothing by itself: its memberships are authored explicitly.
    assert successor_members.members == ()


def test_an_in_place_family_guarantee_change_is_refused(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The database refuses a rewrite, and the operation refuses an identity reuse.

    The two refusals are different failures: a later code path that bypassed this operation must
    still not be able to change a cited guarantee, and a caller that reuses the identity with new
    text must be told to author a successor instead of silently replacing what a membership cites.
    """

    rewritten = "A guarantee rewritten behind the revision that already carries it."
    with fixture.reopen() as store:
        before = families.get_family_revision(store, fixture.family.revision_id)
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE family_revision SET joint_guarantee = ? WHERE revision_id = ?",
                (rewritten, fixture.family.revision_id),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "DELETE FROM family_revision WHERE revision_id = ?", (fixture.family.revision_id,)
            )
        refused = families.create_family_revision(
            store,
            _revision_request(
                fixture,
                fixture.family.family_id,
                FamilySeed(revision_id=fixture.family.revision_id, guarantee=rewritten),
            ),
        )
        after = families.get_family_revision(store, fixture.family.revision_id)
    assert before is not None and after is not None
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "duplicate_identity"
    assert refused.refusal.expected == before.revision.payload_digest
    assert refused.refusal.observed not in (None, refused.refusal.expected)
    assert after == before
    assert after.revision.joint_guarantee == FAMILY_GUARANTEE


def test_reading_a_family_revision_verifies_its_payload_seal(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A guarantee altered outside the operation is detected when the revision is read back.

    The trigger is dropped first because the point of the case is the *second* defence: even a
    database edited by something that bypassed this process must not be served as the revision the
    identity names.
    """

    with fixture.reopen() as store:
        store.connection.execute("DROP TRIGGER family_revision_no_update")
        store.connection.execute(
            "UPDATE family_revision SET joint_guarantee = ? WHERE revision_id = ?",
            ("A substituted guarantee.", fixture.family.revision_id),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            families.get_family_revision(store, fixture.family.revision_id)


def test_a_family_revision_digest_seals_its_predecessor_set(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Sealing one identical family draft twice, with and without a predecessor, changes the digest.

    Only the predecessor set may differ between the two seals. The identity, display version, joint
    guarantee, origin state and provenance are the *same values*, and the node asserts that
    equality explicitly, so a payload that omitted the predecessor set would seal both drafts to one
    digest and fail here. That equality assertion is the point of the node: an earlier version of it
    varied the revision identity as well, and so passed for a reason other than the field under
    test. The read-path refusal an edited edge produces is covered separately in
    ``test_knowledge_revision_seals.py``.
    """

    seed = FamilySeed(revision_id=str(uuid4()), guarantee="A sealed family guarantee.")
    without_edge = sealed_family(fixture, fixture.family.family_id, seed)
    with_edge = sealed_family(
        fixture,
        fixture.family.family_id,
        FamilySeed(
            revision_id=seed.revision_id,
            guarantee=seed.guarantee,
            display_version=seed.display_version,
            predecessors=(fixture.family.revision_id,),
        ),
    )
    assert without_edge.predecessors == ()
    assert with_edge.predecessors == (fixture.family.revision_id,)
    assert with_edge.model_dump(
        exclude={"payload_digest", "predecessors"}
    ) == without_edge.model_dump(exclude={"payload_digest", "predecessors"})
    assert without_edge.payload_digest != with_edge.payload_digest


def test_a_family_revision_of_an_unknown_family_refuses(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A revision whose family identity was never created refuses as a typed outcome."""

    with fixture.reopen() as store:
        counts_before = table_counts(store)
        refused = families.create_family_revision(
            store,
            _revision_request(
                fixture,
                str(uuid4()),
                FamilySeed(revision_id=str(uuid4()), guarantee="A guarantee of no known family."),
            ),
        )
        counts_after = table_counts(store)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "unknown_family"
    assert counts_after == counts_before


def test_a_family_predecessor_from_another_family_refuses_with_the_named_endpoint(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Family lineage stays inside one family and refuses the cross-family endpoint by name."""

    candidate_id = str(uuid4())
    with fixture.reopen() as store:
        counts_before = table_counts(store)
        refused = families.create_family_revision(
            store,
            _revision_request(
                fixture,
                fixture.overlapping_family.family_id,
                FamilySeed(
                    revision_id=candidate_id,
                    guarantee="A guarantee reaching into another family's lineage.",
                    predecessors=(fixture.family.revision_id,),
                ),
            ),
        )
        counts_after = table_counts(store)
        stored = families.get_family_revision(store, candidate_id)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "invalid_reference"
    assert refused.refusal.record_id == fixture.family.revision_id
    assert refused.refusal.expected == fixture.overlapping_family.family_id
    assert refused.refusal.observed == fixture.family.family_id
    assert stored is None
    assert counts_after == counts_before


def test_a_dangling_family_predecessor_refuses_without_leaving_a_row(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A predecessor that was never authored refuses and stores nothing."""

    missing = str(uuid4())
    with fixture.reopen() as store:
        versions_before = families.list_family_revision_ids(store, fixture.family.family_id)
        refused = families.create_family_revision(
            store,
            _revision_request(
                fixture,
                fixture.family.family_id,
                FamilySeed(
                    revision_id=str(uuid4()),
                    guarantee="A successor of a family revision that does not exist.",
                    predecessors=(missing,),
                ),
            ),
        )
        versions_after = families.list_family_revision_ids(store, fixture.family.family_id)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "invalid_reference"
    assert refused.refusal.record_id == missing
    assert versions_after == versions_before


def test_the_family_lineage_rule_matches_the_invariant_rule(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A candidate below a stored family cycle is refused, with the wording of its own branch.

    The cyclic state is written outside the operation -- admission gives a cycle no reachable
    creation path -- so the candidate's own predecessor set is acyclic and it is the *reach* of the
    rule that refuses it. Nothing points at that candidate, so the refusal may not claim
    self-reachability, and it must name the family's lineage rather than an invariant's.
    """

    closer_id, candidate_id = str(uuid4()), str(uuid4())
    with fixture.reopen() as store:
        insert_raw_family_revision(
            store,
            sealed_family(
                fixture,
                fixture.family.family_id,
                FamilySeed(
                    revision_id=closer_id,
                    guarantee="The record that reaches back into the family lineage.",
                ),
            ),
        )
        insert_raw_family_edge(
            store, fixture.family.family_id, closer_id, fixture.family.revision_id
        )
        insert_raw_family_edge(
            store, fixture.family.family_id, fixture.family.revision_id, closer_id
        )
        counts_before = table_counts(store)
        refused = families.create_family_revision(
            store,
            _revision_request(
                fixture,
                fixture.family.family_id,
                FamilySeed(
                    revision_id=candidate_id,
                    guarantee="A successor whose predecessor sits beneath a stored family cycle.",
                    predecessors=(closer_id,),
                ),
            ),
        )
        counts_after = table_counts(store)
        stored = families.get_family_revision(store, candidate_id)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "lineage_cycle"
    assert refused.refusal.record_id == candidate_id
    assert refused.refusal.observed == ", ".join(sorted((closer_id, fixture.family.revision_id)))
    assert refused.refusal.table == "family_predecessor"
    assert refused.refusal.operation == "create_family_revision"
    assert "family's lineage" in refused.refusal.detail
    assert "reachable from itself" not in refused.refusal.detail
    assert "descend" in refused.refusal.detail.lower()
    assert stored is None
    assert counts_after == counts_before


def test_both_family_lineage_branches_are_worded_for_the_branch_they_describe(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The on-cycle wording exists only for a candidate that really is on the cycle.

    Only the descending branch is reachable through the operation, so the other branch is
    exercised through the factory that emits it -- and the two must not be interchangeable, or a
    refusal would describe a state its own facts contradict.
    """

    candidate_id = str(uuid4())
    on_cycle = family_lineage_cycle_refusal(candidate_id, (candidate_id,), candidate_on_cycle=True)
    descending = family_lineage_cycle_refusal(
        candidate_id, (candidate_id,), candidate_on_cycle=False
    )
    assert "reachable from itself" in on_cycle.detail
    assert "reachable from itself" not in descending.detail
    assert "family's lineage" in descending.detail
    assert on_cycle.next_action != descending.next_action
    assert "ancestor cycle" not in on_cycle.next_action
    assert "ancestor cycle" in descending.next_action
    assert candidate_id in on_cycle.next_action
    assert on_cycle.table == descending.table == "family_predecessor"
    assert on_cycle.record_id == candidate_id
