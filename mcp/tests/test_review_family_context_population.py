"""The independently selected family population and its bounded readback (ICR-R31@v1).

A text record holds exactly one revision (MIK-R74 rule 3), so one family recording several
revisions -- memberless successor heads, withdrawn-and-rerecorded rosters, explicit history
beside an ambiguous head -- is not a shape a text tree can express. The cases that needed
those shapes name their removal below rather than asserting it. What the text topology still
measures -- a recorded memberless family on both sides, a member joining or leaving a
recorded roster, the measured zero against the absent family, and the sparse walk preserving
its population -- is pinned here through the same production composition.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_review import (
    ReviewCandidateResolution,
    compose_review,
    read_knowledge_review,
)
from agents_remember.models.knowledge.read import FamilyIdentitySeed, InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.serving.review import KNOWLEDGE_REVIEW_ROUTE
from diff_scope_test_support import rebuild_after_tree, replace_after_tree
from fastapi.testclient import TestClient
from knowledge_rows_test_support import INDEX_REPOSITORY_ID, open_knowledge_store
from read_scope_test_support import (
    SYNCHRONIZATION_PATH,
    ExtraFamilySeed,
    build_read_scope_fixture,
)
from test_knowledge_review_source_endpoints import (
    LEAF_ID,
    EndpointFixture,
    build_endpoint_fixture,
)
from test_review_family_context import (
    FamilyScenario,
    _review_of_unfamiliar_invariant,
    _served,
    build_family_scenario,
    entry_for,
    family_request,
    members_of,
    review,
)
from test_review_git_trees import build_world

pytestmark = pytest.mark.evidence_unit

# The guarantee a family records before any membership exists. It is a real authored sentence with
# its own seal and provenance, which is exactly what the pre-fix composition dropped.
EMPTY_FAMILY_LABEL = "recorded-before-its-members"
EMPTY_FAMILY_GUARANTEE = "A guarantee recorded for a family with no members."


# Dead: a text record holds exactly one revision (MIK-R74 rule 3), so one family recording
# a memberless successor beside its membered revision -- two heads -- is not a shape a text
# tree can express. The canonical-ambiguity case below names its removal rather than
# asserting it.
_REMOVED_CANONICAL_AMBIGUITY_CASE = "test_the_canonical_memberless_successor_shape_is_an_ambiguity"


def test_a_recorded_family_with_no_members_is_recorded_not_absent(tmp_path: Path) -> None:
    """A family that records a guarantee and no membership has a context, not a measured zero.

    Both snapshots record the same memberless family revision -- one text record, so one
    revision -- with a real sealed guarantee. The selection is the family, so the entry is
    `recorded`: the guarantee reaches the reviewer with its provenance and seal, and the empty
    roster is measured -- zero memberships, a complete page, a sentence that says so -- rather
    than rendered as absence.
    """

    seed = ExtraFamilySeed(
        family_id=str(uuid4()),
        revision_id=str(uuid4()),
        label=EMPTY_FAMILY_LABEL,
        guarantee=EMPTY_FAMILY_GUARANTEE,
        member_slots=(),
    )
    before = build_read_scope_fixture(tmp_path / "recorded-empty-before", extra_families=(seed,))
    after = build_read_scope_fixture(tmp_path / "recorded-empty-after", extra_families=(seed,))
    family_id = after.extra_families[0].family_id
    revision_id = after.extra_families[0].revision_id
    assert before.extra_families[0].family_id == family_id
    assert before.extra_families[0].revision_id == revision_id
    payload, entry = _family_review(before, after, family_id)
    context = payload.family_context

    assert context.state == "recorded"
    assert context.families_total == 1
    assert entry.selection.state == "compared"
    assert entry.selection.before_revision_id == revision_id
    assert entry.selection.after_revision_id == revision_id
    for side in (entry.before, entry.after):
        assert side.state == "recorded"
        assert side.family_revision_id == revision_id
        assert side.recorded_revision_ids == (revision_id,)
        assert side.guarantee is not None
        assert side.guarantee.joint_guarantee == EMPTY_FAMILY_GUARANTEE
        assert side.guarantee.payload_digest
        assert side.guarantee.provenance.get("actor_ref")
        assert side.members == ()
        assert side.members_total == 0
        assert side.page is not None and side.page.complete
        assert side.page.counts.memberships_total == 0
        assert "0 recorded membership(s)" in side.detail


def _family_review(before, after, family_id: str):
    """Compose one family selection over two trees through the production adapter."""

    result = compose_review(
        ReviewCandidateResolution(
            repository_id=INDEX_REPOSITORY_ID,
            leaf_id="260928-MIK-L74",
            baseline_database=before.database_path,
            candidate_database=after.database_path,
            baseline_code_root=before.git_root,
            candidate_code_root=after.git_root,
            baseline_code_tree_id=before.git_tree_id,
            candidate_code_tree_id=after.git_tree_id,
        ),
        ReviewSurfaceRequest(
            repository_id=INDEX_REPOSITORY_ID,
            master="260928-MIK",
            leaf_id="260928-MIK-L74",
            selector=FamilyIdentitySeed(family_id=family_id),
        ),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    payload = result.payload
    return payload, entry_for(payload, family_id)


# Dead: a text record holds exactly one revision (MIK-R74 rule 3), so a memberless head
# beside the membered revision -- and an explicit history request against that ambiguity --
# is not a shape a text tree can express. The history-sentence case below names its removal
# rather than asserting it.
_REMOVED_HISTORY_SENTENCE_CASE = "test_the_history_sentence_is_measured_against_the_family_owner"


def _assert_primary_unchanged(before, after) -> None:
    """Family curation cannot replace the selected invariant's operands or evidence."""

    assert after.knowledge.revision_selection == before.knowledge.revision_selection
    assert after.knowledge.before_statement == before.knowledge.before_statement
    assert after.knowledge.after_statement == before.knowledge.after_statement
    assert after.knowledge.before_conditions == before.knowledge.before_conditions
    assert after.knowledge.after_conditions == before.knowledge.after_conditions
    assert after.evidence == before.evidence
    assert after.source.inventory == before.source.inventory


def _join_sibling_to_direct_family(directory: Path, endpoints: EndpointFixture) -> EndpointFixture:
    """Rebuild the candidate with the sibling member recorded in the direct family's roster.

    The reviewed subject here is the auxiliary invariant, which only the direct family records.
    The candidate's direct roster gains the sibling revision beside the two it already holds;
    the family revision itself does not move. It is test-only input to the actual composition.
    """

    after = rebuild_after_tree(
        directory,
        omit_claim_paths=(SYNCHRONIZATION_PATH,),
        add_members=(("direct_family", "batch_revision_id"),),
    )
    return replace_after_tree(endpoints, after)


def test_a_new_member_keeps_the_existing_familys_before_context(tmp_path: Path) -> None:
    """A member joining a recorded roster keeps the family's before context and adds one row.

    The candidate's direct roster holds the sibling revision beside the subject and auxiliary
    revisions it already held. The family revision is the same recorded revision on both sides,
    so the selection is compared: the before roster is unchanged, the after roster adds the
    sibling, and the primary operands and evidence are untouched.
    """

    endpoints = build_endpoint_fixture(tmp_path / "new-member")
    fixture = endpoints.diff.before.fixture
    selector = InvariantIdentitySeed(invariant_id=fixture.auxiliary_invariant_id)
    primary = review(endpoints, family_request(endpoints, selector))
    endpoints = _join_sibling_to_direct_family(tmp_path / "joined-after", endpoints)

    payload = review(endpoints, family_request(endpoints, selector))
    _assert_primary_unchanged(primary, payload)
    assert payload.family_context.families_total == 1
    entry = entry_for(payload, fixture.direct_family.family_id)
    assert entry.selection.state == "compared"
    assert entry.before.family_revision_id == fixture.direct_family.revision_id
    assert entry.after.family_revision_id == fixture.direct_family.revision_id
    assert entry.before.guarantee is not None and entry.after.guarantee is not None
    assert set(members_of(entry.before)) == {
        fixture.subject_revision_id,
        fixture.auxiliary_revision_id,
    }
    assert set(members_of(entry.after)) == {
        endpoints.diff.revised_revision_id,
        fixture.auxiliary_revision_id,
        fixture.batch_revision_id,
    }
    # The staying member is the same recorded revision on both sides, so its statement is not
    # a re-authored copy; the joining member is carried with its own recorded statement.
    assert (
        members_of(entry.before)[fixture.auxiliary_revision_id].statement
        == members_of(entry.after)[fixture.auxiliary_revision_id].statement
    )
    assert entry.before.recorded_revision_ids == (fixture.direct_family.revision_id,)
    assert entry.after.recorded_revision_ids == (fixture.direct_family.revision_id,)


def test_a_removed_member_does_not_pin_family_context_to_its_retained_predecessor(
    tmp_path: Path,
) -> None:
    """A member leaving a recorded roster keeps the family's before context and drops one row.

    The candidate's direct roster no longer holds the auxiliary revision; the subject revision
    stays. The family revision is the same recorded revision on both sides, so the selection is
    compared: the before roster carries both members, the after roster carries the subject, and
    the primary operands and evidence are untouched. Nothing is pinned to a retained row.
    """

    endpoints = build_endpoint_fixture(tmp_path / "removed-member")
    fixture = endpoints.diff.before.fixture
    request = endpoints.request(fixture.auxiliary_invariant_id)
    primary = review(endpoints, request)
    after = rebuild_after_tree(
        tmp_path / "withdrawn-after",
        omit_claim_paths=(SYNCHRONIZATION_PATH,),
        omit_members=(("direct_family", "auxiliary_revision_id"),),
    )
    endpoints = replace_after_tree(endpoints, after)

    payload = review(endpoints, request)
    _assert_primary_unchanged(primary, payload)
    entry = entry_for(payload, fixture.direct_family.family_id)
    assert entry.selection.state == "compared"
    assert entry.before.family_revision_id == fixture.direct_family.revision_id
    assert entry.after.family_revision_id == fixture.direct_family.revision_id
    assert set(members_of(entry.before)) == {
        endpoints.diff.before.fixture.subject_revision_id,
        fixture.auxiliary_revision_id,
    }
    assert set(members_of(entry.after)) == {endpoints.diff.revised_revision_id}


def test_the_measured_zero_and_the_absent_family_stay_distinct(tmp_path: Path) -> None:
    """The fix must not turn an absent subject into an empty context, in either direction.

    Two measurements, because the over-correction has two sides. An **invariant** no snapshot records
    a membership for still reads the measured zero of the family population, with the sentence naming
    the selected invariant -- that state is the contrast this leaf must keep. A **family** neither
    snapshot records at all opens no review in the first place: the comparison refuses the selector by
    name before any family context exists, which is stronger than an empty context and is what the
    response states. Neither is rendered as a family context that read nothing.
    """

    endpoints, selector = _review_of_unfamiliar_invariant(tmp_path / "no-family-invariant")
    payload = review(endpoints, family_request(endpoints, selector))
    context = payload.family_context

    assert context.state == "no_family_recorded"
    assert context.entries == ()
    assert context.families_total == 0
    assert "measured zero" in context.detail
    assert "selected invariant" in context.detail

    world = build_world(tmp_path / "absent-family")
    result = read_knowledge_review(
        world.config,
        world.review(selector=FamilyIdentitySeed(family_id=str(uuid4()))),
    )

    assert result.state == "refused"
    assert result.payload is None
    assert result.refusal is not None
    assert result.refusal.code == "comparison_refused"
    assert result.refusal.offending_input == "family"
    assert "selector_absent" in result.refusal.detail


def recorded_population(scenario: FamilyScenario, side: str) -> tuple[set, set]:
    """Expected exact identities come from the authorship owners, independently of projection."""

    diff = scenario.endpoints.diff
    database = diff.before.database_path if side == "before" else diff.after.database_path
    revision_id = (
        scenario.parent_family_revision_id
        if side == "before"
        else scenario.successor_family_revision_id
    )
    store = open_knowledge_store(database, diff.repository_id)
    try:
        members = {
            (str(member), str(invariant))
            for member, invariant in store.connection.execute(
                "SELECT member_id, invariant_revision_id FROM family_member "
                "WHERE repository_id = ? AND family_revision_id = ?",
                (diff.repository_id, revision_id),
            )
        }
        claims = {
            (str(claim), str(invariant))
            for _member, member_invariant in sorted(members)
            for claim, invariant in store.connection.execute(
                "SELECT claim_id, invariant_revision_id FROM realization_claim "
                "WHERE repository_id = ? AND invariant_revision_id = ?",
                (diff.repository_id, member_invariant),
            )
        }
        return members, claims
    finally:
        store.close()


def walk_responses(scenario: FamilyScenario, side: str, page_size: int) -> list[dict]:
    """Follow only published cursors for one side, with a fixed bounded request."""

    params = {
        "repo": scenario.endpoints.repository_id,
        "master": scenario.endpoints.master,
        "leaf": LEAF_ID,
        "selectorKind": "invariant",
        "selectorId": scenario.endpoints.diff.retry_invariant_id,
        "pageSize": page_size,
    }
    bodies = []
    with TestClient(_served(scenario.endpoints)) as client:
        for _ in range(32):
            response = client.get(KNOWLEDGE_REVIEW_ROUTE, params=params)
            assert response.status_code == 200, response.text
            body = response.json()
            bodies.append(body)
            page = family_side(body, scenario.family_id, side)["page"]
            if page["complete"]:
                return bodies
            params.update(pageOf="family_members", continuation=page["continuation"])
    raise AssertionError("the bounded family walk failed to finish")


def family_side(body: dict, family_id: str, side: str) -> dict:
    return next(
        entry[side]
        for entry in body["payload"]["family_context"]["entries"]
        if entry["family_id"] == family_id
    )


@pytest.mark.parametrize("page_size", [1, 2])
def test_content_and_claim_only_pages_preserve_the_authored_population(
    tmp_path: Path, page_size: int
) -> None:
    scenario = build_family_scenario(tmp_path / "sparse-pages")
    for side in ("before", "after"):
        expected_members, expected_claims = recorded_population(scenario, side)
        responses = walk_responses(scenario, side, page_size)
        pages = [family_side(body, scenario.family_id, side) for body in responses]
        assert len(pages) > 1
        # The first page contains invariant content, before the separately ordered membership rows.
        assert pages[0]["members"]
        assert all(member["state"] == "recorded" for member in pages[0]["members"])
        assert all(not member["sources"] for member in pages[0]["members"])
        # Claim-only pages must transport their sparse updates. Advertised expansions can follow
        # them on a final empty page; those too must preserve the walk's complete population.
        assert any(
            page["members"]
            and all(
                member["state"] == "content_not_on_page" and member["sources"]
                for member in page["members"]
            )
            for page in pages
        )
        assert {
            (member["member_id"], member["invariant_revision_id"])
            for page in pages
            for member in page["members"]
        } == expected_members
        assert {
            (source["claim_id"], source["invariant_revision_id"])
            for page in pages
            for member in page["members"]
            for source in member["sources"]
        } == expected_claims
        assert {
            member["invariant_revision_id"]
            for page in pages
            for member in page["members"]
            if member["state"] == "recorded"
        } == {revision_id for _, revision_id in expected_members}
        first = responses[0]["payload"]
        last_returned = 0
        for body, page in zip(responses, pages, strict=True):
            payload = body["payload"]
            assert payload["source"]["inventory"] == first["source"]["inventory"]
            assert payload["knowledge"] == first["knowledge"]
            assert payload["evidence"] == first["evidence"]
            assert payload["comparison"] == first["comparison"]
            count = page["page"]["counts"]["primary_items_returned"]
            assert 0 < count - last_returned <= page_size
            last_returned = count
        assert pages[-1]["page"]["counts"]["primary_items_remaining"] == 0
