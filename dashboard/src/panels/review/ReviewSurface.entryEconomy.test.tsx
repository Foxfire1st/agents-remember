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
//     task-context review or the complete source explorer beyond `SUBJECT_HOLD_MS`.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewEntry, ReviewPayload, ReviewResult } from '../../data/review';
import { dashboardStore } from '../../data/store';
import { SUBJECT_HOLD_MS } from './ReviewNavigation';
import { ReviewSurface } from './ReviewSurface';

const captured = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'familyReview.complete.captured.json',
    ),
    'utf8',
  ),
) as ReviewResult;
const recorded = captured.payload as ReviewPayload;
const families = recorded.family_context!.entries;
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
});

function familyReview(familyId: string | null, afterSnapshot: string) {
  const family = families.find((item) => item.family_id === familyId);
  return {
    ...captured,
    payload: {
      ...recorded,
      comparison: { ...recorded.comparison!, after_snapshot_digest: afterSnapshot },
      family_context: {
        ...recorded.family_context,
        entries: family ? [family] : [],
        families_returned: family ? 1 : 0,
        state: family ? 'recorded' : 'no_subject_selected',
      },
    },
  };
}

it('reads the catalogue once on entry, then asks the review only for the subject it chose', async () => {
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
      return response(familyReview(url.searchParams.get('selectorId'), afterSnapshot));
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
      .getAllByTestId('review-catalogue-subject')
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

it('reads the task-context review and shows the source explorer when the catalogue never answers', async () => {
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      // The catalogue stalls for the whole case: its answer never arrives.
      if (url.pathname.endsWith('/entries')) return await new Promise<Response>(() => {});
      return response(familyReview(null, recorded.comparison!.after_snapshot_digest!));
    }),
  );
  const reviewReads = () => urls.filter((url) => url.pathname === '/api/review/intent');
  const opened = Date.now();

  const view = render(
    <ReviewSurface
      repo="agents-remember"
      master="review-economy"
      leaf="stalled-catalogue"
      onBack={() => undefined}
    />,
  );

  await waitFor(() => expect(reviewReads()).toHaveLength(1), { timeout: SUBJECT_HOLD_MS * 4 });
  // The bounded wait released into the task-context review: no subject is named.
  expect(reviewReads()[0].searchParams.get('selectorKind')).toBeNull();
  expect(Date.now() - opened).toBeLessThan(SUBJECT_HOLD_MS * 4);
  await waitFor(() =>
    expect(view.getAllByTestId('review-inventory-entry')).toHaveLength(
      recorded.source.inventory.entries.length,
    ),
  );
  expect(urls.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(1);
});
