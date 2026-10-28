// One open of a leaf's review page sends one id with its six opening reads, marks the sixth as the
// open's last, and changes nothing about any other read (D569; serving/review_open_observation.py).
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  LAST_MARK,
  OPEN_HEADER,
  PAGE_HEADER,
  openingFetch,
  openingReadOf,
  resetReviewOpenForTests,
  reviewPageId,
  sendDrawnOrder,
} from './reviewOpen';

const LEAF =
  'repo=agents-remember&master=260928_maintained-invariant-knowledge&leaf=260928-MIK-L201';
const OTHER =
  'repo=agents-remember&master=260928_maintained-invariant-knowledge&leaf=260928-MIK-L202';

// The opening's six reads as the page sends them (a recorded open of L201).
const SIX = (leaf: string, n = 199) => {
  const [repo, master, id] = new URLSearchParams(leaf).values();
  return {
    intent: `/api/review/intent?${leaf}`,
    entries: `/api/review/intent/entries?${leaf}`,
    files: `/api/review/trees?${leaf}&comparison=${n}&lane=files`,
    summary: `/api/review/intent/summary?${leaf}&comparison=${n}`,
    overview: `/api/review/trees?${leaf}&comparison=${n}&view=overview`,
    stale: `/api/explainer/${repo}/${master}/${id}/stale?comparisonNumber=${n}&comparisonOwner=recorded`,
  };
};

function served() {
  const fetchFn = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response('{}'),
  );
  vi.stubGlobal('fetch', fetchFn);
  const headerOf = (call: number) =>
    new Headers(fetchFn.mock.calls[call]?.[1]?.headers).get(OPEN_HEADER);
  return { fetchFn, headerOf };
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetReviewOpenForTests();
});

describe('the review open', () => {
  it('names exactly the six opening reads, and no subject or focused read', () => {
    for (const [name, url] of Object.entries(SIX(LEAF)))
      expect(openingReadOf(url)?.name).toBe(name);
    expect(openingReadOf(`/api/review/intent?${LEAF}&selector=invariant&id=INV-1`)).toBeNull();
    expect(openingReadOf(`/api/review/trees?${LEAF}&comparison=199&invariants=INV-1`)).toBeNull();
    expect(openingReadOf(`/api/review/trees?${LEAF}&comparison=199&file=a.py`)).toBeNull();
    expect(openingReadOf('/api/state')).toBeNull();
  });

  it('sends one id with the six reads and marks the sixth that starts as the last', async () => {
    const { fetchFn, headerOf } = served();
    for (const url of Object.values(SIX(LEAF)))
      await openingFetch(url, { signal: new AbortController().signal });
    const headers = fetchFn.mock.calls.map((_call, index) => headerOf(index));
    const id = headers[0];
    expect(id).toBeTruthy();
    expect(headers.slice(0, 5)).toEqual(Array(5).fill(id));
    expect(headers[5]).toBe(`${id}; ${LAST_MARK}`);
  });

  it('gives a second read of the same name no id, and the next open a fresh one', async () => {
    const { headerOf } = served();
    const reads = SIX(LEAF);
    await openingFetch(reads.intent);
    await openingFetch(reads.intent); // a repeated read observes for itself
    expect(headerOf(1)).toBeNull();
    for (const url of Object.values(reads).slice(1)) await openingFetch(url);
    await openingFetch(reads.intent); // the page opened again
    expect(headerOf(7)).toBeTruthy();
    expect(headerOf(7)).not.toBe(headerOf(0));
  });

  it('names its page on each opening read, one id for every open of the page load (D585)', async () => {
    const { fetchFn } = served();
    for (const url of [...Object.values(SIX(LEAF)), ...Object.values(SIX(OTHER))])
      await openingFetch(url);
    const pages = fetchFn.mock.calls.map(([, init]) => new Headers(init?.headers).get(PAGE_HEADER));
    expect(pages).toEqual(Array(12).fill(reviewPageId()));
    expect(reviewPageId()).toBeTruthy();
  });

  it('names its drawn order only after its open sent all six, with that open, once per order', async () => {
    const { fetchFn, headerOf } = served();
    const scope = { repo: 'agents-remember', master: '260928_maintained-invariant-knowledge', leaf: '260928-MIK-L201' };
    const reads = Object.values(SIX(LEAF));
    for (const url of reads.slice(0, 5)) await openingFetch(url);
    sendDrawnOrder(scope, 199, ['b', 'a']); // drawn while the sixth is not sent yet
    await Promise.resolve();
    expect(fetchFn).toHaveBeenCalledTimes(5); // no seventh read, nothing ahead of the sixth
    await openingFetch(reads[5]);
    await Promise.resolve();
    const posted = fetchFn.mock.calls.slice(6);
    expect(posted).toHaveLength(1);
    expect(posted[0][0]).toBe('/api/review/trees/order');
    expect(posted[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(posted[0][1]?.body))).toEqual({ ...scope, comparison: 199, order: ['b', 'a'], open: headerOf(0) });
    sendDrawnOrder(scope, 199, ['b', 'a']); // the same order again: nothing to say
    sendDrawnOrder(scope, 199, ['a', 'b']); // the reader re-sorted
    await Promise.resolve();
    expect(fetchFn.mock.calls.slice(6).map(([, init]) => JSON.parse(String(init?.body)).order)).toEqual([['b', 'a'], ['a', 'b']]);
  });

  it('mints a new open for another leaf', async () => {
    const { headerOf } = served();
    await openingFetch(SIX(LEAF).intent);
    await openingFetch(SIX(OTHER).intent);
    expect(headerOf(1)).toBeTruthy();
    expect(headerOf(1)).not.toBe(headerOf(0));
  });

  it('sends any other read exactly as before', async () => {
    const { fetchFn } = served();
    const subject = `/api/review/intent?${LEAF}&selector=invariant&id=INV-1`;
    const signal = new AbortController().signal;
    await openingFetch(subject);
    await openingFetch(subject, { signal });
    expect(fetchFn.mock.calls).toEqual([[subject], [subject, { signal }]]);
  });
});
