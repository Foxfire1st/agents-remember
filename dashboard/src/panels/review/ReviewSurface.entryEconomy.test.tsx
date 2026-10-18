// The reviewer's own subject catalogue, read the economical way once the task entry stops loading it.
//
// The entry no longer reads the catalogue, so the reviewer opened from it carries no subject. These
// cases hold the reviewer to the request economy that makes that change pay off:
//   * the catalogue is read ONCE on entry -- keyed on the comparison, not on the global analytics
//     document, so an unrelated workspace publication does not re-read it;
//   * the first review read waits for the catalogue and asks for the subject it chose, rather than
//     reading the whole task and then reading again for the subject;
//   * choosing another subject reads that subject's review and never the catalogue again;
//   * a refresh that reaches a new candidate generation (other snapshot digests) re-reads it once;
//   * the wait for the catalogue is bounded: a catalogue that never answers does not withhold the
//     task-context review or its All changes destination beyond `SUBJECT_HOLD_MS`.
import { captured, selectedMemberId, replyFor } from './l86.test-utils';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewEntry, ReviewPayload, ReviewResult } from '../../data/review';
import { dashboardStore } from '../../data/store';
import { SUBJECT_HOLD_MS } from './ReviewNavigation';
import { ReviewSurface } from './ReviewSurface';

// Two independently captured family-selector answers from the same real L37 comparison.
const familyBodies = [
  captured<ReviewResult>('subjectReview.successorFamily.captured.json'),
  captured<ReviewResult>('l86.family.captured.json'),
];
const taskBody = captured<ReviewResult>('l86.task.captured.json');
const recorded = familyBodies[0].payload as ReviewPayload;
const families = familyBodies.map((body) => body.payload!.family_context!.entries[0]);
const catalogue: ReviewEntry[] = families.map((family) => ({
  selector_kind: 'family',
  selector_id: family.family_id,
  label: family.display_label ?? family.family_id,
  presence: 'both',
}));
const CATALOGUE = {
  state: 'entries',
  entries: catalogue,
  total_subjects: catalogue.length,
  family_total: catalogue.length,
  invariant_total: 0,
};
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function familyReview(familyId: string | null, afterSnapshot: string): ReviewResult {
  const body = familyId === null
    ? taskBody
    : familyBodies.find((item) => item.payload!.knowledge.revision_selection!.record_id === familyId);
  if (!body) throw new Error(`Uncaptured family selector: ${familyId}`);
  // Explicit generation-only perturbation; semantic content/judgment is never manufactured.
  if (!body.payload!.comparison || body.payload!.comparison.after_snapshot_digest === afterSnapshot)
    return structuredClone(body);
  return { ...body, payload: { ...body.payload!, comparison: {
    ...body.payload!.comparison, after_snapshot_digest: afterSnapshot,
  } } };
}

it('reads the catalogue once on entry, then asks the review only for the subject it chose', async () => {
  expect(selectedMemberId).toBe(captured<ReviewResult>('l86.member.captured.json').payload!.knowledge.revision_selection!.record_id);
  const urls: URL[] = [];
  let releaseCatalogue: (() => void) | undefined;
  let afterSnapshot = recorded.comparison!.after_snapshot_digest!;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      if (url.pathname.endsWith('/entries'))
        return await new Promise<Response>((resolve) => {
          releaseCatalogue = () => resolve(response(CATALOGUE));
        });
      if (url.pathname.endsWith('/source-content'))
        return response({
          state: 'refused',
          refusal: { code: 'not-found', detail: 'outside this case', next_action: 'none' },
        });
      return response(url.pathname === '/api/review/intent'
        ? familyReview(url.searchParams.get('selectorId'), afterSnapshot)
        : replyFor(url));
    }),
  );
  const catalogueReads = () => urls.filter((url) => url.pathname.endsWith('/entries'));
  const reviewReads = () => urls.filter((url) => url.pathname === '/api/review/intent');

  const view = render(
    <ReviewSurface
      repo="agents-remember"
      master="review-economy"
      leaf="recorded-task"
      onBack={() => undefined}
    />,
  );
  await waitFor(() => expect(catalogueReads()).toHaveLength(1));
  // The subject is being chosen: no review is read for a question about to be replaced.
  await act(async () => {});
  expect(reviewReads()).toHaveLength(0);

  await act(async () => releaseCatalogue?.());
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id),
  );
  expect(reviewReads()).toHaveLength(1);
  expect(reviewReads()[0].searchParams.get('selectorKind')).toBe('family');
  expect(reviewReads()[0].searchParams.get('selectorId')).toBe(families[0].family_id);

  // An unrelated workspace publication does not re-read the reviewer's catalogue.
  act(() => {
    dashboardStore.getState().applyDelta('analytics', {
      ...dashboardStore.getState().analytics,
      publishedRevision: 7,
    });
  });
  // Another subject reads that subject's review, never the catalogue again.
  fireEvent.click(
    view
      .getAllByTestId('review-family-open')
      .find((button) => button.dataset.subjectId === families[1].family_id)!,
  );
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id),
  );
  expect(catalogueReads()).toHaveLength(1);

  // A refresh that reaches another candidate generation re-reads the catalogue once for it.
  afterSnapshot = 'f'.repeat(64);
  fireEvent.click(view.getByTestId('review-refresh'));
  await waitFor(() => expect(catalogueReads()).toHaveLength(2));
  await act(async () => releaseCatalogue?.());
  await act(async () => {});
  expect(catalogueReads()).toHaveLength(2);
});

// MIK-R40 rule 5 and its budgets: the entries read is budgeted at 0.3 s, and at twice that while the
// leaf-wide view is computed. Both are inside the bounded wait, so opening the reviewer issues one
// review read, for the subject the catalogue chose, and no whole-task read whose answer the choice
// would then replace.
it.each([300, 600])(
  'opens with one review read when the catalogue answers after %i ms',
  async (catalogueMs) => {
    vi.useFakeTimers();
    expect(catalogueMs).toBeLessThan(SUBJECT_HOLD_MS);
    const urls: URL[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string) => {
        const url = new URL(address, 'http://localhost');
        urls.push(url);
        if (url.pathname.endsWith('/entries')) {
          // load-independent: catalogue delay and product hold run on the same fake clock
          await new Promise((resolve) => setTimeout(resolve, catalogueMs));
          return response(CATALOGUE);
        }
        if (url.pathname.endsWith('/source-content'))
          return response({
            state: 'refused',
            refusal: { code: 'not-found', detail: 'outside this case', next_action: 'none' },
          });
        return response(url.pathname !== '/api/review/intent' ? replyFor(url) :
          familyReview(
            url.searchParams.get('selectorId'),
            recorded.comparison!.after_snapshot_digest!,
          ),
        );
      }),
    );
    const reviewReads = () => urls.filter((url) => url.pathname === '/api/review/intent');
    const view = render(
      <ReviewSurface
        repo="agents-remember"
        master="review-economy"
        leaf={`catalogue-after-${catalogueMs}-ms`}
        onBack={() => undefined}
      />,
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(catalogueMs); });
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id);
    // Past the bound as well: the wait ended with the catalogue's answer, so nothing is released.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SUBJECT_HOLD_MS);
    });
    expect(reviewReads()).toHaveLength(1);
    expect(reviewReads()[0].searchParams.get('selectorKind')).toBe('family');
    expect(reviewReads()[0].searchParams.get('selectorId')).toBe(families[0].family_id);
    expect(urls.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(1);
  },
);

it('reads the task-context review and keeps All changes reachable when the catalogue never answers', async () => {
  vi.useFakeTimers();
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      // The catalogue stalls for the whole case: its answer never arrives.
      if (url.pathname.endsWith('/entries')) return await new Promise<Response>(() => {});
      return response(url.pathname === '/api/review/intent'
        ? familyReview(null, recorded.comparison!.after_snapshot_digest!)
        : replyFor(url));
    }),
  );
  const reviewReads = () => urls.filter((url) => url.pathname === '/api/review/intent');

  const view = render(
    <ReviewSurface
      repo="agents-remember"
      master="review-economy"
      leaf="stalled-catalogue"
      onBack={() => undefined}
    />,
  );

  await act(async () => { await vi.advanceTimersByTimeAsync(SUBJECT_HOLD_MS - 1); });
  expect(reviewReads()).toHaveLength(0);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(reviewReads()).toHaveLength(1);
  // The bounded wait released into the task-context review: no subject is named.
  expect(reviewReads()[0].searchParams.get('selectorKind')).toBeNull();
  vi.useRealTimers();
  await waitFor(() =>
    expect(view.getByTestId('review-scope-record').textContent).toContain(
      `${recorded.source.inventory.entries.length} changed files`,
    ),
  );
  expect(urls.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(1);
  expect(view.getAllByRole('button', { name: /^All changes/ }).length).toBeGreaterThan(0);
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
});
