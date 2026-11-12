// The current native partial-family scenario, not the former synthetic chosen-family adapter.
// The after family does not parse: the catalogue outline and roster are unavailable, but the
// selected invariant's own expressions remain readable. Marker reasons/focus/return are preserved
// without inventing a family occurrence or claiming current family absence. All answers are untouched
// current public-route captures (markerUnknown.capture-provenance.json).
// j after a held return is exercised on complete comparison 2 in ReviewSurface.markers.test.tsx;
// an unavailable outline cannot honestly promise a next changed member.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { ReviewSurface } from './ReviewSurface';
import { treeViewStore } from './triageViewPreference';
import { treeOrderStore } from './triageOrderPreference';

const captured = <T,>(name: string): T => JSON.parse(
  readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
) as T;
const task = captured<ReviewResult>('markerUnknown.task.captured.json');
const entries = captured<unknown>('markerUnknown.entries.captured.json');
const trees = captured<unknown>('markerUnknown.trees.captured.json');
const lane = captured<unknown>('markerUnknown.lane.captured.json');
const file = captured<{ file_classification: ReviewFileClassification }>(
  'markerUnknown.file.captured.json',
);
const source = captured<unknown>('markerUnknown.source.captured.json');
const memberUnknown = captured<ReviewResult>('markerUnknown.memberUnknown.captured.json');
const cards = captured<unknown>('markerUnknown.cards.captured.json');
const NOTES = 'mcp/src/agents_remember/serving/notes.py';
const occurrence = file.file_classification.hunks[0].links.find(
  (link) => link.invariant === 'INV-Z66EMHMH' && link.side === 'before',
)!;

function answer(url: URL): unknown {
  if (url.pathname.endsWith('/entries')) return entries;
  if (url.pathname.endsWith('/source-content')) return source;
  if (url.pathname.endsWith('/trees')) {
    if (url.searchParams.get('lane')) return lane;
    if (url.searchParams.get('invariants')) return cards;
    return url.searchParams.get('file') === NOTES ? file : trees;
  }
  const selector = url.searchParams.get('selectorId');
  if (selector === null) return task;
  return selector === occurrence.invariant_key ? memberUnknown : undefined;
}

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('priority');
  treeViewStore.getState().setView('all');
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    const body = answer(url);
    if (body === undefined) throw new Error(`unexpected request ${address}`);
    return { ok: true, status: 200, json: async () => body } as Response;
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function open() {
  const payload = task.payload!;
  return render(<ReviewSurface
    repo={payload.candidate.repository_id}
    master={payload.candidate.master}
    leaf={payload.candidate.leaf_id}
    selectorKind="invariant"
    selectorId={occurrence.invariant_key}
    onBack={() => undefined}
  />);
}

async function toUnknownTarget(view: ReturnType<typeof open>) {
  const card = await waitFor(() => {
    const found = view.queryAllByTestId('review-expression-card').find(
      (node) => node.dataset.entries === 'RLZ-7X2C4VRQ',
    );
    expect(found).toBeTruthy();
    return found!;
  });
  // The card's excerpt draws the hunk's mark (a card has no Full file of its own, MIK-R89 v2 rule 4).
  const mark = await waitFor(() => {
    const found = within(card).queryAllByTestId('review-hunk-mark').find(
      (node) => node.dataset.hunk === '124:1:124:1',
    );
    expect(found).toBeTruthy();
    return found!;
  });
  fireEvent.click(mark);
  fireEvent.click(within(view.getByTestId('review-hunk-mark-panel')).getByRole('button', {
    name: 'FAM-4V4GSQCS r1 · membership unknown (before, r1)',
  }));
  const rail = await view.findByTestId('review-rail-target-state');
  await waitFor(() => expect(document.activeElement).toBe(rail));
  return rail;
}

it('states the native unknown reason once per focus/reading zone without inventing a family occurrence', async () => {
  const view = open();
  const rail = await toUnknownTarget(view);
  const center = view.getByTestId('review-center-target-state');
  for (const zone of [rail, center]) {
    expect(within(zone).getAllByText('Attribution unknown', { exact: true })).toHaveLength(1);
    expect(zone.textContent).toContain(occurrence.families[0].reason);
    expect(zone.closest('details')).toBeNull();
  }
  expect(rail.dataset.familyState).toBe('partial');
  expect(center.dataset.memberState).toBe('membership_unknown');
  const row = view.container.querySelector<HTMLElement>(
    '[data-tree-node="member"][aria-current="true"]',
  );
  expect(row).toBeTruthy();
  // The real catalogue can focus a generic invariant, not the unavailable named family occurrence.
  expect(row!.dataset.family).not.toBe(occurrence.families[0].family_key);
  expect(row!.textContent).not.toMatch(/Attribution unknown|membership unknown/);
  expect(document.activeElement).toBe(rail);
  expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
  expect(view.getByTestId('review-surface').dataset.markerReturn).toBe('open');
  fireEvent.click(view.getByTestId('review-marker-return'));
  await waitFor(() => expect(document.activeElement?.getAttribute('data-hunk')).toBe('124:1:124:1'));
  expect(view.getByTestId('review-surface').dataset.markerReturn).toBeUndefined();
});

it('does not steal focus from a held marker return when its outline is unavailable', async () => {
  const view = open();
  await toUnknownTarget(view);
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  fireEvent.click(view.getByTestId('review-marker-return'));
  const held = await waitFor(async () => {
    await act(async () => { vi.advanceTimersToNextFrame(); });
    const active = document.activeElement as HTMLElement;
    expect(active.dataset.testid).toBe('review-hunk-mark');
    expect(active.dataset.hunk).toBe('124:1:124:1');
    return active;
  });
  await act(async () => { vi.advanceTimersToNextFrame(); });
  expect(document.activeElement).toBe(held);
  expect(held.getAttribute('aria-expanded')).toBe('true');
  expect(view.queryByTestId('review-rail-target-state')).toBeNull();
  expect(view.queryByTestId('review-marker-return')).toBeNull();
});
