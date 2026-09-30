// The task entry's unexplained-changes count (MIK-R32 rule 9): `· K unexplained`, or
// `· K unexplained · U unknown`, beside -- never inside -- the intent review's `+N −N`.
//
// The first case reads the REAL served summary of the MIK-L32 converted scratch leaf
// (panels/review/laneReview.capture-provenance.json). The state table derives each other state from
// that body by setting the attribution the owner's vocabulary defines (models/knowledge/review_lane.py:
// counted, partial, unavailable), so every row is a shape the route can answer.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { IntentReviewEntry } from './intentReviewEntry';

const summary = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      '../review/laneReview.summary.captured.json',
    ),
    'utf8',
  ),
) as Record<string, unknown>;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function serve(body: unknown): URL[] {
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      urls.push(new URL(address, 'http://localhost'));
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
  return urls;
}

function entry() {
  return render(
    <IntentReviewEntry
      repo="agents-remember"
      master="260928_maintained-invariant-knowledge"
      leaf="260928-MIK-L32"
      live
      facts="f"
      onOpen={() => undefined}
    />,
  );
}

it("shows the real leaf's unexplained and unknown files beside +N −N, from the one summary read", async () => {
  const urls = serve(summary);
  const view = entry();
  const counts = await view.findByTestId('intent-review-counts');
  const attribution = await view.findByTestId('intent-review-attribution');
  expect(counts.textContent).toBe('+0 −0');
  expect(attribution.textContent).toBe('· 3 unexplained · 1 unknown');
  // Two elements: the unexplained count is never folded into the intent counts.
  expect(counts.contains(attribution) || attribution.contains(counts)).toBe(false);
  // Requested with the intent counts and no more eagerly: the entry made exactly one read.
  expect(urls.map((url) => url.pathname)).toEqual(['/api/review/intent/summary']);
});

const withAttribution = (attribution: unknown) => ({ ...summary, attribution });

it.each([
  [
    'unexplained only',
    withAttribution({
      state: 'counted',
      changed_total: 4,
      attributed: 1,
      unexplained: 3,
      attribution_unknown: 0,
      unmeasured: [],
    }),
    '· 3 unexplained',
  ],
  [
    'unknown with zero unexplained',
    withAttribution({
      state: 'counted',
      changed_total: 2,
      attributed: 0,
      unexplained: 0,
      attribution_unknown: 2,
      unmeasured: [],
    }),
    '· 0 unexplained · 2 unknown',
  ],
  [
    'nothing unexplained or unknown',
    withAttribution({
      state: 'counted',
      changed_total: 2,
      attributed: 2,
      unexplained: 0,
      attribution_unknown: 0,
      unmeasured: [],
    }),
    null,
  ],
  [
    'a partially measured change set',
    withAttribution({
      state: 'partial',
      detail: '1 path is not text',
      unmeasured: ["b'caf\\xe9.txt' (name is not text)"],
    }),
    '· attribution partial',
  ],
  [
    'an unmeasured change set',
    withAttribution({
      state: 'unavailable',
      detail: 'the trees could not be compared',
      unmeasured: [],
    }),
    '· attribution unknown',
  ],
  ['a dataset comparison', { ...summary, attribution: undefined }, null],
])('renders %s as its own state', async (_, body, expected) => {
  serve(body);
  const view = entry();
  await view.findByTestId('intent-review-counts');
  const attribution = view.queryByTestId('intent-review-attribution');
  expect(attribution?.textContent ?? null).toBe(expected);
  if (expected === '· attribution partial')
    expect(view.getByTestId('intent-review-attribution-details').textContent).toContain(
      "Not measured: b'caf\\xe9.txt' (name is not text)",
    );
});

it('shows nothing while the summary is pending: never a zero', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => undefined)),
  );
  const view = entry();
  await waitFor(() =>
    expect(view.getByTestId('open-intent-review').dataset.intentState).toBe('loading'),
  );
  expect(view.queryByTestId('intent-review-attribution')).toBeNull();
});
