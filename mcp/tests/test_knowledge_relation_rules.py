"""Focused behaviour of the relation writes: anchors, memberships and realization claims.

Each case protects one consequential operation or failure: an endpoint the database and the
operation both constrain, a pair that may be related only once, an identity reuse that must not
replace a stored relation, a new anchor that must roll back with the claim that carried it, a
removal that must name the row it expects, and the database-level triggers that keep a stored
relation from being re-pointed. Constructor validation is not re-tested here.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge import (
    admitted_anchor_request,
    admitted_claim_request,
    admitted_family_request,
    admitted_family_revision_request,
    admitted_knowledge_destination,
    admitted_member_request,
    create_knowledge_anchor,
    create_knowledge_family,
    create_knowledge_family_member,
    create_knowledge_family_revision,
    create_knowledge_realization_claim,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.memory.knowledge import anchors, families, memberships, realizations
from agents_remember.models.knowledge.family import (
    FamilyDraft,
    FamilyRevisionDraft,
)
from agents_remember.models.knowledge.graph import (
    FamilyMemberDraft,
    RealizationClaimDraft,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    AnchorReference,
    FamilyRequest,
    FamilyRevisionRequest,
    InvariantRequest,
    NewAnchor,
    RemoveFamilyMemberRequest,
    RemoveRealizationClaimRequest,
    RemoveSourceAnchorRequest,
    RevisionDraft,
    RevisionRequest,
    SourceAnchorRequest,
)
from knowledge_fixture_test_support import (
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
    make_authorship,
)
from knowledge_graph_test_support import (
    AnchorSeed,
    ClaimSeed,
    MemberSeed,
    anchor_draft,
    claim_request,
    member_request,
    table_counts,
)


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "graph")


def test_a_new_anchor_and_its_claim_are_one_transaction(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A refusal after the anchor was written rolls the anchor back with the claim.

    The refusal is reachable, not simulated: an author who reuses a claim identity while recording
    a new location gets a duplicate-identity refusal, and the new anchor must not survive as an
    orphan row that no claim ever cited. Only the pair being one transaction makes that true.
    """

    orphan_anchor_id = str(uuid4())
    with fixture.reopen() as store:
        counts_before = table_counts(store)
        refused = realizations.create_realization_claim(
            store,
            claim_request(
                fixture,
                ClaimSeed(
                    claim_id=fixture.integration.claim_id,
                    invariant_revision_id=fixture.second_revision_id,
                    anchor=NewAnchor(
                        anchor=anchor_draft(
                            AnchorSeed(anchor_id=orphan_anchor_id, path="src/a_new_location.py")
                        )
                    ),
                ),
            ),
        )
        counts_after = table_counts(store)
        orphan = anchors.get_anchor(store, orphan_anchor_id)
        original = realizations.get_realization_claim(store, fixture.integration.claim_id)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "duplicate_identity"
    assert orphan is None
    assert counts_after == counts_before
    assert original is not None
    assert original.invariant_revision_id == fixture.base_revision_id
    assert original.anchor_id == fixture.integration.anchor_id


def test_a_membership_endpoint_that_does_not_exist_refuses_and_writes_nothing(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Both membership endpoints are checked before any row is written."""

    with fixture.reopen() as store:
        counts_before = table_counts(store)
        missing_family_revision = memberships.create_family_member(
            store,
            member_request(
                fixture,
                MemberSeed(
                    member_id=str(uuid4()),
                    family_revision_id=str(uuid4()),
                    invariant_revision_id=fixture.base_revision_id,
                ),
            ),
        )
        missing_invariant_revision = memberships.create_family_member(
            store,
            member_request(
                fixture,
                MemberSeed(
                    member_id=str(uuid4()),
                    family_revision_id=fixture.family.revision_id,
                    invariant_revision_id=str(uuid4()),
                ),
            ),
        )
        counts_after = table_counts(store)
    assert missing_family_revision.state == "refused"
    assert missing_family_revision.refusal is not None
    assert missing_family_revision.refusal.code == "invalid_reference"
    assert missing_family_revision.refusal.table == "family_revision"
    assert missing_invariant_revision.state == "refused"
    assert missing_invariant_revision.refusal is not None
    assert missing_invariant_revision.refusal.code == "invalid_reference"
    assert missing_invariant_revision.refusal.table == "invariant_revision"
    assert missing_invariant_revision.refusal.operation == "create_family_member"
    assert counts_after == counts_before


def test_a_realization_of_a_missing_anchor_refuses_and_writes_nothing(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A claim that cites an anchor nobody stored refuses with the anchor named."""

    missing_anchor = str(uuid4())
    with fixture.reopen() as store:
        counts_before = table_counts(store)
        refused = realizations.create_realization_claim(
            store,
            claim_request(
                fixture,
                ClaimSeed(
                    claim_id=str(uuid4()),
                    invariant_revision_id=fixture.base_revision_id,
                    anchor=AnchorReference(anchor_id=missing_anchor),
                ),
            ),
        )
        counts_after = table_counts(store)
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "invalid_reference"
    assert refused.refusal.record_id == missing_anchor
    assert refused.refusal.table == "source_anchor"
    assert counts_after == counts_before


def test_the_same_endpoint_pair_is_related_only_once(fixture: BranchingKnowledgeFixture) -> None:
    """A second membership or claim for an already related pair refuses under a new identity.

    The declared unique tuples are what stop one relationship from being authored twice; a store
    without that constraint would answer both directions with two rows that look like two facts.
    """

    with fixture.reopen() as store:
        counts_before = table_counts(store)
        member = memberships.create_family_member(
            store,
            member_request(
                fixture,
                MemberSeed(
                    member_id=str(uuid4()),
                    family_revision_id=fixture.family.revision_id,
                    invariant_revision_id=fixture.base_revision_id,
                ),
            ),
        )
        claim = realizations.create_realization_claim(
            store,
            claim_request(
                fixture,
                ClaimSeed(
                    claim_id=str(uuid4()),
                    invariant_revision_id=fixture.base_revision_id,
                    anchor=AnchorReference(anchor_id=fixture.integration.anchor_id),
                ),
            ),
        )
        counts_after = table_counts(store)
    assert member.state == "refused"
    assert member.refusal is not None
    assert member.refusal.code == "relationship_constraint"
    assert member.refusal.table == "family_member"
    assert member.refusal.observed == fixture.family.member_ids[0]
    assert claim.state == "refused"
    assert claim.refusal is not None
    assert claim.refusal.code == "relationship_constraint"
    assert claim.refusal.table == "realization_claim"
    assert claim.refusal.observed == fixture.integration.claim_id
    assert counts_after == counts_before


def test_a_reused_relation_identity_with_other_endpoints_refuses(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Reusing an identity for a different relation refuses and leaves the stored row intact."""

    with fixture.reopen() as store:
        before = realizations.get_realization_claim(store, fixture.integration.claim_id)
        refused = realizations.create_realization_claim(
            store,
            claim_request(
                fixture,
                ClaimSeed(
                    claim_id=fixture.integration.claim_id,
                    invariant_revision_id=fixture.base_revision_id,
                    anchor=AnchorReference(anchor_id=fixture.synchronization.anchor_id),
                    rationale="A different attribution behind an identity already in use.",
                ),
            ),
        )
        after = realizations.get_realization_claim(store, fixture.integration.claim_id)
    assert before is not None and after is not None
    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "duplicate_identity"
    assert refused.refusal.expected == before.row_digest
    assert refused.refusal.observed not in (None, refused.refusal.expected)
    assert after == before
    assert after.anchor_id == fixture.integration.anchor_id


def test_a_realization_role_is_authored_vocabulary(fixture: BranchingKnowledgeFixture) -> None:
    """A role is either one of the declared words or the explicit "not classified" value.

    The store never infers a role from a file, so an undeclared word must fail at the vocabulary
    boundary instead of being stored as an arbitrary string a reader would have to interpret.
    """

    with pytest.raises(ValueError, match="role"):
        claim_request(
            fixture,
            ClaimSeed(
                claim_id=str(uuid4()),
                invariant_revision_id=fixture.base_revision_id,
                anchor=AnchorReference(anchor_id=fixture.integration.anchor_id),
                role="definitely-enforces-it",  # type: ignore[arg-type]
            ),
        )
    with fixture.reopen() as store:
        created = realizations.create_realization_claim(
            store,
            claim_request(
                fixture,
                ClaimSeed(
                    claim_id=str(uuid4()),
                    invariant_revision_id=fixture.third_revision_id,
                    anchor=NewAnchor(
                        anchor=anchor_draft(
                            AnchorSeed(anchor_id=str(uuid4()), path="src/unclassified.py")
                        )
                    ),
                    role="unclassified",
                    rationale="The author recorded the location without classifying its role.",
                ),
            ),
        )
        stored = realizations.get_realization_claim(store, created.claim_id)
    assert created.state == "created"
    assert stored is not None
    assert stored.role == "unclassified"
    assert stored.rationale == "The author recorded the location without classifying its role."


def test_an_explicit_removal_requires_the_row_the_caller_expects(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A removal by identity and expected digest refuses an absent row and a changed row.

    Without it, a stale caller would delete whatever now carries the identity it remembered. The
    stored row must survive both refusals byte for byte, because its digest enters the comparison.
    """

    with fixture.reopen() as store:
        before = realizations.get_realization_claim(store, fixture.synchronization.claim_id)
        assert before is not None
        counts_before = table_counts(store)
        absent = realizations.remove_realization_claim(
            store,
            RemoveRealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim_id=str(uuid4()),
                expected_row_digest="0" * 64,
            ),
        )
        stale = realizations.remove_realization_claim(
            store,
            RemoveRealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim_id=fixture.synchronization.claim_id,
                expected_row_digest="1" * 64,
            ),
        )
        counts_after_refusals = table_counts(store)
        survivor = realizations.get_realization_claim(store, fixture.synchronization.claim_id)
        removed = realizations.remove_realization_claim(
            store,
            RemoveRealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim_id=fixture.synchronization.claim_id,
                expected_row_digest=before.row_digest,
            ),
        )
        counts_after_removal = table_counts(store)
        gone = realizations.get_realization_claim(store, fixture.synchronization.claim_id)
    assert absent.state == "refused"
    assert absent.refusal is not None
    assert absent.refusal.code == "missing_expected_row"
    assert stale.state == "refused"
    assert stale.refusal is not None
    assert stale.refusal.code == "stale_precondition"
    assert stale.refusal.expected == "1" * 64
    assert stale.refusal.observed == before.row_digest
    assert survivor == before
    assert counts_after_refusals == counts_before
    assert removed.state == "removed"
    assert removed.refusal is None
    assert gone is None
    assert counts_after_removal["realization_claim"] == counts_before["realization_claim"] - 1
    assert counts_after_removal["family_member"] == counts_before["family_member"]
    assert counts_after_removal["source_anchor"] == counts_before["source_anchor"]


def test_a_cited_anchor_cannot_be_removed_and_an_uncited_one_can(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Removal refuses while a claim cites the anchor, and succeeds for an unreferenced one.

    An anchor is not retired because a source disappeared, and it is not deleted out from under a
    stored attribution: the claim has to be removed first, explicitly.
    """

    spare_anchor_id = str(uuid4())
    with fixture.reopen() as store:
        created = anchors.create_source_anchor(
            store,
            SourceAnchorRequest(
                repository_id=fixture.repository_id,
                anchor=anchor_draft(AnchorSeed(anchor_id=spare_anchor_id, path="src/spare.py")),
                provenance=fixture.authorship,
            ),
        )
        cited = anchors.remove_source_anchor(
            store,
            RemoveSourceAnchorRequest(
                repository_id=fixture.repository_id, anchor_id=fixture.integration.anchor_id
            ),
        )
        still_cited = anchors.get_anchor(store, fixture.integration.anchor_id)
        removed = anchors.remove_source_anchor(
            store,
            RemoveSourceAnchorRequest(
                repository_id=fixture.repository_id, anchor_id=spare_anchor_id
            ),
        )
        spare = anchors.get_anchor(store, spare_anchor_id)
        unknown = anchors.remove_source_anchor(
            store,
            RemoveSourceAnchorRequest(repository_id=fixture.repository_id, anchor_id=str(uuid4())),
        )
    assert created.state == "created"
    assert cited.state == "refused"
    assert cited.refusal is not None
    assert cited.refusal.code == "relationship_constraint"
    assert cited.refusal.observed == fixture.integration.claim_id
    assert still_cited is not None
    assert removed.state == "removed"
    assert spare is None
    assert unknown.state == "refused"
    assert unknown.refusal is not None
    assert unknown.refusal.code == "missing_expected_row"


def test_relation_payloads_refuse_an_in_place_rewrite(fixture: BranchingKnowledgeFixture) -> None:
    """The database refuses a repointed membership or a rewritten claim or anchor payload.

    These rows have no stored digest of their own, so the trigger is the only thing that keeps a
    relation from being silently re-pointed at different endpoints after it was authored.
    """

    with fixture.reopen() as store:
        before = memberships.get_family_member(store, fixture.family.member_ids[0])
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE family_member SET invariant_revision_id = ? WHERE member_id = ?",
                (fixture.third_revision_id, fixture.family.member_ids[0]),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE realization_claim SET rationale = ? WHERE claim_id = ?",
                ("A rewritten rationale.", fixture.integration.claim_id),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE source_anchor SET path = ? WHERE anchor_id = ?",
                ("src/moved.py", fixture.integration.anchor_id),
            )
        after = memberships.get_family_member(store, fixture.family.member_ids[0])
    assert after == before


def test_foreign_keys_are_enforced_on_the_relation_tables(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The opened connection enforces the declared endpoint foreign keys.

    Without the pragma a deferred constraint never fires, and every "the database refuses it"
    statement in this module would be an intention rather than a guarantee.
    """

    with fixture.reopen() as store:
        assert next(iter(store.connection.execute("PRAGMA foreign_keys")))[0] == 1
        with pytest.raises(apsw.ConstraintError, match="FOREIGN KEY"):
            store.connection.execute(
                "INSERT INTO family_member "
                "(repository_id, member_id, family_revision_id, invariant_revision_id, provenance) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    fixture.repository_id,
                    str(uuid4()),
                    fixture.family.revision_id,
                    str(uuid4()),
                    "{}",
                ),
            )
        with pytest.raises(apsw.ConstraintError, match="FOREIGN KEY"):
            store.connection.execute(
                "DELETE FROM source_anchor WHERE repository_id = ? AND anchor_id = ?",
                (fixture.repository_id, fixture.integration.anchor_id),
            )


def test_graph_operations_refuse_another_repository_namespace(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Every new operation refuses a request addressed to a namespace it is not bound to."""

    foreign = str(uuid4())
    with fixture.reopen() as store:
        family = families.create_family(
            store,
            FamilyRequest(
                repository_id=foreign,
                family_id=str(uuid4()),
                display_label="a family in another namespace",
                provenance=make_authorship(),
            ),
        )
        revision = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=foreign,
                revision=FamilyRevisionDraft(
                    family_id=str(uuid4()),
                    revision_id=str(uuid4()),
                    display_version="v1",
                    joint_guarantee="A guarantee in another namespace.",
                    provenance=make_authorship(),
                ),
            ),
        )
        anchor = anchors.create_source_anchor(
            store,
            SourceAnchorRequest(
                repository_id=foreign,
                anchor=anchor_draft(AnchorSeed(anchor_id=str(uuid4()), path="src/elsewhere.py")),
                provenance=make_authorship(),
            ),
        )
        member = memberships.create_family_member(
            store,
            member_request(
                fixture,
                MemberSeed(
                    member_id=str(uuid4()),
                    family_revision_id=str(uuid4()),
                    invariant_revision_id=str(uuid4()),
                ),
            ).model_copy(update={"repository_id": foreign}),
        )
        claim = realizations.create_realization_claim(
            store,
            claim_request(
                fixture,
                ClaimSeed(
                    claim_id=str(uuid4()),
                    invariant_revision_id=fixture.base_revision_id,
                    anchor=AnchorReference(anchor_id=fixture.integration.anchor_id),
                ),
            ).model_copy(update={"repository_id": foreign}),
        )
        anchor_removal = anchors.remove_source_anchor(
            store, RemoveSourceAnchorRequest(repository_id=foreign, anchor_id=str(uuid4()))
        )
        member_removal = memberships.remove_family_member(
            store,
            RemoveFamilyMemberRequest(
                repository_id=foreign, member_id=str(uuid4()), expected_row_digest="0" * 64
            ),
        )
        claim_removal = realizations.remove_realization_claim(
            store,
            RemoveRealizationClaimRequest(
                repository_id=foreign, claim_id=str(uuid4()), expected_row_digest="0" * 64
            ),
        )
        repository = store.get_repository()
    for result in (
        family,
        revision,
        anchor,
        member,
        claim,
        anchor_removal,
        member_removal,
        claim_removal,
    ):
        assert result.state == "refused"
        assert result.refusal is not None
        assert result.refusal.code == "unauthorized_scope"
        assert result.refusal.expected == fixture.repository_id
        assert result.refusal.observed == foreign
    assert repository is not None and repository.repository_id == fixture.repository_id


def test_the_application_seam_authors_a_graph_through_an_admitted_destination(
    tmp_path: Path,
) -> None:
    """The composed application calls create the graph with the destination's own provenance.

    This is the path a later leaf consumes, so it is exercised directly: provenance must come from
    the admitted destination rather than from anything the caller supplied, and the read handle
    must return the authored rows.
    """

    repository_id, invariant_id, revision_id = str(uuid4()), str(uuid4()), str(uuid4())
    family_id, family_revision_id = str(uuid4()), str(uuid4())
    anchor_id, claim_id, member_id = str(uuid4()), str(uuid4()), str(uuid4())
    authorship = write_authorship(
        actor_ref="agent:composed",
        authorization_ref="260915-KS developer kickoff ruling",
        origin_refs=("requirement:KS-R02@v1",),
    )
    destination = admitted_knowledge_destination(
        tmp_path / "composed" / "graph.db",
        RepositoryIdentity(repository_id=repository_id, authority_home="agents-remember"),
        authorship,
    )
    assert initialize_knowledge_namespace(destination).state == "created"
    with open_admitted_knowledge_store(destination) as store:
        store.create_invariant(
            InvariantRequest(
                repository_id=repository_id,
                invariant_id=invariant_id,
                display_label="a composed invariant",
                provenance=authorship,
            )
        )
        store.create_revision(
            RevisionRequest(
                repository_id=repository_id,
                revision=RevisionDraft(
                    revision_id=revision_id,
                    invariant_id=invariant_id,
                    display_version="v1",
                    statement="A composed obligation.",
                    applicability="Every admitted candidate write.",
                    provenance=authorship,
                ),
            )
        )
    family = create_knowledge_family(
        destination,
        admitted_family_request(
            destination,
            FamilyDraft(
                family_id=family_id, display_label="a composed family", label_provenance=authorship
            ),
        ),
    )
    revision = create_knowledge_family_revision(
        destination,
        admitted_family_revision_request(
            destination,
            FamilyRevisionDraft(
                family_id=family_id,
                revision_id=family_revision_id,
                display_version="v1",
                joint_guarantee="A composed joint guarantee.",
                provenance=authorship,
            ),
        ),
    )
    anchor = create_knowledge_anchor(
        destination,
        admitted_anchor_request(
            destination,
            anchor_draft(AnchorSeed(anchor_id=anchor_id, path="src/composed.py")),
        ),
    )
    claim = create_knowledge_realization_claim(
        destination,
        admitted_claim_request(
            destination,
            RealizationClaimDraft(
                claim_id=claim_id,
                invariant_revision_id=revision_id,
                role="primary-authority",
                rationale="The composed entry point states the obligation.",
            ),
            AnchorReference(anchor_id=anchor_id),
        ),
    )
    member = create_knowledge_family_member(
        destination,
        admitted_member_request(
            destination,
            FamilyMemberDraft(
                member_id=member_id,
                family_revision_id=family_revision_id,
                invariant_revision_id=revision_id,
                provenance=authorship,
            ),
        ),
    )
    assert (family.state, revision.state, anchor.state, claim.state, member.state) == (
        "created",
        "created",
        "created",
        "created",
        "created",
    )
    with open_admitted_knowledge_store(destination) as store:
        stored_claim = realizations.get_realization_claim(store, claim_id)
        stored_member = memberships.get_family_member(store, member_id)
        reads = realizations.list_claims_for_invariant_revision(store, revision_id)
    assert stored_claim is not None and stored_member is not None
    assert stored_claim.provenance == authorship
    assert stored_member.provenance == authorship
    assert [item.claim_id for item in reads.claims] == [claim_id]
