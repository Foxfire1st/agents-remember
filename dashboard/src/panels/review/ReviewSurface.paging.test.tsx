// R10 (complete bounded pagination) at the mounted surface: the real `ReviewSurface` over the real
// review client, for the page control and the states it must render.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client; only
// `fetch` is stubbed, so every request is built by the shipped client and every response travels the
// way the browser's does (status, body, the shared decode in `data/reviewTransport.ts`, the component
// tree). No assertion reads a prop this test itself passed: each case asserts what the rendered DOM
// contains and what the client actually requested.
//
// THE DEFECT THESE CASES CATCH. The surface used to render `locations_remaining: 100` (or any other
// remainder) with no control that reached the rest of the collection: a reader could see that items
// existed and had no way to inspect them. The cases below fail against that surface, and they also
// fail against the two wrong repairs -- a "next page" button that sends the cursor it was already
// standing on (so the page never advances), and one that offers itself for a response that published
// no cursor at all (so the button fetches nothing).
//
// WHERE THE VALUES COME FROM. Every page body below is the measured shape of the real route's answer
// in this leaf's evidence run: a `page` object with the collection, the state, the owner's own
// `total`/`returned`/`remaining`, the owner's opaque cursor, and the active scope. The cursors are
// opaque strings here exactly as they are on the wire -- the client never constructs or parses one,
// and these cases assert that the value the server published is the value the next request carries.

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ReviewPayload, ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';

// Comparison-focused cases isolate the catalogue. The normal catalogue-to-review journey is
// exercised through both real readers in ReviewSurface.navigation.test.tsx.
vi.mock("../../data/useReviewCatalogue", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../data/useReviewCatalogue")>();
  return {
    ...actual,
  useReviewCatalogue: () => ({
    loading: false, entries: [], empty: true, stale: false, facts: "test", refresh: () => undefined,
  }),
  };
});

const REPO = 'agents-remember';
const MASTER = '260921_complete-code-and-intent-review';
const LEAF = '260921-ICR-L10';
const SUBJECT = '3c513a59-5e5f-428a-b5e2-7301fcb280f7';

// The two cursors the server published in the evidence run, verbatim: one for the comparison's walk
// and one for the record selection's. They are different documents, which is why a control that
// mixed them up would be a real defect rather than a cosmetic one.
const KNOWLEDGE_CURSOR =
  'eyJjdX...E2fQ==';
const RECORDS_CURSOR =
  'review_matrix:0f9c1c5b8a1d4e0f9a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7081:3';

function payloadWith(page: ReviewPayload['page']): ReviewPayload {
  return {
    surface_version: 'knowledge-review-surface/1',
    candidate: { repository_id: REPO, master: MASTER, leaf_id: LEAF, task_ref: MASTER },
    comparison: {
      reference: 'c'.repeat(64),
      policy_version: 'recorded-two-snapshot-union/v1',
      binding_digest: 'd'.repeat(64),
      selector_digest: 'e'.repeat(64),
      before_snapshot_digest: 'a'.repeat(64),
      after_snapshot_digest: 'b'.repeat(64),
      before_code_tree_id: '1'.repeat(40),
      after_code_tree_id: '2'.repeat(40),
      knowledge_compared: true,
    },
    knowledge: {
      invariant_ids: ['i-1'],
      family_ids: [],
      before_statement: { state: 'present', language: 'text', text: 'before text', detail: '' },
      after_statement: { state: 'present', language: 'text', text: 'after text', detail: '' },
      before_conditions: [],
      after_conditions: [],
      revision_groups: [],
      field_changes: [],

      unresolved: [],
      selection_state: 'subject_selected',
    },
    source: {
      inventory: {
        state: 'measured',
        entries: [],
        listed_total: 0,
        detail: 'the bound pair differs at no path',
        partial: false,
        command: 'git diff --raw -z',
        unrepresentable_paths: [],
      },
      locations: [],
      remaining: [{ name: 'locations_remaining', value: page?.remaining ?? 0 }],
      unattributed_changed_paths: [],
      attributed_changed_paths: [],
      unresolved: [],
    },
    judgment: { state: "none", detail: "This leaf recorded no judgment about this record", rows: [], proofs: [] },
    staleness: { state: 'current', statement: 'the displayed comparison is current', moved: [] },
    submission: {
      state: 'unavailable',
      reason: 'this increment ships the surface display-only',
      next_action: 'publish through the curator authority',
      proposed_dispositions: [],
      none_is_approval: true,
    },
    page: page ?? null,
    limitations: [],
  };
}

const reviewed = (payload: ReviewPayload): ReviewResult => ({
  state: 'review',
  operation: 'read_knowledge_review',
  repository_id: REPO,
  payload,
});

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: '',
    json: async () => body,
  } as unknown as Response;
}

// The signature the surface's own client calls `fetch` with, so a case can assert the exact request
// the shipped client built (its URL and query string) rather than a value the test supplied.
type FetchSignature = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

// The page makes its other opening reads (summary, outline, trees, lane; MIK-R89 v2 rule 2) beside
// the review read, so a case counts and inspects the review reads by their route.
const isReviewRead = (input: RequestInfo | URL) =>
  new URL(String(input), 'http://localhost').pathname === '/api/review/intent';
const reviewCalls = (fetchFn: { mock: { calls: Parameters<FetchSignature>[] } }) =>
  fetchFn.mock.calls.filter(([input]) => isReviewRead(input));
const notServed = () =>
  response({
    state: 'refused',
    refusal: { code: 'not-served', detail: 'Only the review read is part of this check.', next_action: 'Open the review.' },
  });

const mountSubject = () =>
  render(
    <ReviewSurface
      repo={REPO}
      master={MASTER}
      leaf={LEAF}
      selectorKind="invariant"
      selectorId={SUBJECT}
      onBack={vi.fn()}
    />,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the review surface's page control", () => {
  it('states the bounds and scope of a page and offers the one action that reaches the rest', async () => {
    const fetchFn = vi.fn<FetchSignature>(async () =>
      response(
        reviewed(
          payloadWith({
            collection: 'knowledge',
            state: 'first_page',
            total: 103,
            returned: 16,
            remaining: 87,
            continuation: KNOWLEDGE_CURSOR,
            scope: ['selector_digest=' + 'e'.repeat(64), 'page_size=16'],
          }),
        ),
      ),
    );
    vi.stubGlobal('fetch', fetchFn);

    const view = mountSubject();

    const bounds = await view.findByTestId('review-page-bounds');
    expect(bounds.textContent).toContain('returned 16 of 103');
    expect(bounds.textContent).toContain('87 remaining');
    expect(bounds.textContent).toContain('page_size=16');
    const controls = view.getByTestId('review-page-controls');
    expect(controls.dataset.pageCollection).toBe('knowledge');
    const next = view.getByTestId('review-next-page');
    expect(next.dataset.continuation).toBe(KNOWLEDGE_CURSOR);
  });

  it('advances with the cursor the server published, and keeps the same collection', async () => {
    let call = 0;
    const fetchFn = vi.fn<FetchSignature>(async (input) => {
      if (!isReviewRead(input)) return notServed();
      call += 1;
      return response(
        reviewed(
          call === 1
            ? payloadWith({
                collection: 'knowledge',
                state: 'first_page',
                total: 103,
                returned: 16,
                remaining: 87,
                continuation: KNOWLEDGE_CURSOR,
                scope: ['page_size=16'],
              })
            : payloadWith({
                collection: 'knowledge',
                state: 'continued',
                total: 103,
                returned: 32,
                remaining: 71,
                continuation: RECORDS_CURSOR,
                continued_from: KNOWLEDGE_CURSOR,
                scope: ['page_size=16'],
              }),
        ),
      );
    });
    vi.stubGlobal('fetch', fetchFn);

    const view = mountSubject();
    fireEvent.click(await view.findByTestId('review-next-page'));

    await waitFor(() => expect(reviewCalls(fetchFn)).toHaveLength(2));
    const second = String(reviewCalls(fetchFn)[1][0]);
    // The request carries back exactly what the server published: the same collection name and the
    // opaque cursor verbatim, which is what makes the next page a continuation rather than a
    // re-read of page one.
    expect(second).toContain(`pageOf=knowledge`);
    expect(second).toContain(`continuation=${encodeURIComponent(KNOWLEDGE_CURSOR)}`);
    const bounds = await view.findByTestId('review-page-bounds');
    expect(bounds.textContent).toContain('returned 32 of 103');
    expect(view.getByTestId('review-next-page').dataset.continuation).toBe(RECORDS_CURSOR);
  });

  it('offers no next action for a page that published no cursor, whatever it reported', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<FetchSignature>(async () =>
        response(
          reviewed(
            payloadWith({
              collection: 'knowledge',
              state: 'first_page',
              total: 103,
              returned: 103,
              remaining: 0,
              continuation: null,
              scope: ['page_size=103'],
            }),
          ),
        ),
      ),
    );

    const view = mountSubject();

    const bounds = await view.findByTestId('review-page-bounds');
    expect(bounds.textContent).toContain('0 remaining');
    expect(view.queryByTestId('review-next-page')).toBeNull();
    // A complete page still lets the reader switch collection deliberately -- that is a new question,
    // not a continuation -- and returning to the whole review stays reachable.
    const picker = view.getByTestId('review-page-collection');
    expect(picker.textContent).toContain('whole review');
    expect(picker.textContent).toContain('knowledge');
    // The retired records collection is not offered: it left with the review matrix (MIK-R74
    // rule 1), no records page exists, and naming it earns the collection's own refusal.
    expect(picker.textContent).not.toContain('records');
    expect(
      Array.from(picker.querySelectorAll('[data-testid="review-page-option"]')).map(
        (option) => (option as HTMLOptionElement).value,
      ),
    ).toEqual(['knowledge']);
  });

  it('asks for a named collection deliberately, and states a reset as its own action', async () => {
    const fetchFn = vi.fn<FetchSignature>(async () =>
      response(
        reviewed(
          payloadWith({
            collection: 'knowledge',
            state: 'reset',
            total: 40,
            returned: 3,
            remaining: 37,
            continuation: KNOWLEDGE_CURSOR,
            scope: ['page_size=3'],
            reset: {
              code: 'comparison_page_reset',
              detail: 'continuation_binding_mismatch: the continuation does not bind this read',
              next_action:
                'open a new comparison: a cursor is a position in one comparison of two named snapshots',
              offending_input: KNOWLEDGE_CURSOR,
              expected: 'b'.repeat(64),
              observed: 'f'.repeat(64),
            },
          }),
        ),
      ),
    );
    vi.stubGlobal('fetch', fetchFn);

    const view = mountSubject();
    fireEvent.change(await view.findByTestId('review-page-collection'), {
      target: { value: 'knowledge' },
    });

    await waitFor(() => expect(reviewCalls(fetchFn)).toHaveLength(2));
    const requested = String(reviewCalls(fetchFn)[1][0]);
    expect(requested).toContain('pageOf=knowledge');
    expect(requested).not.toContain('continuation=');
    const reset = await view.findByTestId('review-page-reset');
    expect(reset.textContent).toContain('comparison_page_reset');
    expect(reset.textContent).toContain('open a new comparison');
    // The refusal does not dead-end the reader: the first page of the comparison that is there now is
    // one click away, and it is asked as the same collection with no cursor.
    fireEvent.click(view.getByTestId('review-first-page'));
    await waitFor(() => expect(reviewCalls(fetchFn)).toHaveLength(3));
    const firstAgain = String(reviewCalls(fetchFn)[2][0]);
    expect(firstAgain).toContain('pageOf=knowledge');
    expect(firstAgain).not.toContain('continuation=');
  });

  it('cannot ask for the retired records collection: the control is gone and no such request goes out', async () => {
    // The records collection left with the review matrix (MIK-R74 rule 1): the picker does not
    // offer it, and no shipped UI path can select it. A stale client that still tries -- the way
    // only an old bookmark or a hand-driven event could -- resolves to the whole review, and the
    // client never issues a `pageOf=records` request at all. The attempt resolves to the
    // whole review: no collection is paged and no refusal renders.
    const fetchFn = vi.fn<FetchSignature>(async () =>
      response(reviewed(payloadWith({ collection: 'knowledge', state: 'first_page', total: 1, returned: 1, remaining: 0, continuation: null, scope: [] }))),
    );
    vi.stubGlobal('fetch', fetchFn);

    const view = mountSubject();
    const picker = (await view.findByTestId(
      'review-page-collection',
    )) as unknown as HTMLSelectElement;
    const options = Array.from(picker.querySelectorAll('[data-testid="review-page-option"]')).map(
      (option) => (option as HTMLOptionElement).value,
    );
    expect(options).not.toContain('records');

    fireEvent.change(picker, { target: { value: 'records' } });
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    for (const call of fetchFn.mock.calls) {
      expect(String(call[0])).not.toContain('pageOf=records');
    }
    // The attempt resolves to the whole review: no collection is paged and no refusal renders.
    expect((view.getByTestId('review-page-collection') as HTMLSelectElement).value).not.toBe('records');
    expect(view.queryByTestId('review-page-refusal')).toBeNull();
  });

  it('words a reset from its refusal code, so a foreign cursor is not called a moved comparison', async () => {
    // Both states are `reset`; only one of them is a generation change. The gloss reads the code.
    vi.stubGlobal(
      'fetch',
      vi.fn<FetchSignature>(async () =>
        response(
          reviewed(
            payloadWith({
              collection: 'knowledge',
              state: 'reset',
              total_basis: 'selection',
              total: 29,
              returned: 5,
              remaining: 24,
              continuation: KNOWLEDGE_CURSOR,
              scope: ['page_size=5'],
              reset: {
                code: 'comparison_page_unreadable',
                detail: 'review_matrix: the continuation is not a token this substrate minted',
                next_action: 'present the token a previous page of this view returned',
                offending_input: RECORDS_CURSOR,
                expected: 'review_matrix',
                observed: 'review_matrix',
              },
            }),
          ),
        ),
      ),
    );

    const view = mountSubject();
    const bounds = await view.findByTestId('review-page-bounds');
    expect(bounds.textContent).toContain("reset — that cursor is not this collection's");
    expect(bounds.textContent).not.toContain('this comparison moved');
    cleanup();

    // And the moved-generation gloss is unchanged for the code that means it.
    vi.stubGlobal(
      'fetch',
      vi.fn<FetchSignature>(async () =>
        response(
          reviewed(
            payloadWith({
              collection: 'knowledge',
              state: 'reset',
              total_basis: 'selection',
              total: 113,
              returned: 16,
              remaining: 97,
              continuation: KNOWLEDGE_CURSOR,
              scope: ['page_size=16'],
              reset: {
                code: 'comparison_page_reset',
                detail: 'continuation_binding_mismatch: the continuation does not bind this read',
                next_action: 'open a new comparison: …',
                offending_input: KNOWLEDGE_CURSOR,
                expected: 'a'.repeat(64),
                observed: 'b'.repeat(64),
              },
            }),
          ),
        ),
      ),
    );
    const moved = mountSubject();
    const movedBounds = await moved.findByTestId('review-page-bounds');
    expect(movedBounds.textContent).toContain('reset — this comparison moved');
  });

  it("keeps the two collections' totals apart in the sentence the reader sees", async () => {
    // Same numbers, two meanings: the comparison's total is the selection's size with a cumulative
    // returned count, the view's is what the walk still covers. The sentence says which. The
    // totals sentence is collection-agnostic, so it is shown on the surviving knowledge walk.
    vi.stubGlobal(
      'fetch',
      vi.fn<FetchSignature>(async () =>
        response(
          reviewed(
            payloadWith({
              collection: 'knowledge',
              state: 'continued',
              total_basis: 'walk',
              total: 19,
              returned: 4,
              remaining: 15,
              continuation: KNOWLEDGE_CURSOR,
              scope: ['page_size=4'],
            }),
          ),
        ),
      ),
    );

    const view = mountSubject();
    const bounds = await view.findByTestId('review-page-bounds');
    expect(bounds.textContent).toContain('returned 4');
    expect(bounds.textContent).toContain('15 remaining');
    expect(bounds.textContent).toContain('this walk still covers');
    expect(bounds.textContent).not.toContain('returned 4 of 19');
    cleanup();

    vi.stubGlobal(
      'fetch',
      vi.fn<FetchSignature>(async () =>
        response(
          reviewed(
            payloadWith({
              collection: 'knowledge',
              state: 'first_page',
              total_basis: 'selection',
              total: 112,
              returned: 16,
              remaining: 96,
              continuation: KNOWLEDGE_CURSOR,
              scope: ['page_size=16'],
            }),
          ),
        ),
      ),
    );
    const knowledge = mountSubject();
    const knowledgeBounds = await knowledge.findByTestId('review-page-bounds');
    expect(knowledgeBounds.textContent).toContain('returned 16 of 112 in the selection');
    expect(knowledgeBounds.textContent).toContain('96 remaining');
  });

  it('keeps a whole review free of paging controls and carries its subject selector', async () => {
    const fetchFn = vi.fn<FetchSignature>(async () => response(reviewed(payloadWith(null))));
    vi.stubGlobal('fetch', fetchFn);

    const view = mountSubject();

    await view.findByTestId('review-page-controls');
    expect(view.queryByTestId('review-page-bounds')).toBeNull();
    expect(view.queryByTestId('review-page-collection')).toBeNull();
    expect(view.queryByTestId('review-next-page')).toBeNull();
    expect(view.queryByTestId('review-first-page')).toBeNull();
    const requested = String(reviewCalls(fetchFn)[0][0]);
    expect(requested).not.toContain('pageOf=');
    expect(requested).toContain('selectorKind=invariant');
    expect(requested).toContain(`selectorId=${SUBJECT}`);
  });
});
