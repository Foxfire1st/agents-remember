// D576: a leaf's move refreshes only the invariants it changed. On a move from the comparison the
// page shows (#8) to a newer one (#9) the page asks `/api/review/trees/changes` once. A named answer
// carries every visited invariant it does not name to #9 with no read, asks the named visited ones
// again in place, and marks exactly the named ones stale -- in their tree rows and on the open card --
// until their detail at #9 has loaded. Selection and scroll stay. An empty list does nothing; an
// unavailable answer or a failed request renews the page as before. The marks are counted apart from
// the Explanation's stale parts.
//
// The page is the L181 review page (reading/reviewPage.test-utils.tsx) over an overview answer, so a
// visited invariant's revealed changes come from its own card read alone. Its two synthetic
// invariants are given changed locations of their own here, named so.
import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type { ReviewEntryListResult, ReviewResult } from '../../data/review';
import { resetCardCachesForTests, type ReviewTreeEntry, type ReviewTreesResult } from '../../data/reviewTrees';
import { connectState } from '../../data/stream';
import { enclosure, seedProjection } from '../detail-panel/test-utils';
import { l181Answer } from './changeSetOrder.test-utils';
import { L181_SUBJECT, PLACE, servePage, stubIntersection, waitForOpening } from './reading/reviewPage.test-utils';
import { publishLeafJudgments } from './reading/useChangeSet';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';

const EXPLAINER = `/api/explainer/${PLACE.repo}/${PLACE.master}/${PLACE.leaf}`;
const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;
const SYNTH_0 = 'synthetic-unchanged-0';
const SYNTH_Z = 'synthetic-unchanged-z';
// The tree's order of the three invariants (changeSetOrder.test-utils.ts `l181Outline`).
const THREE = [SYNTH_0, L181_SUBJECT, SYNTH_Z];

class FakeSource {
  static latest: FakeSource | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  constructor(public url: string) {
    FakeSource.latest = this;
  }
  addEventListener(name: string, callback: (event: MessageEvent) => void) {
    this.listeners.set(name, callback);
  }
  close() {}
  emit(name: string, data: unknown = {}) {
    this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) }));
  }
}

// `/stale` with no explainer work: a moved notice then renews the page's deterministic reads.
const NO_WORK = { state: 'ok', stale: { parts: [] }, parts: [], explanation: null, movedNotice: null };
// `/stale` with one stale part of the Explanation.
const ONE_PART = {
  state: 'ok',
  stale: { total: 1, parts: [{ id: 'look', title: 'Where to look first', reason: 'changed in #8' }] },
  total: 1,
  parts: [{ id: 'look', title: 'Where to look first', reason: 'changed in #8' }],
  explanation: { revision: 8, at: '2026-10-19T12:36:00Z', entrypoint: 'index.html' },
  movedNotice: null,
};
const MOVE = { kind: 'comparison-moved', from: 8, to: 9, blocksChanged: 1, updateOffered: true, text: 'task moved: #7 -> #8' };

type Changes = { state: 'named'; invariants: string[] } | { state: 'unavailable'; reason: string } | 'fail';

interface World {
  latest: number;
  changes: Changes;
  stale: unknown;
  // An invariant the newer comparison no longer records.
  gone?: string;
  // Card reads of these invariants at #9 wait until `release`.
  hold?: readonly string[];
  // The changes answer waits until this promise resolves.
  changesHeld?: Promise<void>;
}

let dispose: (() => void) | undefined;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
  resetCardCachesForTests();
  publishLeafJudgments(PLACE.repo, PLACE.master, PLACE.leaf, [], []);
  treeViewStore.getState().setView('all');
  treeOrderStore.getState().setOrder('priority');
});

afterEach(() => {
  cleanup();
  dispose?.();
  dispose = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// Two changed locations of a synthetic invariant, on paths of its own.
function syntheticEntries(key: string): ReviewTreeEntry[] {
  const changed = (l181Answer.entries ?? []).filter((entry) => entry.change === 'changed').slice(0, 2);
  return changed.map((original, index) => {
    const entry = structuredClone(original);
    entry.id = `${original.id}-${key}`;
    entry.invariant = key === SYNTH_0 ? 'INV-0SYNTH' : 'INV-ZSYNTH';
    entry.invariant_key = key;
    for (const side of [entry.before, entry.after]) if (side.path) side.path = `synthetic/${key}/${index}.ts`;
    return entry;
  });
}

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

// The leaf-wide answer as an overview: its rows name the three invariants and carry no location.
function overviewAnswer(body: ReviewTreesResult): ReviewTreesResult {
  const rows = THREE.map((key) => ({
    invariant_key: key, changed: 2, unchanged: 0, not_comparable: 0, changed_span: 2, changed_paths: [],
    entry_currentness: 'current' as const, proofs: {},
  }));
  return { ...body, entry_inventory: undefined, overview: { state: 'ready', subjects: rows } };
}

function cardAnswer(body: ReviewTreesResult, url: URL): ReviewTreesResult {
  const asked = url.searchParams.get('invariants')!.split(',');
  const synthetic = asked.filter((key) => key === SYNTH_0 || key === SYNTH_Z).flatMap(syntheticEntries);
  return { ...body, entries: [...(body.entries ?? []), ...synthetic] };
}

// The task context and the subject answers name the latest comparison; a newer one has new trees.
function intentAnswer(body: ReviewResult, world: World): ReviewResult {
  const payload = body.payload!;
  payload.limitations = payload.limitations.map((token) => (token.startsWith('review:trees:') ? `review:trees:${world.latest}` : token));
  if (world.latest !== 8) payload.source.inventory.after_code_tree_id = `moved-${world.latest}`;
  return body;
}

function entriesAnswer(body: ReviewEntryListResult, world: World): ReviewEntryListResult {
  if (world.latest === 8 || !world.gone) return body;
  return {
    ...body,
    outline: { ...body.outline!, invariants: body.outline!.invariants.filter((row) => row.selector_id !== world.gone) },
    entries: (body.entries ?? []).filter((row) => row.selector_id !== world.gone),
  };
}

function changesAnswer(world: World, url: URL): Response {
  if (world.changes === 'fail') return new Response(JSON.stringify({ detail: 'down' }), { status: 500 });
  const from = Number(url.searchParams.get('from'));
  const to = Number(url.searchParams.get('to'));
  return json(world.changes.state === 'named' ? { ...world.changes, from_comparison: from, to_comparison: to } : world.changes);
}

async function answer(world: World, url: URL, response: Response): Promise<Response> {
  if (url.pathname === '/api/review/intent') return json(intentAnswer((await response.json()) as ReviewResult, world));
  if (url.pathname === '/api/review/intent/entries') return json(entriesAnswer((await response.json()) as ReviewEntryListResult, world));
  if (url.pathname !== '/api/review/trees' || ['lane', 'file', 'entry'].some((name) => url.searchParams.has(name))) return response;
  const body = (await response.json()) as ReviewTreesResult;
  return json(url.searchParams.has('invariants') ? cardAnswer(body, url) : overviewAnswer(body));
}

const held = (world: World, url: URL) =>
  url.searchParams.get('comparison') === '9' && (world.hold ?? []).includes(url.searchParams.get('invariants') ?? '');

// The page over the world, recording every request; `release` answers the held card reads.
function serve(world: World) {
  const page = servePage((url) => held(world, url));
  const served = globalThis.fetch;
  const asked: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      asked.push(url);
      if (url.pathname === `${EXPLAINER}/stale`) return json(world.stale);
      if (url.pathname === '/api/review/trees/changes') return (world.changesHeld ?? Promise.resolve()).then(() => changesAnswer(world, url));
      return answer(world, url, await served(address, init));
    }),
  );
  return { asked, release: page.release };
}

function openLive() {
  seedProjection({
    enclosures: [
      enclosure({
        enclosure: '/contracts/l181',
        lifecycleId: 'l181',
        repoName: PLACE.repo,
        taskName: PLACE.master,
        leafId: PLACE.leaf,
        worktreeGroup: '/worktrees/l181-ar',
      }),
    ],
    activeWorktreeGroups: ['l181-ar'],
  });
  vi.stubGlobal('EventSource', FakeSource);
  dispose = connectState();
}

async function arm() {
  act(() => {
    FakeSource.latest!.emit('open');
    FakeSource.latest!.emit('review.memory', { ...PLACE, revision: 'armed:0', changedAgoMs: 10 * 60_000 });
  });
  await act(async () => {});
}

const count = (asked: readonly URL[], test: (url: URL) => boolean) => asked.filter(test).length;
const intent = (url: URL) => url.pathname === '/api/review/intent';
const changes = (url: URL) => url.pathname === '/api/review/trees/changes';
const overview = (number: number) => (url: URL) =>
  url.pathname === '/api/review/trees' && url.searchParams.get('view') === 'overview' && url.searchParams.get('comparison') === String(number);
const cardsOf = (number: number, key?: string) => (url: URL) =>
  url.pathname === '/api/review/trees' &&
  url.searchParams.get('comparison') === String(number) &&
  (key === undefined ? url.searchParams.has('invariants') : url.searchParams.get('invariants') === key);

const row = (key: string) => document.querySelector<HTMLElement>(`[data-testid="review-family-member-open"][data-subject-id="${key}"]`)!;
const revealed = (key: string) => row(key).closest('li')!.querySelectorAll('[data-testid="review-tree-location"]').length;
const marked = () =>
  [...document.querySelectorAll<HTMLElement>('[data-testid="review-family-member-open"][data-stale="true"]')].map((one) => one.dataset.subjectId);
const card = (key: string) => document.querySelector<HTMLElement>(`[data-testid="review-subject-section"][data-subject="${key}"]`);
const selected = () => document.querySelector<HTMLElement>('[data-testid="review-family-member-open"][aria-current="true"]')?.dataset.subjectId;
const scroller = () => document.querySelector<HTMLElement>('[data-review-scroller]')!;

async function open(world: World) {
  stubIntersection();
  const server = serve(world);
  openLive();
  render(
    <StrictMode>
      <ReviewSurface {...PLACE} onBack={() => undefined} />
    </StrictMode>,
  );
  await waitForOpening();
  await arm();
  return server;
}

// Each invariant opened in turn, in `keys` order: the last stays open, every one is revealed.
async function visit(asked: readonly URL[], keys: readonly string[]) {
  for (const key of keys) {
    fireEvent.click(row(key));
    await waitFor(() => expect(count(asked, cardsOf(8, key))).toBe(1));
    await waitFor(() => expect(revealed(key)).toBeGreaterThan(0));
  }
  await waitFor(() => expect(selected()).toBe(keys[keys.length - 1]));
}

// The leaf moves to #9 and its moved notice arrives.
async function move(world: World) {
  world.latest = 9;
  act(() => FakeSource.latest!.emit('review.explainer.moved', { ...PLACE, ...MOVE }));
  await waitFor(() => expect(count(serverAsked(), changes)).toBe(1));
  await act(async () => {});
}

let serverAsked: () => readonly URL[] = () => [];

async function opened(world: World, keys: readonly string[]) {
  const server = await open(world);
  serverAsked = () => server.asked;
  await visit(server.asked, keys);
  return server;
}

it('his path: three visited, a move changes one; that one alone is marked and read again, the others carried with no read, selection and scroll kept', async () => {
  const world: World = { latest: 8, changes: { state: 'named', invariants: [L181_SUBJECT] }, stale: NO_WORK, hold: [L181_SUBJECT] };
  const { asked, release } = await opened(world, THREE);
  scroller().scrollTop = 240;
  const hash = window.location.hash;
  const intents = count(asked, intent);

  await move(world);
  await waitFor(() => expect(count(asked, overview(9))).toBe(1));
  await waitFor(() => expect(count(asked, cardsOf(9, L181_SUBJECT))).toBe(1));
  // The changed one is marked while its detail at #9 is read; the count is the marks' own set.
  expect(marked()).toEqual([L181_SUBJECT]);
  expect(screen.getByTestId('review-stale-invariants').dataset.count).toBe('1');
  const changesAsked = asked.find(changes)!.searchParams;
  expect([changesAsked.get('from'), changesAsked.get('to')]).toEqual(['8', '9']);

  await act(async () => release());
  await waitFor(() => expect(marked()).toEqual([]));
  expect(screen.queryByTestId('review-stale-invariants')).toBeNull();
  // The unchanged visited ones kept their revealed changes with no read at #9.
  expect(count(asked, cardsOf(9, SYNTH_0))).toBe(0);
  expect(count(asked, cardsOf(9, SYNTH_Z))).toBe(0);
  expect(count(asked, cardsOf(9))).toBe(1);
  expect(revealed(SYNTH_0)).toBeGreaterThan(0);
  expect(revealed(SYNTH_Z)).toBeGreaterThan(0);
  expect(revealed(L181_SUBJECT)).toBeGreaterThan(0);
  // The question on screen was asked once more for #9; the page was not renewed.
  expect(count(asked, intent)).toBe(intents + 1);
  expect(count(asked, overview(8))).toBe(1);
  expect(count(asked, changes)).toBe(1);
  expect(selected()).toBe(SYNTH_Z);
  expect(window.location.hash).toBe(hash);
  expect(scroller().scrollTop).toBe(240);
});

it('mirror: the open one of three visited is the one changed; it alone is marked, on its row and its card, and the other two are not read', async () => {
  const world: World = { latest: 8, changes: { state: 'named', invariants: [SYNTH_Z] }, stale: NO_WORK, hold: [SYNTH_Z] };
  const { asked, release } = await opened(world, THREE);

  await move(world);
  await waitFor(() => expect(count(asked, cardsOf(9, SYNTH_Z))).toBe(1));
  expect(marked()).toEqual([SYNTH_Z]);
  await waitFor(() => expect(card(SYNTH_Z)?.dataset.stale).toBe('true'));
  expect(card(SYNTH_Z)!.querySelector('[data-testid="review-stale-mark"]')).not.toBeNull();

  await act(async () => release());
  await waitFor(() => expect(marked()).toEqual([]));
  expect(card(SYNTH_Z)?.dataset.stale).toBeUndefined();
  expect(count(asked, cardsOf(9))).toBe(1);
  expect(revealed(SYNTH_0)).toBeGreaterThan(0);
  expect(revealed(L181_SUBJECT)).toBeGreaterThan(0);
  expect(selected()).toBe(SYNTH_Z);
});

it('an empty list marks nothing, reads nothing and renews nothing', async () => {
  const world: World = { latest: 8, changes: { state: 'named', invariants: [] }, stale: NO_WORK };
  const { asked } = await opened(world, THREE);
  const before = asked.length;

  await move(world);
  await act(async () => {});
  expect(asked.slice(before).map((url) => url.pathname)).toEqual(['/api/review/trees/changes']);
  expect(marked()).toEqual([]);
  expect(screen.queryByTestId('review-stale-invariants')).toBeNull();
  for (const key of THREE) expect(revealed(key)).toBeGreaterThan(0);
});

it('a closed changed invariant carries its mark, with no read, until it is opened; then it clears', async () => {
  const world: World = { latest: 8, changes: { state: 'named', invariants: [SYNTH_Z] }, stale: NO_WORK };
  const { asked } = await opened(world, [SYNTH_0, L181_SUBJECT]);

  await move(world);
  await waitFor(() => expect(count(asked, overview(9))).toBe(1));
  await waitFor(() => expect(marked()).toEqual([SYNTH_Z]));
  await act(async () => {});
  expect(count(asked, cardsOf(9))).toBe(0);
  expect(marked()).toEqual([SYNTH_Z]);

  fireEvent.click(row(SYNTH_Z));
  await waitFor(() => expect(count(asked, cardsOf(9, SYNTH_Z))).toBe(1));
  await waitFor(() => expect(marked()).toEqual([]));
  expect(count(asked, cardsOf(9))).toBe(1);
});

it('a changed invariant the newer comparison no longer records closes because it is gone', async () => {
  const world: World = { latest: 8, changes: { state: 'named', invariants: [L181_SUBJECT] }, stale: NO_WORK, gone: L181_SUBJECT };
  const { asked } = await opened(world, [SYNTH_0, L181_SUBJECT]);
  expect(selected()).toBe(L181_SUBJECT);

  await move(world);
  await waitFor(() => expect(count(asked, (url) => url.pathname === '/api/review/intent/entries')).toBe(2));
  await waitFor(() => expect(document.querySelector('[data-testid="review-family-member-open"][aria-current="true"]')).toBeNull());
  expect(document.querySelector(`[data-subject-id="${L181_SUBJECT}"]`)).toBeNull();
  expect(window.location.hash).not.toContain('sel=');
  // Gone, it is no longer counted stale: no detail of it will ever load to clear the mark.
  await waitFor(() => expect(screen.queryByTestId('review-stale-invariants')).toBeNull());
  expect(marked()).toEqual([]);
  // Its neighbour, unchanged, is still revealed without a read.
  expect(revealed(SYNTH_0)).toBeGreaterThan(0);
  expect(count(asked, cardsOf(9, SYNTH_0))).toBe(0);
});

it.each([
  ['an unavailable answer', { state: 'unavailable', reason: 'comparison #9 has no readable overview' } as Changes],
  ['a failed request', 'fail' as Changes],
])('%s renews the page as before: every read again, nothing marked', async (_case, answerOf) => {
  const world: World = { latest: 8, changes: answerOf, stale: NO_WORK };
  const { asked } = await opened(world, THREE);
  const intents = count(asked, intent);

  await move(world);
  await waitFor(() => expect(count(asked, intent)).toBeGreaterThanOrEqual(intents + 1));
  await waitFor(() => expect(count(asked, overview(9))).toBe(1));
  // The open invariant is read again at #9; the visited ones are no longer kept.
  await waitFor(() => expect(count(asked, cardsOf(9, SYNTH_Z))).toBe(1));
  expect(revealed(SYNTH_0)).toBe(0);
  expect(marked()).toEqual([]);
  expect(count(asked, changes)).toBe(1);
});

it("the review's stale invariants are counted from the marks alone, apart from the Explanation's stale parts", async () => {
  // Explainer work exists, so no moved notice renews; the memory watch moves the page instead.
  const world: World = { latest: 8, changes: { state: 'named', invariants: [L181_SUBJECT, SYNTH_0] }, stale: ONE_PART, hold: [L181_SUBJECT, SYNTH_0] };
  const { asked, release } = await opened(world, THREE);
  serverAsked = () => asked;
  await waitFor(() => expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('1'));

  let answerChanges!: () => void;
  world.changesHeld = new Promise((resolve) => (answerChanges = resolve));
  world.latest = 9;
  act(() => FakeSource.latest!.emit('review.memory', { ...PLACE, revision: 'moved:1' }));
  await waitFor(() => expect(count(asked, changes)).toBe(1));
  // #9's overview is read while the move's answer is out; no card is read at #9 before that answer.
  await waitFor(() => expect(count(asked, overview(9))).toBe(1));
  await act(async () => {});
  expect(count(asked, cardsOf(9))).toBe(0);
  await act(async () => answerChanges());
  await waitFor(() => expect(screen.getByTestId('review-stale-invariants').dataset.count).toBe('2'));
  expect(marked().sort()).toEqual([L181_SUBJECT, SYNTH_0].sort());
  expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('1');

  await act(async () => release());
  // Both visited changed ones are read again at #9 and clear; the count follows the marks only.
  await waitFor(() => expect(marked()).toEqual([]));
  expect(screen.queryByTestId('review-stale-invariants')).toBeNull();
  expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('1');
  // This move came with no notice: the open one, unchanged, was carried and not read at #9.
  expect(count(asked, cardsOf(9, SYNTH_Z))).toBe(0);
  expect(count(asked, cardsOf(9))).toBe(2);
});
