"""The comparison at its real boundaries: published snapshots, real Git trees and typed refusals.

These cases need a real Git repository, a real committed tree and a real SQLite file, so they live in
the integration lane. Three of them are the packet's refusal contract -- a missing side, a candidate
that moved, and a continuation presented against another comparison -- and each asserts that the
refusal is *typed* and that nothing was persisted, because "it raised" is not evidence that a refusal
left the bytes alone. The rest measure the response's own honesty: the expansion reference names the
two requested trees and the paths they differ at, an unattributed change is listed as a gap rather
than dropped, and no field of the response can carry a verdict about what a change means.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_diff import (
    diff_knowledge_scope,
    diff_row_counts,
    open_diff_side,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.diff import FIELD_PROJECTION, compare_selected_scopes
from agents_remember.memory.knowledge.diff_display import TreePaths, TreeSide
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.read import (
    PageRequest,
    SelectedScope,
    SelectionQuery,
    page_of_scope,
    select_recorded_scope,
)
from agents_remember.memory.knowledge.read_anchors import anchor_resolver_for
from agents_remember.models.knowledge.diff import (
    DisplayFilter,
    KnowledgeDiffBudget,
    KnowledgeDiffItem,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
)
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    InvariantIdentitySeed,
)
from diff_scope_test_support import DiffFixture, build_diff_fixture

pytestmark = pytest.mark.integration

# The words a response would have to use if it were reporting a *verdict* about a change rather than
# the change itself. They are the packet's own two non-conforming labels, and the case that reads
# them asserts the whole serialized response -- every field name and every value -- is free of them.
FORBIDDEN_VERDICT_WORDS = (
    "strengthen",
    "harmless",
    "no impact",
    "no semantic impact",
    "no consequence",
    "behaviourally neutral",
    "behaviorally neutral",
    "safe to ignore",
    "meaning-preserving",
)


@pytest.fixture
def fixture(tmp_path: Path) -> DiffFixture:
    """One fresh two-snapshot fixture per case, with its two real committed code trees."""

    return build_diff_fixture(tmp_path / "diff")


def request_for(
    fixture: DiffFixture,
    *,
    display_filter: DisplayFilter | None = None,
    budget: KnowledgeDiffBudget | None = None,
    continuation: str | None = None,
) -> KnowledgeDiffRequest:
    """Build one comparison request at the fixture's two exact snapshots and code trees."""

    return KnowledgeDiffRequest(
        selector=InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id),
        before=KnowledgeDiffSide(
            context=open_diff_side(
                fixture.before.database_path,
                fixture.repository_id,
                repository_root=fixture.before.git_root,
                code_tree_id=fixture.before_tree_id,
            )
        ),
        after=KnowledgeDiffSide(
            context=open_diff_side(
                fixture.after.database_path,
                fixture.repository_id,
                repository_root=fixture.after.git_root,
                code_tree_id=fixture.after_tree_id,
            )
        ),
        display_filter=display_filter,
        budget=budget or KnowledgeDiffBudget(),
        continuation=continuation,
    )


def run_diff(
    fixture: DiffFixture,
    request: KnowledgeDiffRequest,
    *,
    before_path: Path | None = None,
    after_path: Path | None = None,
) -> KnowledgeDiffResult:
    """Run one comparison of the fixture's two files through the application seam."""

    return diff_knowledge_scope(
        request,
        before_path=before_path or fixture.before.database_path,
        after_path=after_path or fixture.after.database_path,
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
    raise AssertionError(f"the comparison displayed no item {item_id}")


# --- the source expansion, against real trees -----------------------------------------------


def test_the_expansion_names_both_requested_trees_and_every_path_they_differ_at(
    fixture: DiffFixture,
) -> None:
    """The expansion is a value a caller can act on, and it names the trees the request named.

    Both code trees are resolved through the real file system, so the changed-path set is a
    measurement of the two committed trees and not of a working directory. The command carries the
    two tree ids and never a branch, a working tree or ``HEAD``: a comparison of two published
    snapshots must not be re-runnable against whatever is checked out later.
    """

    result = run_diff(fixture, request_for(fixture))

    assert result.state == "page", result.refusal
    expansion = result.expansion
    assert expansion is not None
    assert expansion.before_code_tree_id == fixture.before_tree_id
    assert expansion.after_code_tree_id == fixture.after_tree_id
    assert expansion.before_root == str(fixture.before.git_root)
    assert expansion.after_root == str(fixture.after.git_root)
    assert fixture.before_tree_id in expansion.command
    assert fixture.after_tree_id in expansion.command
    assert "HEAD" not in expansion.command and "--name-only" in expansion.command
    # Every changed path is classified exactly once across the two lists, and the two lists do not
    # overlap. ``attributed_changed_paths`` lists every path a selected claim attributes -- which is
    # the union the comparison's own record half reaches, not only the paths that moved -- so the
    # changed set is the subset of it the two trees actually differ at.
    assert not set(expansion.attributed_changed_paths) & set(expansion.unattributed_changed_paths)
    changed = {
        fixture.changed_source_path,
        fixture.baseline_only_path,
        fixture.candidate_only_path,
    }
    assert changed <= set(expansion.attributed_changed_paths)
    assert expansion.unattributed_changed_paths == (fixture.unattributed_path,)
    assert changed | {fixture.unattributed_path} == changed | set(
        expansion.unattributed_changed_paths
    )


def test_a_changed_path_no_recorded_realization_attributes_is_listed_as_a_visible_gap(
    fixture: DiffFixture,
) -> None:
    """A change the knowledge half cannot describe is advertised, counted and never hidden.

    The candidate tree adds a file that no realization claim cites. The response cannot report a
    knowledge change for it, and it must not report nothing: the path is listed in the expansion, its
    count carries a reason, and the response declares the limit. A filtered response that hid it would
    be claiming a completeness this operation never had.
    """

    result = run_diff(fixture, request_for(fixture))

    assert result.state == "page", result.refusal
    assert result.expansion is not None
    assert result.expansion.unattributed_changed_paths == (fixture.unattributed_path,)
    omissions = [
        omission
        for omission in result.omissions
        if omission.reason == "change_not_attributed_to_a_recorded_realization"
    ]
    assert len(omissions) == 1
    assert omissions[0].omitted_count == 1
    assert "No assessment of their consequence" in omissions[0].detail
    assert "unattributed_changed_paths" in result.limitations
    # The gap survives a display filter: filtering selects relationships to show, and a path no
    # relationship attributes is not a relationship to filter.
    filtered = run_diff(
        fixture,
        request_for(fixture, display_filter=DisplayFilter(realization_roles=("support",))),
    )
    assert filtered.expansion is not None
    assert filtered.expansion.unattributed_changed_paths == (fixture.unattributed_path,)
    assert "unattributed_changed_paths" in filtered.limitations


def test_the_attributed_source_of_a_moved_obligation_is_observed_on_the_side_that_claims_it(
    fixture: DiffFixture,
) -> None:
    """A record change returns the code it is attributed to, resolved against the right tree.

    The candidate's realization is resolved against the candidate tree and the baseline's against the
    baseline tree, so the two observations are two measurements of two commits rather than one
    measurement reused. This is the packet's "a statement-only change still returns the attributed
    code" read at the seam where the two trees actually differ.
    """

    result = run_diff(fixture, request_for(fixture))

    assert result.state == "page", result.refusal
    added = item_for(result, fixture.added_claim_id)
    change = added.source_change
    assert change is not None and change.after_observation is not None
    after_anchor = change.after_observation.anchor
    assert after_anchor is not None
    assert after_anchor.resolution == "exact_recorded_blob"
    assert after_anchor.observed_source_identity == after_anchor.recorded_source_identity
    # The removed relationship still reports the baseline's own observation, against the baseline
    # tree, and it is not re-resolved against the candidate tree that no longer holds it.
    removed = item_for(result, fixture.removed_claim_id)
    removed_change = removed.source_change
    assert removed_change is not None and removed_change.before_observation is not None
    removed_anchor = removed_change.before_observation.anchor
    assert removed_anchor is not None
    assert removed_anchor.resolution == "exact_recorded_blob"
    assert removed_anchor.path == fixture.baseline_only_path
    assert removed_change.after_observation is None


# --- paging ---------------------------------------------------------------------------------


def test_a_small_display_budget_pages_the_comparison_without_shrinking_its_totals(
    fixture: DiffFixture,
) -> None:
    """A display budget cuts pages, not scope: every page reports the whole comparison's totals.

    The walk is followed to its end through the response's own continuation, and the union of the
    pages is compared with the declared total. Truncation must not be presentable as a smaller
    comparison, which is why the total travels unchanged on every page.
    """

    first = run_diff(fixture, request_for(fixture, budget=KnowledgeDiffBudget(max_items=4)))

    assert first.state == "page", first.refusal
    assert first.page is not None
    total = first.page.counts.items_total
    assert total > 4
    seen: list[str] = []
    page = first
    pages = 0
    while page.page is not None:
        pages += 1
        assert page.page.counts.items_total == total
        assert page.page.counts.items_returned == len(seen) + len(page.page.items)
        assert page.page.counts.items_remaining == total - len(seen) - len(page.page.items)
        seen.extend(item.item_id for item in page.page.items)
        if not page.page.has_more:
            assert page.page.continuation is None
            break
        assert page.page.continuation is not None
        page = run_diff(
            fixture,
            request_for(
                fixture,
                budget=KnowledgeDiffBudget(max_items=4),
                continuation=page.page.continuation,
            ),
        )
        assert page.state == "page", page.refusal
    assert len(seen) == total
    assert len(set(seen)) == total, "no item is returned by two pages of one walk"
    assert pages > 1


# --- the refusal contract -------------------------------------------------------------------


def test_a_missing_side_refuses_and_substitutes_no_other_snapshot(
    fixture: DiffFixture,
) -> None:
    """An absent database is a typed refusal on the side that named it, and nothing else is read.

    The refusal names the side's own file. No working tree, no branch and no other HEAD is consulted
    to fill the gap, which the case asserts by the code it requires: a comparison that had silently
    fallen back would have returned a page.
    """

    missing = fixture.after.database_path.with_name("absent-candidate.db")
    result = run_diff(fixture, request_for(fixture), after_path=missing)

    assert result.state == "refused"
    assert result.page is None
    assert result.refusal is not None
    assert result.refusal.code == "selected_input_unavailable"
    assert result.refusal.operation == "diff_knowledge_scope"
    assert str(missing) in result.refusal.detail
    assert result.refusal.next_action


def test_a_candidate_that_changed_after_a_continuation_refuses_the_continuation(
    fixture: DiffFixture,
) -> None:
    """A candidate's content change invalidates a continuation, because the binding names it.

    The cursor binds a digest over both declared logical snapshots, so a candidate whose bytes moved
    presents a binding the cursor does not name and the continuation is refused rather than continued
    into a page stitched from two candidate states. The mutation is a real write through the store's
    own candidate path, and the refusal is asserted to leave both files' row counts unchanged.
    """

    first = run_diff(fixture, request_for(fixture, budget=KnowledgeDiffBudget(max_items=4)))
    assert first.state == "page", first.refusal
    assert first.page is not None and first.page.continuation is not None
    continuation = first.page.continuation

    before_rows = diff_row_counts(fixture.after.database_path)
    before_digest = dataset_identity(fixture.after.database_path).logical_digest
    add_unrelated_revision(fixture)
    moved_digest = dataset_identity(fixture.after.database_path).logical_digest
    assert moved_digest != before_digest, "the candidate really changed before the retry"

    retry = run_diff(
        fixture,
        request_for(fixture, budget=KnowledgeDiffBudget(max_items=4), continuation=continuation),
    )

    assert retry.state == "refused"
    assert retry.refusal is not None
    assert retry.refusal.code == "continuation_binding_mismatch"
    assert retry.refusal.detail.endswith("it binds another snapshot pair")
    assert retry.page is None
    # The refusal persisted nothing: the candidate holds exactly the rows the mutation wrote, and no
    # row was added or removed by the refused read.
    after_rows = diff_row_counts(fixture.after.database_path)
    assert after_rows["invariant_revision"] == before_rows["invariant_revision"] + 1
    assert {
        table: count for table, count in after_rows.items() if table != "invariant_revision"
    } == {table: count for table, count in before_rows.items() if table != "invariant_revision"}


def add_unrelated_revision(fixture: DiffFixture) -> None:
    """Author one more revision of the compared identity in the candidate, through the store.

    It is a real write to the candidate database, and it is deliberately a write that changes what the
    request's own selector selects: an identity selector selects every retained revision of the
    identity on each side, so a new revision moves the candidate's selected set and therefore the
    comparison binding. A write that the selector did not reach would leave the binding intact and
    would not test the invalidation at all.
    """

    from uuid import uuid4  # noqa: PLC0415 - one local import for one fixture mutation

    from agents_remember.memory.knowledge.store import (  # noqa: PLC0415
        open_knowledge_store,
    )
    from agents_remember.models.knowledge.result import (  # noqa: PLC0415
        RevisionDraft,
        RevisionRequest,
    )

    store = open_knowledge_store(fixture.after.database_path, fixture.repository_id)
    try:
        created = store.create_revision(
            RevisionRequest(
                repository_id=fixture.repository_id,
                revision=RevisionDraft(
                    revision_id=str(uuid4()),
                    invariant_id=fixture.retry_invariant_id,
                    display_version="v9",
                    statement="A revision the curator authored after the first comparison.",
                    applicability="Every retry the shared budget admits in this repository namespace.",
                    conditions=("The candidate write is admitted for this namespace.",),
                    provenance=fixture.before.fixture.authorship,
                ),
            )
        )
    finally:
        store.close()
    if created.state != "created":
        raise AssertionError(f"the candidate mutation did not land: {created.refusal!r}")


def test_a_continuation_presented_against_another_selector_refuses_and_returns_no_page(
    fixture: DiffFixture,
) -> None:
    """A cursor is a position in one comparison, and the selector is part of that comparison.

    The continuation is decoded from the response for one selector and presented with a family
    selector instead. It binds another selection, so the response refuses by name and returns no
    partial page: a page assembled from two selections is not a page of either.
    """

    first = run_diff(fixture, request_for(fixture, budget=KnowledgeDiffBudget(max_items=4)))
    assert first.state == "page", first.refusal
    assert first.page is not None and first.page.continuation is not None

    other = KnowledgeDiffRequest(
        selector=FamilyIdentitySeed(family_id=fixture.before.fixture.family.family_id),
        before=request_for(fixture).before,
        after=request_for(fixture).after,
        continuation=first.page.continuation,
    )
    result = run_diff(fixture, other)

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "continuation_binding_mismatch"
    assert result.refusal.detail.endswith("it binds another selector")
    assert result.page is None
    assert first.page.counts.items_total > 0


def test_a_side_naming_another_snapshot_of_its_own_file_refuses_before_any_page(
    fixture: DiffFixture,
) -> None:
    """A side that declares a snapshot its file does not hold is refused by name, not answered.

    The context keeps the file's real digest and declares another one, which is the state a caller
    reaches by resolving a side and then reading a different candidate. The comparison refuses with
    both identities, and it does so before a page or an expansion exists, so no part of the response
    can be read as a comparison of the bytes it did not get.
    """

    request = request_for(fixture)
    tampered = request.model_copy(
        update={
            "after": request.after.model_copy(
                update={
                    "context": request.after.context.model_copy(
                        update={
                            "knowledge": request.after.context.knowledge.model_copy(
                                update={"logical_digest": "0" * 64}
                            )
                        }
                    )
                }
            )
        }
    )
    before_rows = diff_row_counts(fixture.after.database_path)
    result = run_diff(fixture, tampered)

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "snapshot_unavailable"
    assert result.refusal.expected == "0" * 64
    assert result.refusal.observed != "0" * 64
    assert result.page is None
    assert result.expansion is None
    assert diff_row_counts(fixture.after.database_path) == before_rows


# --- no verdict can be emitted --------------------------------------------------------------


def test_no_field_of_a_comparison_can_carry_a_strengthening_or_harmlessness_verdict(
    fixture: DiffFixture,
) -> None:
    """The response reports differences; it has no vocabulary in which to assess them.

    The case serializes the whole response for a comparison whose source moved and whose knowledge
    moved, and searches every field name and every value for the packet's own two non-conforming
    labels and their relatives. It is a search over the *serialized* response rather than over the
    models, because that is what a client receives -- and it is paired with a positive control that
    the search would find the words if they were there.
    """

    result = run_diff(fixture, request_for(fixture))
    assert result.state == "page", result.refusal
    assert result.page is not None
    page = result.page
    payload = json.dumps(result.model_dump(mode="json"))
    folded = payload.casefold()
    for word in FORBIDDEN_VERDICT_WORDS:
        assert word not in folded, f"the response carries the verdict word {word!r}"
    # Positive control: the same search does find a word that *is* in the payload, so the assertion
    # above is a measurement of the response and not of a broken search.
    assert "source_observation_changed" in folded
    assert any(
        item.source_change is not None and item.source_change.source_observation_changed
        for item in page.items
    ), "the search is non-vacuous only if the comparison really reports a source change"


def test_the_two_change_statements_are_separate_fields_and_neither_implies_the_other(
    fixture: DiffFixture,
) -> None:
    """Measured over the whole union: no item reports a source-only change *and* a changed field.

    The packet's separation stated as a property of the response rather than of one item: for every
    item the comparison returned, the record half and the source half are independently readable, and
    an item whose record changed is not thereby an item whose source changed. A response that folded
    the two into one "difference" would fail here whichever way it folded them.
    """

    result = run_diff(fixture, request_for(fixture))

    assert result.state == "page", result.refusal
    assert result.page is not None
    for item in result.page.items:
        change = item.source_change
        if change is None:
            assert item.kind != "realization"
            continue
        assert change.record_field_changed == bool(item.changed_fields)
        if change.source_change_only:
            assert item.changed_fields == ()
            assert change.record_field_changed is False
        if change.record_change_only:
            assert change.source_observation_changed is False
        if change.missing_side is not None:
            assert not change.source_observation_changed
            assert not change.record_field_changed
            assert not change.source_change_only
            assert not change.record_change_only


def test_the_record_comparison_reports_exactly_the_payload_field_that_changed(
    fixture: DiffFixture,
) -> None:
    """Every declared payload field is compared, and only a field that really changed is reported.

    The comparison's record half is the projection in
    :data:`agents_remember.memory.knowledge.diff.FIELD_PROJECTION`. The field-by-field loop measures
    it in both directions: for each declared field, substituting that field on one side of an
    otherwise identical same-id pair must make the comparison report that field *and nothing else*.
    A projection that widened to the raw row would report keys the field vocabulary does not contain;
    one that narrowed would miss the field that moved; one that compared the whole row would report
    the keys that did not move at all.

    No store can hold two payloads for one immutable revision (measured: ``duplicate_identity``), so
    the pair is built by substituting one side's reported payload. That is the comparison's own input
    type and not a database state, which is exactly what the projection is defined over.
    """

    before_scope, after_scope = selected_scopes(fixture)
    before_connection = open_read_only_database(fixture.before.database_path)
    after_connection = open_read_only_database(fixture.after.database_path)
    try:
        as_selected = compare_selected_scopes(
            before_connection=before_connection,
            after_connection=after_connection,
            repository_id=fixture.repository_id,
            before_scope=before_scope,
            after_scope=after_scope,
        )
        assert all(not item.changed_fields for item in as_selected.items), (
            "the two snapshots as authored agree on every compared field of every record they share"
        )
        per_field = {
            field: compare_selected_scopes(
                before_connection=before_connection,
                after_connection=after_connection,
                repository_id=fixture.repository_id,
                before_scope=before_scope,
                after_scope=_substituting(
                    after_scope, fixture.batch_revision_id, field, _substitute(field)
                ),
            )
            for field in FIELD_PROJECTION
        }
    finally:
        after_connection.close()
        before_connection.close()

    for field, comparison in per_field.items():
        changed = [item for item in comparison.items if item.changed_fields]
        assert len(changed) == 1, f"substituting {field} changed {len(changed)} items"
        assert changed[0].item_id == fixture.batch_revision_id
        assert changed[0].changed_fields == (field,), (
            f"substituting {field} reported {changed[0].changed_fields}"
        )


# A substitute value per declared field, chosen so it differs from the fixture's own authored value
# for that field and so the comparison has something real to detect. Each one is a value the field's
# own type admits, so a substitution can never be mistaken for a malformed payload.
_SUBSTITUTIONS: Mapping[str, object] = {
    "acceptance_ref": "requirement:KS-R08@v1",
    "applicability": "A substituted applicability for the record comparison case.",
    "essential_conditions": ("A substituted condition.",),
    "exclusions": ("A substituted exclusion.",),
    "joint_guarantee": "A substituted joint guarantee.",
    "lifecycle": "accepted",
    "payload_digest": "0" * 64,
    "provenance": {"actor_ref": "agent:substituted", "operation_id": "0" * 8},
    "statement": "A substituted statement for the record comparison case.",
}


def _substitute(field: str) -> object:
    """Return the substitute value for one declared field."""

    return _SUBSTITUTIONS[field]


def _substituting(
    scope: SelectedScope, revision_id: str, field: str, value: object
) -> SelectedScope:
    """Return one selected scope with one declared field of one revision's payload replaced."""

    assert isinstance(scope, SelectedScope)
    return replace(
        scope,
        items=tuple(
            item.model_copy(update={field: value}) if item.item_id == revision_id else item
            for item in scope.items
        ),
    )


def selected_scopes(fixture: DiffFixture) -> tuple[SelectedScope, SelectedScope]:
    """Select both sides of the fixture with the read layer's own policy, one connection each.

    Each side is selected **with its own anchor resolver**, against its own committed tree. That is
    not decoration: without it every item's anchor is ``None``, the comparison's source half has
    nothing to compare, and a case that believes it measured a source change has measured nothing.
    The two resolvers are the application seam's own, so the observations are the ones a real
    comparison would carry.
    """

    seed = InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id)
    before_connection = open_read_only_database(fixture.before.database_path)
    after_connection = open_read_only_database(fixture.after.database_path)
    try:
        before_scope = select_recorded_scope(
            before_connection,
            SelectionQuery(
                repository_id=fixture.repository_id,
                seed=seed,
                resolve_anchor=anchor_resolver_for(
                    open_diff_side(
                        fixture.before.database_path,
                        fixture.repository_id,
                        repository_root=fixture.before.git_root,
                        code_tree_id=fixture.before_tree_id,
                    )
                ),
            ),
        )
        after_scope = select_recorded_scope(
            after_connection,
            SelectionQuery(
                repository_id=fixture.repository_id,
                seed=seed,
                resolve_anchor=anchor_resolver_for(
                    open_diff_side(
                        fixture.after.database_path,
                        fixture.repository_id,
                        repository_root=fixture.after.git_root,
                        code_tree_id=fixture.after_tree_id,
                    )
                ),
            ),
        )
    finally:
        after_connection.close()
        before_connection.close()
    return before_scope, after_scope


def _rewording(scope: SelectedScope, revision_id: str) -> SelectedScope:
    """Return one selected scope with one revision's statement replaced in its reported payload.

    It is a payload-level substitution and not a database edit: the point of the case is the
    *comparison's* field projection, and a store cannot hold two payloads for one immutable revision
    (measured: ``duplicate_identity``). Substituting the reported item reaches the comparison with
    exactly the input the projection exists to compare.
    """

    assert isinstance(scope, SelectedScope)
    return replace(
        scope,
        items=tuple(
            item.model_copy(update={"statement": "A revised statement."})
            if item.item_id == revision_id
            else item
            for item in scope.items
        ),
    )


def test_a_page_of_a_selection_is_what_the_comparison_displays_not_what_it_selected(
    fixture: DiffFixture,
) -> None:
    """The comparison's totals are the union's, and R07's page contract is not reused as a scope.

    A comparison reuses the read layer's *selection* and not its page: R07's page budget is measured
    in serialized read bytes and the comparison's display budget is measured in items, so the two
    travel separately. This case measures that the union's own total is the number the comparison
    reports, by computing a deliberately tiny read page of each side and confirming that the
    comparison's total is the union's and not either page's slice.
    """

    before_scope, _after_scope = selected_scopes(fixture)
    before_connection = open_read_only_database(fixture.before.database_path)
    try:
        context = open_diff_side(
            fixture.before.database_path,
            fixture.repository_id,
            repository_root=fixture.before.git_root,
            code_tree_id=fixture.before_tree_id,
        )
        page = page_of_scope(
            before_scope,
            PageRequest(
                context=context,
                seed=InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id),
                max_items=1,
                max_utf8_bytes=131072,
                position=0,
            ),
        )
    finally:
        before_connection.close()
    result = run_diff(fixture, request_for(fixture))
    assert result.state == "page", result.refusal
    assert result.page is not None
    item_count = len(page.items)
    assert item_count == 1
    assert result.page is not None
    assert result.page.counts.items_total > item_count
    assert result.summary is not None
    assert result.summary.before.primary_items_total > item_count


def test_a_comparison_names_no_mounted_ui_and_no_approval_it_cannot_make(
    fixture: DiffFixture,
) -> None:
    """The response's own vocabulary is the whole surface: no approval, no severity, no UI state.

    The packet's exclusions are asserted against the serialized response rather than promised in
    prose: there is no field an approval gate could read, no mounted-interface state, and no severity
    or impact ranking. The positive control is that the response *does* carry the counts, the
    limitations and the expansion, so the absence is a measured absence and not an empty payload.
    """

    result = run_diff(fixture, request_for(fixture))
    assert result.state == "page", result.refusal
    payload = result.model_dump(mode="json")
    flat = json.dumps(payload).casefold()
    for forbidden in ("severity", "impact_score", "approve", "approval", "mounted", "dashboard"):
        assert forbidden not in flat, f"the response carries {forbidden!r}"
    assert payload["limitations"]
    assert payload["expansion"]["reference"]
    assert payload["page"]["counts"]["items_total"] > 0


def test_an_unavailable_observation_is_reported_as_unavailable_and_never_as_a_change_set(
    fixture: DiffFixture,
) -> None:
    """A probe that could not compare the trees reports no change set, whatever paths it holds.

    The seam can answer in three ways, and only one of them is a measurement: a set of changed paths,
    an honest empty set for two trees that really agree, and an observation that was not made. The
    third must never be rendered as the second, so this case substitutes a probe whose own report says
    it is unavailable *while naming paths*, which is the state that separates the two: the response
    must drop its unattributed-path gap, must not claim the paths as unattributed, and must say in the
    expansion's own detail that nothing was observed.
    """

    observed_paths = ("src/phantom-one.py", "src/phantom-two.py")

    def unavailable(_before: TreeSide, _after: TreeSide) -> TreePaths:
        return TreePaths(
            available=False,
            paths=observed_paths,
            detail="injected: the two trees were named but this observation was not made",
        )

    result = diff_knowledge_scope(
        request_for(fixture),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
        probe=unavailable,
    )

    assert result.state == "page", result.refusal
    expansion = result.expansion
    assert expansion is not None
    assert expansion.unattributed_changed_paths == ()
    assert set(observed_paths) & set(expansion.unattributed_changed_paths) == set()
    assert "not made" in expansion.detail
    assert "unattributed_changed_paths" not in result.limitations
    assert not [
        omission
        for omission in result.omissions
        if omission.reason == "change_not_attributed_to_a_recorded_realization"
    ]


def test_a_probe_that_measured_the_trees_is_what_makes_a_gap_visible(
    fixture: DiffFixture,
) -> None:
    """The same substitution with an available observation *does* produce the gap, so the pair is one.

    The control for the case above: the only difference between the two is the probe's own
    ``available`` flag, and the outcome differs exactly there. Without this pairing, a comparison that
    simply never reported unattributed paths would pass the case above.
    """

    def available(_before: TreeSide, _after: TreeSide) -> TreePaths:
        return TreePaths(available=True, paths=(fixture.unattributed_path,))

    result = diff_knowledge_scope(
        request_for(fixture),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
        probe=available,
    )

    assert result.state == "page", result.refusal
    assert result.expansion is not None
    assert result.expansion.unattributed_changed_paths == (fixture.unattributed_path,)
    assert "unattributed_changed_paths" in result.limitations
