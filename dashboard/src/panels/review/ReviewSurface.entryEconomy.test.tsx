// The reviewer's own subject catalogue, read the economical way once the task entry stops loading it.
//
// The entry no longer reads the catalogue, and the page opens with nothing selected (MIK-R89 v2 rule
// 2). These cases hold the reviewer to the request economy that pays off:
//   * the catalogue is read ONCE on entry -- keyed on the comparison, not on the global analytics
//     document, so an unrelated workspace publication does not re-read it;
//   * the opening's one review read is the task context's and waits for nothing, so a catalogue that
//     answers late, or never, withholds neither that review nor its All changes destination; a late
//     catalogue selects nothing;
//   * choosing a subject reads that subject's review and never the catalogue again;
//   * a refresh that reaches a new candidate generation (other snapshot digests) re-reads it once.
import { captured, selectedMemberId, replyFor } from './l86.test-utils';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewEntry, ReviewPayload, ReviewResult } from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { dashboardStore } from '../../data/store';
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
  vi.unstubAllGlobals();
  resetCardCachesForTests();
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

const sourceContentRefusal = {
  state: 'refused',
  refusal: { code: 'not-found', detail: 'outside this case', next_action: 'none' },
};
// The reviewer's reads; `answerCatalogue` answers the entries read (a promise that never settles stalls it).
function serveEconomy(answerCatalogue: () => Promise<Response>, afterSnapshot: () => string) {
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      if (url.pathname.endsWith('/entries')) return await answerCatalogue();
      if (url.pathname.endsWith('/source-content')) return response(sourceContentRefusal);
      return response(url.pathname === '/api/review/intent'
        ? familyReview(url.searchParams.get('selectorId'), afterSnapshot())
        : replyFor(url));
    }),
  );
  return {
    catalogueReads: () => urls.filter((url) => url.pathname.endsWith('/entries')),
    reviewReads: () => urls.filter((url) => url.pathname === '/api/review/intent'),
  };
}
const familyRow = (view: ReturnType<typeof render>, familyId: string) =>
  view.getAllByTestId('review-family-open').find((button) => button.dataset.subjectId === familyId)!;

it('reads the catalogue once on entry, opens on the task context, then asks the review only for the subject chosen', async () => {
  expect(selectedMemberId).toBe(captured<ReviewResult>('l86.member.captured.json').payload!.knowledge.revision_selection!.record_id);
  let releaseCatalogue: (() => void) | undefined;
  let afterSnapshot = recorded.comparison!.after_snapshot_digest!;
  const { catalogueReads, reviewReads } = serveEconomy(
    () => new Promise<Response>((resolve) => {
      releaseCatalogue = () => resolve(response(CATALOGUE));
    }),
    () => afterSnapshot,
  );

  const view = render(
    <ReviewSurface
      repo="agents-remember"
      master="review-economy"
      leaf="recorded-task"
      onBack={() => undefined}
    />,
  );
  await waitFor(() => expect(catalogueReads()).toHaveLength(1));
  // The opening waits for nothing: its one review read is the task context's.
  await waitFor(() => expect(reviewReads()).toHaveLength(1));
  expect(reviewReads()[0].searchParams.has('selectorId')).toBe(false);

  // The late catalogue selects nothing and reads no subject.
  await act(async () => releaseCatalogue?.());
  await waitFor(() => expect(familyRow(view, families[0].family_id)).toBeTruthy());
  expect(view.queryByTestId('review-subject-slot')).toBeNull();
  expect(reviewReads()).toHaveLength(1);

  // An unrelated workspace publication does not re-read the reviewer's catalogue.
  act(() => {
    dashboardStore.getState().applyDelta('analytics', {
      ...dashboardStore.getState().analytics,
      publishedRevision: 7,
    });
  });
  // A subject reads that subject's review, never the catalogue again.
  for (const [index, family] of families.entries()) {
    fireEvent.click(familyRow(view, family.family_id));
    await waitFor(() => expect(view.getByTestId('review-center-family').dataset.family).toBe(family.family_id));
    expect(reviewReads()).toHaveLength(index + 2);
    expect(reviewReads()[index + 1].searchParams.get('selectorKind')).toBe('family');
    expect(reviewReads()[index + 1].searchParams.get('selectorId')).toBe(family.family_id);
  }
  expect(catalogueReads()).toHaveLength(1);

  // A refresh that reaches another candidate generation re-reads the catalogue once for it.
  afterSnapshot = 'f'.repeat(64);
  fireEvent.click(view.getByTestId('review-refresh'));
  await waitFor(() => expect(catalogueReads()).toHaveLength(2));
  await act(async () => releaseCatalogue?.());
  await act(async () => {});
  expect(catalogueReads()).toHaveLength(2);
});

it('reads the task-context review at once and keeps All changes reachable when the catalogue never answers', async () => {
  // The catalogue stalls for the whole case: its answer never arrives.
  const { catalogueReads, reviewReads } = serveEconomy(
    () => new Promise<Response>(() => {}),
    () => recorded.comparison!.after_snapshot_digest!,
  );
  const view = render(
    <ReviewSurface
      repo="agents-remember"
      master="review-economy"
      leaf="stalled-catalogue"
      onBack={() => undefined}
    />,
  );
  await waitFor(() => expect(reviewReads()).toHaveLength(1));
  // No subject is named: the opening reads the task context without waiting for the catalogue.
  expect(reviewReads()[0].searchParams.get('selectorKind')).toBeNull();
  await waitFor(() =>
    expect(view.getByTestId('review-scope-record').textContent).toContain(
      `${recorded.source.inventory.entries.length} changed files`,
    ),
  );
  expect(catalogueReads()).toHaveLength(1);
  expect(view.getByTestId('review-all-changes').closest('[data-testid="review-tree"]')).not.toBeNull();
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
});
