// D569: the Review view is drawn from each read once per open, and only from what is on screen.
//
// The page is mounted under React StrictMode, as the dev build mounts it, so every effect is released
// and re-acquired once: a read the remount re-asks must join the request already in flight, never
// send a second one or abort the first. The open review's memory watch arms after the HTTP reads were
// asked; its baseline renews them only when memory changed since the page opened. The Review tab's
// top-bar badge is one `/stale` read; the seat state and the revision list are read only when the
// Explanation tab opens.
import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { resetCardCachesForTests } from '../../data/reviewTrees';
import { connectState } from '../../data/stream';
import { enclosure, seedProjection } from '../detail-panel/test-utils';
import {
  PLACE,
  servePage,
  stubIntersection,
  waitForOpening,
} from './reading/reviewPage.test-utils';
import { publishLeafJudgments } from './reading/useChangeSet';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';

const EXPLAINER = `/api/explainer/${PLACE.repo}/${PLACE.master}/${PLACE.leaf}`;
const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;

class FakeSource {
  static latest: FakeSource | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  closed = false;
  constructor(public url: string) {
    FakeSource.latest = this;
  }
  addEventListener(name: string, callback: (event: MessageEvent) => void) {
    this.listeners.set(name, callback);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown = {}) {
    this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) }));
  }
}

const SAVED = {
  state: 'ok',
  seat: { state: 'live' },
  retired: false,
  retiredAt: null,
  explanation: { revision: 8, at: '2026-10-19T12:36:00Z', entrypoint: 'index.html' },
  stale: {
    total: 1,
    parts: [{ id: 'look', title: 'Where to look first', reason: 'changed in #8' }],
  },
  parts: [{ id: 'look', title: 'Where to look first' }],
  claims: [],
  moved: null,
};
// What `GET .../stale` answers for the same leaf (explainer_routes.py `handle_stale`): no seat.
const STALE = {
  state: 'ok',
  stale: SAVED.stale,
  ...SAVED.stale,
  explanation: SAVED.explanation,
  movedNotice: null,
};
const REVISIONS = { state: 'ok', pinned: null, revisions: [] };

interface Asked {
  url: URL;
  // The request was cancelled before it answered (an abort after the answer cancels nothing).
  abortedInFlight: boolean;
}

let dispose: (() => void) | undefined;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
  resetCardCachesForTests();
  publishLeafJudgments(PLACE.repo, PLACE.master, PLACE.leaf, [], []);
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('priority');
});

afterEach(() => {
  cleanup();
  dispose?.();
  dispose = undefined;
  publishLeafJudgments(PLACE.repo, PLACE.master, PLACE.leaf, [], []);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// The page and its explainer routes, recording every request and whether it was cancelled in flight.
// `stale` is the badge's answer, and `held` keeps it until the test releases it.
function serve(options: { stale?: unknown; held?: Promise<void> } = {}): Asked[] {
  servePage();
  const page = globalThis.fetch;
  const asked: Asked[] = [];
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      const record: Asked = { url, abortedInFlight: false };
      asked.push(record);
      let answered = false;
      init?.signal?.addEventListener('abort', () => {
        if (!answered) record.abortedInFlight = true;
      });
      const answer = (() => {
        if (url.pathname === `${EXPLAINER}/stale`)
          return (options.held ?? Promise.resolve()).then(() => json(options.stale ?? STALE));
        if (url.pathname === `${EXPLAINER}/state`) return json(SAVED);
        if (url.pathname === `${EXPLAINER}/revisions`) return json(REVISIONS);
        return page(address, init);
      })();
      // On the wire for at least one tick, as any request is.
      const response = await answer;
      answered = true;
      return response;
    }),
  );
  return asked;
}

// A live leaf, so the open review arms its memory watch on the one state stream.
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

const count = (asked: readonly Asked[], test: (url: URL) => boolean) =>
  asked.filter(({ url }) => test(url)).length;
const intent = (url: URL) => url.pathname === '/api/review/intent';
const entries = (url: URL) => url.pathname === '/api/review/intent/entries';
const summary = (url: URL) => url.pathname === '/api/review/intent/summary';
const overview = (url: URL) =>
  url.pathname === '/api/review/trees' && url.searchParams.get('view') === 'overview';
const lane = (url: URL) =>
  url.pathname === '/api/review/trees' && url.searchParams.get('lane') === 'files';
const explainer = (route: string) => (url: URL) => url.pathname === `${EXPLAINER}/${route}`;
const aborted = (asked: readonly Asked[]) =>
  asked.filter((read) => read.abortedInFlight).map(({ url }) => `${url.pathname}${url.search}`);

function renderStrict() {
  return render(
    <StrictMode>
      <ReviewSurface {...PLACE} onBack={() => undefined} />
    </StrictMode>,
  );
}

// The open review's arming baseline: the server names how long ago its memory last changed.
async function arm(changedAgoMs: number) {
  const source = FakeSource.latest!;
  expect(new URL(source.url, 'http://localhost').searchParams.get('reviewLeaf')).toBe(PLACE.leaf);
  act(() => {
    source.emit('open');
    source.emit('review.memory', { ...PLACE, revision: 'armed:0', changedAgoMs });
  });
  await act(async () => {});
}

async function drawn() {
  await waitForOpening();
  await waitFor(() =>
    expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('1'),
  );
}

it('opens the Review tab under StrictMode with each read once, nothing aborted, and no seat read', async () => {
  stubIntersection();
  const asked = serve();
  openLive();
  renderStrict();
  await drawn();
  // Memory last changed long before this page opened: the HTTP answers already hold it.
  await arm(10 * 60_000);
  await act(async () => {});

  expect(count(asked, intent)).toBe(1);
  expect(count(asked, entries)).toBe(1);
  expect(count(asked, summary)).toBe(1);
  expect(count(asked, overview)).toBe(1);
  expect(count(asked, lane)).toBe(1);
  expect(count(asked, explainer('stale'))).toBe(1);
  expect(count(asked, explainer('state'))).toBe(0);
  expect(count(asked, explainer('revisions'))).toBe(0);
  expect(aborted(asked)).toEqual([]);
});

it("marks the Review tab's Explanation label retired and building from the badge, with no seat read", async () => {
  stubIntersection();
  const asked = serve({
    stale: { ...STALE, retired: true, building: { state: 'building', step: 'environment' } },
  });
  renderStrict();
  await waitForOpening();
  const label = screen.getByTestId('switch-explanation');
  await waitFor(() => expect(label.getAttribute('data-retired')).toBe('true'));
  expect(label.getAttribute('data-building')).toBe('true');
  expect(label.textContent).toContain('◌');
  expect(label.textContent).toContain('retired');
  expect(count(asked, explainer('stale'))).toBe(1);
  expect(count(asked, explainer('state'))).toBe(0);
});

it('renews the opening reads once when memory changed after the page opened, before arming', async () => {
  stubIntersection();
  const asked = serve();
  openLive();
  renderStrict();
  await drawn();
  expect(count(asked, intent)).toBe(1);
  // An edit after the page asked its answers but before the watch armed: no native notification
  // reported it, so the baseline's age is what catches it.
  await arm(0);
  await waitFor(() => expect(count(asked, intent)).toBe(2));
  // A duplicate baseline of the same connection renews nothing more.
  act(() => FakeSource.latest!.emit('review.memory', { ...PLACE, revision: 'armed:0' }));
  await act(async () => {});
  expect(count(asked, intent)).toBe(2);
  expect(count(asked, explainer('state'))).toBe(0);
});

it('reads the full explainer state and the revisions once when the Explanation tab opens', async () => {
  stubIntersection();
  const asked = serve();
  openLive();
  renderStrict();
  await drawn();
  await arm(10 * 60_000);

  fireEvent.click(screen.getByTestId('switch-explanation'));
  expect(await screen.findByTestId('explanation-view')).toBeTruthy();
  await waitFor(() => expect(count(asked, explainer('revisions'))).toBe(1));
  await act(async () => {});
  expect(count(asked, explainer('state'))).toBe(1);
  expect(count(asked, explainer('stale'))).toBe(1);
  const state = asked.find(({ url }) => explainer('state')(url))!.url.searchParams;
  const stale = asked.find(({ url }) => explainer('stale')(url))!.url.searchParams;
  expect(state.get('comparisonNumber')).toBe(stale.get('comparisonNumber'));

  // Back to Review and again to Explanation: the opened tab's reads are kept, not asked again.
  fireEvent.click(screen.getByTestId('switch-review'));
  fireEvent.click(screen.getByTestId('switch-explanation'));
  await act(async () => {});
  expect(count(asked, explainer('state'))).toBe(1);
  expect(count(asked, explainer('revisions'))).toBe(1);
  expect(count(asked, intent)).toBe(1);
  expect(aborted(asked)).toEqual([]);
});

// The stream replays the leaf's latest moved notice as soon as it opens -- before the badge's facts
// answered. Whether it renews the deterministic reads depends on those facts, so it waits for them.
const NOTICE = {
  kind: 'comparison-moved',
  from: 7,
  to: 8,
  blocksChanged: 3,
  updateOffered: true,
  text: 'task moved: #7 -> #8, 3 blocks changed',
};

async function replayMovedBeforeTheBadge(stale: unknown) {
  stubIntersection();
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const asked = serve({ stale, held });
  openLive();
  renderStrict();
  await waitForOpening();
  await waitFor(() => expect(count(asked, explainer('stale'))).toBe(1));
  act(() => FakeSource.latest!.emit('review.explainer.moved', { ...PLACE, ...NOTICE }));
  await act(async () => {});
  expect(count(asked, intent)).toBe(1);
  await act(async () => release());
  await act(async () => {});
  return asked;
}

it('does not renew the opening reads for a replayed moved notice when explainer work exists', async () => {
  // The host's `/stale` answer carries the same notice the stream replayed.
  const asked = await replayMovedBeforeTheBadge({ ...STALE, movedNotice: NOTICE });
  await waitFor(() =>
    expect(screen.getByTestId('explanation-stale-badge').getAttribute('data-newer')).toBe('true'),
  );
  expect(count(asked, intent)).toBe(1);
  expect(count(asked, overview)).toBe(1);
  expect(aborted(asked)).toEqual([]);
});

it('renews the opening reads once for that notice when the facts say no explainer work exists', async () => {
  const none = {
    state: 'ok',
    stale: { parts: [] },
    parts: [],
    explanation: null,
    movedNotice: null,
  };
  const asked = await replayMovedBeforeTheBadge(none);
  await waitFor(() => expect(count(asked, intent)).toBe(2));
  await waitFor(() => expect(count(asked, overview)).toBe(2));
});
