"""Focused behaviour of the graph reads: one relation, two directions, one set of identities.

Each case protects a read contract: the forward query from an invariant revision and the reverse
query from an anchor must answer with the same stored claim identities, a family query must return
the same membership rows from either side, a recorded location whose source is unavailable must
still read back in full, and a removed relation must remain observable on the side that still has
it. There is no second index behind these reads, and that is what the identity comparisons check.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import anchors, families, memberships, realizations
from agents_remember.models.knowledge.result import SourceAnchorRequest
from agents_remember.models.knowledge.source import SymbolLocator
from knowledge_fixture_test_support import (
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
)
from knowledge_graph_test_support import (
    AnchorSeed,
    anchor_draft,
    build_removed_relation_successor,
)


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "graph")


def test_two_realizations_resolve_from_either_direction_with_the_same_claim_ids(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Both recorded implementations are reachable from the invariant and from each anchor.

    The failure this catches is the one the requirement exists for: two independently maintained
    lists that answer the same question with different identities, or a reverse view that invents
    its own copy of an edge instead of reading the stored one.
    """

    with fixture.reopen() as store:
        forward = realizations.list_claims_for_invariant_revision(store, fixture.base_revision_id)
        integration = realizations.list_claims_for_anchor(store, fixture.integration.anchor_id)
        synchronization = realizations.list_claims_for_anchor(
            store, fixture.synchronization.anchor_id
        )
        absent = realizations.list_claims_for_anchor(store, fixture.absent_source.anchor_id)
    assert {claim.claim_id for claim in forward.claims} == {
        fixture.integration.claim_id,
        fixture.synchronization.claim_id,
        fixture.absent_source.claim_id,
    }
    for reverse in (integration, synchronization, absent):
        assert len(reverse.claims) == 1
        assert reverse.claims[0] in forward.claims
    assert integration.claims[0].claim_id == fixture.integration.claim_id
    assert integration.claims[0].anchor_id == fixture.integration.anchor_id
    assert integration.claims[0].invariant_revision_id == fixture.base_revision_id
    assert synchronization.claims[0].claim_id == fixture.synchronization.claim_id


def test_overlapping_families_answer_both_directions_with_the_same_member_ids(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """One call answers "who is in this family", the other "which families hold this revision".

    The sibling revision belongs to both families while the realized revision belongs to exactly
    one, so the two directions are distinguishable; a reader that expanded a family transitively
    without being asked would answer the sibling's second family from the realized revision too.
    The identity comparison is what makes the two directions the same rows rather than two
    authored answers.
    """

    with fixture.reopen() as store:
        first_family = memberships.list_members(store, fixture.family.revision_id)
        overlapping_family = memberships.list_members(store, fixture.overlapping_family.revision_id)
        realized = memberships.list_families_for_invariant_revision(store, fixture.base_revision_id)
        sibling = memberships.list_families_for_invariant_revision(
            store, fixture.second_revision_id
        )
    assert {member.member_id for member in first_family.members} == set(fixture.family.member_ids)
    assert {member.member_id for member in overlapping_family.members} == set(
        fixture.overlapping_family.member_ids
    )
    assert [member.family_revision_id for member in realized.members] == [
        fixture.family.revision_id
    ]
    assert {member.family_revision_id for member in sibling.members} == {
        fixture.family.revision_id,
        fixture.overlapping_family.revision_id,
    }
    assert set(realized.members) <= set(first_family.members)
    assert set(sibling.members) <= set(first_family.members) | set(overlapping_family.members)


def test_an_absent_source_anchor_is_retained_exactly_as_authored(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """An anchor whose path exists in no snapshot keeps its path, blob and locator.

    Storage never resolves a source, so an unavailable location is a resolution fact for a later
    reader rather than a reason to drop the anchor. A store that checked the filesystem or a Git
    object here would lose the historical attribution the requirement keeps.
    """

    with fixture.reopen() as store:
        anchor = anchors.get_anchor(store, fixture.absent_source.anchor_id)
        claims = realizations.list_claims_for_anchor(store, fixture.absent_source.anchor_id)
    assert anchor is not None
    assert anchor.path == "src/retired_adapter.py"
    assert anchor.source_identity.kind == "git_blob"
    assert len(anchor.source_identity.object_id) == 40
    assert anchor.locator.kind == "file"
    assert anchor.provenance == fixture.authorship
    assert [claim.claim_id for claim in claims.claims] == [fixture.absent_source.claim_id]


def test_a_symbol_locator_is_stored_and_read_back_while_no_resolver_supports_it(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The stored locator union accepts a symbol record that the initial resolver cannot resolve.

    Rejecting it at the storage boundary would make a legitimate historical anchor unwritable, and
    a second locator shape invented for storage would diverge from the one a reader parses.
    """

    anchor_id = str(uuid4())
    locator = SymbolLocator(language="python", qualified_name="module.function")
    with fixture.reopen() as store:
        created = anchors.create_source_anchor(
            store,
            SourceAnchorRequest(
                repository_id=fixture.repository_id,
                anchor=anchor_draft(
                    AnchorSeed(anchor_id=anchor_id, path="src/symbolic.py", locator=locator)
                ),
                provenance=fixture.authorship,
            ),
        )
        stored = anchors.get_anchor(store, anchor_id)
    assert created.state == "created"
    assert stored is not None
    assert str(stored.anchor_id) == anchor_id
    assert stored.path == "src/symbolic.py"
    assert stored.locator == locator
    assert (
        stored.source_identity.object_id
        == anchor_draft(
            AnchorSeed(anchor_id=anchor_id, path="src/symbolic.py")
        ).source_identity.object_id
    )
    assert stored.provenance == fixture.authorship


def test_a_removed_claim_stays_in_the_baseline_and_is_absent_from_the_successor(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The successor dataset drops one claim; the baseline keeps it with the same row digest.

    A comparison needs both sides. An always-current pointer would answer the baseline side with
    the successor's post-removal state, and the removed attribution would become unobservable.
    """

    successor = build_removed_relation_successor(fixture, tmp_path / "successor")
    with fixture.reopen() as baseline:
        still_there = realizations.get_realization_claim(baseline, fixture.synchronization.claim_id)
        baseline_claims = realizations.list_claims_for_invariant_revision(
            baseline, fixture.base_revision_id
        )
    with successor.reopen() as candidate:
        removed = realizations.get_realization_claim(candidate, successor.removed_claim_id)
        candidate_claims = realizations.list_claims_for_invariant_revision(
            candidate, fixture.base_revision_id
        )
        reverse = realizations.list_claims_for_anchor(candidate, fixture.synchronization.anchor_id)
    assert still_there is not None
    assert still_there.row_digest == successor.removed_claim_row_digest
    assert still_there.claim_id == successor.removed_claim_id
    assert removed is None
    assert reverse.claims == ()
    assert {claim.claim_id for claim in baseline_claims.claims} - {
        claim.claim_id for claim in candidate_claims.claims
    } == {successor.removed_claim_id}
    assert set(successor.unchanged_claim_ids) == {
        claim.claim_id for claim in candidate_claims.claims
    }
    assert fixture.integration.claim_id in successor.unchanged_claim_ids
    assert successor.database_path != fixture.database_path


def test_a_family_revision_read_reports_the_family_it_belongs_to(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The family a stored revision belongs to is readable without decoding the aggregate.

    The write path's predecessor check depends on this ownership read, so a revision whose family
    cannot be named there would silently become an unrefusable predecessor.
    """

    with fixture.reopen() as store:
        successor_family = families.family_id_of_revision(
            store, fixture.family_successor_revision_id
        )
        overlapping_family = families.family_id_of_revision(
            store, fixture.overlapping_family.revision_id
        )
        unknown = families.family_id_of_revision(store, str(uuid4()))
        revisions = families.list_family_revision_ids(store, fixture.family.family_id)
    assert successor_family == fixture.family.family_id
    assert overlapping_family == fixture.overlapping_family.family_id
    assert unknown is None
    assert set(revisions) == {
        fixture.family.revision_id,
        fixture.family_successor_revision_id,
        fixture.family_sibling_revision_id,
    }
