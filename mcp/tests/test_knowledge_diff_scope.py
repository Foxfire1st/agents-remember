"""The baseline-to-candidate comparison: the union, the change separation and the record identity.

These cases drive the real comparison over two real databases built by
:mod:`diff_scope_test_support`, and they read it through the application seam. They occupy the
unit-regression lane because what they measure is the comparison's own logic -- which records the
union holds, which side each half came from, and which of the two change statements a difference
belongs to -- and not a process, a publication or a Git object. The cases that need the real Git
tree, the real source expansion and the real refusal boundaries are in
``test_knowledge_diff_candidates.py`` under ``integration``.

Every case is named for the property its own assertions measure. The two properties this module
exists for are the packet's hard ones: a realization link the candidate removed must keep its
before-side code in the comparison, and a record change must be reported separately from a source
change -- with a source-only change never readable as a changed obligation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.read import SelectionQuery, select_recorded_scope
from agents_remember.memory.knowledge.read_queries import (
    family_revision_is_recorded,
    fetch_predecessor_edges,
    invariant_revision_is_recorded,
)
from agents_remember.models.knowledge.diff import (
    DiffLimitation,
    DisplayFilter,
    KnowledgeDiffCounts,
    KnowledgeDiffItem,
    KnowledgeDiffPage,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
    OmittedChanges,
)
from agents_remember.models.knowledge.read import (
    InvariantIdentitySeed,
    InvariantRevisionSeed,
    KnowledgeReadSeed,
)
from diff_scope_test_support import (
    RETRY_REVISED_STATEMENT,
    DiffFixture,
    build_diff_fixture,
)
from pydantic import ValidationError

pytestmark = pytest.mark.evidence_unit

# The role filter these cases use: it matches the candidate's added realization and the retry
# obligation's baseline realization, and excludes the two claims that only support them.
SUPPORT_ROLE = "support"


@pytest.fixture
def fixture(tmp_path: Path) -> DiffFixture:
    """One fresh two-snapshot fixture per case: no case can observe another's candidate state."""

    return build_diff_fixture(tmp_path / "diff")


def diff_seed(fixture: DiffFixture) -> KnowledgeReadSeed:
    """The selector every case starts from: every retained revision of the retry obligation.

    An identity selector is the packet's own recommendation for a comparison, because it selects the
    same identity on both sides and therefore shows a divergent retained-revision set as what it is.
    """

    return InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id)


def run_diff(
    fixture: DiffFixture,
    seed: KnowledgeReadSeed,
    *,
    display_filter: DisplayFilter | None = None,
    before_selector: KnowledgeReadSeed | None = None,
    after_selector: KnowledgeReadSeed | None = None,
) -> KnowledgeDiffResult:
    """Run one comparison of the fixture's two snapshots through the application seam."""

    return diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=seed,
            before=KnowledgeDiffSide(
                context=open_diff_side(
                    fixture.before.database_path,
                    fixture.repository_id,
                    repository_root=fixture.before.git_root,
                    code_tree_id=fixture.before_tree_id,
                ),
                selector=before_selector,
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(
                    fixture.after.database_path,
                    fixture.repository_id,
                    repository_root=fixture.after.git_root,
                    code_tree_id=fixture.after_tree_id,
                ),
                selector=after_selector,
            ),
            display_filter=display_filter,
        ),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
    )


def items_of(result: KnowledgeDiffResult, kind: str) -> list[KnowledgeDiffItem]:
    """The displayed items of one kind, in union order."""

    assert result.page is not None
    return [item for item in result.page.items if item.kind == kind]


def item_for(result: KnowledgeDiffResult, item_id: str) -> KnowledgeDiffItem:
    """The displayed item with one identity, or a failure naming what the page did hold."""

    assert result.page is not None
    for item in result.page.items:
        if item.item_id == item_id:
            return item
    raise AssertionError(
        f"the comparison displayed no item {item_id}; it held "
        f"{sorted(item.item_id for item in result.page.items)}"
    )


# --- the record change and the source change are two separate statements ----------------------


def test_a_revised_statement_arrives_as_a_successor_alongside_the_revision_it_replaced(
    fixture: DiffFixture,
) -> None:
    """The candidate's revised statement is the pair the substrate's immutability forces it to be.

    ``create_invariant_revision`` refuses a second revision with the same identity and another sealed
    payload -- measured, ``duplicate_identity``, whose own next action is "author a successor with its
    own identity and this revision as an exact predecessor" -- so an author who revises a statement
    keeps the identity, authors a successor, and records the predecessor it replaced. The comparison
    must therefore report the *identity*: both revisions appear, each from the snapshot that selected
    it, the successor is recognised as a replacement of a revision the baseline selected, and the
    baseline revision itself stays a record both snapshots hold.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    replaced = item_for(result, fixture.subject_revision_id)
    successor = item_for(result, fixture.revised_revision_id)

    # The revision that was revised is not deleted by the revision that replaced it: both snapshots
    # hold it, so it is context with no change of its own.
    assert replaced.before is not None and replaced.after is not None
    assert replaced.record_transition == "unchanged"
    assert replaced.changed_fields == ()
    # The successor is the candidate's own addition, and it is recognised as a replacement rather than
    # as an unrelated new revision: its authored predecessor edge names a revision the baseline
    # selected, and no display version, label or insertion order was consulted to decide that.
    assert successor.before is None and successor.after is not None
    assert successor.record_transition == "superseding"
    assert successor.coverage == "present_outside_selection"
    # The pair is one record identity whose two halves are different exact revisions, and the revised
    # text travels on the successor while the text it replaced travels on the revision it named.
    assert replaced.record_id == successor.record_id == fixture.retry_invariant_id
    assert replaced.revision_id != successor.revision_id
    assert replaced.before is not None and successor.after is not None
    assert replaced.before.statement != successor.after.statement
    assert successor.after.statement == RETRY_REVISED_STATEMENT


def test_a_source_only_change_is_reported_as_a_source_observation_and_no_record_field_changed(
    fixture: DiffFixture,
) -> None:
    """The candidate tree rewrote one attributed file; the claim that names it did not move.

    Both claims at the changed path are selected by both snapshots with byte-identical rows, so
    nothing about the *record* changed. What changed is the observation: the requested tree holds
    different bytes at the recorded path than the recorded blob. The two statements are separate
    fields on purpose, and this case asserts both of them: no record field moved, the source
    observation did, and the item says the source moved alone.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    source_only = [
        item
        for item in items_of(result, "realization")
        if item.source_change is not None and item.source_change.source_change_only
    ]
    assert len(source_only) == 2, "both claims at the rewritten path report a source-only change"
    for item in source_only:
        assert item.changed_fields == ()
        assert item.record_transition == "unchanged"
        assert item.coverage == "selected_both"
        change = item.source_change
        assert change is not None
        assert change.record_field_changed is False
        assert change.source_observation_changed is True
        assert change.missing_side is None
        assert change.before_observation is not None and change.after_observation is not None
        # The observation moved and the recorded attribution did not: the anchor's path and its
        # recorded identity are the same on both sides, which is what makes this a source change
        # rather than an author's re-attribution.
        before_anchor = change.before_observation.anchor
        after_anchor = change.after_observation.anchor
        assert before_anchor is not None and after_anchor is not None
        assert before_anchor.path == after_anchor.path
        assert before_anchor.recorded_source_identity == after_anchor.recorded_source_identity
        assert before_anchor.observed_source_identity != after_anchor.observed_source_identity
    assert result.page is not None
    assert result.page.counts.changed_field_count == 0
    assert result.page.counts.changed_source_observation_count == 2


def test_a_statement_revision_with_an_unmoved_source_reports_the_change_and_keeps_the_code_visible(
    fixture: DiffFixture,
) -> None:
    """A relationship the candidate authorized where the baseline had none, and where the code is.

    The candidate moves the retry obligation's realization onto the revision it authored, at a path
    the baseline records no claim at and the candidate tree really holds. So the response carries both
    halves the packet requires: the changed knowledge -- the pair of revision payloads, asserted
    separately -- *and* the attributed code that answers it, observed on the side that claims it.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    added = item_for(result, fixture.added_claim_id)
    assert added.record_transition == "added"
    assert added.changed_fields == ()
    change = added.source_change
    assert change is not None
    assert change.missing_side == "before"
    # An added relationship is not a source change: there is no second observation to compare
    # against, and the comparison says exactly that instead of reporting a movement it did not see.
    assert change.source_observation_changed is False
    assert change.source_change_only is False
    assert change.after_observation is not None
    assert change.after_observation.anchor is not None
    assert change.after_observation.anchor.path == fixture.candidate_only_path
    assert change.after_observation.anchor.resolution == "exact_recorded_blob", (
        "the candidate tree really holds the bytes the new claim records, so the observation is "
        "the exact recorded blob and not a mismatch"
    )


def test_an_unchanged_sibling_is_returned_identically_on_both_sides_rather_than_omitted(
    fixture: DiffFixture,
) -> None:
    """A realization both snapshots select is context, not noise: it is in the union and unchanged.

    The unchanged sibling is the packet's own requirement that *relevant unchanged* realizations stay
    inspectable. It is asserted as its own item with both payloads and no change statement, because a
    comparison that dropped unchanged context would leave a reviewer unable to see what the change
    did *not* disturb.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    unchanged = [
        item
        for item in items_of(result, "realization")
        if item.item_id in fixture.unchanged_claim_ids
    ]
    assert len(unchanged) == len(fixture.unchanged_claim_ids), (
        "every unchanged sibling is returned; a page that dropped one would be presenting a "
        "comparison narrower than the one it selected"
    )
    for item in unchanged:
        assert item.coverage == "selected_both"
        assert item.changed_fields == ()
        assert item.before is not None and item.after is not None
        change = item.source_change
        assert change is not None
        assert change.missing_side is None
        # Whatever moved, the record did not: these items are the comparison's unchanged context.
        assert change.record_field_changed is False
        assert change.record_change_only is False
    # Two of the siblings sit at the path the candidate tree rewrote, and two do not. The four are
    # separated by the source half alone, which is what "the same item can be unchanged as a record
    # and moved as an observation" means, and the record half is identical for all four.
    still = [
        item
        for item in unchanged
        if item.source_change is not None and not item.source_change.source_observation_changed
    ]
    moved = [
        item
        for item in unchanged
        if item.source_change is not None and item.source_change.source_observation_changed
    ]
    assert len(still) == 0 and len(moved) == 2
    for item in still:
        assert item.record_transition == "unchanged"


# --- a removed relationship keeps its before-side code ----------------------------------------


def test_a_realization_the_candidate_removed_keeps_its_baseline_source_in_the_union(
    fixture: DiffFixture,
) -> None:
    """Deleting the candidate-side link must not delete the baseline's code from the comparison.

    The removed claim exists only in the baseline, and the union is built from both selected sets
    rather than by walking the candidate, so the item is present with its before-side payload intact:
    the anchor, its recorded blob identity and the resolution the baseline tree earned for it. This is
    the packet's own non-conforming example, and the assertion is the exact opposite of it.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    removed = item_for(result, fixture.removed_claim_id)
    assert removed.record_transition == "removed"
    assert removed.coverage == "absent_from_snapshot"
    assert removed.before is not None
    assert removed.after is None
    change = removed.source_change
    assert change is not None
    assert change.before_observation is not None
    assert change.after_observation is None
    assert change.missing_side == "after"
    anchor = change.before_observation.anchor
    assert anchor is not None
    assert anchor.path == fixture.baseline_only_path
    assert anchor.recorded_source_identity
    assert anchor.resolution == "exact_recorded_blob"
    # The path is still *attributed*, by exactly the claim that was removed: an expansion that
    # called it unattributed would tell a reviewer the earlier code had no recorded attribution.
    assert result.expansion is not None
    assert fixture.baseline_only_path in result.expansion.attributed_changed_paths
    assert fixture.baseline_only_path not in result.expansion.unattributed_changed_paths


# --- the two sides' selected revision sets are each retained -----------------------------------


def test_the_identity_seed_retains_a_revision_group_for_each_side_even_where_they_diverge(
    fixture: DiffFixture,
) -> None:
    """One identity selector, two snapshots, two different retained-revision sets.

    The candidate holds a revision the baseline does not, so the two sides' groups differ. The
    comparison keeps one group per side rather than merging them into a single count, which is what
    lets a reader see that the sides disagree about how many revisions the identity retains without
    the response choosing one of them.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    before_counts = {
        group.record_id: group.selected_revision_count for group in result.revision_groups.before
    }
    after_counts = {
        group.record_id: group.selected_revision_count for group in result.revision_groups.after
    }
    assert before_counts[fixture.retry_invariant_id] == 3
    assert after_counts[fixture.retry_invariant_id] == 5
    assert result.summary is not None
    assert result.summary.before.primary_items_total != result.summary.after.primary_items_total
    assert superseding_revisions(result, fixture) == {
        fixture.revised_revision_id,
        fixture.unselected_revision_id,
    }


def superseding_revisions(result: KnowledgeDiffResult, fixture: DiffFixture) -> set[str]:
    """The revisions the candidate holds that the baseline's selection does not reach."""

    del fixture
    assert result.page is not None
    return {
        str(item.item_id)
        for item in result.page.items
        if item.kind == "invariant" and item.coverage == "present_outside_selection"
    }


def test_explicit_side_selectors_address_a_different_exact_revision_on_each_side(
    fixture: DiffFixture,
) -> None:
    """An explicit selector per side is how one comparison names two different exact revisions.

    The sides address the revision the candidate superseded and the revision that superseded it. Each
    is selected on its own side only, so the union holds the pair and each half reports the side it
    came from -- which is what makes "an explicit revision selector may address different before/after
    revision IDs" a value rather than a special case inside the selection policy.
    """

    result = run_diff(
        fixture,
        diff_seed(fixture),
        before_selector=InvariantRevisionSeed(
            invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
        ),
        after_selector=InvariantRevisionSeed(
            invariant_id=fixture.retry_invariant_id, revision_id=fixture.revised_revision_id
        ),
    )

    assert result.state == "page", result.refusal
    before_only = item_for(result, fixture.subject_revision_id)
    after_only = item_for(result, fixture.revised_revision_id)
    assert before_only.before is not None and before_only.after is None
    assert after_only.before is None and after_only.after is not None
    # Neither side selected the revision the other addressed, so neither is present outside the
    # selection: each snapshot *does* hold the other revision, and the record identity proves it.
    assert before_only.record_id == after_only.record_id == fixture.retry_invariant_id
    assert before_only.record_transition == "superseded"
    assert after_only.record_transition == "superseding"


def test_a_record_the_other_snapshot_holds_but_the_selection_missed_is_not_an_absence(
    fixture: DiffFixture,
) -> None:
    """Present-but-outside-the-selected-scope is not deletion, and the response says which it is.

    The candidate snapshot holds a fourth retry revision that carries no claim and that neither
    side's selection reaches. It is reported as present outside the selection -- with the omission
    that advertises it -- rather than as absent from the snapshot, which is the design's own named
    misreading. The removed claim, by contrast, really is absent from the candidate snapshot, and the
    two states are asserted side by side so the distinction cannot collapse.

    The second half of the case measures the coverage decision's own rule structure on those two
    items: the rule that reads the other side's *authored predecessor edges* never decides a state
    the probe does not, because an edge is a foreign key into the snapshot that declares it -- so a
    snapshot that declares an edge naming this revision necessarily holds it. Each of the two
    predecessor tables is asked with its own probe, because ``fetch_predecessor_edges`` unions them.
    That rule is kept as a cheaper short-circuit, not as a second opinion, and this assertion is
    what keeps the comment on ``_coverage`` honest for the invariant half this fixture authors; the
    family half is carried by the same schema constraint and no family edge is authored here.
    """

    result = run_diff(fixture, diff_seed(fixture))

    assert result.state == "page", result.refusal
    unreached = item_for(result, fixture.unselected_revision_id)
    assert unreached.coverage == "present_outside_selection"
    # Its transition names why it is one-sided: the baseline's selection reached other exact
    # revisions of the same identity instead, which is a supersession and not an absence.
    assert unreached.record_transition == "superseding"
    assert unreached.after is not None and unreached.before is None
    removed = item_for(result, fixture.removed_claim_id)
    assert removed.coverage == "absent_from_snapshot"
    reasons = {omission.reason for omission in result.omissions}
    assert "present_outside_the_declared_selection" in reasons
    assert "records_present_outside_the_selection" in result.limitations

    # The rule-2-is-subsumed-by-rule-3 measurement, on the same two items and through the published
    # read surface rather than through the comparison's private helpers: for every authored edge each
    # side declares, that side's own tables hold *both* revisions it names, which is why reading the
    # edge can never answer a question the probe answers differently.
    #
    # Each predecessor table is asked with its own probe (fix round 2). `fetch_predecessor_edges`
    # unions the invariant and the family edge table, so asking the invariant probe about a family
    # endpoint is a false alarm on a legal snapshot -- one authored family edge, valid under the
    # schema, made this assertion fail before the split. This fixture authors 2 invariant and 0
    # family edges before, and 4 and 0 after, so the invariant half below is exercised here and the
    # family half rests on the same `DEFERRABLE INITIALLY DEFERRED` foreign keys into
    # `family_revision` rather than on this case; the split is what keeps a family edge from being
    # read as a broken invariant one.
    seed = diff_seed(fixture)
    invariant_edge_query = (
        "SELECT child_revision_id, parent_revision_id FROM invariant_predecessor "
        "WHERE repository_id = ? ORDER BY 1, 2"
    )
    family_edge_query = (
        "SELECT child_revision_id, parent_revision_id FROM family_predecessor "
        "WHERE repository_id = ? ORDER BY 1, 2"
    )
    before_connection = open_read_only_database(fixture.before.database_path)
    try:
        after_connection = open_read_only_database(fixture.after.database_path)
        try:
            for connection in (before_connection, after_connection):
                invariant_edges = tuple(
                    (str(row[0]), str(row[1]))
                    for row in connection.execute(invariant_edge_query, (fixture.repository_id,))
                )
                family_edges = tuple(
                    (str(row[0]), str(row[1]))
                    for row in connection.execute(family_edge_query, (fixture.repository_id,))
                )
                assert invariant_edges or family_edges, (
                    "each fixture snapshot declares at least one authored edge"
                )
                for successor, predecessor in invariant_edges:
                    for revision_id in (successor, predecessor):
                        assert invariant_revision_is_recorded(
                            connection, fixture.repository_id, revision_id
                        ), (
                            "an authored edge names a revision its own snapshot must hold; if this "
                            "ever fails, rule 2 of the coverage decision can decide a state rule 3 "
                            "cannot and the subsumption comment on _coverage is wrong"
                        )
                for successor, predecessor in family_edges:
                    for revision_id in (successor, predecessor):
                        assert family_revision_is_recorded(
                            connection, fixture.repository_id, revision_id
                        ), (
                            "an authored family edge names a family revision its own snapshot must "
                            "hold; if this ever fails, rule 2 of the coverage decision can decide a "
                            "state rule 3 cannot and the subsumption comment on _coverage is wrong"
                        )
                assert fetch_predecessor_edges(connection, fixture.repository_id) == tuple(
                    sorted((*invariant_edges, *family_edges))
                ), "fetch_predecessor_edges is the union of the two per-table edge sets"
            before_scope = select_recorded_scope(
                before_connection, SelectionQuery(repository_id=fixture.repository_id, seed=seed)
            )
            after_scope = select_recorded_scope(
                after_connection, SelectionQuery(repository_id=fixture.repository_id, seed=seed)
            )
        finally:
            after_connection.close()
    finally:
        before_connection.close()
    assert before_scope.selected_invariant_revision_ids
    assert after_scope.selected_invariant_revision_ids


# --- the display filter is a display decision --------------------------------------------------


def test_a_role_filter_narrows_the_display_and_never_the_comparison(
    fixture: DiffFixture,
) -> None:
    """Filtering reduces what is shown, and the response states both totals and the omission.

    The filter selects one registered relationship role, which is the only kind of selection the
    packet permits a display filter to make. What it must not do is change the comparison: the raw
    total stays the whole union, the suppressed items are counted with their reason, and the response
    declares that it was filtered. A filtered response therefore cannot be read as a smaller
    comparison, and cannot claim the complete semantic review it did not perform.
    """

    unfiltered = run_diff(fixture, diff_seed(fixture))
    filtered = run_diff(
        fixture, diff_seed(fixture), display_filter=DisplayFilter(realization_roles=(SUPPORT_ROLE,))
    )

    assert unfiltered.state == "page" and filtered.state == "page"
    assert unfiltered.page is not None and filtered.page is not None
    assert filtered.page.counts.items_total == unfiltered.page.counts.items_total
    assert filtered.page.counts.displayed_total < unfiltered.page.counts.items_total
    assert filtered.page.counts.suppressed_total > 0
    assert "display_filtered" in filtered.limitations
    filtered_omission = [
        omission
        for omission in filtered.omissions
        if omission.reason == "outside_the_display_filter"
    ]
    assert len(filtered_omission) == 1
    assert filtered_omission[0].item_kind == "realization"
    assert filtered_omission[0].omitted_count == filtered.page.counts.suppressed_total
    assert filtered_omission[0].detail
    # The filter is a display decision and not a selection one: every displayed realization is one
    # the unfiltered comparison selected, and the excluded role is absent from the display.
    shown = {item.item_id for item in items_of(filtered, "realization")}
    assert shown <= {item.item_id for item in items_of(unfiltered, "realization")}
    assert item_for(unfiltered, fixture.removed_claim_id).source_change is not None
    assert filtered.policy is not None and "support" in filtered.policy


def test_the_filter_is_bound_into_the_comparison_identity_so_another_filter_cannot_reuse_it(
    fixture: DiffFixture,
) -> None:
    """A continuation belongs to one display decision as well as to one pair of snapshots.

    The filter is part of the cursor's binding, so a cursor issued under one filter cannot be
    presented under another: the response would otherwise continue a walk whose positions mean
    something else. This case asserts the binding *differs*, which is the fact the continuation check
    is built on -- the refusal itself is exercised in the integration module.
    """

    unfiltered = run_diff(fixture, diff_seed(fixture))
    filtered = run_diff(
        fixture, diff_seed(fixture), display_filter=DisplayFilter(realization_roles=(SUPPORT_ROLE,))
    )

    assert unfiltered.policy != filtered.policy
    assert unfiltered.binding_digest == filtered.binding_digest, (
        "the snapshot pair is the same comparison; the filter is a request binding, not a "
        "comparison binding"
    )
    assert unfiltered.selector_digest == filtered.selector_digest


# --- the invariants the response's own models enforce -----------------------------------------


def test_a_comparison_that_declares_a_limit_it_did_not_establish_fails_construction(
    fixture: DiffFixture,
) -> None:
    """A declared limitation and an actual omission are one statement, checked in both directions.

    The packet requires a partial response to advertise its limits, and a response that advertised a
    limit it did not have would teach a reviewer to ignore the field. The model therefore refuses
    both mismatches, and this case measures each: an omission without its limitation, and a
    limitation without its omission. The honest state -- the omission *and* its limitation together --
    is asserted to construct, so the refusals are a measurement of the guard and not of a model that
    refuses everything.
    """

    counts = KnowledgeDiffCounts(
        items_total=1,
        items_returned=1,
        items_remaining=0,
        displayed_total=1,
        suppressed_total=0,
        changed_field_count=0,
        changed_source_observation_count=0,
    )
    omission = OmittedChanges(
        reason="present_outside_the_declared_selection",
        item_kind="invariant",
        omitted_count=1,
        detail="one invariant revision the other snapshot holds and this selection did not reach",
    )
    page = KnowledgeDiffPage(
        items=(), counts=counts, has_more=False, enumeration_complete=True, continuation=None
    )

    def build(
        *,
        limitations: tuple[DiffLimitation, ...] = ("no_semantic_assessment_performed",),
        omissions: tuple[OmittedChanges, ...] = (),
    ) -> KnowledgeDiffResult:
        """Build a comparison response, varying only the two fields the guards below read.

        The declaration is what the model declares rather than a mapping of ``object`` values: a
        keyword mapping would hand the constructor values no reader could check, and the two fields
        this case varies are exactly the ones whose own guard is under measurement.
        """

        return KnowledgeDiffResult(
            state="page",
            repository_id=fixture.repository_id,
            limitations=limitations,
            omissions=omissions,
            page=page,
        )

    # The honest state constructs: an omission travels with the limitation that advertises it.
    honest = build(
        limitations=(
            "records_present_outside_the_selection",
            "no_semantic_assessment_performed",
        ),
        omissions=(omission,),
    )
    assert honest.limitations
    assert honest.omissions == (omission,)
    # An omission whose limitation is missing is refused, so a partial view cannot wear a whole
    # one's declaration.
    with pytest.raises(ValidationError, match="must declare the"):
        build(omissions=(omission,))
    # A limitation with no omission behind it is refused too, so the field keeps its meaning. This is
    # the *measured control* for the refusal above: the value is rejected by the guard that owns it,
    # which is what makes the honest construction beside it a measurement rather than a tautology.
    undeclared = (
        "records_present_outside_the_selection",
        "no_semantic_assessment_performed",
    )
    with pytest.raises(ValidationError) as refused:
        build(limitations=undeclared)
    assert "must have omitted something" in str(refused.value)
    assert all(error["loc"] == () for error in refused.value.errors()), (
        "the refusal must be the model's own limitation/omission guard, not a field's type check"
    )
    # And the one limit every comparison carries is not optional.
    with pytest.raises(ValidationError, match="performs no semantic assessment"):
        build(limitations=())


def test_a_truncated_comparison_cannot_be_presented_as_a_complete_one() -> None:
    """The page's three completeness facts are one statement and are refused when they disagree.

    A comparison page that says it has more and also says its enumeration is complete is the exact
    shape the packet refuses -- truncation presentable as completeness -- and the model rejects it at
    construction rather than leaving a caller to decide which of the two fields to believe. The
    honest truncated page and the honest complete page are both asserted to construct.
    """

    counts = KnowledgeDiffCounts(
        items_total=2,
        items_returned=1,
        items_remaining=1,
        displayed_total=2,
        suppressed_total=0,
        changed_field_count=0,
        changed_source_observation_count=0,
    )
    truncated = KnowledgeDiffPage(
        items=(),
        counts=counts,
        has_more=True,
        enumeration_complete=False,
        continuation="a-continuation",
    )
    assert truncated.has_more and truncated.continuation is not None
    complete = KnowledgeDiffPage(
        items=(),
        counts=counts.model_copy(update={"items_returned": 2, "items_remaining": 0}),
        has_more=False,
        enumeration_complete=True,
        continuation=None,
    )
    assert complete.enumeration_complete
    # The two contradictory states, each asserted with the message of the guard that owns it, so a
    # page whose opposite guard happens to fire too cannot make the case pass for the wrong reason:
    # the second carries no continuation at all, so the opposites guard is the only one that can
    # refuse it.
    with pytest.raises(ValidationError, match="must be opposites"):
        KnowledgeDiffPage(
            items=(),
            counts=counts,
            has_more=True,
            enumeration_complete=True,
            continuation="a-continuation",
        )
    with pytest.raises(ValidationError, match="must be opposites"):
        KnowledgeDiffPage(
            items=(), counts=counts, has_more=True, enumeration_complete=True, continuation=None
        )
    with pytest.raises(ValidationError, match="must carry a continuation"):
        KnowledgeDiffPage(
            items=(), counts=counts, has_more=True, enumeration_complete=False, continuation=None
        )
    # The measured control: the contradictory page is *rejected*, so the statement this case makes is
    # a statement about the guard and not about values the model would have refused anyway.
    opposite_states = {"has_more": True, "enumeration_complete": True, "continuation": None}
    with pytest.raises(ValidationError) as refused:
        KnowledgeDiffPage(items=(), counts=counts, **opposite_states)
    assert "must be opposites" in str(refused.value), (
        "the contradiction must be refused by the guard that owns it, not by a neighbouring one"
    )


def test_the_expansion_of_a_comparison_that_observed_no_trees_claims_no_change_set(
    fixture: DiffFixture,
) -> None:
    """An observation that could not be made is reported as unmade, never as an empty change set.

    The probe is the one seam this operation delegates, and this case substitutes the honest probe for
    a comparison whose sides named no code tree. The record half of the comparison is complete and
    still pages; the source half reports that nothing was observed, so no caller can read an
    unobserved pair of trees as two trees that agree. The alternative -- an empty changed-path list --
    is the fabricated fact this case exists to refuse.
    """

    result = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=diff_seed(fixture),
            before=KnowledgeDiffSide(
                context=open_diff_side(fixture.before.database_path, fixture.repository_id)
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(fixture.after.database_path, fixture.repository_id)
            ),
        ),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
    )

    assert result.state == "page", result.refusal
    assert result.page is not None and result.page.counts.items_total > 0
    expansion = result.expansion
    assert expansion is not None
    assert expansion.before_code_tree_id is None and expansion.after_code_tree_id is None
    assert expansion.unattributed_changed_paths == ()
    assert "no source expansion was observed" in expansion.detail
    assert "<no baseline tree requested>" in expansion.command
    # The gap is not advertised as a gap, because none was observed; and the comparison still states
    # that it made no semantic assessment.
    assert "unattributed_changed_paths" not in result.limitations
    assert "no_semantic_assessment_performed" in result.limitations
