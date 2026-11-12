// Identical reads in flight are one request, and a request no consumer needs any more is cancelled on
// the wire (D569 rule 10), exercised through the review transport and the explainer reads.
import { afterEach, expect, it, vi } from 'vitest';

import { detachInFlightReads, isAbortedRead } from './abortableRead';
import { getReviewJson } from './reviewTransport';
import {
  readExplainerRevisions,
  readExplainerState,
} from '../panels/review/explainer/explainerApi';

const target = { repository: 'repo', master: 'master', leaf: 'leaf' };
const REVIEW = '/api/review/intent?repo=repo&master=master&leaf=leaf';

interface Sent {
  url: string;
  signal: AbortSignal | null | undefined;
  answer: () => void;
}

// A fetch whose answers wait until the test releases them, recording each request's own signal.
function held(): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((address: string | URL, init?: RequestInit) => {
      const url = String(address);
      return new Promise<Response>((resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('aborted', 'AbortError')),
        );
        const body = url.includes('/revisions')
          ? { state: 'ok', revisions: [] }
          : url.includes('/api/explainer/')
            ? { state: 'ok', seat: { state: 'live' }, explanation: null }
            : { state: 'review', operation: 'review-intent', repository_id: 'repo' };
        sent.push({
          url,
          signal: init?.signal,
          answer: () => resolve(new Response(JSON.stringify(body))),
        });
      });
    }),
  );
  return sent;
}

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  detachInFlightReads();
  vi.unstubAllGlobals();
});

it('sends one request for a StrictMode release and re-acquire of the same read, never one aborted', async () => {
  const sent = held();
  // Effect, cleanup, effect again -- all in one synchronous pass, as StrictMode runs them.
  const first = new AbortController();
  const left = getReviewJson(REVIEW, first.signal).catch((cause: unknown) => cause);
  first.abort();
  const second = new AbortController();
  const kept = getReviewJson<{ state: string }>(REVIEW, second.signal);
  const state = readExplainerState(target, undefined, new AbortController().signal);
  await tick();

  expect(sent.map((request) => request.url).sort()).toEqual([
    '/api/explainer/repo/master/leaf/state',
    REVIEW,
  ]);
  sent.forEach((request) => request.answer());
  expect((await kept).state).toBe('review');
  expect((await state).seat.state).toBe('live');
  expect(isAbortedRead(await left)).toBe(true);
  expect(sent.some((request) => request.signal?.aborted)).toBe(false);
});

it('joins identical explainer reads in flight, StrictMode-remounted or concurrent', async () => {
  const sent = held();
  const strict = new AbortController();
  void readExplainerState(target, { comparisonNumber: 8 }, strict.signal).catch(() => undefined);
  void readExplainerRevisions(target, strict.signal).catch(() => undefined);
  strict.abort();
  const mounted = new AbortController();
  const state = readExplainerState(target, { comparisonNumber: 8 }, mounted.signal);
  const revisions = readExplainerRevisions(target, mounted.signal);
  const other = readExplainerState(target, { comparisonNumber: 8 });
  await tick();

  expect(sent.map((request) => request.url)).toEqual([
    '/api/explainer/repo/master/leaf/state?comparisonNumber=8',
    '/api/explainer/repo/master/leaf/revisions',
  ]);
  expect(sent.some((request) => request.signal?.aborted)).toBe(false);
  sent.forEach((request) => request.answer());
  expect(await state).toBe(await other);
  expect((await revisions).revisions).toEqual([]);
});

it('cancels the request on the wire once no consumer wants it, and not while one still does', async () => {
  const sent = held();
  const a = new AbortController();
  const b = new AbortController();
  const first = getReviewJson(REVIEW, a.signal).catch((cause: unknown) => cause);
  const second = getReviewJson(REVIEW, b.signal).catch((cause: unknown) => cause);
  await tick();
  expect(sent).toHaveLength(1);

  a.abort();
  await tick();
  expect(isAbortedRead(await first)).toBe(true);
  expect(sent[0].signal?.aborted).toBe(false);

  b.abort();
  await tick();
  expect(isAbortedRead(await second)).toBe(true);
  expect(sent[0].signal?.aborted).toBe(true);
});

it('sends nothing for a read released in the same pass with nobody re-joining', async () => {
  const sent = held();
  const owner = new AbortController();
  const read = getReviewJson(REVIEW, owner.signal).catch((cause: unknown) => cause);
  owner.abort();
  await tick();
  expect(isAbortedRead(await read)).toBe(true);
  expect(sent).toHaveLength(0);
});

it('asks again after a change is announced: a renewal never joins a request asked before it', async () => {
  const sent = held();
  void getReviewJson(REVIEW, new AbortController().signal);
  await tick();
  detachInFlightReads('review:');
  void getReviewJson(REVIEW, new AbortController().signal);
  await tick();
  expect(sent).toHaveLength(2);
  expect(sent[0].signal?.aborted).toBe(false);
});
