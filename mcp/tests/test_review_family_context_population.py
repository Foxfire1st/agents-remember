"""The independently selected family population and its bounded readback (ICR-R31@v1).

The family owner's complete revision list determines family heads, including memberless heads and
revisions that do not cite the selected invariant. Invariant policy discovers only relevant family
identities: a new member does not erase its family's before context, and a removed member does not
pin after context to a stored predecessor. Ambiguity remains explicit; primary invariant operands and
evidence stay independent. Exact family-revision requests still select their named revision and count
the owner's complete history. An absent family remains different from a recorded empty roster.

The real-store enclosure and shared helpers come from ``test_review_family_context``. The HTTP walks
also verify sparse member/claim pages and preserve primary panes across continuation (L38).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.memory.knowledge import families, memberships, realizations
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.read import FamilyIdentitySeed, FamilyRevisionSeed
from agents_remember.models.knowledge.result import (
    FamilyRequest,
    FamilyRevisionRequest,
    RemoveFamilyMemberRequest,
)
from agents_remember.serving.review import KNOWLEDGE_REVIEW_ROUTE
from fastapi.testclient import TestClient
from read_scope_test_support import BASE_LABEL
from test_knowledge_review_source_endpoints import (
    LEAF_ID,
    _place_datasets,
    build_endpoint_fixture,
)
from test_review_family_context import (
    LEFT_GUARANTEE,
    RIGHT_GUARANTEE,
    FamilyScenario,
    _author_family_successor,
    _author_membership,
    _review_of_unfamiliar_invariant,
    _served,
    build_family_scenario,
    entry_for,
    family_request,
    members_of,
    review,
)

pytestmark = pytest.mark.evidence_unit

# The guarantee a family records before any membership exists. It is a real authored sentence with
# its own seal and provenance, which is exactly what the pre-fix composition dropped.
EMPTY_FAMILY_LABEL = "recorded-before-its-members"
EMPTY_FAMILY_GUARANTEE = "A guarantee recorded for a family with no members."


def recorded_revisions(scenario: FamilyScenario, side: str) -> tuple[str, ...]:
    """The family's own revision list on one snapshot, read from the family owner."""

    diff = scenario.endpoints.diff
    database = diff.before.database_path if side == "before" else diff.after.database_path
    store = open_knowledge_store(database, diff.repository_id)
    try:
        return families.list_family_revision_ids(store, scenario.family_id)
    finally:
        store.close()


def family_selection(scenario: FamilyScenario, family_id: str):
    """One review of a family identity, composed through the production entry."""

    return review(
        scenario.endpoints,
        family_request(scenario.endpoints, FamilyIdentitySeed(family_id=family_id)),
    )


def family_context(scenario: FamilyScenario, family_id: str):
    """The composed context and the one entry of a family selection."""

    payload = family_selection(scenario, family_id)
    return payload, entry_for(payload, family_id)


def test_the_canonical_memberless_successor_shape_is_an_ambiguity(tmp_path: Path) -> None:
    """Two memberless successors of one revision are two heads, and no revision may be chosen.

    This is the repository's own ambiguity shape -- two family revisions naming one predecessor and
    citing no members -- authored here through the same store operations the canonical fixture uses.
    The family owner records four revisions on the candidate side and three of them are legitimate
    heads, so the selection must be `ambiguous` with all three as candidates. The pre-fix composition
    derived the population from membership-bearing rows, so only the membered predecessor was visible:
    it reported `compared` and named the **superseded** revision as the established after revision,
    which is the forced determinacy this case exists to keep out.
    """

    scenario = build_family_scenario(tmp_path / "canonical-ambiguity")
    memberless = _author_memberless_successors(scenario)
    _place_datasets(scenario.endpoints.diff, scenario.endpoints.contract)
    payload, entry = family_context(scenario, scenario.family_id)
    after_recorded = recorded_revisions(scenario, "after")

    assert set(after_recorded) == {
        scenario.parent_family_revision_id,
        scenario.successor_family_revision_id,
        *memberless,
    }
    assert set(entry.selection.after_heads) == {
        scenario.successor_family_revision_id,
        *memberless,
    }
    assert entry.selection.before_heads == (scenario.parent_family_revision_id,)
    assert entry.selection.state == "ambiguous"
    assert entry.selection.after_revision_id is None
    assert entry.selection.before_revision_id is None
    assert entry.selection.after_retained == tuple(sorted(after_recorded))
    assert entry.state == "unresolved"
    assert {
        scenario.parent_family_revision_id,
        scenario.successor_family_revision_id,
        *memberless,
    } <= {candidate.revision_id for candidate in entry.candidates}
    # No revision is presented as the family's own, and the superseded revision is not named as the
    # selected after revision -- the two readings the pre-fix bytes produced.
    assert entry.before.guarantee is None and entry.after.guarantee is None
    assert entry.before.family_revision_id is None and entry.after.family_revision_id is None
    assert "ambiguous" in entry.selection.statement
    for head in (scenario.successor_family_revision_id, *memberless):
        assert head in entry.selection.statement
    assert payload.family_context.state == "partial"


def _author_memberless_successor(scenario: FamilyScenario, guarantee: str) -> str:
    """Author one successor family revision that cites no member, through the store's own write."""

    diff = scenario.endpoints.diff
    fixture = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        return _author_family_successor(
            store, diff.repository_id, fixture, fixture.direct_family, guarantee
        )
    finally:
        store.close()


def _author_memberless_successors(scenario: FamilyScenario) -> tuple[str, ...]:
    """Author two successor family revisions that cite no member, through the store's own writes."""

    return (
        _author_memberless_successor(scenario, LEFT_GUARANTEE),
        _author_memberless_successor(scenario, RIGHT_GUARANTEE),
    )


def test_a_recorded_family_with_no_members_is_recorded_not_absent(tmp_path: Path) -> None:
    """A family that records a guarantee and no membership has a context, not a measured zero.

    Both snapshots record the family identity and one revision whose authored guarantee is a real
    sealed aggregate; neither records a membership for it. The selection is the family, so the entry
    is `recorded`: the guarantee reaches the reviewer with its provenance and seal, and the empty
    roster is measured -- zero memberships, a complete page, a sentence that says so -- rather than
    rendered as absence. The pre-fix bytes reported `no_family_recorded`, zero entries and no
    guarantee, with a sentence claiming no family context existed.
    """

    scenario = build_family_scenario(tmp_path / "recorded-empty-family")
    family_id, revision_id = _author_recorded_empty_family(scenario)
    _place_datasets(scenario.endpoints.diff, scenario.endpoints.contract)
    payload, entry = family_context(scenario, family_id)
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


def _author_recorded_empty_family(scenario: FamilyScenario) -> tuple[str, str]:
    """Author one family and one memberless revision into **both** snapshots."""

    diff = scenario.endpoints.diff
    fixture = diff.before.fixture
    family_id = str(uuid4())
    revision_id = str(uuid4())
    for database in (diff.before.database_path, diff.after.database_path):
        store = open_knowledge_store(database, diff.repository_id)
        try:
            created = families.create_family(
                store,
                FamilyRequest(
                    repository_id=diff.repository_id,
                    family_id=family_id,
                    display_label=EMPTY_FAMILY_LABEL,
                    provenance=fixture.authorship,
                ),
            )
            assert created.state == "created", created.refusal
            authored = families.create_family_revision(
                store,
                FamilyRevisionRequest(
                    repository_id=diff.repository_id,
                    revision=FamilyRevisionDraft(
                        family_id=family_id,
                        revision_id=revision_id,
                        display_version=BASE_LABEL,
                        joint_guarantee=EMPTY_FAMILY_GUARANTEE,
                        provenance=fixture.authorship,
                    ),
                ),
            )
            assert authored.state == "created", authored.refusal
        finally:
            store.close()
    return family_id, revision_id


def test_the_history_sentence_is_measured_against_the_family_owner(tmp_path: Path) -> None:
    """Independent family heads participate even when only one contains the selected invariant.

    This case previously expected the membership-containing subset to choose a family head while a
    competing memberless head appeared only in a history count. L40 corrects that false determinacy:
    the invariant's own operands remain unchanged, but its family context is genuinely ambiguous.
    An explicit family-revision request still selects exactly that revision and reports all history.
    """

    scenario = build_family_scenario(tmp_path / "history")
    primary = review(scenario.endpoints)
    memberless = _author_memberless_successor(scenario, LEFT_GUARANTEE)
    _withdraw_parent_membership(scenario)
    _place_datasets(scenario.endpoints.diff, scenario.endpoints.contract)
    payload = review(scenario.endpoints)
    entry = entry_for(payload, scenario.family_id)
    selection = entry.selection
    before_recorded = recorded_revisions(scenario, "before")
    after_recorded = recorded_revisions(scenario, "after")
    assert len(before_recorded) == 1
    assert len(after_recorded) == 3
    assert selection.state == "ambiguous"
    assert selection.before_revision_id is None and selection.after_revision_id is None
    assert set(selection.after_heads) == {scenario.successor_family_revision_id, memberless}
    assert selection.before_retained == (scenario.parent_family_revision_id,)
    assert selection.after_retained == tuple(sorted(after_recorded))
    assert {candidate.revision_id for candidate in entry.candidates} == set(after_recorded)
    assert entry.before.guarantee is None and entry.after.guarantee is None
    assert entry.before.state == entry.after.state == "not_resolved"
    _assert_primary_unchanged(primary, payload)

    explicit = review(
        scenario.endpoints,
        scenario.endpoints.request().model_copy(
            update={
                "selector": FamilyRevisionSeed(
                    family_id=scenario.family_id,
                    revision_id=scenario.parent_family_revision_id,
                )
            }
        ),
    )
    explicit_entry = entry_for(explicit, scenario.family_id)
    selected = explicit_entry.selection
    assert selected.state == "compared"
    assert (
        selected.before_revision_id
        == selected.after_revision_id
        == scenario.parent_family_revision_id
    )
    assert "1 before and 3 after revision(s)" in selected.statement
    assert "2 other recorded revision(s)" in selected.statement
    assert explicit_entry.after.recorded_revision_ids == tuple(sorted(after_recorded))


def _assert_primary_unchanged(before, after) -> None:
    """Family curation cannot replace the selected invariant's operands or evidence."""

    assert after.knowledge.revision_selection == before.knowledge.revision_selection
    assert after.knowledge.before_statement == before.knowledge.before_statement
    assert after.knowledge.after_statement == before.knowledge.after_statement
    assert after.knowledge.before_conditions == before.knowledge.before_conditions
    assert after.knowledge.after_conditions == before.knowledge.after_conditions
    assert after.evidence == before.evidence
    assert after.source.inventory == before.source.inventory


def _author_successor_roster(endpoints, revisions: set[str]) -> str:
    """Author a family's successor and only the requested exact memberships through store owners."""

    diff = endpoints.diff
    fixture = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        successor = _author_family_successor(
            store, diff.repository_id, fixture, fixture.direct_family
        )
        for revision in sorted(revisions):
            _author_membership(store, diff.repository_id, successor, revision, fixture)
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return successor


def test_a_new_member_keeps_the_existing_familys_before_context(tmp_path: Path) -> None:
    endpoints, selector = _review_of_unfamiliar_invariant(tmp_path / "new-member")
    primary = review(endpoints, family_request(endpoints, selector))
    selected = primary.knowledge.revision_selection
    assert selected is not None and selected.state == "added"
    assert selected.before_revision_id is None and selected.after_revision_id is not None
    assert primary.knowledge.before_statement.state == "absent"
    fixture = endpoints.diff.before.fixture
    old_revisions = {endpoints.diff.subject_revision_id, fixture.auxiliary_revision_id}
    new_revisions = old_revisions | {selected.after_revision_id}
    successor = _author_successor_roster(endpoints, new_revisions)

    payload = review(endpoints, family_request(endpoints, selector))
    _assert_primary_unchanged(primary, payload)
    assert payload.family_context.families_total == 1
    entry = entry_for(payload, fixture.direct_family.family_id)
    assert entry.selection.state == "compared"
    assert entry.before.family_revision_id == fixture.direct_family.revision_id
    assert entry.after.family_revision_id == successor
    assert entry.before.guarantee is not None and entry.after.guarantee is not None
    assert set(members_of(entry.before)) == old_revisions
    assert set(members_of(entry.after)) == new_revisions
    for revision in old_revisions:
        assert (
            members_of(entry.before)[revision].statement
            == members_of(entry.after)[revision].statement
        )
    assert entry.before.recorded_revision_ids == (fixture.direct_family.revision_id,)
    assert set(entry.after.recorded_revision_ids) == {fixture.direct_family.revision_id, successor}


def test_a_removed_member_does_not_pin_family_context_to_its_retained_predecessor(
    tmp_path: Path,
) -> None:
    endpoints = build_endpoint_fixture(tmp_path / "removed-member")
    fixture = endpoints.diff.before.fixture
    request = endpoints.request(fixture.auxiliary_invariant_id)
    primary = review(endpoints, request)
    successor = _author_successor_roster(endpoints, {endpoints.diff.subject_revision_id})

    payload = review(endpoints, request)
    _assert_primary_unchanged(primary, payload)
    entry = entry_for(payload, fixture.direct_family.family_id)
    assert entry.selection.state == "compared"
    assert entry.before.family_revision_id == fixture.direct_family.revision_id
    assert entry.after.family_revision_id == successor
    assert set(members_of(entry.before)) == {
        endpoints.diff.subject_revision_id,
        fixture.auxiliary_revision_id,
    }
    assert set(members_of(entry.after)) == {endpoints.diff.subject_revision_id}
    store = open_knowledge_store(endpoints.diff.after.database_path, endpoints.diff.repository_id)
    try:
        retained = memberships.find_membership_by_pair(
            store, fixture.direct_family.revision_id, fixture.auxiliary_revision_id
        )
        assert (
            retained is not None
        )  # The historical after membership was never deleted to force v2.
    finally:
        store.close()


def _withdraw_parent_membership(scenario: FamilyScenario) -> None:
    """Withdraw the reviewed subject's membership in the parent revision, on the candidate only.

    The candidate re-records the membership under the successor revision, which is what a curator's
    movement looks like in this store; the parent revision then records no membership of the subject
    while remaining a recorded revision of the family -- the shape in which a count over the selected
    population reports zero other revisions and the store records two.
    """

    diff = scenario.endpoints.diff
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        parent = memberships.find_membership_by_pair(
            store, scenario.parent_family_revision_id, diff.subject_revision_id
        )
        assert parent is not None
        removed = memberships.remove_family_member(
            store,
            RemoveFamilyMemberRequest(
                repository_id=diff.repository_id,
                member_id=parent.member_id,
                expected_row_digest=parent.row_digest,
            ),
        )
        assert removed.state == "removed", removed.refusal
    finally:
        store.close()


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

    scenario = build_family_scenario(tmp_path / "absent-family")
    result = read_knowledge_review(
        scenario.endpoints.config,
        family_request(scenario.endpoints, FamilyIdentitySeed(family_id=str(uuid4()))),
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
        rows = memberships.list_members(store, revision_id).members
        return (
            {(row.member_id, row.invariant_revision_id) for row in rows},
            {
                (claim.claim_id, claim.invariant_revision_id)
                for row in rows
                for claim in realizations.list_claims_for_invariant_revision(
                    store, row.invariant_revision_id
                ).claims
            },
        )
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
