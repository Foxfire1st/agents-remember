"""The selective recorded-scope read: selection policy, grouping, budget and absence.

These cases are hermetic in the sense that matters here: each one builds its own fixture through
the public write operations and reads it back through the real read path, with no shared database,
no Git repository and no publication. They therefore run in the unit-regression lane, which is the
lane the other knowledge-substrate modules already occupy; the cases that need a real Git tree, a
real publication boundary or a real anchor resolution are in
``test_knowledge_read_boundaries.py`` under ``integration``.

Every case is named for the property its own assertions measure. The packet's stopping rule is the
load-bearing one: for ``P -> I1``, ``F1 -> {I1, J1}`` and ``G1 -> {J1, K1}``, a read seeded at the
path, at ``I1`` or at exact ``F1`` returns ``I1``'s and ``J1``'s realizations and *advertises*
``J1``'s membership in ``G1`` -- and does not include ``K1``'s realizations.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from agents_remember.application.knowledge_read import (
    open_read_context,
    read_knowledge_scope,
    read_row_counts,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import logical_digest
from agents_remember.memory.knowledge.read import SelectionQuery, select_recorded_scope
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.read import (
    FamilyRevisionSeed,
    InvariantIdentitySeed,
    InvariantRevisionSeed,
    KnowledgeReadBudget,
    KnowledgeReadContext,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    PathSeed,
    ReadItem,
    continue_from_cursor,
)
from read_scope_test_support import ReadScopeFixture, build_read_scope_fixture

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def fixture(tmp_path: Path) -> ReadScopeFixture:
    """One fresh fixture per case: no case can observe another's fixture state."""

    return build_read_scope_fixture(tmp_path / "read-scope")


def context_for(fixture: ReadScopeFixture) -> KnowledgeReadContext:
    """Resolve the read context of the fixture's own dataset and its fixture Git tree.

    The tree is the fixture's real, committed one, so the anchors these cases select are observed
    against real bytes rather than reported as unresolved. A case that wants the no-tree context
    builds it itself.
    """

    return open_read_context(
        fixture.database_path,
        fixture.repository_id,
        repository_root=fixture.git_root,
        code_tree_id=fixture.git_tree_id,
    )


def read(
    fixture: ReadScopeFixture,
    seed: KnowledgeReadSeed,
    *,
    budget: KnowledgeReadBudget | None = None,
    continuation: str | None = None,
) -> KnowledgeReadResult:
    """Run one read through the application seam at the fixture's own snapshot."""

    return read_knowledge_scope(
        fixture.database_path,
        context_for(fixture),
        KnowledgeReadRequest(
            seed=seed,
            budget=budget or KnowledgeReadBudget(),
            continuation=continuation,
        ),
    )


def item_ids(result: KnowledgeReadResult) -> set[str]:
    """The primary item identities of one page, as a set."""

    assert result.page is not None
    return {item.item_id for item in result.page.items}


def kinds(result: KnowledgeReadResult, kind: str) -> list[ReadItem]:
    """The items of one kind on a page, in stream order."""

    assert result.page is not None
    return [item for item in result.page.items if item.kind == kind]


def walk_all_pages(
    fixture: ReadScopeFixture, seed: KnowledgeReadSeed, budget: KnowledgeReadBudget
) -> Iterator[KnowledgeReadResult]:
    """Yield every page of one selection, following its own continuation to the end.

    The walk asserts its own termination property as it goes: a page that claims to have no more
    items must carry no continuation, and a page that has more must carry one. A walk that could
    loop is not allowed to hide that by returning early.
    """

    continuation: str | None = None
    seen_positions: set[int] = set()
    while True:
        result = read(fixture, seed, budget=budget, continuation=continuation)
        assert result.state == "page", result.refusal
        assert result.page is not None
        if continuation is not None:
            cursor = continue_from_cursor(continuation)
            assert cursor is not None
            assert cursor.position not in seen_positions, "the walk revisited a cursor position"
            seen_positions.add(cursor.position)
        yield result
        if not result.page.has_more:
            assert result.page.continuation is None
            return
        assert result.page.continuation is not None
        continuation = result.page.continuation


# --- the packet's stopping rule ------------------------------------------------------------


def test_a_path_seed_returns_the_sibling_realizations_and_advertises_the_unreached_family(
    fixture: ReadScopeFixture,
) -> None:
    """P seeds I1; F1 is entered directly, so I1's *and* J1's realizations appear, K1's do not.

    The assertions are the whole packet row: both of ``I1``'s locations, both of the sibling
    ``J1``'s claims at one location, one advertised ``G1`` membership, and no claim of ``K1`` --
    which is reachable only through ``G1`` and therefore only by selecting ``G1`` explicitly.
    """

    result = read(fixture, PathSeed(path=fixture.integration.path))

    assert result.state == "page", result.refusal
    assert result.page is not None
    claim_ids = {item.claim_id for item in result.page.items if item.kind == "realization_claim"}
    assert claim_ids == {
        fixture.integration.claim_id,
        fixture.synchronization.claim_id,
        fixture.batch_primary.claim_id,
        fixture.batch_secondary.claim_id,
        fixture.auxiliary.claim_id,
        fixture.absent_anchor.claim_id,
        fixture.mismatch_anchor.claim_id,
    }, "every claim of every selected revision is returned, whatever its anchor resolves to"
    assert fixture.resolution.claim_id not in claim_ids, "K1 is reachable only through G1"
    advertised = kinds(result, "advertised_family")
    assert len(advertised) == 1
    assert advertised[0].family_id == fixture.overlapping_family.family_id
    assert advertised[0].invariant_revision_id == fixture.batch_revision_id


def test_an_exact_invariant_revision_seed_selects_that_revision_and_its_directly_containing_families(
    fixture: ReadScopeFixture,
) -> None:
    """An explicit I1 seed selects I1, every family directly containing it, and their members.

    ``I1`` belongs to two families directly -- ``F1`` and ``H1`` -- so both are entered, ``J1``
    arrives through ``F1`` and the auxiliary revision through ``H1``. ``J1``'s own membership in
    ``G1`` is advertised rather than traversed, which is what keeps ``K1`` out.
    """

    result = read(
        fixture,
        InvariantRevisionSeed(
            invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
        ),
    )

    assert result.state == "page", result.refusal
    assert result.page is not None
    revision_ids = {
        item.revision_id for item in result.page.items if item.kind == "invariant_revision"
    }
    assert revision_ids == {
        fixture.subject_revision_id,
        fixture.batch_revision_id,
        fixture.auxiliary_revision_id,
    }
    family_ids = {item.family_id for item in result.page.items if item.kind == "family_revision"}
    assert family_ids == {fixture.family.family_id, fixture.direct_family.family_id}
    assert {family.family_id for family in result.directly_containing_families} == {
        fixture.family.family_id,
        fixture.direct_family.family_id,
    }
    assert result.page.counts.advertised_expansions_total == 1


def test_an_exact_family_revision_seed_stops_after_its_own_members(
    fixture: ReadScopeFixture,
) -> None:
    """Exact F1 selects F1's members only, and advertises where each member otherwise belongs.

    This is the family half of the same rule read the other way round: ``F1`` is ``{I1, J1}``, so
    ``G1`` is not entered even though ``J1`` is in it, and ``H1`` is not entered even though ``I1``
    is in it. Both appear as advertised expansions instead of as further traversal.
    """

    result = read(
        fixture,
        FamilyRevisionSeed(
            family_id=fixture.family.family_id, revision_id=fixture.family.revision_id
        ),
    )

    assert result.state == "page", result.refusal
    assert result.page is not None
    assert {item.family_id for item in result.page.items if item.kind == "family_revision"} == {
        fixture.family.family_id
    }
    assert {
        item.revision_id for item in result.page.items if item.kind == "invariant_revision"
    } == {
        fixture.subject_revision_id,
        fixture.batch_revision_id,
    }
    advertised_families = {
        item.family_id for item in result.page.items if item.kind == "advertised_family"
    }
    assert advertised_families == {
        fixture.overlapping_family.family_id,
        fixture.direct_family.family_id,
    }
    claim_ids = {item.claim_id for item in result.page.items if item.kind == "realization_claim"}
    assert fixture.resolution.claim_id not in claim_ids
    assert fixture.auxiliary.claim_id not in claim_ids


def test_selecting_the_advertised_family_explicitly_is_what_reaches_the_further_realizations(
    fixture: ReadScopeFixture,
) -> None:
    """The advertised G1 expansion, selected explicitly, is the only way K1's claims appear.

    This case is the positive half of the stopping rule: it proves the frontier link is *actionable*
    rather than decorative, and that acting on it is a new explicit selection at the same snapshot
    and not an automatic traversal.
    """

    result = read(
        fixture,
        FamilyRevisionSeed(
            family_id=fixture.overlapping_family.family_id,
            revision_id=fixture.overlapping_family.revision_id,
        ),
    )

    assert result.state == "page", result.refusal
    assert result.page is not None
    claim_ids = {item.claim_id for item in result.page.items if item.kind == "realization_claim"}
    assert claim_ids == {
        fixture.batch_primary.claim_id,
        fixture.batch_secondary.claim_id,
        fixture.resolution.claim_id,
    }
    assert fixture.integration.claim_id not in claim_ids


def test_the_invariant_seed_and_the_family_seed_enumerate_the_same_claims_and_locations(
    fixture: ReadScopeFixture,
) -> None:
    """Exact F1 selects F1's members only; exact I1 also enters H1, and that is the whole difference.

    The packet's rule read in both directions at once. ``I1`` belongs to ``F1`` *and* ``H1``, so the
    invariant seed closes over ``F1``'s sibling as well as ``H1``'s; ``F1`` holds only its own two
    members, so the family seed stops one step earlier. Both exclude ``K1``, which only ``G1``
    reaches, and both include every claim of every revision they select.
    """

    invariant_seed = InvariantRevisionSeed(
        invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
    )
    family_seed = FamilyRevisionSeed(
        family_id=fixture.family.family_id, revision_id=fixture.family.revision_id
    )
    observed = []
    for seed in (invariant_seed, family_seed):
        result = read(fixture, seed)
        assert result.state == "page", (seed, result.refusal)
        assert result.page is not None
        observed.append(
            (
                tuple(
                    sorted(
                        (item.claim_id, item.invariant_revision_id)
                        for item in result.page.items
                        if item.kind == "realization_claim"
                    )
                ),
                result.page.counts.distinct_source_locations_total,
                result.page.counts.primary_items_total,
            )
        )

    invariant_claims, invariant_locations, invariant_total = observed[0]
    family_claims, family_locations, family_total = observed[1]
    assert len(invariant_claims) == 7
    assert invariant_locations == 6
    assert invariant_total == 17
    assert len(family_claims) == 6
    assert family_locations == 5
    assert family_total == 13
    # The invariant seed's selection is the family seed's plus H1's supplementary member's
    # claims, and nothing else: that single step is the whole difference between the rules.
    assert {claim_id for claim_id, _ in family_claims} < {
        claim_id for claim_id, _ in invariant_claims
    }
    family_claim_ids = {claim_id for claim_id, _ in family_claims}
    assert {
        claim_id
        for claim_id, revision_id in invariant_claims
        if revision_id == fixture.auxiliary_revision_id
    } == {fixture.auxiliary.claim_id}
    assert fixture.auxiliary.claim_id not in family_claim_ids
    assert fixture.resolution.claim_id not in family_claim_ids
    assert fixture.resolution.claim_id not in {claim_id for claim_id, _ in invariant_claims}


def test_a_path_seed_and_the_exact_invariant_revision_seed_close_over_the_same_records(
    fixture: ReadScopeFixture,
) -> None:
    """A path at ``I1``'s location and an explicit ``I1`` select one identical set of records.

    The path rule and the invariant rule meet at the same place from two directions: the path
    resolves to ``I1`` through its claim, ``I1``'s directly containing families are entered either
    way, and the resulting manifest is the same. The supplementary revision that only ``H1`` carries
    is therefore in *both* selections, and ``K1`` -- reachable only through the family neither seed
    enters -- is in neither.
    """

    path_result = read(fixture, PathSeed(path=fixture.integration.path))
    identity_result = read(
        fixture,
        InvariantRevisionSeed(
            invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
        ),
    )
    assert path_result.page is not None
    assert identity_result.page is not None
    path_revisions = {
        item.revision_id for item in path_result.page.items if item.kind == "invariant_revision"
    }
    identity_revisions = {
        item.revision_id for item in identity_result.page.items if item.kind == "invariant_revision"
    }
    assert path_revisions == identity_revisions
    assert path_revisions == {
        fixture.subject_revision_id,
        fixture.batch_revision_id,
        fixture.auxiliary_revision_id,
    }
    assert (
        path_result.page.counts.primary_items_total
        == identity_result.page.counts.primary_items_total
        == 17
    )
    assert {item.item_id for item in path_result.page.items} == {
        item.item_id for item in identity_result.page.items
    }, "the same records are selected by either route"
    # The two manifests are *not* forced equal, and that is deliberate: a manifest seals each item's
    # selection reasons, and the route that reached an item is a fact about how it was selected. A
    # caller comparing two selections compares the item identities above, or the counts; the digest
    # is the identity of one selection, not a normalised form of it.
    assert path_result.seed_digest != identity_result.seed_digest
    for result in (path_result, identity_result):
        assert result.page is not None
        claim_ids = {
            item.claim_id for item in result.page.items if item.kind == "realization_claim"
        }
        assert {fixture.integration.claim_id, fixture.synchronization.claim_id} <= claim_ids
        assert {fixture.batch_primary.claim_id, fixture.batch_secondary.claim_id} <= claim_ids
        assert fixture.resolution.claim_id not in claim_ids


# --- revision grouping and the absent current-revision pointer ------------------------------


def test_an_identity_seed_returns_every_retained_revision_as_its_own_group(
    fixture: ReadScopeFixture,
) -> None:
    """I1, I2 and the predecessor all come back, grouped, with no successor selected as current.

    Two of the three revisions display the same ``v2`` label on purpose. The assertions check that
    the label is carried as authored text, that the group counts describe the whole selected
    revision set, and that nothing in the result ranks one revision above another.
    """

    result = read(fixture, InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id))

    assert result.state == "page", result.refusal
    assert result.page is not None
    revision_ids = {
        item.revision_id for item in result.page.items if item.kind == "invariant_revision"
    }
    assert {
        fixture.base_revision_id,
        fixture.subject_revision_id,
        fixture.successor_revision_id,
    } <= revision_ids, "every retained revision of the named identity is selected"
    assert fixture.resolution_revision_id not in revision_ids, "K1 is reachable only through G1"
    assert revision_ids == {
        fixture.base_revision_id,
        fixture.subject_revision_id,
        fixture.successor_revision_id,
        fixture.batch_revision_id,
        fixture.auxiliary_revision_id,
    }
    group = next(
        group for group in result.revision_groups if group.record_id == fixture.retry_invariant_id
    )
    assert group.selected_revision_count == 3
    assert {g.selected_revision_count for g in result.revision_groups} == {1, 3}
    labels = {
        item.display_version
        for item in result.page.items
        if item.kind == "invariant_revision"
        and item.revision_id in {fixture.subject_revision_id, fixture.successor_revision_id}
    }
    assert labels == {"v2"}, "both retained successors carry the same authored label"
    dumped = result.model_dump(mode="json")
    for forbidden in ("current", "latest", "accepted_revision", "is_current", "greatest"):
        assert forbidden not in _keys(dumped), (
            f"a read result must not carry a field that could name one revision as current: "
            f"{forbidden}"
        )


def test_an_exact_revision_seed_does_not_select_the_identitys_other_retained_revisions(
    fixture: ReadScopeFixture,
) -> None:
    """An explicit I1 seed returns I1 alone, and the successor stays unselected.

    The mirror of the identity case, and the assertion that makes "a display version selects
    nothing" checkable: the successor displays the same ``v2`` as the selected revision and is
    still absent.
    """

    result = read(
        fixture,
        InvariantRevisionSeed(
            invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
        ),
    )

    assert result.state == "page", result.refusal
    assert result.page is not None
    selected = [item.revision_id for item in result.page.items if item.kind == "invariant_revision"]
    assert fixture.successor_revision_id not in selected
    assert fixture.base_revision_id not in selected
    group = next(
        group for group in result.revision_groups if group.record_id == fixture.retry_invariant_id
    )
    assert group.selected_revision_count == 1


# --- counts ---------------------------------------------------------------------------------


def test_claim_identities_and_distinct_source_locations_are_counted_separately(
    fixture: ReadScopeFixture,
) -> None:
    """Two claims at one location are two claims and one location, in the counts and the items.

    ``J1`` carries both of its claims at ``src/batch.py``. A count that collapsed them by location
    would under-report the authored relationships, and one that split the shared location would
    over-report the source surface.
    """

    result = read(fixture, PathSeed(path=fixture.integration.path))

    assert result.state == "page", result.refusal
    assert result.page is not None
    batch_claims = [
        item
        for item in result.page.items
        if item.kind == "realization_claim"
        and item.anchor is not None
        and item.anchor.path == fixture.batch_primary.path
    ]
    assert {item.claim_id for item in batch_claims} == {
        fixture.batch_primary.claim_id,
        fixture.batch_secondary.claim_id,
    }
    assert result.page.counts.realization_claims_total == 7
    assert result.page.counts.distinct_source_locations_total == 6
    assert result.page.counts.distinct_source_paths_total == 6
    assert len(batch_claims) == 2, "two claim identities at one location stay two claims"
    assert (
        result.page.counts.realization_claims_total
        > result.page.counts.distinct_source_locations_total
    ), "the count of claim identities cannot be read as a count of source locations"


def test_a_revision_reached_through_two_families_appears_once_with_both_membership_rows(
    fixture: ReadScopeFixture,
) -> None:
    """A shared member is one item, and both recorded memberships that place it remain visible.

    Reported through the G1 seed, whose members are the shared ``J1`` and the further ``K1``: the
    two membership rows are distinct items while the invariant revision is one, so neither a
    duplicate revision nor a dropped membership can hide in the counts.
    """

    result = read(
        fixture,
        FamilyRevisionSeed(
            family_id=fixture.overlapping_family.family_id,
            revision_id=fixture.overlapping_family.revision_id,
        ),
    )

    assert result.state == "page", result.refusal
    assert result.page is not None
    memberships = kinds(result, "family_membership")
    assert {item.member_id for item in memberships} == set(fixture.overlapping_family.member_ids)
    assert result.page.counts.advertised_expansions_total == 1
    revisions = [
        item.revision_id
        for item in result.page.items
        if item.kind == "invariant_revision" and item.revision_id is not None
    ]
    assert sorted(revisions) == sorted({fixture.batch_revision_id, fixture.resolution_revision_id})
    assert len(revisions) == len(set(revisions))
    assert result.page.counts.memberships_total == 2
    assert result.page.counts.invariant_revisions_total == 2


# --- paging: completeness by continuation ---------------------------------------------------


def test_a_page_budget_of_one_item_still_advertises_the_second_location(
    fixture: ReadScopeFixture,
) -> None:
    """A one-item page cannot be read as a one-location scope, and the second location is reached.

    The packet's own example: the first page returns one claim, the counts still advertise the whole
    selected set, and following the continuation enumerates the remaining claims with no duplicate
    and no omission. The mandatory set the walk is measured against is derived from the fixture's
    stored rows and the requirement's path rule -- the claim at the seeded path selects that claim's
    invariant revision, whose directly containing families contribute their members' claims -- so a
    page that dropped or invented a claim cannot move its own target.
    """

    seed = PathSeed(path=fixture.integration.path)
    declared = _declared_path_seed_claim_ids(fixture)
    assert declared, "the seeded path has recorded claims to declare"

    pages = list(walk_all_pages(fixture, seed, KnowledgeReadBudget(max_items=1)))
    assert len(pages) > 1

    first = pages[0]
    assert first.page is not None
    assert len(first.page.items) == 1
    assert first.page.has_more is True
    assert first.page.enumeration_complete is False
    assert first.page.counts.realization_claims_total == 7
    assert first.page.counts.distinct_source_locations_total == 6

    union = [item for page_result in pages for item in item_ids(page_result)]
    assert len(union) == len(set(union)), "a claim id was emitted twice across pages"
    assert declared <= set(union), "every declared mandatory claim is reached by the walk"
    emitted_claims = {
        item.item_id
        for page_result in pages
        for item in (page_result.page.items if page_result.page is not None else ())
        if item.kind == "realization_claim"
    }
    assert emitted_claims == declared, "the walk emits exactly the declared mandatory claim set"

    for page_result in pages:
        assert page_result.page is not None
        counts = page_result.page.counts
        # One statement, measured on every page rather than only at position 0: the declared
        # selection total is what the walk is a walk *of*, and the returned/remaining figures are
        # its progress rather than a restatement of this page's slice.
        assert counts.primary_items_total == 17, (
            "a continuation page advertises the declared selection total, not the tail ahead of it"
        )
        assert counts.primary_items_returned + counts.primary_items_remaining == (
            counts.primary_items_total
        )
        assert counts.primary_items_remaining == 17 - counts.primary_items_returned

    walked = [
        page_result.page.counts.primary_items_returned for page_result in pages if page_result.page
    ]
    assert walked == list(range(1, len(pages) + 1)), (
        "the returned count is the walk's progress, one item per page at a one-item budget"
    )
    last = pages[-1]
    assert last.page is not None
    assert last.page.enumeration_complete is True
    assert last.page.counts.primary_items_remaining == 0


def _declared_path_seed_claim_ids(fixture: ReadScopeFixture) -> set[str]:
    """The claims a path seed must select, derived from the requirement's path rule alone.

    The packet's path rule: "All recorded realization claims at that repository/path ..., and their
    exact invariant revisions. Follow every family revision directly containing a seeded invariant
    revision; add those families' exact member revisions and all realization claims for the
    resulting invariant set. Stop there." Two statements against the stored rows execute it: the
    claims at the path, then the members of the families those claims' revisions are recorded in.
    """

    seeded = {fixture.integration.claim_id}
    connection = open_read_only_database(fixture.database_path)
    try:
        revisions = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT invariant_revision_id FROM realization_claim "
                "WHERE repository_id = ? AND claim_id = ?",
                (fixture.repository_id, fixture.integration.claim_id),
            )
        }
        families = {
            str(row[0])
            for revision_id in sorted(revisions)
            for row in connection.execute(
                "SELECT DISTINCT family_revision_id FROM family_member "
                "WHERE repository_id = ? AND invariant_revision_id = ?",
                (fixture.repository_id, revision_id),
            )
        }
        members = {
            str(row[0])
            for family_revision_id in sorted(families)
            for row in connection.execute(
                "SELECT invariant_revision_id FROM family_member "
                "WHERE repository_id = ? AND family_revision_id = ?",
                (fixture.repository_id, family_revision_id),
            )
        }
    finally:
        connection.close()
    assert revisions, "the seeded claim names a recorded revision"
    assert families, "the seeded revision is a recorded member of at least one family"
    return seeded | _claims_of_revisions(fixture, revisions | members)


def test_every_page_declares_the_same_snapshot_and_manifest_and_the_union_equals_the_selection(
    fixture: ReadScopeFixture,
) -> None:
    """All pages of one walk name one snapshot and one manifest, and together enumerate the declaration.

    This is the mechanical meaning of "complete by continuation": the union of the pages equals the
    **declared mandatory claim-id set**, with no duplicate claim identity and nothing hidden behind a
    page this walk never reached.

    The declared set is derived here from the requirement's own selection policy and the fixture's
    stored rows -- the family seed selects its exact members' revisions and every claim recorded at
    those revisions -- rather than from a page this implementation produced. Comparing the walk
    against a full-budget page of the same code would be self-consistency rather than completeness:
    it can show that paging is a lossless slice of one computation, but not that the computed set is
    the declared one. The independent query below reads the tables the fixture wrote through the
    public store operations, so a page builder that lost, duplicated or invented a claim cannot move
    the target it is measured against.
    """

    seed = FamilyRevisionSeed(
        family_id=fixture.family.family_id, revision_id=fixture.family.revision_id
    )
    declared = _declared_family_seed_claim_ids(fixture)
    assert declared, "the fixture's family seed has a declared mandatory claim set"

    pages = list(walk_all_pages(fixture, seed, KnowledgeReadBudget(max_items=2)))
    snapshots = {
        page_result.snapshot.model_dump_json()
        for page_result in pages
        if page_result.snapshot is not None
    }
    manifests = {page_result.manifest_digest for page_result in pages}
    assert len(snapshots) == 1, "a continuation changed the declared snapshot"
    assert len(manifests) == 1, "a continuation changed the declared manifest"

    ordered = [
        item.item_id
        for page_result in pages
        for item in (page_result.page.items if page_result.page is not None else ())
    ]
    assert len(ordered) == len(set(ordered))
    emitted_claims = [
        item.claim_id
        for page_result in pages
        for item in (page_result.page.items if page_result.page is not None else ())
        if item.kind == "realization_claim"
    ]
    assert len(emitted_claims) == len(set(emitted_claims)), "a claim id was emitted twice"
    assert set(emitted_claims) == declared, (
        "the union of every page is the declared mandatory claim set"
    )
    # The same walk is also where the declared total is measured: every page reports the whole
    # selected set, so a continuation page cannot advertise a smaller scope than the one it
    # continues (the packet's "a one-item page implies that the invariant has only one
    # implementation" is exactly this quantification read the other way round).
    totals = {
        page_result.page.counts.primary_items_total
        for page_result in pages
        if page_result.page is not None
    }
    assert len(totals) == 1, "every page of one walk declares the same selection total"
    assert len(ordered) == totals.pop()


def test_the_item_stream_is_ordered_by_stored_identity_and_never_by_an_authored_label(
    fixture: ReadScopeFixture,
) -> None:
    """The whole page's item order equals the documented key, so no authored text decides it.

    The ordering rule is part of the contract -- the same graph authored in another order must page
    identically -- and it is only checkable by comparing the emitted order against the key the policy
    declares. Two of the fixture's revisions deliberately share one display label, so an order that
    consulted a label or an insertion counter cannot reproduce this sequence.
    """

    # An identity seed is the one that carries more than one revision of a single identity, which is
    # the shape this rule needs: a page holding one revision of an identity cannot show whether an
    # order consulted a label.
    result = read(fixture, InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id))

    assert result.state == "page", result.refusal
    assert result.page is not None
    items = result.page.items
    kind_order = {
        "invariant_revision": 1,
        "family_revision": 2,
        "family_membership": 3,
        "realization_claim": 4,
        "advertised_family": 5,
    }
    first_key = {
        "invariant_revision": "invariant_id",
        "family_revision": "family_id",
        "family_membership": "family_revision_id",
        "realization_claim": "invariant_revision_id",
        "advertised_family": "family_revision_id",
    }
    second_key = {
        "invariant_revision": "revision_id",
        "family_revision": "revision_id",
        "family_membership": "member_id",
        "realization_claim": "claim_id",
        "advertised_family": "member_id",
    }

    def key(item: ReadItem) -> tuple[int, str, str, str]:
        dumped = item.model_dump()
        return (
            kind_order[item.kind],
            str(dumped.get(first_key[item.kind]) or ""),
            str(dumped.get(second_key[item.kind]) or ""),
            item.item_id,
        )

    keys = [key(item) for item in items]
    assert keys == sorted(keys), "the emitted page order is the declared lexical order"
    assert [item.kind for item in items] == sorted(
        (item.kind for item in items), key=lambda kind: kind_order[kind]
    ), "the item kinds appear in their declared order"

    # Within one identity, the revisions are ordered by their stored revision ids.
    by_identity: dict[str, list[str]] = {}
    for item in items:
        if item.kind != "invariant_revision" or item.revision_id is None:
            continue
        by_identity.setdefault(str(item.invariant_id), []).append(item.revision_id)
    assert by_identity, "the page carries at least one identity's revisions"
    for invariant_id, revision_ids in by_identity.items():
        assert revision_ids == sorted(revision_ids), (
            f"revisions of {invariant_id} are ordered by stored revision id, not by a label"
        )
    retry_revisions = by_identity.get(fixture.retry_invariant_id, [])
    assert retry_revisions == sorted(
        {fixture.base_revision_id, fixture.subject_revision_id, fixture.successor_revision_id}
    ), "every retained revision of the identity is present and in stored-id order"
    # The page carries authored labels whose order is *not* the emitted order, and the fixture
    # authors that disagreement so it is a property of the graph rather than of the draw: the
    # predecessors (the predecessor revision here, and the first revisions of the other three
    # identities) carry the label that sorts last, and the two successors share the label that sorts
    # before it. Asserted on the stored rows through the read result, so the disagreement is
    # measured rather than assumed.
    labels = {
        str(item.revision_id): str(item.display_version)
        for item in items
        if item.kind == "invariant_revision"
    }
    assert labels[fixture.subject_revision_id] == labels[fixture.successor_revision_id]
    assert labels[fixture.base_revision_id] != labels[fixture.subject_revision_id]
    # The disagreement between the emitted order and a label-consulting order is authored into the
    # fixture rather than left to the draw: the predecessor revision carries the label that sorts
    # last while its stored id sorts it last as well, and the successors share the label that sorts
    # before it while their stored ids sort them first. Both facts are asserted from the served page,
    # so a fixture change that made the comparison vacuous would fail here rather than pass quietly.
    shared_successor_label = labels[fixture.subject_revision_id]
    assert labels[fixture.base_revision_id] == max(labels.values()), (
        "the predecessor carries the label that sorts last among the served revisions"
    )
    assert retry_revisions[-1] == fixture.base_revision_id, (
        "the predecessor is emitted last among the identity's revisions"
    )
    assert labels[retry_revisions[0]] == shared_successor_label, (
        "the revision the stored-id order emits first carries the successors' shared label"
    )
    assert max(retry_revisions, key=lambda revision_id: labels[revision_id]) == (
        fixture.base_revision_id
    ), (
        "a label-first order over the identity's revisions emits the predecessor first, which is "
        "the opposite of the stored-id order"
    )
    kind_of = {item.item_id: item.kind for item in items}
    emitted_order = [item.item_id for item in items]
    label_order = sorted(
        emitted_order,
        key=lambda item_id: (kind_order[kind_of[item_id]], labels.get(item_id, ""), item_id),
    )
    assert emitted_order != label_order, (
        "a key that consulted the authored display label would emit a different sequence"
    )


def test_a_truncated_page_states_that_items_remain_rather_than_claiming_completeness(
    fixture: ReadScopeFixture,
) -> None:
    """A page that leaves items behind says so, and its own arithmetic adds up.

    Three fields are one statement: a page returns some items, leaves the rest, and the two add up to
    the selected total. This case asserts all three directly, so a page that reported a truncated
    selection as complete -- or whose counts contradicted themselves -- fails as a failed assertion
    rather than only as a construction error.
    """

    seed = PathSeed(path=fixture.integration.path)
    full = read(fixture, seed)
    assert full.page is not None
    total = full.page.counts.primary_items_total

    result = read(fixture, seed, budget=KnowledgeReadBudget(max_items=4))

    assert result.state == "page", result.refusal
    assert result.page is not None
    counts = result.page.counts
    assert len(result.page.items) == 4
    assert counts.primary_items_total == total
    assert counts.primary_items_returned == 4
    assert counts.primary_items_remaining == total - 4
    assert (
        counts.primary_items_returned + counts.primary_items_remaining == counts.primary_items_total
    )
    assert result.page.has_more is True
    assert result.page.enumeration_complete is False
    assert result.page.continuation is not None
    assert total > 4


def test_a_page_budget_that_cannot_hold_one_item_refuses_and_keeps_the_position(
    fixture: ReadScopeFixture,
) -> None:
    """A budget below one whole item refuses with the size that would fit it, and skips nothing.

    The refusal reports the minimum, and the same cursor read again with a sufficient budget
    returns the *same* item rather than the one after it, which is what makes a budget a
    presentation choice rather than a selection one.
    """

    seed = PathSeed(path=fixture.integration.path)
    tiny = read(fixture, seed, budget=KnowledgeReadBudget(max_items=1, max_utf8_bytes=64))

    assert tiny.state == "refused"
    assert tiny.refusal is not None
    assert tiny.refusal.code == "page_budget_too_small"
    assert tiny.refusal.expected == "64"
    minimum = int(tiny.refusal.observed or "0")
    assert minimum > 64
    assert tiny.page is None

    with_room = read(fixture, seed, budget=KnowledgeReadBudget(max_items=1, max_utf8_bytes=minimum))
    assert with_room.state == "page", with_room.refusal
    assert with_room.page is not None
    assert len(with_room.page.items) == 1
    full = read(fixture, seed)
    assert full.page is not None
    assert with_room.page.items[0].item_id == full.page.items[0].item_id


def test_a_selection_that_reaches_its_declared_bound_refuses_rather_than_reporting_a_total(
    fixture: ReadScopeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reaching the declared execution bound refuses and emits no guessed total or partial manifest.

    The bound exists so "too large to enumerate completely" is a state a caller is told about. The
    limit is lowered here rather than scaled up, because the alternative -- building a selection of
    the real bound's size -- measures the fixture's write throughput instead of this rule, and the
    rule is what must hold at every bound.
    """

    monkeypatch.setattr(
        "agents_remember.memory.knowledge.read.SELECTION_ITEM_LIMIT", 3, raising=True
    )
    result = read(fixture, PathSeed(path=fixture.integration.path))

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "selection_incomplete"
    assert result.refusal.expected == "at most 3 items"
    assert result.refusal.observed == "17"
    assert result.refusal.operation == "read_knowledge_scope"
    assert result.page is None, (
        "a selection that reached its bound carries no partial manifest and no guessed total"
    )


def test_the_declared_bound_admits_a_selection_that_fits_and_refuses_one_that_does_not(
    fixture: ReadScopeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound is a boundary: the same selection is served at the bound and refused below it.

    The control for the case above. A guard that refused every selection -- or that never refused --
    would satisfy a single-sided assertion, so both sides of the boundary are measured on one
    selection.
    """

    seed = PathSeed(path=fixture.integration.path)
    monkeypatch.setattr(
        "agents_remember.memory.knowledge.read.SELECTION_ITEM_LIMIT", 17, raising=True
    )
    at_bound = read(fixture, seed)
    assert at_bound.state == "page", at_bound.refusal
    assert at_bound.page is not None
    assert at_bound.page.counts.primary_items_total == 17

    monkeypatch.setattr(
        "agents_remember.memory.knowledge.read.SELECTION_ITEM_LIMIT", 16, raising=True
    )
    below_bound = read(fixture, seed)
    assert below_bound.state == "refused"
    assert below_bound.refusal is not None
    assert below_bound.refusal.code == "selection_incomplete"


# --- absence, refusals and the persisted-nothing property ----------------------------------


def test_an_unregistered_path_reports_registration_absence_rather_than_an_empty_scope(
    fixture: ReadScopeFixture,
) -> None:
    """A path with no recorded claim is explicit absence, not "no obligations" and not empty.

    The refusal names the path and the snapshot, and carries no claim that the path has no
    meaning: nothing here read a working tree, a Markdown document or a build of the code to
    decide what the path does.
    """

    result = read(fixture, PathSeed(path="src/never-recorded.py"))

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "registration_absent"
    assert result.refusal.record_id == "src/never-recorded.py"
    assert result.refusal.operation == "read_knowledge_scope"
    assert "semantic impact" not in result.refusal.next_action.lower().replace("not a semantic", "")


def test_a_selector_naming_no_recorded_identity_is_told_that_the_selector_is_absent(
    fixture: ReadScopeFixture,
) -> None:
    """An identity the snapshot does not hold is a wrong question, not an unregistered path.

    The two absence codes are separated because a caller acts on them differently, and this case
    is the one that keeps them from being folded together.
    """

    result = read(
        fixture, InvariantIdentitySeed(invariant_id="0" * 8 + "-0000-0000-0000-" + "0" * 12)
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "selector_absent"
    assert result.refusal.expected == "invariant identity"


def test_a_refused_read_leaves_every_table_and_the_logical_digest_unchanged(
    fixture: ReadScopeFixture,
) -> None:
    """A refusal persists nothing: measured row counts per table and the digest are identical.

    The measurement is the acceptance class this master uses for refusals, and it is taken through
    the same read-only handle the operation uses rather than asserted about the operation's
    intent. Three refusals are measured, and the third is the one that matters most: it is raised
    against a *continuation*, i.e. after a selection has been built, anchors resolved and one page
    already served, so the property is not only about a refusal raised before any work happened.
    """

    before_counts = read_row_counts(fixture.database_path)
    before_digest = _digest(fixture)

    unregistered = read(fixture, PathSeed(path="src/never-recorded.py"))
    assert unregistered.state == "refused"
    tiny = read(
        fixture,
        PathSeed(path=fixture.integration.path),
        budget=KnowledgeReadBudget(max_items=1, max_utf8_bytes=64),
    )
    assert tiny.state == "refused"
    # A page that succeeded, then a refusal at the *next* position of the same walk: the first page
    # is served with a full byte budget and hands back a continuation, and the same walk is then
    # continued under a byte ceiling that cannot hold its next item. The refusal is therefore raised
    # with a continuation in hand, after a selection was built and anchors resolved -- not before any
    # work happened -- which is what makes this the later-page half of the property.
    first_of_walk = read(
        fixture,
        PathSeed(path=fixture.integration.path),
        budget=KnowledgeReadBudget(max_items=1),
    )
    assert first_of_walk.state == "page", first_of_walk.refusal
    assert first_of_walk.page is not None
    assert first_of_walk.page.continuation is not None
    assert first_of_walk.page.counts.primary_items_returned == 1, "this is the walk's first page"
    later = read(
        fixture,
        PathSeed(path=fixture.integration.path),
        budget=KnowledgeReadBudget(max_items=1, max_utf8_bytes=64),
        continuation=first_of_walk.page.continuation,
    )
    assert later.state == "refused", "a later page of a real walk refuses rather than serving"
    assert later.refusal is not None
    assert later.refusal.code == "page_budget_too_small"
    assert later.page is None

    connection = open_read_only_database(fixture.database_path)
    try:
        after_digest = logical_digest(connection, "ar-knowledge-sqlite/v1")
    finally:
        connection.close()
    assert read_row_counts(fixture.database_path) == before_counts
    assert before_counts["realization_claim"] == 8
    assert after_digest == before_digest


def test_the_selection_reads_only_the_requested_namespace(
    fixture: ReadScopeFixture,
) -> None:
    """A second dataset in another namespace contributes nothing to this namespace's selection.

    The read is addressed at one namespace and one snapshot, so a record that exists elsewhere --
    including one at the very same path -- is not part of this answer.
    """

    other = build_read_scope_fixture(Path(tempfile.mkdtemp()) / "other")
    store = open_knowledge_store(other.database_path, other.repository_id)
    try:
        assert other.repository_id != fixture.repository_id
        query = SelectionQuery(
            repository_id=fixture.repository_id, seed=PathSeed(path=fixture.integration.path)
        )
        scope = select_recorded_scope(store.connection, query)
        assert scope.items == (), "another dataset's rows cannot enter this namespace's selection"
        own = select_recorded_scope(
            store.connection,
            SelectionQuery(
                repository_id=other.repository_id,
                seed=PathSeed(path=other.integration.path),
            ),
        )
        assert own.items != (), "the read does select the dataset it is addressed at"
    finally:
        store.close()


def _digest(fixture: ReadScopeFixture) -> str:
    connection = open_read_only_database(fixture.database_path)
    try:
        return logical_digest(connection, "ar-knowledge-sqlite/v1")
    finally:
        connection.close()


def _keys(value: object) -> set[str]:
    """Every mapping key anywhere inside one dumped result."""

    if isinstance(value, dict):
        found: set[str] = set()
        for key, item in value.items():
            found.add(str(key))
            found |= _keys(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _keys(item)
        return found
    return set()


def _declared_family_seed_claim_ids(fixture: ReadScopeFixture) -> set[str]:
    """The claims a family-revision seed must select, derived from the requirement's policy alone.

    The packet's family rule is one sentence: "Add their exact member invariant revisions and all
    realization claims for those members. Stop there." This function executes exactly that sentence
    against the stored rows, with no call into the read path: the memberships are the *stored* edges
    of the named family revision, and the claims are every ``realization_claim`` row whose
    ``invariant_revision_id`` is one of those members.

    It is deliberately a separate derivation rather than a page of the implementation under test.
    A walk whose union is compared against a page the same code produced proves only that paging is
    a lossless slice of one computation; comparing it against this query is what tests the claim
    the packet makes -- that the union covers *the declared set*.
    """

    connection = open_read_only_database(fixture.database_path)
    try:
        members = {
            str(row[0])
            for row in connection.execute(
                "SELECT invariant_revision_id FROM family_member "
                "WHERE repository_id = ? AND family_revision_id = ?",
                (fixture.repository_id, fixture.family.revision_id),
            )
        }
    finally:
        connection.close()
    assert members == {
        fixture.subject_revision_id,
        fixture.batch_revision_id,
    }, "the fixture's family holds exactly the two members its rule selects"
    return _claims_of_revisions(fixture, members)


def _claims_of_revisions(fixture: ReadScopeFixture, revision_ids: set[str]) -> set[str]:
    """Every claim id recorded at one of ``revision_ids``, read straight from the fixture's tables."""

    connection = open_read_only_database(fixture.database_path)
    try:
        return {
            str(row[0])
            for revision_id in sorted(revision_ids)
            for row in connection.execute(
                "SELECT claim_id FROM realization_claim WHERE repository_id = ? "
                "AND invariant_revision_id = ?",
                (fixture.repository_id, revision_id),
            )
        }
    finally:
        connection.close()
