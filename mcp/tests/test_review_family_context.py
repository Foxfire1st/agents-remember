"""The comparison-bound family review context through the production composition (ICR-R31@v1).

These cases drive the review the dashboard drives -- :func:`…knowledge_review.read_knowledge_review`
over a real leaf enclosure with a real Git worktree and two real knowledge datasets -- and measure the
one composition ICR-R31 adds: the recorded families the selected subject belongs to on each snapshot,
each selected family revision's own authored guarantee, and that revision's recorded member roster,
including the siblings a reviewer has to be able to read beside a changed member.

Every population is produced by the store's own operations over the shared endpoint fixture: the
successor family revision and its memberships are authored through the family and membership owners,
the changed member is an authored successor revision of the sibling invariant, and the datasets the
review reads are the files its own resolution selected. Nothing here builds a review payload, injects
a family context, or reads a database the resolution did not choose.

The load-bearing properties, one case each:

* a selected invariant resolves **every** recorded family it belongs to on both snapshots, each with
  its exact selected family revision, its own stored guarantee text and its recorded roster;
* a family revision's roster is read from **its own** membership rows: a successor revision that
  drops a member does not inherit the parent's roster, and the dropped membership is still carried on
  the side that records it;
* one invariant recorded under two families is **one** canonical revision referenced twice -- the
  unique member count does not grow with the repeated membership, and each context names the other
  family revision the membership owner records;
* a changed member makes its family context available and changes nothing about the guarantee: both
  sides carry the exact text the store holds, and no field states a conclusion about it;
* a side that records no family is ``not_recorded`` rather than an empty roster, a selection in
  no family is a *measured* zero, and missing knowledge is the review's own refusal rather than an
  empty family collection;
* a roster that did not fit its page stays partial with the read owner's own counts and the owner's
  own cursor, which continues exactly that walk and is refused when it binds nothing;
* the complete source population is measured independently of family membership, so a repeated
  membership adds a context and changes no file total.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_review import (
    read_knowledge_review,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    InvariantIdentitySeed,
)
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.review_family_context import (
    ReviewFamilyContext,
    ReviewFamilyMember,
    ReviewFamilyRevisionContext,
)
from agents_remember.serving.review import (
    KNOWLEDGE_REVIEW_ROUTE,
    register_review_routes,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_rows_test_support import (
    FamilyMemberRequest,
    FamilyRequest,
    FamilyRevisionRequest,
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
    families,
    memberships,
    open_knowledge_store,
)
from read_scope_test_support import (
    APPLICABILITY,
    BASE_LABEL,
    CONDITIONS,
    DIRECT_FAMILY_GUARANTEE,
    EXCLUSIONS,
)
from test_knowledge_review_source_endpoints import (
    LEAF_ID,
    EndpointFixture,
    _place_datasets,
    build_endpoint_fixture,
    compose_endpoint_review,
)

pytestmark = pytest.mark.evidence_unit

# The successor family revision's own guarantee text. It is deliberately a second authored sentence
# rather than the parent revision's, so a case can tell the exact recorded text of the revision the
# review selected from the text of any other revision of the same family.
SUCCESSOR_FAMILY_GUARANTEE = (
    "The retry budget and the batch obligation hold together under the revised member."
)

# The successor member revision's statement and label. The member moved, the family's guarantee did
# not, and the two are separate stored facts this module measures rather than asserts.
MEMBER_SUCCESSOR_STATEMENT = "Apply every admitted candidate change as one sealed batch."
MEMBER_SUCCESSOR_VERSION = "v2"

# The candidate-only family identity and its guarantee: a family one snapshot records and the other
# does not, which is what makes a one-sided family context's *absent* side measurable.
# The two sibling family revisions the ambiguity case authors: each is a legitimate head with its
# own authored guarantee text, so a composition that chose one of them would be visible in the text
# it presented as the family's own.
LEFT_GUARANTEE = "The retry budget holds with the batch rule recorded on the left branch."
RIGHT_GUARANTEE = "The retry budget holds with the batch rule recorded on the right branch."

ADDED_FAMILY_LABEL = "candidate-only-family"
ADDED_FAMILY_GUARANTEE = "The retry budget is recorded with the candidate's own batch rule."


@dataclass(frozen=True)
class FamilyScenario:
    """One enclosure whose candidate records a successor family revision and its memberships."""

    endpoints: EndpointFixture
    family_id: str
    parent_family_revision_id: str
    successor_family_revision_id: str
    member_successor_revision_id: str


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory) -> FamilyScenario:
    """One enclosure, curated once: a successor family revision, a moved member and a dropped one."""

    return build_family_scenario(tmp_path_factory.mktemp("family-context"))


def build_family_scenario(directory: Path) -> FamilyScenario:
    """Build the enclosure and author the family movement through the shipped store operations.

    The candidate authors a successor revision of the family the retry obligation directly belongs to
    and names the parent revision as its exact predecessor. The successor's memberships are its own
    rows: the unchanged member (the reviewed revision), a *successor* revision of the sibling member,
    and -- deliberately -- no row for the parent's other member, which is what makes "a successor
    revision's roster is authored, not inherited" measurable.
    """

    endpoints = build_endpoint_fixture(directory / "endpoints")
    diff = endpoints.diff
    fixture = diff.before.fixture
    parent = fixture.direct_family
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        member_successor = _author_member_successor(store, diff.repository_id, fixture)
        successor = _author_family_successor(store, diff.repository_id, fixture, parent)
        _author_membership(store, diff.repository_id, successor, diff.subject_revision_id, fixture)
        _author_membership(store, diff.repository_id, successor, member_successor, fixture)
    finally:
        store.close()
    # The datasets the review reads are the ones its own resolution selects: the two halves live in
    # the leaf's disposable knowledge root, re-copied because the candidate was curated after them.
    _place_datasets(diff, endpoints.contract)
    return FamilyScenario(
        endpoints=endpoints,
        family_id=parent.family_id,
        parent_family_revision_id=parent.revision_id,
        successor_family_revision_id=successor,
        member_successor_revision_id=member_successor,
    )


def _author_member_successor(store, repository_id: str, fixture) -> str:
    """Author one successor revision of the sibling invariant, through the store's own operation."""

    revision_id = str(uuid4())
    created = store.create_revision(
        RevisionRequest(
            repository_id=repository_id,
            revision=RevisionDraft(
                revision_id=revision_id,
                invariant_id=fixture.batch_invariant_id,
                display_version=MEMBER_SUCCESSOR_VERSION,
                statement=MEMBER_SUCCESSOR_STATEMENT,
                applicability=APPLICABILITY,
                conditions=CONDITIONS,
                exclusions=EXCLUSIONS,
                predecessors=(fixture.batch_revision_id,),
                provenance=fixture.authorship,
            ),
        )
    )
    assert created.state == "created", created.refusal
    return revision_id


def _author_family_successor(
    store, repository_id: str, fixture, parent, guarantee: str = SUCCESSOR_FAMILY_GUARANTEE
) -> str:
    """Author one successor family revision naming the parent revision as its exact predecessor."""

    revision_id = str(uuid4())
    authored = families.create_family_revision(
        store,
        FamilyRevisionRequest(
            repository_id=repository_id,
            revision=FamilyRevisionDraft(
                family_id=parent.family_id,
                revision_id=revision_id,
                display_version=BASE_LABEL,
                joint_guarantee=guarantee,
                predecessors=(parent.revision_id,),
                provenance=fixture.authorship,
            ),
        ),
    )
    assert authored.state == "created", authored.refusal
    return revision_id


def _author_membership(store, repository_id: str, family_revision_id: str, member: str, fixture):
    """Author one membership of an exact invariant revision in an exact family revision."""

    created = memberships.create_family_member(
        store,
        FamilyMemberRequest(
            repository_id=repository_id,
            member=FamilyMemberDraft(
                member_id=str(uuid4()),
                family_revision_id=family_revision_id,
                invariant_revision_id=member,
                provenance=fixture.authorship,
            ),
        ),
    )
    assert created.state == "created", created.refusal


# --- the review under test ---------------------------------------------------------------------


def family_request(
    endpoints: EndpointFixture,
    selector: InvariantIdentitySeed | FamilyIdentitySeed | None = None,
    *,
    page_of: str | None = None,
    continuation: str | None = None,
    page_size: int = 0,
) -> ReviewSurfaceRequest:
    """The request the dashboard sends, with the paging fields the case under test needs."""

    base = endpoints.request()
    return base.model_copy(
        update={
            "selector": base.selector if selector is None else selector,
            "page_of": page_of,
            "continuation": continuation,
            "page_size": page_size,
        }
    )


def review(endpoints: EndpointFixture, request: ReviewSurfaceRequest | None = None):
    """Compose family rows from explicit synthetic index-shaped inputs."""

    result = compose_endpoint_review(endpoints, request or endpoints.request())
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def entry_for(payload, family_id: str):
    """The one family context of the named family."""

    found = [entry for entry in payload.family_context.entries if entry.family_id == family_id]
    assert len(found) == 1, [entry.family_id for entry in payload.family_context.entries]
    return found[0]


def members_of(context: ReviewFamilyRevisionContext) -> dict[str, ReviewFamilyMember]:
    """One side context's roster, keyed by the exact member revision it cites."""

    return {member.invariant_revision_id: member for member in context.members}


# -- every recorded family of the selected invariant, with its own guarantee ---------------------


def test_a_selected_invariant_resolves_both_recorded_families_with_their_own_guarantees(
    scenario: FamilyScenario,
) -> None:
    """The reviewed invariant's two recorded families are composed, each at its exact revision.

    The retry obligation is recorded in two families by the fixture itself, and each one is carried
    with the family's independently selected authored head, its own stored guarantee, and its recorded
    roster -- read through the production composition, not built.
    """

    fixture = scenario.endpoints.diff.before.fixture
    payload = review(scenario.endpoints)
    context = payload.family_context

    assert context.state == "recorded", context.detail
    assert context.families_total == 2
    assert context.families_remaining == 0
    assert {entry.family_id for entry in context.entries} == {
        fixture.direct_family.family_id,
        fixture.family.family_id,
    }
    direct = entry_for(payload, fixture.direct_family.family_id)
    assert direct.selection.state == "compared"
    assert direct.before.family_revision_id == scenario.parent_family_revision_id
    assert direct.after.family_revision_id == scenario.successor_family_revision_id
    assert direct.before.guarantee is not None and direct.after.guarantee is not None
    assert direct.before.guarantee.joint_guarantee == DIRECT_FAMILY_GUARANTEE
    assert direct.after.guarantee.joint_guarantee == SUCCESSOR_FAMILY_GUARANTEE
    assert direct.before.guarantee.payload_digest != direct.after.guarantee.payload_digest
    # The two guarantees are the store's own texts, and neither was assembled from the rosters the
    # same context carries: every recorded member statement is a different sentence.
    assert all(
        member.statement != direct.after.guarantee.joint_guarantee
        for member in direct.after.members
    )


def test_a_family_that_did_not_move_is_still_composed_at_its_own_revision(
    scenario: FamilyScenario,
) -> None:
    """A family both snapshots record identically is carried with the same exact revision."""

    fixture = scenario.endpoints.diff.before.fixture
    payload = review(scenario.endpoints)
    unchanged = entry_for(payload, fixture.family.family_id)

    assert unchanged.selection.state == "compared"
    assert unchanged.before.family_revision_id == fixture.family.revision_id
    assert unchanged.after.family_revision_id == fixture.family.revision_id
    assert unchanged.before.guarantee is not None and unchanged.after.guarantee is not None
    assert unchanged.before.guarantee.joint_guarantee == unchanged.after.guarantee.joint_guarantee
    assert set(members_of(unchanged.before)) == set(members_of(unchanged.after))
    assert unchanged.before.members_total == unchanged.after.members_total


# -- a roster is authored per revision, and the siblings stay visible ----------------------------


def test_a_successor_family_revision_is_read_from_its_own_rows_not_inherited(
    scenario: FamilyScenario,
) -> None:
    """The successor's roster is its own memberships, and the dropped one survives on its own side.

    The parent revision records two members; the successor records the reviewed revision and a moved
    sibling. A composition that inherited the parent's roster -- or that read the family's newest
    revision by label or order -- would show the dropped member on the candidate side, which is what
    this case measures. The dropped membership is not lost either: it is carried where it is
    recorded, on the before side, which is the state a reviewer reads as removed.
    """

    fixture = scenario.endpoints.diff.before.fixture
    payload = review(scenario.endpoints)
    direct = entry_for(payload, fixture.direct_family.family_id)
    before, after = members_of(direct.before), members_of(direct.after)

    assert set(before) == {
        scenario.endpoints.diff.subject_revision_id,
        fixture.auxiliary_revision_id,
    }
    assert set(after) == {
        scenario.endpoints.diff.subject_revision_id,
        scenario.member_successor_revision_id,
    }
    assert fixture.auxiliary_revision_id not in after
    assert direct.after.members_total == 2
    assert direct.after.page is not None and direct.after.page.complete
    # The moved member carries its own new statement and version, so a member change is visible as
    # the member's own recorded content rather than as a change to the family's guarantee.
    moved = after[scenario.member_successor_revision_id]
    assert moved.state == "recorded"
    assert moved.statement == MEMBER_SUCCESSOR_STATEMENT
    assert moved.display_version == MEMBER_SUCCESSOR_VERSION
    # The reviewed revision is an unchanged sibling: the same exact revision on both sides, with the
    # same recorded statement, so its presence is not a re-authored copy.
    assert (
        after[scenario.endpoints.diff.subject_revision_id].statement
        == before[scenario.endpoints.diff.subject_revision_id].statement
    )


def test_a_shared_member_is_one_canonical_revision_referenced_in_two_contexts(
    scenario: FamilyScenario,
) -> None:
    """The invariant recorded under two families is one revision in two rosters, not two facts.

    Each context carries the membership row's own identity and the exact revision it cites, and the
    context's counts keep the two apart: the repeated membership raises the row total and leaves the
    unique member total alone. The other family revision the membership owner records is named, so a
    reader can follow the sharing without a second read.
    """

    fixture = scenario.endpoints.diff.before.fixture
    payload = review(scenario.endpoints)
    context = payload.family_context
    direct = entry_for(payload, fixture.direct_family.family_id)
    other = entry_for(payload, fixture.family.family_id)
    reviewed_revision = scenario.endpoints.diff.subject_revision_id

    assert reviewed_revision in members_of(direct.after)
    assert reviewed_revision in members_of(other.after)
    assert (
        members_of(direct.after)[reviewed_revision].member_id
        != members_of(other.after)[reviewed_revision].member_id
    )
    assert context.membership_rows_total == sum(
        entry.before.members_total + entry.after.members_total for entry in context.entries
    )
    unique = {
        member.invariant_revision_id
        for entry in context.entries
        for side in (entry.before, entry.after)
        for member in side.members
    }
    assert context.unique_member_revision_total == len(unique)
    assert context.unique_member_revision_total < context.membership_rows_total
    # The sharing is inspectable from either context: the member names the other family revision the
    # membership owner records it in.
    names = members_of(direct.after)[reviewed_revision].other_family_revision_ids
    assert fixture.family.revision_id in names


def test_a_member_change_makes_its_family_context_available_and_concludes_nothing(
    scenario: FamilyScenario,
) -> None:
    """The changed member's statement travels; the guarantee is carried, never recomputed or judged.

    The candidate's successor family revision records the same guarantee text beside a member whose
    revision changed. The payload states the member's own new statement and the guarantee's own
    recorded text, and no field of the family context could hold a conclusion about what the member
    change means for the guarantee.
    """

    fixture = scenario.endpoints.diff.before.fixture
    payload = review(scenario.endpoints)
    direct = entry_for(payload, fixture.direct_family.family_id)
    moved = members_of(direct.after)[scenario.member_successor_revision_id]

    assert moved.statement == MEMBER_SUCCESSOR_STATEMENT
    assert direct.after.guarantee is not None
    assert direct.after.guarantee.joint_guarantee == SUCCESSOR_FAMILY_GUARANTEE
    assert set(ReviewFamilyMember.model_fields).isdisjoint(
        {"changed", "passed", "verdict", "guarantee_state"}
    )
    assert set(ReviewFamilyContext.model_fields).isdisjoint(
        {"conclusion", "guarantee_change_state", "verdict"}
    )


# -- states that are not an empty collection -----------------------------------------------------


def test_a_selection_in_no_recorded_family_is_a_measured_zero(tmp_path: Path) -> None:
    """An invariant no snapshot records a membership for reports the measured zero, not an absence.

    The identity is authored through the store's own operation and holds one revision and no
    membership. Both snapshots' scopes are read, the family set is empty, and the context says which
    kind of zero that is -- a state a caller can act on, unlike an unread scope.
    """

    endpoints, selector = _review_of_unfamiliar_invariant(tmp_path)
    payload = review(endpoints, family_request(endpoints, selector))
    context = payload.family_context

    assert context.state == "no_family_recorded", context.detail
    assert context.entries == ()
    assert context.families_total == 0
    assert "measured zero" in context.detail


def test_missing_knowledge_is_the_reviews_refusal_not_an_empty_family_context(
    tmp_path: Path,
) -> None:
    """A leaf with no datasets is refused whole; nothing renders as a family read that found none."""

    endpoints = build_endpoint_fixture(tmp_path / "never-initialized", datasets=False)
    result = read_knowledge_review(endpoints.config, endpoints.request())

    assert result.state == "refused", result.payload
    assert result.refusal is not None
    assert result.refusal.code == "candidate_dataset_absent"
    assert endpoints.worktree.is_dir()


def test_a_task_context_review_states_that_it_selected_no_subject(
    scenario: FamilyScenario,
) -> None:
    """The subjectless entry claims no family and keeps the complete source inventory."""

    payload = review(scenario.endpoints, scenario.endpoints.task_request())
    context = payload.family_context

    assert context.state == "no_subject_selected"
    assert context.entries == ()
    assert payload.source.inventory.listed_total > 0
    assert payload.comparison is None


def test_a_family_only_the_candidate_records_is_a_one_sided_context_with_a_stated_absence(
    tmp_path: Path,
) -> None:
    """The side that records no family is ``not_recorded`` -- never a measured empty roster."""

    scenario = build_family_scenario(tmp_path / "one-sided")
    added = _author_added_family(scenario)
    _place_datasets(scenario.endpoints.diff, scenario.endpoints.contract)
    payload = review(scenario.endpoints)
    entry = entry_for(payload, added)

    assert entry.selection.state == "added"
    assert entry.before.state == "not_recorded"
    assert entry.before.members == ()
    assert entry.before.members_total == 0
    assert entry.before.page is None
    assert entry.after.state == "recorded"
    assert entry.after.family_revision_id is not None
    assert entry.after.guarantee is not None
    assert entry.after.guarantee.joint_guarantee == ADDED_FAMILY_GUARANTEE


def test_an_ambiguous_family_lineage_names_its_candidates_and_chooses_no_revision(
    tmp_path: Path,
) -> None:
    """Two recorded heads with no authored ordering between them are two candidates, not a pick.

    The candidate records two further family revisions of the same family, each naming the parent as
    its predecessor and each citing the reviewed revision, and neither names the other. ``ICR-R07@v1``
    establishes no unique head there, so the context carries both heads with their own guarantee texts
    and presents none of them as the family's own -- which is the difference between a composition
    that preserves the ambiguity and one that reads a label, a version or an order as a winner.
    """

    scenario = build_family_scenario(tmp_path / "ambiguous")
    left, right = _author_sibling_family_revisions(scenario)
    _place_datasets(scenario.endpoints.diff, scenario.endpoints.contract)
    payload = review(scenario.endpoints)
    entry = entry_for(payload, scenario.family_id)

    assert entry.selection.state == "ambiguous"
    assert entry.state == "unresolved"
    after_heads = {scenario.successor_family_revision_id, left, right}
    assert set(entry.selection.after_heads) == after_heads
    assert entry.selection.before_heads == (scenario.parent_family_revision_id,)
    assert entry.selection.after_revision_id is None
    assert entry.selection.before_revision_id is None
    # Every head either snapshot establishes is a candidate: the before side's one head and the
    # after side's three, so a reader inspects what was recorded instead of a chosen winner.
    assert {candidate.revision_id for candidate in entry.candidates} == {
        scenario.parent_family_revision_id,
        *after_heads,
    }
    assert {candidate.joint_guarantee for candidate in entry.candidates} == {
        DIRECT_FAMILY_GUARANTEE,
        SUCCESSOR_FAMILY_GUARANTEE,
        LEFT_GUARANTEE,
        RIGHT_GUARANTEE,
    }
    assert entry.before.guarantee is None and entry.after.guarantee is None
    assert entry.before.members == () and entry.after.members == ()
    assert payload.family_context.state == "partial"
    assert "ambiguous" in entry.detail


def _author_sibling_family_revisions(scenario: FamilyScenario) -> tuple[str, str]:
    """Author two further family revisions of one family, with no authored edge between them."""

    diff = scenario.endpoints.diff
    fixture = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        created = [
            _author_family_successor(
                store, diff.repository_id, fixture, fixture.direct_family, LEFT_GUARANTEE
            ),
            _author_family_successor(
                store, diff.repository_id, fixture, fixture.direct_family, RIGHT_GUARANTEE
            ),
        ]
        for revision_id in created:
            _author_membership(
                store, diff.repository_id, revision_id, diff.subject_revision_id, fixture
            )
    finally:
        store.close()
    return created[0], created[1]


# -- the one bounded collection: the roster, its cursor, and a cursor that binds nothing ---------


def test_a_truncated_roster_stays_partial_with_the_owners_counts_and_continuation(
    scenario: FamilyScenario,
) -> None:
    """A page that did not carry the whole roster says so, and offers the cursor that reaches it."""

    fixture = scenario.endpoints.diff.before.fixture
    payload = review(scenario.endpoints, family_request(scenario.endpoints, page_size=1))
    context = payload.family_context
    direct = entry_for(payload, fixture.direct_family.family_id)

    assert context.state == "partial", context.detail
    assert any(
        side.page is not None and not side.page.complete
        for entry in context.entries
        for side in (entry.before, entry.after)
    )
    truncated = [
        side
        for entry in context.entries
        for side in (entry.before, entry.after)
        if side.page is not None and not side.page.complete
    ]
    for side in truncated:
        assert side.page is not None
        assert side.page.continuation is not None
        assert len(side.members) < side.members_total
        assert side.page.counts.primary_items_remaining > 0
        assert "remainder is" in side.detail
    incomplete = [side for side in (direct.before, direct.after) if not _complete(side)]
    assert incomplete


def _complete(context: ReviewFamilyRevisionContext) -> bool:
    """Whether one side context carried its whole roster."""

    return context.page is not None and context.page.complete


def test_the_published_cursor_continues_exactly_the_walk_that_minted_it(
    scenario: FamilyScenario,
) -> None:
    """The first page's cursor is presented back and the owner serves the next page of that walk."""

    fixture = scenario.endpoints.diff.before.fixture
    first = review(scenario.endpoints, family_request(scenario.endpoints, page_size=1))
    truncated = [
        (entry.family_id, side)
        for entry in first.family_context.entries
        for side in (entry.before, entry.after)
        if side.page is not None and side.page.continuation is not None
    ]
    assert truncated
    family_id, side = truncated[0]
    assert side.page is not None
    cursor = side.page.continuation
    assert cursor is not None
    continued = review(
        scenario.endpoints,
        family_request(
            scenario.endpoints, page_of="family_members", continuation=cursor, page_size=1
        ),
    )
    published = continued.page

    assert published is not None
    assert published.collection == "family_members"
    assert published.state == "continued"
    assert published.continued_from == cursor
    assert published.scope == side.page.scope
    assert continued.page_refusal is None
    resumed = [
        walked
        for entry in continued.family_context.entries
        if entry.family_id == family_id
        for walked in (entry.before, entry.after)
        if walked.page is not None and walked.page.continued_from == cursor
    ]
    assert len(resumed) == 1
    assert resumed[0].page is not None
    assert resumed[0].page.counts.primary_items_returned > side.page.counts.primary_items_returned
    assert fixture.direct_family.family_id


def test_a_cursor_that_binds_no_walk_is_refused_beside_the_first_pages(
    tmp_path: Path, scenario: FamilyScenario
) -> None:
    """A cursor from another comparison is refused with a reachable action, not reinterpreted.

    Two enclosures are built, and the second one's cursor is presented to the first: it is a position
    in another comparison's walk, so it binds nothing here. The response states the refusal and still
    serves every family context's first page with its own continuation, so the reader is not left
    without the context the response did compose.
    """

    other = build_family_scenario(tmp_path / "other-enclosure")
    other_payload = review(other.endpoints, family_request(other.endpoints, page_size=1))
    foreign = [
        side.page.continuation
        for entry in other_payload.family_context.entries
        for side in (entry.before, entry.after)
        if side.page is not None and side.page.continuation is not None
    ]
    assert foreign
    cursor = foreign[0]
    assert cursor is not None
    refused = review(
        scenario.endpoints,
        family_request(
            scenario.endpoints, page_of="family_members", continuation=cursor, page_size=1
        ),
    )

    assert refused.page is None
    assert refused.page_refusal is not None
    assert refused.page_refusal.code == "comparison_page_reset"
    assert refused.page_refusal.offending_input == cursor
    assert refused.family_context.entries
    assert any(
        side.page is not None and side.page.continuation is not None
        for entry in refused.family_context.entries
        for side in (entry.before, entry.after)
    )


def test_naming_the_roster_collection_without_a_cursor_is_refused(
    scenario: FamilyScenario,
) -> None:
    """The collection is a set of walks: a request names it with a cursor, or it addresses none.

    Answering with an arbitrary family revision's page would present one walk as the collection, so
    the response states the refusal and still carries every walk's first page on the family contexts
    themselves, each with the continuation that reaches the rest.
    """

    refused = review(
        scenario.endpoints,
        family_request(scenario.endpoints, page_of="family_members"),
    )

    assert refused.page is None
    assert refused.page_refusal is not None
    assert refused.page_refusal.code == "comparison_page_unreadable"
    assert "no cursor was presented" in refused.page_refusal.detail
    assert refused.family_context.entries


def test_a_token_that_is_not_a_roster_cursor_is_answered_with_the_owner_vocabulary(
    scenario: FamilyScenario,
) -> None:
    """Text that no read operation minted is the unreadable-page state, not a moved generation."""

    refused = review(
        scenario.endpoints,
        family_request(
            scenario.endpoints, page_of="family_members", continuation="not-a-cursor", page_size=1
        ),
    )

    assert refused.page is None
    assert refused.page_refusal is not None
    assert refused.page_refusal.code == "comparison_page_unreadable"
    assert refused.page_refusal.offending_input == "not-a-cursor"


# -- the complete source population, independent of membership -----------------------------------


def test_the_complete_source_population_does_not_grow_with_a_repeated_membership(
    tmp_path: Path,
) -> None:
    """A second family recording the same revision adds a context and changes no file total.

    The same enclosure is reviewed twice: once as built, and once after the candidate records one
    further family whose single membership cites the reviewed revision. The family context grows by
    exactly that entry, the repeated revision is still one unique member, and the source pane's own
    measured population -- the complete change set of the bound pair -- answers identically, because
    membership is an attribution lens and never a filter or a total.
    """

    scenario = build_family_scenario(tmp_path / "source-independence")
    before = review(scenario.endpoints)
    _author_added_family(scenario)
    _place_datasets(scenario.endpoints.diff, scenario.endpoints.contract)
    after = review(scenario.endpoints)

    assert after.family_context.families_total == before.family_context.families_total + 1
    assert (
        after.family_context.unique_member_revision_total
        == before.family_context.unique_member_revision_total
    )
    assert (
        after.family_context.membership_rows_total
        == before.family_context.membership_rows_total + 1
    )
    assert after.source.inventory.listed_total == before.source.inventory.listed_total
    assert after.source.attributed_changed_paths == before.source.attributed_changed_paths
    assert after.source.unattributed_changed_paths == before.source.unattributed_changed_paths
    assert after.family_context.references.source_inventory == "source.inventory"


def _author_added_family(scenario: FamilyScenario) -> str:
    """Author one family the candidate records and the baseline does not, with one membership."""

    diff = scenario.endpoints.diff
    fixture = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        family_id = str(uuid4())
        created = families.create_family(
            store,
            FamilyRequest(
                repository_id=diff.repository_id,
                family_id=family_id,
                display_label=ADDED_FAMILY_LABEL,
                provenance=fixture.authorship,
            ),
        )
        assert created.state == "created", created.refusal
        revision_id = str(uuid4())
        authored = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=diff.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=family_id,
                    revision_id=revision_id,
                    display_version=BASE_LABEL,
                    joint_guarantee=ADDED_FAMILY_GUARANTEE,
                    provenance=fixture.authorship,
                ),
            ),
        )
        assert authored.state == "created", authored.refusal
        _author_membership(
            store, diff.repository_id, revision_id, diff.subject_revision_id, fixture
        )
    finally:
        store.close()
    return family_id


def _review_of_unfamiliar_invariant(
    tmp_path: Path,
) -> tuple[EndpointFixture, InvariantIdentitySeed]:
    """Author an invariant no snapshot records a family membership for, and return its selection."""

    endpoints = build_endpoint_fixture(tmp_path / "unfamiliar")
    diff = endpoints.diff
    fixture = diff.before.fixture
    invariant_id = str(uuid4())
    revision_id = str(uuid4())
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        created = store.create_invariant(
            InvariantRequest(
                repository_id=diff.repository_id,
                invariant_id=invariant_id,
                display_label="unfamiliar-obligation",
                provenance=fixture.authorship,
            )
        )
        assert created.state == "created", created.refusal
        authored = store.create_revision(
            RevisionRequest(
                repository_id=diff.repository_id,
                revision=RevisionDraft(
                    revision_id=revision_id,
                    invariant_id=invariant_id,
                    display_version=BASE_LABEL,
                    statement="An obligation no curator has placed in a family.",
                    applicability=APPLICABILITY,
                    provenance=fixture.authorship,
                ),
            )
        )
        assert authored.state == "created", authored.refusal
    finally:
        store.close()
    _place_datasets(diff, endpoints.contract)
    return endpoints, InvariantIdentitySeed(invariant_id=invariant_id)


# -- the transport the client reads: the same composition, over the real route -------------------


def test_the_family_context_reaches_the_client_over_the_real_review_route(
    scenario: FamilyScenario,
) -> None:
    """The served body carries the family context, and the route admits its roster collection.

    The route is wired the way the composition root wires it -- the real application owners behind the
    port -- so what this reads is the JSON a browser receives, not a value this test assembled. The
    family identities, the two selected family revisions and their guarantee texts are all in the
    body, and the roster collection is one the transport admits rather than refusing as an unknown
    page name.
    """

    fixture = scenario.endpoints.diff.before.fixture
    with TestClient(_served(scenario.endpoints)) as client:
        response = client.get(
            KNOWLEDGE_REVIEW_ROUTE,
            params={
                "repo": scenario.endpoints.repository_id,
                "master": scenario.endpoints.master,
                "leaf": LEAF_ID,
                "selectorKind": "invariant",
                "selectorId": scenario.endpoints.diff.retry_invariant_id,
                "pageSize": 1,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        admitted = client.get(
            KNOWLEDGE_REVIEW_ROUTE,
            params={
                "repo": scenario.endpoints.repository_id,
                "master": scenario.endpoints.master,
                "leaf": LEAF_ID,
                "selectorKind": "invariant",
                "selectorId": scenario.endpoints.diff.retry_invariant_id,
                "pageOf": "family_members",
                "pageSize": 1,
            },
        )
    context = body["payload"]["family_context"]
    assert context["state"] == "partial", context["detail"]
    families = {entry["family_id"]: entry for entry in context["entries"]}
    assert set(families) == {
        fixture.direct_family.family_id,
        fixture.family.family_id,
    }
    direct = families[fixture.direct_family.family_id]
    assert direct["selection"]["state"] == "compared"
    assert direct["after"]["family_revision_id"] == scenario.successor_family_revision_id
    assert direct["after"]["guarantee"]["joint_guarantee"] == SUCCESSOR_FAMILY_GUARANTEE
    assert context["unique_member_revision_total"] < context["membership_rows_total"]
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["payload"]["page_refusal"]["code"] == "comparison_page_unreadable"


def _served(fixture: EndpointFixture) -> FastAPI:
    """The real registrar and composition over explicit synthetic index-shaped inputs."""

    app = FastAPI()
    config = fixture.config
    register_review_routes(
        app,
        config,
        lambda request: compose_endpoint_review(fixture, request),
    )
    return app


# -- the walk the delivered continuation control drives, over the real route -----------------------


def _author_extra_roster_rows(scenario: FamilyScenario, count: int) -> None:
    """Author ``count`` further member revisions into the successor family revision's roster.

    They are ordinary authored memberships through the shipped store operations: each is a new
    invariant identity with one revision cited by one membership row, which is what makes a roster
    need more than one page of the read walk.
    """

    diff = scenario.endpoints.diff
    fixture = diff.before.fixture
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        for index in range(count):
            invariant_id, revision_id = str(uuid4()), str(uuid4())
            identity = store.create_invariant(
                InvariantRequest(
                    repository_id=diff.repository_id,
                    invariant_id=invariant_id,
                    display_label=f"walk-row-{index}",
                    provenance=fixture.authorship,
                )
            )
            assert identity.state == "created", identity.refusal
            authored = store.create_revision(
                RevisionRequest(
                    repository_id=diff.repository_id,
                    revision=RevisionDraft(
                        revision_id=revision_id,
                        invariant_id=invariant_id,
                        display_version=MEMBER_SUCCESSOR_VERSION,
                        statement=f"An obligation the roster walk places at row {index}.",
                        applicability=APPLICABILITY,
                        provenance=fixture.authorship,
                    ),
                )
            )
            assert authored.state == "created", authored.refusal
            _author_membership(
                store,
                diff.repository_id,
                scenario.successor_family_revision_id,
                revision_id,
                fixture,
            )
    finally:
        store.close()
    _place_datasets(diff, scenario.endpoints.contract)


def _walk_the_route(scenario: FamilyScenario, side: str) -> list[dict]:
    """Follow ONE side's roster walk exactly as the delivered continuation control does.

    The first request names the subject and no page; each step after it presents the collection WITH
    the cursor the previous page published, and no page bound -- which is the request the surface's
    "continue the … roster walk" control builds. A page that is not this route's answer is reported as
    the status it carried rather than raised, because a server exception is what this case exists to
    catch: ``raise_server_exceptions=False`` is what makes an unhandled ``ValidationError`` visible as
    the HTTP 500 the browser would receive instead of hiding it in a traceback.
    """

    base = {
        "repo": scenario.endpoints.repository_id,
        "master": scenario.endpoints.master,
        "leaf": LEAF_ID,
        "selectorKind": "invariant",
        "selectorId": scenario.endpoints.diff.retry_invariant_id,
    }
    steps: list[dict] = []
    continuation: str | None = None
    with TestClient(_served(scenario.endpoints), raise_server_exceptions=False) as client:
        while len(steps) < 20:
            params = dict(base)
            if continuation is not None:
                params["pageOf"] = "family_members"
                params["continuation"] = continuation
            response = client.get(KNOWLEDGE_REVIEW_ROUTE, params=params)
            assert response.status_code == 200, (
                f"step {len(steps)} answered {response.status_code}: {response.text[:400]}"
            )
            body = response.json()
            entries = {
                entry["family_id"]: entry for entry in body["payload"]["family_context"]["entries"]
            }
            step = entries[scenario.family_id][side]
            steps.append(step)
            page = step["page"]
            assert page is not None
            if page["complete"] or page.get("continuation") is None:
                return steps
            continuation = page["continuation"]
    raise AssertionError("the roster walk did not terminate inside 20 pages")


def test_a_roster_that_fits_one_page_is_carried_whole_and_says_so(tmp_path: Path) -> None:
    """The single-page boundary: one walk page IS the roster, and it carries every recorded row."""

    scenario = build_family_scenario(tmp_path / "single-page-walk")
    steps = _walk_the_route(scenario, "after")

    assert len(steps) == 1, [step["page"]["state"] for step in steps]
    page, members = steps[0]["page"], steps[0]["members"]
    assert page["complete"] is True
    assert page["state"] == "first_page"
    assert page.get("continuation") is None
    assert len(members) == steps[0]["members_total"] > 0
    assert "all carried here" in steps[0]["detail"]


def test_the_final_page_of_a_long_walk_may_carry_only_its_own_share(tmp_path: Path) -> None:
    """The shape that used to answer HTTP 500: a walk whose last page carries part of the roster.

    Seventy further memberships put seventy-two rows on the successor revision's roster, which the
    read walk takes several pages to reach. Its FINAL page is complete -- the walk enumerated the
    whole selection -- and carries only its own share of those rows, never all of them at once. That
    is the page the guard used to refuse, because it compared the page's carried rows against the
    revision-wide count; the route then answered the reader's own continuation request with an
    unhandled HTTP 500 and the surface replaced the whole review with an unexplained failure.
    """

    scenario = build_family_scenario(tmp_path / "long-walk")
    _author_extra_roster_rows(scenario, 70)
    steps = _walk_the_route(scenario, "after")

    assert len(steps) >= 3, [len(step["members"]) for step in steps]
    final = steps[-1]
    page = final["page"]
    assert page["complete"] is True
    assert page.get("continuation") is None
    assert page["state"] == "continued"
    # The page that carried only its own share of the roster, which is the state that used to raise.
    assert 0 < len(final["members"]) < final["members_total"]
    # Every earlier page is still a position in the walk, and no step above answered a failure.
    for step in steps[:-1]:
        assert step["page"]["complete"] is False
        assert step["page"].get("continuation") is not None
    # The pages together carried every recorded membership, which is what the continuation control
    # promises a reader; and the completed page says the WALK finished rather than claiming to hold
    # the whole roster itself.
    assert (
        len({member["member_id"] for step in steps for member in step["members"]})
        == final["members_total"]
    )
    assert "completes the read walk" in final["detail"]
    assert "all carried here" not in final["detail"]


def test_a_roster_walk_larger_than_one_page_terminates_with_a_page_and_no_failure(
    tmp_path: Path,
) -> None:
    """The multi-page boundary: the walk the control drives reaches the rest instead of a 500.

    Forty further memberships put forty-two rows on the successor family revision's roster, which is
    more than one page of the read walk carries (the request the surface sends names no page size, so
    the walk's own bound applies). Before this leaf's correction the guard in
    ``ReviewFamilyRevisionContext`` compared the FINAL page's carried rows against the
    revision-wide membership count, so the page that completes the walk raised a ``ValidationError``
    and the route answered the reader's own continuation request with HTTP 500. Every step below is
    asserted to be this route's answer, and the pages together are asserted to have carried every
    recorded membership -- which is exactly the claim the surface's continuation control makes.
    """

    scenario = build_family_scenario(tmp_path / "multi-page-walk")
    _author_extra_roster_rows(scenario, 40)
    steps = _walk_the_route(scenario, "after")

    assert len(steps) >= 2, "the roster must be past one page for this case to measure anything"
    assert steps[0]["members_total"] >= 42
    for index, step in enumerate(steps):
        page = step["page"]
        assert page is not None
        assert len(step["members"]) <= step["members_total"]
        if index == len(steps) - 1:
            # The walk terminates: the final page is the whole selection and offers no cursor.
            assert page["complete"] is True
            assert page.get("continuation") is None
            assert page["state"] == "continued"
            # And it says what it is: a page that completes a multi-page walk did NOT carry every
            # recorded row here, so the single-page sentence must not appear on it.
            assert "all carried here" not in step["detail"]
            assert "completes the read walk" in step["detail"]
        else:
            assert page["complete"] is False
            assert page.get("continuation") is not None
    carried = len({member["member_id"] for step in steps for member in step["members"]})
    assert carried == steps[-1]["members_total"]
    assert steps[0]["page"]["counts"]["primary_items_remaining"] > 0
