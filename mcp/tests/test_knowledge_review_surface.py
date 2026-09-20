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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_read import read_row_counts
from agents_remember.application.knowledge_review import (
    ReviewCandidateResolution,
    ReviewRecordInputs,
    _reviewable_entries,
    _selected_item_count,
    compose_review,
    list_knowledge_review_entries,
    resolve_review_candidate,
    review_records_for,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge import review as vocabulary
from agents_remember.models.knowledge.detection import (
    DetectionInputSide,
    DetectionRecordedInputSet,
    DetectionRelationshipPath,
    DetectionScopeManifest,
    DetectionSignalPayload,
)
from agents_remember.models.knowledge.diff import KnowledgeDiffRequest, KnowledgeDiffSide
from agents_remember.models.knowledge.evidence import (
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
)
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import (
    PROPOSED_ASSESSMENT_DISPOSITIONS,
    KnowledgeReviewPayload,
    ReviewAssessmentDisplay,
    ReviewEvidencePane,
    ReviewKnowledgePane,
    ReviewSideContent,
    ReviewSignal,
    ReviewSourceLocation,
    ReviewSourcePane,
    ReviewSurfaceRequest,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceReference,
    AssessmentSubject,
    ReviewAssessment,
    ReviewAssessmentDisposition,
    ReviewAssessmentRevision,
)
from agents_remember.models.lifecycles.review_assessment_store import (
    AssessmentInputs,
    bind_assessment,
)
from agents_remember.serving.review import register_review_routes, review_request_from_query
from diff_scope_test_support import DiffFixture, build_diff_fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

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


NO_RECORDS = ReviewRecordInputs()


def render(
    fixture: DiffFixture,
    records: ReviewRecordInputs = NO_RECORDS,
    *,
    previous_binding_digest: str | None = None,
) -> KnowledgeReviewPayload:
    """Render one review and return its payload, failing loudly on a refusal."""

    result = compose_review(
        resolution_for(fixture),
        review_request(fixture),
        records,
        previous_binding_digest=previous_binding_digest,
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def published_assessment(
    fixture: DiffFixture,
    *,
    disposition: ReviewAssessmentDisposition = "concern_found",
    assessment_id: str = "AS-1",
    author: str = "curator-3",
    finding: str = "The combined retry configuration can exceed the five-second budget.",
) -> ReviewAssessment:
    """One assessment bound to the exact inputs it examined, through the shipped binding seam."""

    revision = ReviewAssessmentRevision(
        assessmentId=assessment_id,
        subject=AssessmentSubject(
            kind="invariant-revision",
            recordId=fixture.retry_invariant_id,
            beforeRevisionIds=(fixture.subject_revision_id,),
            afterRevisionIds=(fixture.revised_revision_id,),
        ),
        disposition=disposition,
        finding=finding,
        rationale=(
            "Four attempts may each consume two seconds and the inspected call path permits all "
            "four with no earlier shared deadline."
        ),
        evidenceRefs=(AssessmentEvidenceReference(namespace="code", ref="src/retry_interval.py"),),
        comparisonRef="comparison-B-M-F1",
        scopeManifestRef="scope-union-B-M-F1",
    )
    return bind_assessment(
        authorized=revision,
        inputs=AssessmentInputs(
            scopeManifestRef="scope-union-B-M-F1",
            comparisonRef="comparison-B-M-F1",
            codeCandidateTree=fixture.after_tree_id,
            memoryCandidateTree=None,
            pairIdentityDigest="a" * 64,
            taskTopologyFingerprint="b" * 64,
            taskIntentDigest="c" * 64,
            resolverVersion="resolver/v1",
            policyVersion="policy/v1",
        ),
        author_ref=author,
        author_role="curator",
        publication_ref="publication/1",
    )


def observation(execution_result: str = "passed") -> VerificationObservationPayload:
    return VerificationObservationPayload(
        command_name="pytest",
        command_identity="mcp/.venv/bin/python -m pytest mcp/tests -m 'not integration'",
        code_candidate_tree_id="e" * 40,
        result_artifact=ResultArtifactReference(
            path="reports/test-results.json",
            sha256="d" * 64,
            size_bytes=1024,
            digest_checked_against_bytes=True,
        ),
        execution_result=execution_result,  # type: ignore[arg-type]
        environment=RunEnvironment(host="builder", interpreter="cpython-3.13.15"),
    )


def signal(fixture: DiffFixture) -> DetectionSignalPayload:
    return DetectionSignalPayload(
        signal_id="11111111-1111-1111-1111-111111111111",
        repository_id="22222222-2222-2222-2222-222222222222",
        governing_route_id="33333333-3333-3333-3333-333333333333",
        condition="absent_anchor",
        input_set=DetectionRecordedInputSet(
            declared="trigger_side_only",
            sides=(
                DetectionInputSide(
                    side="trigger",
                    context=open_diff_side(
                        fixture.after.database_path,
                        fixture.repository_id,
                        repository_root=fixture.after.git_root,
                        code_tree_id=fixture.after_tree_id,
                    ),
                    selector_digest="f" * 64,
                    selector_policy_version="knowledge-read-selection/v1",
                ),
            ),
        ),
        observed_changes=(),
        relationship_paths=(
            DetectionRelationshipPath(
                path_id="path-1", snapshot_side="trigger", edges=("e1",), reached_item_id="item-1"
            ),
        ),
        extractor_version="recorded-anchor-locator/v1",
        policy_version="family-detection/v1",
        scope_manifest=DetectionScopeManifest(
            manifest_ref="manifest-1",
            retention_required=False,
            destination_kind="enclosure_local",
            retention_basis="the manifest is retained with the run's own report",
        ),
        registered_scope_status="incomplete_scan",
        unmapped_changed_paths=("src/unmapped.py",),
        limitations=(
            "unmapped_changed_paths",
            "truncated_scan",
            "no_semantic_assessment_performed",
        ),
        detail=(
            "condition=absent_anchor; followed_paths=path-1; limitations=unmapped_changed_paths | "
            "truncated_scan | no_semantic_assessment_performed"
        ),
    )


# -- the panes ---------------------------------------------------------------------------------


def test_the_knowledge_pane_renders_the_subjects_own_statements_and_the_comparisons_own_facts(
    fixture: DiffFixture,
) -> None:
    """Pane 1 shows the reviewed subject's two recorded statements and its own classification."""

    request = review_request(fixture)
    shipped = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=request.selector,
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
    assert knowledge.before_statement.state == "present"
    assert knowledge.after_statement.state == "present"
    # Both rendered statements are recorded ones, and the pane's field changes are exactly the
    # comparison's own -- it adds no field transition of its own making.
    recorded = {item.before.statement for item in subject if item.before is not None} | {
        item.after.statement for item in subject if item.after is not None
    }
    assert knowledge.before_statement.text in recorded
    assert knowledge.after_statement.text in recorded
    assert {(change.item_id, change.field) for change in knowledge.field_changes} == {
        (item.item_id, name) for item in shipped.page.items for name in item.changed_fields
    }
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

    request = review_request(fixture)
    shipped = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=request.selector,
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


def test_an_unassessed_subject_is_displayed_unassessed_and_never_defaulted_to_compatible(
    fixture: DiffFixture,
) -> None:
    """With no published assessment the pane says ``unassessed`` and offers no disposition at all."""

    payload = render(fixture)
    assert payload.evidence.assessment_state == "unassessed"
    assert payload.evidence.assessments == ()
    assert payload.knowledge.assessment is None
    assert payload.knowledge.assessments == ()
    # No disposition value is carried anywhere but the published vocabulary of the authority.
    assert "disposition" not in json.dumps(
        payload.knowledge.model_dump(mode="json", exclude={"assessment", "assessments"})
    )
    assert payload.submission.none_is_approval is True

    # The evidence pane's version of the same rule: an empty corpus is a *stated* absence with
    # source inspection still available beside it, never a silent blank and never a conclusion. This
    # was a second case until the two were merged, because both measure one rendering rule -- what
    # the surface does when it has no record to show -- across the two panes it applies to.
    assert payload.evidence.evidence_state == "none_recorded"
    assert payload.evidence.evidence_links == ()
    assert payload.evidence.observations == ()
    assert payload.evidence.source_inspection_available is True
    assert payload.source.locations


def test_a_passing_observation_is_displayed_as_an_observation_and_never_as_invariant_satisfied(
    fixture: DiffFixture,
) -> None:
    """A passed run keeps its candidate, command, digest, result and environment, and stays a run."""

    payload = render(fixture, ReviewRecordInputs(observations=(observation("passed"),)))
    assert payload.evidence.evidence_state == "recorded"
    assert len(payload.evidence.observations) == 1
    shown = payload.evidence.observations[0]
    assert shown.execution_result == "passed"
    assert shown.command_identity is not None
    assert shown.result_artifact_digest == "d" * 64
    assert shown.environment_identity == "builder/cpython-3.13.15"
    # The passing run is not an assessment, and the payload contains no sufficiency verdict.
    assert payload.evidence.assessment_state == "unassessed"
    assert "invariant satisfied" not in json.dumps(payload.model_dump(mode="json")).lower()


def test_a_detection_signal_carries_its_facts_and_scope_limitations_and_no_severity(
    fixture: DiffFixture,
) -> None:
    """A signal is the matched condition with its versions and limits -- never a finding."""

    payload = render(fixture, ReviewRecordInputs(signals=(signal(fixture),)))
    assert len(payload.knowledge.signals) == 1
    shown = payload.knowledge.signals[0]
    assert shown.condition == "absent_anchor"
    assert shown.policy_version == "family-detection/v1"
    assert shown.extractor_version == "recorded-anchor-locator/v1"
    assert "unmapped_changed_paths" in shown.scope_limitations
    assert shown.relationship_paths == ("path-1:item-1",)
    fields = set(ReviewSignal.model_fields)
    assert fields & FORBIDDEN_FIELD_NAMES == set()
    # A signal is a different kind from an authored effect, so neither collection can absorb it.
    assert payload.knowledge.authored_effects == ()


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
    assert "curator authority" in payload.submission.next_action


def test_an_item_whose_author_is_not_published_is_shown_as_an_unresolved_reference(
    fixture: DiffFixture,
) -> None:
    """An unresolved attribution is displayed as unresolved rather than rendered anonymously."""

    payload = render(fixture)
    if payload.knowledge.authored_effects:
        for effect in payload.knowledge.authored_effects:
            assert effect.author_ref is None
            assert any(entry.field == "author" for entry in effect.unresolved)
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


def test_two_disagreeing_assessments_are_both_displayed_with_their_authors_and_no_resolution(
    fixture: DiffFixture,
) -> None:
    """Both authored records are shown; the surface picks no winner and has no resolution field."""

    stored = (
        published_assessment(
            fixture, disposition="concern_found", assessment_id="AS-1", author="c1"
        ),
        published_assessment(
            fixture,
            disposition="no_concern_found",
            assessment_id="AS-2",
            author="c2",
            finding="",
        ),
    )
    payload = render(fixture, ReviewRecordInputs(assessments=stored))
    shown = payload.evidence.assessments
    assert {entry.assessment_id for entry in shown} == {"AS-1", "AS-2"}
    assert {entry.author_ref for entry in shown} == {"c1", "c2"}
    assert {entry.disposition for entry in shown} == {"concern_found", "no_concern_found"}
    assert payload.evidence.assessment_state == "assessed"
    fields = set(ReviewAssessmentDisplay.model_fields)
    assert fields & {"resolution", "winner", "resolved", "conflict"} == set()
    # An unmeasured assessment is reported stale rather than promoted to current.
    assert {entry.binding_state for entry in shown} == {"stale"}


def test_a_measured_matching_binding_reports_the_assessment_current(fixture: DiffFixture) -> None:
    """A caller that measured the world gets the assessment's own status, carried verbatim."""

    stored = published_assessment(fixture)
    payload = render(fixture, ReviewRecordInputs(assessments=(stored,), current={}))
    assert [entry.binding_state for entry in payload.evidence.assessments] == ["current"]
    assert payload.evidence.assessments[0].author_ref == "curator-3"
    assert payload.evidence.assessments[0].examined_inputs


# -- the worked review --------------------------------------------------------------------------


def test_the_worked_review_journey_renders_every_step_it_walks(fixture: DiffFixture) -> None:
    """``retrieval-review-design.md`` §8's journey, read back from the surface at each step."""

    # 1. Change knowledge and open the review before any commit: the comparison is page-rendered.
    payload = render(fixture)
    assert payload.comparison.after_snapshot_digest
    # 2. Expand the unchanged sibling and the removed claim, each on its own side.
    locations = {location.path: location for location in payload.source.locations}
    assert locations[fixture.unchanged_source_path].change_state == "unchanged"
    assert locations[fixture.baseline_only_path].before_only is True
    # 3. Inspect the unmapped path: it is reported outside any recorded attribution.
    assert fixture.unattributed_path in payload.source.unattributed_changed_paths
    # 4. Author an assessment bound to the exact examined inputs and read it back as authored.
    stored = published_assessment(fixture)
    authored = render(fixture, ReviewRecordInputs(assessments=(stored,), current={}))
    displayed = authored.evidence.assessments[0]
    assert displayed.author_ref == "curator-3"
    assert displayed.role_ref == "curator"
    assert displayed.binding_state == "current"
    assert displayed.evidence_refs == ("code:src/retry_interval.py",)
    # 5. Change only the source: the assessment is stale, still readable, and never current again.
    moved = render(fixture, ReviewRecordInputs(assessments=(stored,)))
    after = moved.evidence.assessments[0]
    assert after.binding_state == "stale"
    assert after.assessment_id == displayed.assessment_id
    assert moved.evidence.assessment_state == "assessed"


def test_a_stale_assessment_is_never_reused_as_a_review_of_the_new_candidate(
    fixture: DiffFixture,
) -> None:
    """The stale assessment stays displayed as stale, and submission against it is disabled."""

    stored = published_assessment(fixture)
    payload = render(
        fixture,
        ReviewRecordInputs(assessments=(stored,)),
        previous_binding_digest="9" * 64,
    )
    assert payload.evidence.assessments[0].binding_state == "stale"
    assert payload.submission.state == "disabled_stale"
    # Re-rendering against the current comparison does not carry the old status forward.
    fresh = render(fixture, ReviewRecordInputs(assessments=(stored,), current={}))
    assert fresh.evidence.assessments[0].binding_state == "current"


# -- resolution and transport -------------------------------------------------------------------


def test_the_candidate_is_resolved_from_task_context_and_never_from_a_browser_chosen_path() -> None:
    """A path-shaped selector is refused rather than resolved, and no fallback dataset is used."""

    config = review_config()
    for bad in ("../escape", "a/b", ".hidden", ""):
        outcome = resolve_review_candidate(config, "agents-remember", "master", bad)
        assert not isinstance(outcome, ReviewCandidateResolution)
        assert outcome.code == "candidate_unresolved"
    missing = resolve_review_candidate(
        config, "agents-remember", "260915_knowledge-substrate", "L1"
    )
    assert not isinstance(missing, ReviewCandidateResolution)
    assert missing.code == "candidate_unresolved"
    assert missing.next_action


def test_an_absent_candidate_dataset_refuses_by_name_rather_than_substituting_one(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """A candidate whose dataset is gone is refused; no other dataset is read in its place."""

    resolution = ReviewCandidateResolution(
        repository_id=fixture.repository_id,
        leaf_id=REPOSITORY_LEAF,
        baseline_database=fixture.before.database_path,
        candidate_database=tmp_path / "absent" / "knowledge-candidate.sqlite",
        baseline_code_root=fixture.before.git_root,
        candidate_code_root=fixture.after.git_root,
        baseline_code_tree_id=fixture.before_tree_id,
        candidate_code_tree_id=fixture.after_tree_id,
    )
    result = compose_review(resolution, review_request(fixture))
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "candidate_dataset_absent"
    assert result.refusal.next_action


def test_the_transport_admits_exactly_the_two_reviewable_selector_kinds() -> None:
    """Only an invariant or family identity is a reviewable subject; nothing else is mapped onto one."""

    invariant = review_request_from_query(
        "agents-remember",
        "master",
        "leaf",
        "invariant",
        "11111111-1111-1111-1111-111111111111",
    )
    assert invariant is not None and invariant.selector.kind == "invariant"
    family = review_request_from_query(
        "agents-remember", "master", "leaf", "family", "11111111-1111-1111-1111-111111111111"
    )
    assert family is not None and family.selector.kind == "family"
    for refused in ("path", "invariant_revision", "", "latest"):
        assert review_request_from_query("agents-remember", "master", "leaf", refused, "x") is None


def test_the_route_serves_the_typed_result_and_refuses_by_name_with_no_adapter(
    fixture: DiffFixture,
) -> None:
    """The route is transport only: it serializes the port's own result and refuses without one."""

    config = review_config()
    request = review_request(fixture)
    payload = render(fixture)
    served = FastAPI()
    register_review_routes(
        served, config, lambda _: compose_review(resolution_for(fixture), request)
    )
    with TestClient(served) as client:
        body = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
            },
        )
        assert body.status_code == 200
        assert body.json()["state"] == "review"
        assert (
            body.json()["payload"]["comparison"]["binding_digest"]
            == payload.comparison.binding_digest
        )
        bad = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "latest",
                "selectorId": "x",
            },
        )
        assert bad.status_code == 400
        assert bad.json()["status"] == "bad-request"

    unwired = FastAPI()
    register_review_routes(unwired, config, None)
    with TestClient(unwired) as client:
        refused = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
            },
        )
        assert refused.status_code == 503
        assert refused.json()["status"] == "unavailable"


def test_the_published_assessment_loader_returns_nothing_for_an_unresolvable_candidate() -> None:
    """A candidate with no readable authority yields an empty collection, not a fabricated record.

    The same unresolvable context is asked for the surface's *entry* list (260915-KS-L45 S2): the
    entry read resolves through the identical operation the review does, so it refuses with the same
    code rather than answering with an empty list. An empty list would read as "this candidate
    records nothing to review", which is a different fact from "no candidate resolves here", and the
    task view renders no entry for either -- but only one of the two is what happened.
    """

    config = review_config()
    request = ReviewSurfaceRequest(
        repository_id="agents-remember",
        master="260915_knowledge-substrate",
        leaf_id=REPOSITORY_LEAF,
        selector=InvariantIdentitySeed(invariant_id="11111111-1111-1111-1111-111111111111"),
    )
    assert review_records_for(config, request).assessments == ()

    entries = list_knowledge_review_entries(
        config, request.repository_id, request.master, request.leaf_id
    )
    assert entries.state == "refused"
    assert entries.refusal is not None
    assert entries.refusal.code == "candidate_unresolved"
    assert entries.entries == ()


def test_the_rendered_pane_types_are_the_three_the_design_names(fixture: DiffFixture) -> None:
    """The three panes exist as three distinct types, so a rendering cannot merge their contents.

    The entry read (260915-KS-L45 S2) is measured in the same case because it is the same surface's
    other half and exists for one reason: ``changeSetBar`` offers the Intent review entry only when
    it holds a reviewed subject, and that subject must be a recorded identity inside the candidate
    the *server* resolved -- the browser never chooses the candidate. So the entry the resolver
    offers is measured against the shipped comparison's own answer for the same identity, item for
    item, and a subject the comparison cannot answer for is absent from the list rather than offered
    with a count nobody measured.
    """

    panes = KnowledgeReviewPayload.model_fields
    assert {"knowledge", "source", "evidence"} <= set(panes)
    assert {panes[name].annotation for name in ("knowledge", "source", "evidence")} == {
        ReviewKnowledgePane,
        ReviewSourcePane,
        ReviewEvidencePane,
    }
    # The knowledge pane keeps authored records and mechanical signals in separate collections of
    # separate element types, so one cannot be rendered in the other's place by a missing branch.
    fields = ReviewKnowledgePane.model_fields
    assert "authored_effects" in fields and "signals" in fields
    assert fields["authored_effects"].annotation != fields["signals"].annotation

    resolved = resolution_for(fixture)
    offered = _reviewable_entries(resolved, probe=None)
    # The reviewed subject is one of the candidate's own recorded identities -- the fixture records
    # several -- and every identity offered is one the comparison answered for.
    by_id = {entry.selector_id: entry for entry in offered}
    assert fixture.retry_invariant_id in by_id
    assert all(entry.selector_kind in ("invariant", "family") for entry in offered)
    assert all(entry.label for entry in offered)
    assert all(entry.selected_item_count > 0 for entry in offered)

    # The count is the comparison's own total for that identity, re-read here through the shipped
    # operation rather than trusted: an entry advertising a number the review would not show is how
    # a caller comes to believe a subject was reviewed when it was not.
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
    assert (
        by_id[fixture.retry_invariant_id].selected_item_count == comparison.page.counts.items_total
    )

    # An identity the candidate does not record is not offered at all, and a subject the comparison
    # cannot answer for is dropped rather than listed with a zero.
    absent = "11111111-1111-1111-1111-111111111111"
    assert absent not in by_id
    assert _selected_item_count(resolved, "invariant", absent, probe=None) is None
