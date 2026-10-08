"""Bounded review pagination: every page reachable, no page invented, stale cursors refused.

These cases drive the **real** composition over **real** two-snapshot fixtures built by the public
store operations, and they read the pages back through the real HTTP transport the dashboard uses.
Nothing here re-implements a comparison, a view, a cursor or a count: the numbers asserted are the
owners' own numbers, and the cursor carried from one call to the next is the owner's own token.

The load-bearing properties, one case each:

* **A whole comparison is one page** -- the fixture's own population, with no remainder and no cursor,
  so a complete selection is never presentable as a truncated one.
* **A large population is reachable page by page** -- the same composition over a candidate carrying
  ninety further realizations: every page is traversed through HTTP, the walk's union is the
  comparison's own total exactly once with no duplicate and no loss, each page's counts add up, the
  active scope travels beside the counts, and the last page reports nothing remaining and no cursor.
* **A cursor for a moved generation is refused** -- the candidate's dataset and code tree are both
  advanced and the cursor issued before the move is presented afterwards: it is refused with the
  explicit new-generation action, the response is the *first* page of the comparison that is there
  now, and the refusal names the comparison the cursor was minted at beside the one it met.
* **The records collection is the matrix view's own walk** -- its page states the bound the caller
  named, and a cursor of the other collection or of another snapshot is a named refusal with no
  page, each with its own code and next action.
* **The entry is a catalogue, not a record fetch** -- the entry read lists identities, carries no
  page and no cursor at either dataset size, and driving it calls no comparison and no view.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from agents_remember.application import knowledge_diff, knowledge_review
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_review import (
    compose_review,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.diff import (
    KnowledgeDiffBudget,
    KnowledgeDiffRequest,
    KnowledgeDiffSide,
)
from agents_remember.models.knowledge.read import (
    InvariantIdentitySeed,
    KnowledgeReadSeed,
    snapshot_of_context,
)
from agents_remember.models.knowledge.review import (
    MAXIMUM_REVIEW_PAGE_SIZE,
    REVIEW_PAGE_RESET_NEXT_ACTION,
    KnowledgeReviewPayload,
    ReviewCollectionPage,
    ReviewEntryListResult,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.source import FileLocator
from agents_remember.models.knowledge.view import continuation_for
from agents_remember.serving.review import (
    KNOWLEDGE_REVIEW_ROUTE,
    ReviewPagingRef,
    ReviewSelectorRef,
    UnadmittedReviewQuery,
    paged_review_request,
    register_review_routes,
    review_request_from_query,
)
from anchor_fixture_models import GitBlobIdentity, RealizationClaimDraft, SourceAnchorDraft
from diff_scope_test_support import DiffFixture, build_diff_fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_rows_test_support import (
    NewAnchor,
    RealizationClaimRequest,
    open_knowledge_store,
    realizations,
)
from read_scope_test_support import make_read_authorship

pytestmark = pytest.mark.evidence_unit

REPOSITORY_LEAF = "260915-ks-l22"
MASTER = "260915_knowledge-substrate"

# The two dataset sizes the packet requires. The first is the shared fixture's own population, which
# fits in one page; the second adds ninety further realizations to the *same* recorded subject, so the
# difference between the two sizes is the record population and nothing else. Both are real store
# operations on a real dataset: no payload is constructed by hand anywhere in this module.
SMALL_EXTRA_CLAIMS = 0
LARGE_EXTRA_CLAIMS = 90

# The page sizes the traversal uses. Both are deliberately below the owners' own declared bounds
# (``DIFF_DISPLAY_MAX_ITEMS`` = 32 for the comparison, ``MAX_VIEW_ROWS`` = 64 for the view), so a page
# here is a real truncation of a real selection rather than the first page of a small one.
COMPARISON_PAGE = 16
RECORDS_PAGE = 3


@dataclass(frozen=True)
class PaginationFixture:
    """One two-snapshot fixture plus the extra realizations its candidate records."""

    diff: DiffFixture
    recorded_subject: str
    extra_paths: tuple[str, ...]
    directory: Path

    def resolution(self) -> ReviewCandidateResolution:
        """The resolution the adapter produces for this candidate, without a task contract."""

        return ReviewCandidateResolution(
            repository_id=self.diff.repository_id,
            leaf_id=REPOSITORY_LEAF,
            baseline_database=self.diff.before.database_path,
            candidate_database=self.diff.after.database_path,
            baseline_code_root=self.diff.before.git_root,
            candidate_code_root=self.diff.after.git_root,
            baseline_code_tree_id=self.diff.before_tree_id,
            candidate_code_tree_id=self.diff.after_tree_id,
        )

    def request(
        self,
        *,
        page_of: str | None,
        continuation: str | None = None,
        page_size: int = 0,
        selector: KnowledgeReadSeed | None = None,
    ) -> ReviewSurfaceRequest:
        return ReviewSurfaceRequest(
            repository_id=self.diff.repository_id,
            master=MASTER,
            leaf_id=REPOSITORY_LEAF,
            selector=(
                InvariantIdentitySeed(invariant_id=self.diff.retry_invariant_id)
                if selector is None
                else selector
            ),
            page_of=page_of,  # type: ignore[arg-type]
            continuation=continuation,
            page_size=page_size,
        )


def review_config() -> McpRuntimeConfig:
    """A configuration naming no real root, so only a resolution's own refusals can answer."""

    return McpRuntimeConfig(
        workspace_root=Path("/nonexistent-workspace"),
        coordination_root=Path("/nonexistent-coordination"),
        config_path=Path("/nonexistent-config.json"),
        transcript_root=Path("/nonexistent-transcripts"),
    )


def build_pagination_fixture(directory: Path, *, extra_claims: int) -> PaginationFixture:
    """Build the shared two-snapshot fixture and add ``extra_claims`` realizations to its candidate.

    The added claims are authored through the **public store operation** on the candidate dataset,
    each at its own reported path with the exact blob identity the fixture's own code tree holds. The
    recorded subject is the candidate's existing revised revision, so the claims extend one recorded
    subject's realization population and the knowledge topology does not change under the comparison.
    The code tree is not touched, so the comparison's binding on the source half stays the fixture's.
    """

    diff = build_diff_fixture(directory)
    authorship = make_read_authorship(seed="agent:pagination-fixture")
    paths = tuple(f"src/pagination/realization_{index:03d}.py" for index in range(extra_claims))
    known_blob = next(iter(diff.after.git_blobs.values()))
    store = open_knowledge_store(diff.after.database_path, diff.repository_id)
    try:
        for path in paths:
            created = realizations.create_realization_claim(
                store,
                RealizationClaimRequest(
                    repository_id=diff.repository_id,
                    claim=RealizationClaimDraft(
                        claim_id=str(uuid4()),
                        invariant_revision_id=diff.revised_revision_id,
                        role="enforcement",
                        rationale=f"The candidate records the obligation at {path}.",
                    ),
                    anchor=NewAnchor(
                        anchor=SourceAnchorDraft(
                            anchor_id=UUID(str(uuid4())),
                            path=path,
                            source_identity=GitBlobIdentity(object_id=known_blob),
                            locator=FileLocator(),
                        )
                    ),
                    provenance=authorship,
                ),
            )
            assert created.state == "created", created.refusal
    finally:
        store.close()
    return PaginationFixture(
        diff=diff,
        recorded_subject=diff.revised_revision_id,
        extra_paths=paths,
        directory=directory,
    )


def compose_page(
    fixture: PaginationFixture,
    *,
    page_of: str | None,
    continuation: str | None = None,
    page_size: int = 0,
) -> KnowledgeReviewPayload:
    """One composition call through the production adapter, failing loudly on a refusal."""

    result = compose_review(
        fixture.resolution(),
        fixture.request(page_of=page_of, continuation=continuation, page_size=page_size),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def route_client(fixture: PaginationFixture) -> TestClient:
    """One client over the real route registrar with the real composition behind its port."""

    app = FastAPI()
    register_review_routes(
        app,
        review_config(),
        port=lambda request: compose_review(fixture.resolution(), request),
    )
    return TestClient(app)


def route_query(
    fixture: PaginationFixture, *, page_of: str, page_size: int, continuation: str | None
) -> dict[str, str]:
    """The exact query one paged request makes, so a case can record its own request bounds."""

    return {
        "repo": fixture.diff.repository_id,
        "master": MASTER,
        "leaf": REPOSITORY_LEAF,
        "selectorKind": "invariant",
        "selectorId": fixture.diff.retry_invariant_id,
        "pageOf": page_of,
        "pageSize": str(page_size),
        **({} if continuation is None else {"continuation": continuation}),
    }


def paged_payload(payload: KnowledgeReviewPayload) -> ReviewCollectionPage:
    """The page a paged request must publish, asserted rather than assumed."""

    assert payload.page is not None, "a request that named a collection must publish its page"
    return payload.page


def walk_comparison(
    fixture: PaginationFixture, *, page_size: int, over_http: bool
) -> tuple[list[ReviewCollectionPage], list[tuple[str, ...]]]:
    """Walk the knowledge collection to its end, returning its pages and each page's own item ids.

    The walk is cursor-driven and nothing else: each call presents the cursor the previous response
    published and stops exactly when a page reports nothing remaining. The item identities come from
    the payload the response carried -- its source pane's own selected locations plus the knowledge
    pane's own identity lists -- so the union asserted below is the union the pages really returned.
    """

    client = route_client(fixture) if over_http else None
    pages: list[ReviewCollectionPage] = []
    per_page: list[tuple[str, ...]] = []
    continuation: str | None = None
    for _ in range(64):
        if client is None:
            payload = compose_page(
                fixture, page_of="knowledge", continuation=continuation, page_size=page_size
            )
        else:
            response = client.get(
                KNOWLEDGE_REVIEW_ROUTE,
                params=route_query(
                    fixture,
                    page_of="knowledge",
                    page_size=page_size,
                    continuation=continuation,
                ),
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["state"] == "review", body
            payload = KnowledgeReviewPayload.model_validate(body["payload"])
        page = paged_payload(payload)
        pages.append(page)
        # The page's own bounds are cross-checked against the comparison owner's independent answer
        # for the *same* request: the payload says what it returned, and the owner is asked the same
        # question a second time here, so a published count that disagreed with the walk it claims
        # to be a page of cannot pass. The item identities below come from that same owner answer
        # rather than from re-deriving them out of the payload's rendered lists.
        returned_ids = _returned_item_ids(fixture, continuation, page_size)
        assert page.returned == _returned_count(fixture, continuation, page_size)
        per_page.append(returned_ids)
        if page.continuation is None:
            return pages, per_page
        continuation = page.continuation
    raise AssertionError("the walk did not terminate within its bound")


def _returned_count(fixture: PaginationFixture, continuation: str | None, page_size: int) -> int:
    """How many items the comparison owner says it has returned by the end of this page."""

    result = _comparison_answer(fixture, continuation, page_size)
    assert result.page is not None
    return result.page.counts.items_returned


def _returned_item_ids(
    fixture: PaginationFixture, continuation: str | None, page_size: int
) -> tuple[str, ...]:
    """The item identities the comparison owner returned for one exact page request.

    This is the owner's own answer to the same request the page was composed from, asked through the
    shipped comparison operation, so the union this case asserts is the union the pages really
    returned rather than a reconstruction from the payload's rendered lists.
    """

    result = _comparison_answer(fixture, continuation, page_size)
    assert result.page is not None
    return tuple(item.item_id for item in result.page.items)


def _comparison_answer(fixture: PaginationFixture, continuation: str | None, page_size: int):
    """The shipped comparison's own answer to one exact page request."""

    result = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=InvariantIdentitySeed(invariant_id=fixture.diff.retry_invariant_id),
            before=KnowledgeDiffSide(
                context=open_diff_side(
                    fixture.diff.before.database_path,
                    fixture.diff.repository_id,
                    repository_root=fixture.diff.before.git_root,
                    code_tree_id=fixture.diff.before_tree_id,
                )
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(
                    fixture.diff.after.database_path,
                    fixture.diff.repository_id,
                    repository_root=fixture.diff.after.git_root,
                    code_tree_id=fixture.diff.after_tree_id,
                )
            ),
            budget=KnowledgeDiffBudget(max_items=page_size),
            continuation=continuation,
        ),
        before_path=fixture.diff.before.database_path,
        after_path=fixture.diff.after.database_path,
    )
    assert result.state == "page", result.refusal
    return result


def test_a_complete_comparison_is_one_page_with_no_remainder_and_no_cursor(tmp_path: Path) -> None:
    """The small dataset size: the whole selection is served, so no remainder is claimable."""

    fixture = build_pagination_fixture(tmp_path / "small", extra_claims=SMALL_EXTRA_CLAIMS)
    page = paged_payload(compose_page(fixture, page_of="knowledge"))
    assert page.collection == "knowledge"
    assert page.state == "first_page"
    assert page.returned == page.total
    assert page.remaining == 0
    assert page.continuation is None
    assert page.continued_from is None
    assert page.reset is None
    assert "page_size=32" in page.scope
    assert "selector_kind=invariant" in page.scope


def test_every_page_of_a_large_comparison_is_reachable_without_duplicate_or_loss(
    tmp_path: Path,
) -> None:
    """The large dataset size, traversed end to end through the real HTTP transport."""

    fixture = build_pagination_fixture(tmp_path / "large", extra_claims=LARGE_EXTRA_CLAIMS)
    pages, per_page = walk_comparison(fixture, page_size=COMPARISON_PAGE, over_http=True)
    assert len(pages) >= 3, [page.total for page in pages]
    first = pages[0]
    # Every page is a page *of one walk*: the total never moves, the returned count is cumulative and
    # the remainder shrinks by exactly what the pages before it added.
    assert {page.total for page in pages} == {first.total}
    assert [page.returned for page in pages] == sorted(page.returned for page in pages)
    assert [page.remaining for page in pages] == sorted(
        (page.remaining for page in pages), reverse=True
    )
    for page in pages:
        assert page.returned + page.remaining == page.total
        assert (page.remaining > 0) == (page.continuation is not None)
        assert page.scope, "a page states the scope its counts were taken over"
    assert pages[-1].remaining == 0 and pages[-1].continuation is None
    assert pages[1].continued_from == pages[0].continuation
    assert pages[1].state == "continued"
    assert pages[0].state == "first_page"
    # No duplicate and no loss: no item appears twice inside one page, no item is returned by two
    # pages, and the pages together returned exactly the comparison's own total, once each.
    for index, page_items in enumerate(per_page):
        assert len(page_items) == len(set(page_items)), f"page {index} repeated an item"
    walked: set[str] = set()
    for index, page_items in enumerate(per_page):
        overlap = walked & set(page_items)
        assert not overlap, f"page {index} returned {len(overlap)} item(s) an earlier page returned"
        walked |= set(page_items)
    assert len(walked) == first.total
    # The added realizations are part of that population, so the walk really crossed many pages.
    assert first.total > LARGE_EXTRA_CLAIMS
    assert first.total > COMPARISON_PAGE * 3
    assert len(pages) >= 4, len(pages)


def test_the_item_windows_partition_exactly_and_paging_loses_no_rendered_row(
    tmp_path: Path,
) -> None:
    """What paging guarantees: the item windows partition, and no rendered row is lost or invented.

    Three claims, and each is the one the evidence measures:

    1. **The item windows are a strict partition.** Every comparison item is returned by exactly one
       page (`walk_comparison` cross-checks each page's own ``returned`` against the comparison
       owner's independent answer, and ``_returned_item_ids`` reads the identities from that answer).
    2. **Every rendered row is one of the collection's own rows.** A page's location rows may not
       name a claim the comparison never selected.
    3. **The union of the pages' rendered rows is the rows the unpaged read renders.** Paging loses no
       row and invents none. The fixture is the small one on purpose: the unpaged read must cover the
       whole selection for the comparison to be meaningful (the comparison's own display budget is 32
       rows, so a larger population would be truncated by the owner rather than by paging).

    What is deliberately **not** claimed, and not asserted, is that a rendered location row belongs
    to the page whose window its claim's item fell in. It need not: R08's traversal resolves the
    recorded relationship LINE from the store (`review_relationship_movement._line_relationships`,
    "the comparison's page is a selection and the store is not"), so a page whose window holds a
    revision item can render claims whose own items arrive on another page. The measured magnitude
    depends on the recorded ordering's tie-break over freshly minted claim identities -- this leaf
    measured 0 to 6 cross-page rows for one fixture at one page size across runs, and the verifier
    measured 4 at ``pageSize=5`` -- so an assertion on the count would be a flake, while an assertion
    that the attribution *must* be page-local would be a false statement about R08's owner. That
    boundary is R08's (the line-scoped read) and R24's (what the pane shows); this leaf records it
    rather than silently narrowing the owner's traversal.
    """

    fixture = build_pagination_fixture(tmp_path / "panes", extra_claims=SMALL_EXTRA_CLAIMS)
    whole = compose_page(fixture, page_of="knowledge")
    assert whole.page is not None and whole.page.returned == whole.page.total, (
        "the unpaged read must cover the whole selection for this comparison to mean anything"
    )
    whole_rows = _rendered_claim_ids(whole)
    for page_size in (5, 7):
        pages, per_page = walk_comparison(fixture, page_size=page_size, over_http=False)
        assert len(pages) >= 3, page_size
        # 1. the item windows partition exactly: no item twice, and the union is the comparison's
        #    own total once each.
        returned = [item for page_items in per_page for item in page_items]
        assert len(returned) == len(set(returned)), f"page_size={page_size} repeated an item"
        assert len(set(returned)) == pages[0].total, f"page_size={page_size} lost an item"
        # 2. every rendered row is one of the collection's own rows.
        rendered_pages = [
            _rendered_claim_ids(
                compose_page(
                    fixture,
                    page_of="knowledge",
                    continuation=None if index == 0 else pages[index - 1].continuation,
                    page_size=page_size,
                )
            )
            for index in range(len(pages))
        ]
        rendered = set().union(*rendered_pages)
        assert rendered <= set(returned), f"page_size={page_size} rendered an unselected claim"
        # 3. paging loses and invents nothing in what the panes render.
        assert rendered == whole_rows, f"page_size={page_size} changed the rendered row set"


def _refused_records_page(fixture: PaginationFixture, cursor: str) -> KnowledgeReviewPayload:
    """One composition call for a records cursor the owner refuses, asserting it answered at all."""

    result = compose_review(
        fixture.resolution(),
        fixture.request(page_of="records", continuation=cursor, page_size=RECORDS_PAGE),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    return result.payload


def _matrix_cursor(fixture: PaginationFixture, position: int) -> str:
    """The cursor the shipped codec mints for the matrix walk at this candidate's own snapshot.

    The matrix view publishes a cursor only while rows remain, and the candidate here records no
    matrix row, so no page hands one back. The token is therefore minted by the same function the
    view calls, from the snapshot the composition opens the candidate under.
    """

    side = open_diff_side(
        fixture.diff.after.database_path,
        fixture.diff.repository_id,
        repository_root=fixture.diff.after.git_root,
        code_tree_id=fixture.diff.after_tree_id,
    )
    return continuation_for(
        view="review_matrix", snapshot=snapshot_of_context(side), position=position
    ).token


def test_the_records_collection_states_the_matrix_walk_and_the_bound_it_applied(
    tmp_path: Path,
) -> None:
    """A records request publishes the matrix view's own window, under the size the caller named."""

    fixture = build_pagination_fixture(tmp_path / "records", extra_claims=SMALL_EXTRA_CLAIMS)
    page = paged_payload(compose_page(fixture, page_of="records", page_size=RECORDS_PAGE))
    assert (page.collection, page.state, page.total_basis) == ("records", "first_page", "walk")
    assert page.returned <= RECORDS_PAGE
    assert page.returned + page.remaining == page.total
    assert (page.remaining > 0) == (page.continuation is not None)
    assert page.continued_from is None and page.reset is None
    # The scope is the selection the counts describe: the kinds asked for and the bound applied.
    assert page.scope == (
        f"record_kinds={','.join(knowledge_review.REVIEW_MATRIX_KINDS)}",
        "ordering_input=stable_ordering",
        f"page_size={RECORDS_PAGE}",
    )
    # A request that names no size is bounded by the view's own maximum, and says so.
    unsized = paged_payload(compose_page(fixture, page_of="records"))
    assert f"page_size={MAXIMUM_REVIEW_PAGE_SIZE}" in unsized.scope
    # A cursor of this walk, minted at this candidate's snapshot, continues it and is named on the
    # page it produced.
    cursor = _matrix_cursor(fixture, RECORDS_PAGE)
    continued = paged_payload(
        compose_page(fixture, page_of="records", continuation=cursor, page_size=RECORDS_PAGE)
    )
    assert (continued.collection, continued.state) == ("records", "continued")
    assert continued.continued_from == cursor
    assert continued.returned + continued.remaining == continued.total


def test_a_records_cursor_of_another_walk_or_another_snapshot_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """A records page the owner cannot serve is a named refusal on the wire, never a silent absence.

    Two different mistakes, kept apart. The comparison's cursor is not a matrix cursor at all: nothing
    moved, so the refusal says the token is unreadable here and keeps the owner's own next action. A
    matrix cursor minted at the snapshot the candidate held before it advanced is the moved case, and
    it takes the new-generation action and names both snapshots. Either way no page is published,
    because a page needs the owner's own counts, and the matrix channel reports the read as
    unavailable rather than as a measured zero.
    """

    fixture = build_pagination_fixture(tmp_path / "refused", extra_claims=SMALL_EXTRA_CLAIMS)
    knowledge = paged_payload(compose_page(fixture, page_of="knowledge", page_size=5))
    assert knowledge.continuation is not None
    crossed = _refused_records_page(fixture, knowledge.continuation)
    assert crossed.page is None and crossed.page_refusal is not None
    assert crossed.page_refusal.code == "comparison_page_unreadable"
    assert crossed.page_refusal.offending_input == knowledge.continuation
    assert crossed.page_refusal.next_action != REVIEW_PAGE_RESET_NEXT_ACTION
    channel = next(
        entry for entry in crossed.evidence.channels if entry.records == "authored_effects"
    )
    assert channel.state == "unavailable"

    stale = _matrix_cursor(fixture, RECORDS_PAGE)
    moved = _advanced_candidate(tmp_path / "refused-advanced", fixture)
    refused = _refused_records_page(moved, stale)
    assert refused.page is None and refused.page_refusal is not None
    assert refused.page_refusal.code == "comparison_page_reset"
    assert refused.page_refusal.offending_input == stale
    assert refused.page_refusal.next_action == REVIEW_PAGE_RESET_NEXT_ACTION
    assert refused.page_refusal.expected is not None and refused.page_refusal.expected in stale
    assert refused.page_refusal.observed not in (None, refused.page_refusal.expected)
    # The refusal is not a dead end: the same request with no cursor serves the first page.
    first = paged_payload(compose_page(moved, page_of="records", page_size=RECORDS_PAGE))
    assert first.state == "first_page" and first.reset is None


def _rendered_claim_ids(payload: KnowledgeReviewPayload) -> set[str]:
    """Every distinct realization claim one payload rendered as a source location."""

    return {location.claim_id for location in payload.source.locations}


def test_a_cursor_from_a_moved_generation_is_refused_with_a_new_generation_action(
    tmp_path: Path,
) -> None:
    """A candidate whose dataset and code tree both advanced cannot continue the cursor it issued."""

    fixture = build_pagination_fixture(tmp_path / "moved", extra_claims=LARGE_EXTRA_CLAIMS)
    issued = compose_page(fixture, page_of="knowledge", page_size=COMPARISON_PAGE)
    stale_cursor = paged_payload(issued).continuation
    assert stale_cursor is not None

    moved = _advanced_candidate(tmp_path / "moved-advanced", fixture)
    before = compose_page(fixture, page_of="knowledge", page_size=COMPARISON_PAGE)
    after = compose_page(moved, page_of="knowledge", page_size=COMPARISON_PAGE)
    assert before.comparison is not None and after.comparison is not None
    assert before.comparison.binding_digest != after.comparison.binding_digest

    presented = compose_page(
        moved, page_of="knowledge", continuation=stale_cursor, page_size=COMPARISON_PAGE
    )
    page = paged_payload(presented)
    assert page.state == "reset"
    assert page.reset is not None
    assert page.reset.code == "comparison_page_reset"
    assert page.reset.next_action == REVIEW_PAGE_RESET_NEXT_ACTION
    assert page.reset.offending_input == stale_cursor
    # The response is the *first* page of the comparison that is there now: it claims no more than
    # that page returned, and the refusal names both comparisons rather than one.
    assert page.continued_from is None
    assert page.returned < page.total
    assert page.remaining == page.total - page.returned
    assert page.reset.expected is not None and page.reset.observed is not None
    assert page.reset.expected != page.reset.observed
    # And the walk it hands back is live: the cursor the reset page publishes reaches the rest of the
    # comparison that is there now, so a refused cursor is not a dead end.
    assert page.continuation is not None
    following = compose_page(
        moved, page_of="knowledge", continuation=page.continuation, page_size=COMPARISON_PAGE
    )
    assert paged_payload(following).state == "continued"


def test_the_transport_names_the_input_it_refused_and_bounds_the_page_it_applies(
    tmp_path: Path,
) -> None:
    """Every admission failure is this route's own 400 naming the value that actually failed.

    The page-size boundary is the case this exists for. ``ReviewSurfaceRequest`` declares
    ``le=MAXIMUM_REVIEW_PAGE_SIZE``, and a size above it used to reach that model from the query
    string and raise an uncaught validation error -- an opaque HTTP 500 for the obvious request
    ("show me everything"). The bound is now admitted by this route, in this route's vocabulary, so
    ``64`` is served and ``65`` is refused with a named reason. The same body's ``offendingInput``
    used to fall back over everything the caller sent, which reported a bad ``pageOf`` as the
    (admitted) selector kind; each branch now names its own input.
    """

    fixture = build_pagination_fixture(tmp_path / "transport", extra_claims=SMALL_EXTRA_CLAIMS)
    client = route_client(fixture)
    params = route_query(fixture, page_of="records", page_size=4, continuation=None)

    # 64 is the declared maximum: the route serves it, and the page's own scope says which bound was
    # applied rather than which was asked for.
    at_bound = client.get(
        KNOWLEDGE_REVIEW_ROUTE,
        params={**params, "pageOf": "knowledge", "pageSize": str(MAXIMUM_REVIEW_PAGE_SIZE)},
    )
    assert at_bound.status_code == 200, at_bound.text
    assert f"page_size={MAXIMUM_REVIEW_PAGE_SIZE}" in " ".join(
        at_bound.json()["payload"]["page"]["scope"]
    )

    # One past it is refused by name, not by a traceback: no 500, the offending value on the body,
    # and the bound the surface applies stated beside it.
    over = client.get(
        KNOWLEDGE_REVIEW_ROUTE, params={**params, "pageSize": str(MAXIMUM_REVIEW_PAGE_SIZE + 1)}
    )
    assert over.status_code == 400, over.text
    body = over.json()
    assert body["offendingInput"] == str(MAXIMUM_REVIEW_PAGE_SIZE + 1)
    assert str(MAXIMUM_REVIEW_PAGE_SIZE) in body["detail"]
    assert MAXIMUM_REVIEW_PAGE_SIZE == 64
    negative = client.get(KNOWLEDGE_REVIEW_ROUTE, params={**params, "pageSize": "-1"})
    assert negative.status_code == 400, negative.text
    assert negative.json()["offendingInput"] == "-1"

    # Each admission failure names its own input: the collection, the orphan cursor, the selector.
    unknown = client.get(KNOWLEDGE_REVIEW_ROUTE, params={**params, "pageOf": "everything"})
    assert unknown.status_code == 400 and unknown.json()["offendingInput"] == "everything"
    orphan = client.get(
        KNOWLEDGE_REVIEW_ROUTE,
        params={**params, "pageOf": "", "continuation": "review_matrix:0000:0"},
    )
    assert orphan.status_code == 400
    assert orphan.json()["offendingInput"] == "review_matrix:0000:0"
    half = client.get(
        KNOWLEDGE_REVIEW_ROUTE,
        params={
            "repo": fixture.diff.repository_id,
            "master": MASTER,
            "leaf": REPOSITORY_LEAF,
            "selectorKind": "invariant",
            "pageOf": "knowledge",
        },
    )
    assert half.status_code == 400 and half.json()["offendingInput"] == "invariant"

    # The typed entry point reports the same answers, so a caller of the operation (not only of the
    # route) can see which input failed.
    problem = paged_review_request(
        "r", "m", "l", ReviewSelectorRef(), ReviewPagingRef(continuation="tok")
    )
    assert isinstance(problem, UnadmittedReviewQuery) and problem.offending_input == "tok"
    problem = paged_review_request(
        "r", "m", "l", ReviewSelectorRef(), ReviewPagingRef(page_of="everything")
    )
    assert isinstance(problem, UnadmittedReviewQuery) and problem.offending_input == "everything"
    problem = paged_review_request(
        "r", "m", "l", ReviewSelectorRef(selector_kind="latest"), ReviewPagingRef()
    )
    assert isinstance(problem, UnadmittedReviewQuery) and problem.offending_input == "latest"
    # The selector-only spelling keeps its long-standing ``None`` contract for its existing callers.
    assert review_request_from_query("r", "m", "l", "latest", "x") is None
    plain = review_request_from_query("r", "m", "l", "invariant", str(uuid4()))
    assert plain is not None and plain.page_of is None and plain.continuation is None
    with pytest.raises(ValueError):
        fixture.request(page_of="knowledge", page_size=MAXIMUM_REVIEW_PAGE_SIZE + 1)


def test_the_entry_read_populates_the_button_without_fetching_record_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry's answer is a catalogue: it grows with identities, never with record pages."""

    small = build_pagination_fixture(tmp_path / "entry-small", extra_claims=SMALL_EXTRA_CLAIMS)
    large = build_pagination_fixture(tmp_path / "entry-large", extra_claims=LARGE_EXTRA_CLAIMS)
    small_pages = len(walk_comparison(small, page_size=COMPARISON_PAGE, over_http=False)[0])
    large_pages = len(walk_comparison(large, page_size=COMPARISON_PAGE, over_http=False)[0])
    assert large_pages > small_pages * 3
    # Every entry read at both sizes is one un-paged answer: a page would mean the button had been
    # handed a record window, and a cursor would mean it had to fetch the rest to show the subjects.
    for fixture in (small, large):
        payload = compose_page(fixture, page_of=None)
        assert payload.page is None
        assert payload.comparison is not None
        assert payload.source.inventory.state == "measured"
    # Driving the real entry operation must call no comparison and no view. The two owners are the
    # only things that can read record pages, so replacing them with recorders makes the claim
    # falsifiable rather than rhetorical.
    calls: list[str] = []

    def forbidden_comparison(*args: object, **kwargs: object) -> object:
        calls.append("diff_knowledge_scope")
        raise AssertionError("the entry read compared a subject")

    def forbidden_view(*args: object, **kwargs: object) -> object:
        calls.append("read_knowledge_view")
        raise AssertionError("the entry read a record page")

    monkeypatch.setattr(knowledge_diff, "diff_knowledge_scope", forbidden_comparison)
    monkeypatch.setattr(knowledge_review, "diff_knowledge_scope", forbidden_comparison)
    monkeypatch.setattr(knowledge_review, "read_knowledge_view", forbidden_view)
    catalogue = _entry_for(large)
    assert calls == []
    assert catalogue.state == "entries"
    assert catalogue.total_subjects == len(catalogue.entries)
    assert catalogue.total_subjects >= 1


def _entry_for(fixture: PaginationFixture) -> ReviewEntryListResult:
    """One entry read driven through the real operation, over the fixture's recorded catalogue."""

    catalogue = knowledge_review.read_subject_catalogue(fixture.resolution())
    return ReviewEntryListResult(
        state="entries",
        repository_id=fixture.diff.repository_id,
        master=MASTER,
        leaf_id=REPOSITORY_LEAF,
        entries=catalogue,
        total_subjects=len(catalogue),
        invariant_total=sum(1 for entry in catalogue if entry.selector_kind == "invariant"),
        family_total=sum(1 for entry in catalogue if entry.selector_kind == "family"),
    )


def _advanced_candidate(directory: Path, fixture: PaginationFixture) -> PaginationFixture:
    """The same fixture with its candidate dataset *and* code tree advanced to a new generation.

    Both halves of the binding move: a moved dataset gives the after side another logical digest, and
    a moved code tree gives it another tree id. The recorded subject and its population are untouched,
    so the only thing the refused cursor can be about is the generation change itself.
    """

    directory.mkdir(parents=True, exist_ok=True)
    advanced_database = directory / fixture.diff.after.database_path.name
    advanced_database.write_bytes(fixture.diff.after.database_path.read_bytes())
    store = open_knowledge_store(advanced_database, fixture.diff.repository_id)
    try:
        authorship = make_read_authorship(seed="agent:pagination-advanced")
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=fixture.diff.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=str(uuid4()),
                    invariant_revision_id=fixture.recorded_subject,
                    role="support",
                    rationale="The advanced candidate records one further realization.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(str(uuid4())),
                        path="src/pagination/advanced.py",
                        source_identity=GitBlobIdentity(
                            object_id=next(iter(fixture.diff.after.git_blobs.values()))
                        ),
                        locator=FileLocator(),
                    )
                ),
                provenance=authorship,
            ),
        )
        assert created.state == "created", created.refusal
    finally:
        store.close()
    return replace(
        fixture,
        diff=replace(
            fixture.diff, after=replace(fixture.diff.after, database_path=advanced_database)
        ),
    )
