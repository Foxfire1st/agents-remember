"""The Intent Reviewer surface: the three panes, every refusal state, and the worked review.

These cases drive the **real** adapter over the **real** two-snapshot comparison built by
:mod:`diff_scope_test_support`, and read the shipped L20 review matrix through
:func:`compose_review`. Nothing here re-implements a read, a comparison or a view, fakes a snapshot,
or asserts a rendering it did not read back.

The load-bearing properties, one case each:

* the surface **stores nothing** -- the two datasets' row counts are identical across a full render,
  and the vocabulary module declares no record kind, no table and no status;
* the surface **selects nothing** -- the comparison identity, the item identities and the counts the
  payload publishes are the shipped operation's own, compared value for value;
* the surface has **no field a generated conclusion could occupy**, checked over the whole payload
  schema rather than over one model;
* every refusal state is a rendering: unassessed is unassessed, no evidence reads
  ``none_recorded`` while source inspection stays available, a passing observation is an observation
  and never an invariant-satisfied status, a signal carries facts and scope limitations and no
  severity, and a stale comparison keeps its previous input and disables submission.

The final group is the worked review of ``retrieval-review-design.md`` §8's journey: a comparison is
opened, the unchanged sibling and the removed claim are read on their own sides, the unmapped path is
inspected through the expansion, an authored assessment bound to the exact examined inputs is
displayed, and changing only the source is shown to make that assessment stale without making it
unreadable or reusable.

The candidate resolution, the pair refusals and the transport route in front of these panes are
measured in ``test_knowledge_review_resolution_and_route.py``, which reuses this module's helpers.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal, get_args

import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_read import read_row_counts
from agents_remember.application.knowledge_review import (
    ReviewCandidateResolution,
    compose_review,
)
from agents_remember.application.review_subject_catalogue import read_subject_catalogue
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge import review as vocabulary
from agents_remember.models.knowledge.diff import KnowledgeDiffRequest, KnowledgeDiffSide
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import InvariantIdentitySeed, KnowledgeReadSeed
from agents_remember.models.knowledge.review import (
    PROPOSED_ASSESSMENT_DISPOSITIONS,
    KnowledgeReviewPayload,
    ReviewJudgmentPane,
    ReviewKnowledgePane,
    ReviewSideContent,
    ReviewSourceInventory,
    ReviewSourceLocation,
    ReviewSourcePane,
    ReviewSurfaceRequest,
    ReviewUnrepresentablePath,
)
from diff_scope_test_support import (
    RETRY_FIRST_REFUSAL_CONDITION,
    DiffFixture,
    build_diff_fixture,
)
from pydantic import ValidationError
from read_scope_test_support import CONDITIONS, SUCCESSOR_CONDITIONS

pytestmark = pytest.mark.evidence_unit

REPOSITORY_LEAF = "260915-ks-l22"

# The names a generated conclusion would have to be carried in. The check below reads the whole
# payload schema, so a field added anywhere under it is measured, not just a field added to the top.
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "summary",
        "narrative",
        "explanation",
        "meaning",
        "severity",
        "score",
        "confidence",
        "ranking",
        "rank",
        "recommendation",
        "verdict",
        "conclusion",
        "conflict",
        "impact",
        "importance",
        "approved",
        "approval",
        "compatible",
        "implied_finding",
        "disposition_suggestion",
    }
)


def review_config() -> McpRuntimeConfig:
    """A configuration naming no real root, so only the resolution's own refusals can answer."""

    return McpRuntimeConfig(
        workspace_root=Path("/nonexistent-workspace"),
        coordination_root=Path("/nonexistent-coordination"),
        config_path=Path("/nonexistent-config.json"),
        transcript_root=Path("/nonexistent-transcripts"),
    )


@pytest.fixture
def fixture(tmp_path: Path) -> DiffFixture:
    """One fresh two-snapshot fixture per case; no case observes another's candidate state."""

    return build_diff_fixture(tmp_path / "review")


def resolution_for(fixture: DiffFixture) -> ReviewCandidateResolution:
    """The resolution the adapter would produce for this fixture's candidate, without a contract."""

    return ReviewCandidateResolution(
        repository_id=fixture.repository_id,
        leaf_id=REPOSITORY_LEAF,
        baseline_database=fixture.before.database_path,
        candidate_database=fixture.after.database_path,
        baseline_code_root=fixture.before.git_root,
        candidate_code_root=fixture.after.git_root,
        baseline_code_tree_id=fixture.before_tree_id,
        candidate_code_tree_id=fixture.after_tree_id,
    )


def review_request(fixture: DiffFixture) -> ReviewSurfaceRequest:
    return ReviewSurfaceRequest(
        repository_id=fixture.repository_id,
        master="260915_knowledge-substrate",
        leaf_id=REPOSITORY_LEAF,
        selector=InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id),
    )


def reviewed_selector(fixture: DiffFixture) -> KnowledgeReadSeed:
    """The reviewed subject's selector, narrowed once: a comparison requires one.

    ``ReviewSurfaceRequest.selector`` is optional because the *task-context* request names no subject
    (ICR-R02). Every case here is a subject review, so the narrowing is an assertion about the
    request this module builds rather than a convenience: a helper that silently stopped naming a
    subject would fail here.
    """

    selector = review_request(fixture).selector
    assert selector is not None
    return selector


def render(
    fixture: DiffFixture,
    *,
    previous_binding_digest: str | None = None,
) -> KnowledgeReviewPayload:
    """Render one review and return its payload, failing loudly on a refusal.

    The previous comparison identity travels on the REQUEST (ICR-R17@v1), which is where the transport
    admits it and where the composition reads it, so this helper asks the way a refresh asks rather
    than passing a parallel keyword beside the request.
    """

    request = review_request(fixture).model_copy(
        update={"previous_binding_digest": previous_binding_digest}
    )
    result = compose_review(resolution_for(fixture), request)
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


# -- the panes ---------------------------------------------------------------------------------


def test_the_knowledge_pane_renders_the_subjects_own_statements_and_the_comparisons_own_facts(
    fixture: DiffFixture,
) -> None:
    """Pane 1 shows the reviewed subject's recorded selection and its own classification.

    One text record holds one revision (MIK-R74 rule 3), so the retry identity holds one head
    per snapshot -- the baseline's subject revision and the candidate's revised one -- and
    ICR-R07@v1 reports an explicit compared selection. Both statements render the compared
    heads' own words, the recorded selection names the compared pair, and the pane's field
    changes are exactly the comparison's own -- it adds no field transition of its own making.
    """

    shipped = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=reviewed_selector(fixture),
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
        ),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
    )
    assert shipped.page is not None
    subject = [
        item
        for item in shipped.page.items
        if item.kind == "invariant" and item.record_id == fixture.retry_invariant_id
    ]
    assert subject, "the comparison selected the reviewed identity"
    payload = render(fixture)
    knowledge = payload.knowledge
    assert fixture.retry_invariant_id in knowledge.invariant_ids
    selection = knowledge.revision_selection
    assert selection is not None, "a compared subject records its revision selection"
    assert selection.record_kind == "invariant"
    assert selection.record_id == fixture.retry_invariant_id
    # One head per snapshot: the selection is explicitly compared and records the pair.
    assert selection.state == "compared"
    assert selection.before_revision_id == fixture.subject_revision_id
    assert selection.after_revision_id == fixture.revised_revision_id
    # Each side's statement is that side's own head's words, with that side's own conditions.
    assert knowledge.before_statement.state == "present"
    assert knowledge.after_statement.state == "present"
    assert tuple(knowledge.before_conditions) == tuple(SUCCESSOR_CONDITIONS)
    assert tuple(knowledge.after_conditions) == (*CONDITIONS, RETRY_FIRST_REFUSAL_CONDITION)
    assert {(change.item_id, change.field) for change in knowledge.field_changes} == {
        (item.item_id, name) for item in shipped.page.items for name in item.changed_fields
    }
    assert payload.comparison is not None
    assert payload.comparison.policy_version
    assert payload.comparison.binding_digest == payload.comparison.reference


def test_the_source_pane_shows_the_unchanged_sibling_and_the_removed_claims_before_side(
    fixture: DiffFixture,
) -> None:
    """Pane 2 keeps a removed realization's before-side location and the unchanged sibling visible."""

    payload = render(fixture)
    paths = {location.path for location in payload.source.locations}
    assert fixture.baseline_only_path in paths
    assert fixture.unchanged_source_path in paths
    removed = [loc for loc in payload.source.locations if loc.path == fixture.baseline_only_path]
    assert removed and all(location.before_only for location in removed)
    assert any(location.change_state == "unchanged" for location in payload.source.locations)


def test_the_source_pane_counts_the_unmapped_path_and_the_records_outside_the_selection(
    fixture: DiffFixture,
) -> None:
    """The persistent count exposes the paths and records the declared selection did not reach."""

    payload = render(fixture)
    counts = {count.name: count for count in payload.source.remaining}
    unattributed = counts["unattributed_changed_paths"]
    assert unattributed.value is not None and unattributed.value >= 1
    assert fixture.unattributed_path in payload.source.unattributed_changed_paths
    assert counts["locations_remaining"].value == 0
    assert counts["records_present_outside_selection"].value is not None
    assert payload.source.expansion_reference is not None
    assert payload.source.expansion_command is not None


def test_a_missing_role_stays_unclassified_and_is_never_guessed_from_a_name(
    fixture: DiffFixture,
) -> None:
    """A location whose claim records no role displays no role rather than one inferred from a path."""

    payload = render(fixture)
    fields = set(ReviewSourceLocation.model_fields)
    assert fields & {"importance", "priority", "rank", "score", "severity", "weight"} == set()
    # Every displayed role is the one its own claim recorded, and a claim that recorded none
    # displays none rather than one guessed from the path it happens to sit at.
    reported = {(location.claim_id, location.role) for location in payload.source.locations}
    assert reported, "the pane rendered no location, so this measured nothing"
    assert all(role is None or role in get_args(RealizationRole) for _, role in reported)


# -- the prohibitions, as values ---------------------------------------------------------------


def walk_property_names(schema: dict, found: set[str]) -> set[str]:
    """Every property name declared anywhere under one JSON schema document."""

    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                found.update(value)
            walk_property_names(value, found)
    elif isinstance(schema, list):
        for entry in schema:
            walk_property_names(entry, found)
    return found


def test_the_whole_payload_schema_has_no_field_a_generated_conclusion_could_occupy(
    fixture: DiffFixture,
) -> None:
    """The surface's own vocabulary cannot express a summary, a severity, a score or a verdict."""

    names = walk_property_names(KnowledgeReviewPayload.model_json_schema(), set())
    assert names, "the schema walk found no fields, so it measured nothing"
    offenders = sorted(names & FORBIDDEN_FIELD_NAMES)
    assert offenders == [], f"a field exists that could carry a generated conclusion: {offenders}"


def test_the_surface_defines_no_record_kind_no_table_and_no_status_of_its_own() -> None:
    """The leaf mounts records; it does not define one. A kind or a table name here would be one."""

    declared = set(vocabulary.__all__)
    assert not [name for name in declared if name.endswith(("_KIND", "_SCHEMA", "_TABLE"))]
    source = Path(vocabulary.__file__).read_text(encoding="utf-8")
    assert "CREATE TABLE" not in source
    assert "INSERT INTO" not in source


def test_the_surface_stores_nothing_so_deleting_every_rendering_loses_no_canonical_information(
    fixture: DiffFixture,
) -> None:
    """Row-level proof: a full render leaves both datasets' canonical row counts identical."""

    before_counts = read_row_counts(fixture.before.database_path)
    after_counts = read_row_counts(fixture.after.database_path)
    render(fixture)
    render(fixture, previous_binding_digest="0" * 64)
    assert read_row_counts(fixture.before.database_path) == before_counts
    assert read_row_counts(fixture.after.database_path) == after_counts


def test_the_adapter_selects_nothing_because_the_shipped_comparison_is_the_comparison_rendered(
    fixture: DiffFixture,
) -> None:
    """The identity, the ordering and the counts are the shared operation's own, value for value."""

    shipped = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=reviewed_selector(fixture),
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
        ),
        before_path=fixture.before.database_path,
        after_path=fixture.after.database_path,
    )
    assert shipped.page is not None
    assert shipped.binding is not None
    payload = render(fixture)
    assert payload.comparison is not None
    assert payload.comparison.binding_digest == shipped.binding_digest
    assert payload.comparison.selector_digest == shipped.selector_digest
    assert payload.comparison.after_snapshot_digest == shipped.binding.after.logical_digest
    rendered = {location.claim_id for location in payload.source.locations}
    shipped_ids = set()
    for item in shipped.page.items:
        if item.kind != "realization":
            continue
        read_item = item.after if item.after is not None else item.before
        if read_item is not None and read_item.claim_id is not None:
            shipped_ids.add(read_item.claim_id)
    assert rendered == shipped_ids
    counts = {count.name: count for count in payload.source.remaining}
    assert counts["locations_remaining"].value == shipped.page.counts.items_remaining
    assert counts["references_unresolved"].value == shipped.page.counts.suppressed_total


# -- the refusal states, each a rendering ------------------------------------------------------


def test_the_stale_rule_holds_in_both_directions(fixture: DiffFixture) -> None:
    """A moved comparison is previous input with submission disabled -- and the pair is not forgeable.

    One rule, two directions, and they were two cases until they were merged: the stale state has to
    keep the previous reference, say what happened, and disable submission; and a payload that claims
    a current comparison while disabling submission for staleness has to be unconstructible rather
    than merely unlikely. Asserting only the first would leave the rule as a rendering convention.
    """

    previous = "9" * 64
    payload = render(fixture, previous_binding_digest=previous)
    assert payload.staleness.state == "stale"
    assert payload.staleness.previous_comparison_ref == previous
    assert payload.staleness.statement == "Candidate changed — open a new comparison"
    assert payload.submission.state == "disabled_stale"
    assert payload.comparison is not None
    assert payload.staleness.previous_comparison_ref != payload.comparison.binding_digest

    current = render(fixture)
    torn = current.model_dump(mode="json")
    torn["submission"]["state"] = "disabled_stale"
    with pytest.raises(ValidationError):
        KnowledgeReviewPayload.model_validate(torn)


def test_the_surface_reports_the_absent_submission_path_instead_of_growing_a_private_one(
    fixture: DiffFixture,
) -> None:
    """With no publishing route mounted, the surface names the existing authority and adds no control."""

    payload = render(fixture)
    assert payload.submission.state == "unavailable"
    assert set(payload.submission.proposed_dispositions) == set(PROPOSED_ASSESSMENT_DISPOSITIONS)
    assert payload.submission.none_is_approval is True
    assert "existing writer" in payload.submission.next_action
    assert "author, task and authority provenance" in payload.submission.next_action


def test_an_item_whose_author_is_not_published_is_shown_as_an_unresolved_reference(
    fixture: DiffFixture,
) -> None:
    """An unresolved attribution is displayed as unresolved rather than rendered anonymously."""

    payload = render(fixture)
    # The pane carries no matrix collection, so there are no authored effects to resolve authors
    # for; the unresolved references below are the whole measurement.
    assert not hasattr(payload.knowledge, "authored_effects")
    assert all(
        entry.recorded_reference
        for entry in payload.knowledge.unresolved + payload.source.unresolved
    )


def test_a_missing_side_is_its_own_state_and_never_an_empty_string(fixture: DiffFixture) -> None:
    """An absent or unresolved operand carries no text, so it cannot read as an empty document."""

    absent = ReviewSideContent(state="absent", language="text", detail="no record selected")
    assert absent.text is None
    with pytest.raises(ValidationError):
        ReviewSideContent(state="absent", text="", language="text", detail="no record selected")
    with pytest.raises(ValidationError):
        ReviewSideContent(state="present", language="text", detail="present with no text")
    payload = render(fixture)
    for side in (payload.knowledge.before_statement, payload.knowledge.after_statement):
        assert (side.text is None) == (side.state != "present")


# -- two authors, one subject, no resolution ----------------------------------------------------


# -- the worked review --------------------------------------------------------------------------


# -- the pane types and the measured source inventory ------------------------------------------


def test_the_rendered_pane_types_are_the_three_the_design_names(fixture: DiffFixture) -> None:
    """The three panes exist as three distinct types, so a rendering cannot merge their contents.

    The entry read (260915-KS-L45 S2, re-contracted by ICR-R09) is measured in the same case
    because it is the same surface's other half and exists for one reason: ``changeSetBar`` offers
    the Intent review entry only when it holds a recorded subject, and that subject must be a
    recorded identity inside the pair the *server* resolved -- the browser never chooses the
    candidate. So the catalogue the resolver offers is measured identity for identity against the
    two snapshots' own tables: every recorded invariant and family is listed with its label and
    its before/after presence, and listing one never compares it. A subject the comparison cannot
    answer for is still listed rather than dropped to imply a smaller complete population; opening
    it is what carries the comparison's own reason.
    """

    panes = KnowledgeReviewPayload.model_fields
    assert {"knowledge", "source", "judgment"} <= set(panes)
    assert {panes[name].annotation for name in ("knowledge", "source", "judgment")} == {
        ReviewKnowledgePane,
        ReviewSourcePane,
        ReviewJudgmentPane,
    }
    # The knowledge pane keeps mechanical signals in their own collection of their own element
    # type, so a signal cannot be rendered in a finding's place by a missing branch. The matrix's
    # authored-effects collection is gone with the matrix (MIK-R74 rule 1): the pane carries no
    # such field at all, and the removed-collection channel is the whole statement about it.
    fields = ReviewKnowledgePane.model_fields
    assert "authored_effects" not in fields
    assert "signals" not in fields

    resolved = resolution_for(fixture)
    offered = read_subject_catalogue(resolved)
    # The reviewed subject is one of the pair's own recorded identities -- the fixture records
    # several in both snapshots -- and every recorded identity is listed with its label and the
    # presence the two snapshots give it.
    by_id = {entry.selector_id: entry for entry in offered}
    assert fixture.retry_invariant_id in by_id
    assert all(entry.selector_kind in ("invariant", "family") for entry in offered)
    assert all(entry.label for entry in offered)
    assert all(entry.presence in ("before_only", "after_only", "both") for entry in offered)
    assert by_id[fixture.retry_invariant_id].presence == "both"

    # The catalogue agrees with the shipped comparison about which subjects open: the reviewed
    # identity opens through the same operation the review itself uses, and the catalogue row is
    # what names the selector it opens with.
    comparison = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=InvariantIdentitySeed(invariant_id=fixture.retry_invariant_id),
            before=KnowledgeDiffSide(
                context=open_diff_side(
                    resolved.baseline_database,
                    resolved.repository_id,
                    repository_root=resolved.baseline_code_root,
                    code_tree_id=resolved.baseline_code_tree_id,
                )
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(
                    resolved.candidate_database,
                    resolved.repository_id,
                    repository_root=resolved.candidate_code_root,
                    code_tree_id=resolved.candidate_code_tree_id,
                )
            ),
        ),
        before_path=resolved.baseline_database,
        after_path=resolved.candidate_database,
    )
    assert comparison.page is not None

    # An identity neither snapshot records is not offered at all: it is not part of the
    # comparison's population, so offering it would invent a subject the pair never held.
    absent = "11111111-1111-1111-1111-111111111111"
    assert absent not in by_id


def test_a_knowledge_only_change_leaves_an_openable_review_with_a_measured_empty_inventory(
    fixture: DiffFixture,
) -> None:
    """A task whose two bound trees agree still opens, with a *measured* empty source inventory.

    This is the packet's second exercise: knowledge changed and no source path did. The two halves
    must be reported as two different measurements -- an empty change set that was really measured,
    beside the knowledge comparison that really has a page -- because rendering the first as
    "not measured" or the second as empty would each be a different false statement. The comparison
    is the shipped one over the fixture's two real datasets; only the *declared* candidate tree is
    the base tree, which is exactly what a knowledge-only change is.
    """

    resolution = replace(
        resolution_for(fixture),
        candidate_code_root=fixture.before.git_root,
        candidate_code_tree_id=fixture.before_tree_id,
    )

    result = compose_review(resolution, review_request(fixture))

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    inventory = payload.source.inventory
    assert inventory.state == "measured"
    assert inventory.partial is False
    assert inventory.entries == ()
    assert inventory.listed_total == 0
    assert "measured empty change set" in inventory.detail
    assert inventory.before_code_tree_id == inventory.after_code_tree_id == fixture.before_tree_id
    # The knowledge half is untouched by the empty source half: the same comparison, the same page.
    assert payload.comparison is not None
    assert payload.comparison.knowledge_compared is True
    assert payload.comparison.before_code_tree_id == fixture.before_tree_id
    assert payload.staleness.state == "current"
    assert payload.knowledge.selection_state == "subject_selected"
    # The knowledge half is untouched by the empty source half: the same compared retry identity
    # selects the same pair here too, with the same compared heads -- only the source
    # measurement changed, and only the source pane answers for it.
    selection = payload.knowledge.revision_selection
    assert selection is not None, "a compared subject records its revision selection"
    assert selection.state == "compared"
    assert selection.before_revision_id == fixture.subject_revision_id
    assert selection.after_revision_id == fixture.revised_revision_id
    assert payload.knowledge.before_statement.state == "present"
    assert payload.knowledge.after_statement.state == "present"
    # Attribution is the comparison's own statement about recorded claims, *intersected with the
    # measured change population* (ICR-R04). An empty source half has no changed path for a claim to
    # attribute, so the attributed list is empty -- and the partition reports that as a measurement
    # rather than by dropping the question: the denominator is zero because the two trees were
    # compared and agreed. The recorded claims are not erased by it, which is what the pane's
    # locations beside this list show. What the empty change set establishes is that no changed path
    # is *unattributed* -- there is no change to attribute -- which is precisely the opposite of the
    # fabricated-empty reading this case exists to prevent. It is also the correction of the earlier
    # behaviour, which listed the fixture's *unchanged* mapped paths as attributed changes and so
    # reported seven attributed changed paths for a five-path change set.
    attribution = payload.source.attribution
    assert attribution is not None
    assert attribution.state == "measured"
    assert attribution.granularity == "changed_path"
    assert attribution.changed_total == 0
    assert attribution.complete is True
    assert payload.source.attributed_changed_paths == ()
    assert payload.source.unattributed_changed_paths == ()
    assert payload.source.unknown_attribution_changed_paths == ()
    assert payload.source.locations, "the recorded claims are still displayed as locations"
    assert "limitation:source_inventory_unavailable" not in payload.limitations


def test_an_inventory_that_could_not_carry_a_name_is_partial_by_construction() -> None:
    """An entry that exists because a path could not be named makes the inventory partial.

    The rule is structural rather than editorial: a changed path carried by its byte form is exactly
    what a partial measurement *is*, so the two cannot be assembled into a response that reads as
    whole, and an unavailable measurement cannot carry one either. The byte form itself stays valid
    text -- it is the path's exact bytes -- which is what lets the entry be carried at all.
    """

    byte_named = ReviewUnrepresentablePath(
        path_bytes="b'src/caf\\xe9-latin1.py'",
        status="added",
        detail="this changed path's name is not valid text under this code",
    )

    def inventory(
        state: Literal["measured", "unavailable"], *, partial: bool
    ) -> ReviewSourceInventory:
        return ReviewSourceInventory(
            state=state,
            listed_total=0,
            detail="one changed path could not be carried as a name",
            partial=partial,
            command="git diff --raw -z --no-renames <before> <after>",
            unrepresentable_paths=(byte_named,),
        )

    refused: tuple[tuple[Literal["measured", "unavailable"], bool], ...] = (
        ("measured", False),
        ("unavailable", False),
        ("unavailable", True),
    )
    for state, partial in refused:
        with pytest.raises(ValidationError):
            inventory(state, partial=partial)
    carried = inventory("measured", partial=True)
    assert carried.partial is True
    assert carried.unrepresentable_paths == (byte_named,)
    assert carried.listed_total == len(carried.entries) == 0
