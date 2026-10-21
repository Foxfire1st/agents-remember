// MIK-R89 v2 rules 3, 11 and 13 and MIK-R86 v2 on the review page over L181 #8
// (reviewPage.test-utils.tsx): the tree and the reading column show one Memory currentness order, by
// each subject's shown state (e9b9d515); the region three entries share is one tree row, one card and
// one `j`/`k` stop, which a URL naming any of the three selects; and `k` from a subject without
// changed blocks moves to the last block above it.
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ReviewTreesResult } from '../../../data/reviewTrees';
import { blockIdentityOf, formatSelection } from '../blockIdentity';
import { l181Answer } from '../changeSetOrder.test-utils';
import { treeOrderStore } from '../triageOrderPreference';
import { treeViewStore } from '../triageViewPreference';
import { L181_SUBJECT, PLACE, renderPage, servePage, stubIntersection } from './reviewPage.test-utils';

const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;
// Captured: RLZ-EC5NRM, RLZ-ZA95M4 and RLZ-ZT2RRK all name turn_ended.ts L750–795 → L1697–1817.
const TRIPLE = 'RLZ-EC5NRM,RLZ-ZA95M4,RLZ-ZT2RRK';

function reset() {
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('priority');
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
  reset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  reset();
});

const treeOrder = () => screen.getAllByTestId('review-family-member-open').map((row) => row.dataset.subjectId);
const columnOrder = () => screen.getAllByTestId('review-subject-section').map((section) => section.dataset.subject);
const cards = () => screen.getAllByTestId('review-expression-card');
const selectedCards = () => cards().filter((card) => card.dataset.selected === 'true');

async function opened() {
  await waitFor(() => expect(cards().length).toBeGreaterThan(0));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
}

// Read 4 (the leaf-wide trees answer) with a synthetic currentness (call after `servePage`): the
// synthetic INV-0SYNTH, which has no entries, is unrealized; INV-1ZKP4R is current. The captured
// entries are current on every recorded side.
function serveCurrentness() {
  const served = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const response = await served(address, init);
      const url = new URL(String(address), 'http://localhost');
      const leafWide = url.pathname === '/api/review/trees' && !['invariants', 'lane', 'file'].some((name) => url.searchParams.has(name));
      if (!leafWide) return response;
      const body = (await response.json()) as ReviewTreesResult;
      body.currentness = {
        after: {
          invariants: [
            { id: 'INV-0SYNTH', state: 'unrealized', entries: [] },
            { id: 'INV-1ZKP4R', state: 'current', entries: [] },
          ],
        },
      } as ReviewTreesResult['currentness'];
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
}

describe('Memory currentness (e9b9d515)', () => {
  it('puts an unrealized subject without entries before current entries, in the tree and the column', async () => {
    stubIntersection();
    treeViewStore.getState().setView('all');
    treeOrderStore.getState().setOrder('currentness');
    servePage();
    serveCurrentness();
    renderPage();
    await opened();
    await waitFor(() => expect(columnOrder()).toEqual(['synthetic-unchanged-0', L181_SUBJECT, 'synthetic-unchanged-z']));
    expect(treeOrder()).toEqual(columnOrder());
    expect(screen.getAllByTestId('review-family-member-open').map((row) => row.getAttribute('aria-label')?.split(' ').at(-1))).toEqual([
      'unrealized',
      'current',
      'measured',
    ]);
  });
});

describe('the region three entries share', () => {
  it('is one tree row and one card, which a URL naming any of its entries selects', async () => {
    stubIntersection();
    const entry = (l181Answer.entries ?? []).find((one) => one.id === 'RLZ-ZA95M4')!;
    const block = blockIdentityOf(entry, l181Answer.comparison!);
    window.history.replaceState(null, '', `/${HASH}&sel=${encodeURIComponent(formatSelection({ kind: 'block', block }))}`);
    servePage();
    renderPage();
    await opened();
    expect(cards()).toHaveLength(13);
    expect(screen.getAllByTestId('review-tree-location')).toHaveLength(13);
    await waitFor(() => expect(selectedCards().map((card) => card.dataset.entries)).toEqual([TRIPLE]));
    const current = screen.getAllByTestId('review-tree-location').filter((row) => row.getAttribute('aria-current') === 'true');
    expect(current.map((row) => row.dataset.entries)).toEqual([TRIPLE]);
    expect(screen.queryByTestId('review-selection-outside')).toBeNull();
  });

  it('is one j/k stop', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    const scroller = document.querySelector<HTMLElement>('[data-review-scroller]')!;
    fireEvent.keyDown(scroller, { key: 'j', code: 'KeyJ' });
    await waitFor(() => expect(selectedCards().map((card) => card.dataset.entries)).toEqual([TRIPLE]));
    fireEvent.keyDown(scroller, { key: 'j', code: 'KeyJ' });
    await waitFor(() => expect(selectedCards().map((card) => card.dataset.entries)).toEqual([cards()[1].dataset.entries]));
    expect(cards()[1].dataset.entries).toBe('RLZ-78JFRR');
  });
});

describe('the keys from a subject without changed blocks', () => {
  it('moves with k to the last block above it, not to the first block of the page', async () => {
    stubIntersection();
    treeViewStore.getState().setView('all');
    servePage();
    renderPage();
    await opened();
    // All: INV-1ZKP4R with its blocks, then the synthetic INV-0SYNTH, which has none.
    const row = screen.getAllByTestId('review-family-member-open').find((one) => one.dataset.subjectId === 'synthetic-unchanged-0')!;
    fireEvent.click(row);
    await waitFor(() => expect(row.getAttribute('aria-current')).toBe('true'));
    fireEvent.keyDown(document.querySelector<HTMLElement>('[data-review-scroller]')!, { key: 'k', code: 'KeyK' });
    await waitFor(() => expect(selectedCards()).toEqual([cards().at(-1)]));
  });
});
