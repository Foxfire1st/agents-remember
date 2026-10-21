// Member and family selections through the production page (MIK-R89 v2 rule 2, W4; L81-R1-F18's
// successor): the cards are the opening's read 5, so a selection -- a member of the displayed
// family, or another family -- reads its subject and no cards, through pending and answer; and the
// old subject's reading stays out from under the new heading while the new subject is pending
// (INV-8VQR13WX). Only `fetch` is stubbed, with deferred subject answers so request order is
// observable.
import { fireEvent, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import type { ReviewResult } from '../../data/review';
import { syntheticCatalogue } from './outline.test-utils';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';
import {
  CHANGES,
  HBJ,
  R6R,
  bodyOf,
  captured,
  open,
  selectedNode,
  subjectOf,
  type View,
} from './walk.test-utils';

const entries = captured<unknown>('walkReal.entries.captured.json');
const task = captured<ReviewResult>('walkReal.task.captured.json');
const bodies = new Map<string, ReviewResult>(
  [R6R, HBJ, ...CHANGES.map(bodyOf)].map((body) => [subjectOf(body), body]),
);
const R6R_FAMILY = `family:${R6R.payload!.family_context!.entries[0].family_id}`;
const HBJ_FAMILY = `family:${HBJ.payload!.family_context!.entries[0].family_id}`;

const REFUSED = {
  state: 'refused',
  refusal: { code: 'not-found', detail: 'not part of this check', next_action: 'open' },
};
const ok = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => structuredClone(body) }) as Response;
// The card read's answer: the captured leaf's comparison (its four trees name the blocks), no entries.
const comparison = captured<{ comparison: Record<string, unknown> }>('walkReal.trees.captured.json').comparison;
const cardsBody = (marker: string) => ({
  state: 'trees',
  repository_id: 'r',
  master: 'm',
  leaf_id: 'l',
  knowledge_sides: [],
  comparison: { ...comparison, marker },
  entries: [],
});

const requested = (url: URL) =>
  url.searchParams.get('selectorId') === null
    ? 'task-context'
    : `${url.searchParams.get('selectorKind')}:${url.searchParams.get('selectorId')}`;

interface Mounted {
  view: View;
  intentCalls: string[];
  cardsCalls: string[];
  release: (asked: string) => void;
}

// The R6R family settled on screen: one subject request, one cards request.
async function mountSettledR6R(): Promise<Mounted> {
  localStorage.clear();
  treeOrderStore.getState().setOrder('priority');
  treeViewStore.getState().setView('all');
  const intentCalls: string[] = [];
  const cardsCalls: string[] = [];
  const gates = new Map<string, (body?: unknown) => void>();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      if (url.pathname === '/api/review/intent') {
        const asked = requested(url);
        intentCalls.push(asked);
        const body = asked === 'task-context' ? task : bodies.get(asked);
        if (body === undefined) throw new Error(`Unexpected subject: ${asked}`);
        return new Promise<Response>((resolve) => {
          gates.set(asked, (override?: unknown) => resolve(ok(override ?? body)));
        });
      }
      if (url.pathname === '/api/review/trees') {
        const invariants = url.searchParams.get('invariants');
        if (invariants) {
          cardsCalls.push(invariants);
          return ok(cardsBody(`cards:${invariants}`));
        }
        return ok(REFUSED);
      }
      if (url.pathname === '/api/review/intent/entries')
        return ok({ ...(entries as object), ...syntheticCatalogue([R6R, HBJ]) });
      throw new Error(`Unexpected path: ${url.pathname}`);
    }),
  );
  const view = open(R6R);
  await waitFor(() => expect(gates.has(R6R_FAMILY)).toBe(true));
  gates.get(R6R_FAMILY)!();
  await waitFor(() => {
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
    expect(selectedNode(view)).not.toBeNull();
  });
  // The opening's read 5 has answered.
  await waitFor(() => expect(cardsCalls).toHaveLength(1));
  return {
    view,
    intentCalls,
    cardsCalls,
    release: (asked: string) => gates.get(asked)!(),
  };
}

const memberOpen = (view: View, subjectId: string) =>
  view
    .getAllByTestId('review-family-member-open')
    .find((row) => (row as HTMLElement).dataset.subjectId === subjectId)!;

afterEach(() => {
  resetCardCachesForTests();
  vi.unstubAllGlobals();
});

const slotState = (view: View) => view.getByTestId('review-subject-slot').dataset.slotState;

it('a member of the displayed family reads its subject and no cards', async () => {
  const { view, intentCalls, cardsCalls, release } = await mountSettledR6R();
  expect(intentCalls).toEqual([R6R_FAMILY]);
  expect(cardsCalls).toHaveLength(1);

  // INV-2E8MG43K was never selected: its subject resolves next, through pending.
  const member = 'invariant:97c94e1f-38e1-51db-ade2-f7bc3438024b';
  fireEvent.click(memberOpen(view, '97c94e1f-38e1-51db-ade2-f7bc3438024b'));
  await waitFor(() => expect(view.queryByTestId('review-reading-pending')).not.toBeNull());
  expect(intentCalls).toEqual([R6R_FAMILY, member]);
  expect(cardsCalls).toHaveLength(1);

  // The old subject stays out from under the new heading while it pends: one slot, pending.
  const pending = view.getByTestId('review-reading-pending');
  expect(pending.dataset.pendingSubject).toBe(member);
  expect(view.getAllByTestId('review-subject-slot')).toHaveLength(1);
  expect(slotState(view)).toBe('pending');
  expect(view.queryByTestId('review-center-family')).toBeNull();

  release(member);
  await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
  await waitFor(() => expect(slotState(view)).toBe('answered'));
  expect(cardsCalls).toHaveLength(1);
});

it('another family reads its subject and no cards either', async () => {
  const { view, cardsCalls, release } = await mountSettledR6R();
  // The shared member's review displays both families.
  const shared = 'invariant:09b76b4c-9d5e-50a2-8a4d-de1485e8911c';
  fireEvent.click(memberOpen(view, '09b76b4c-9d5e-50a2-8a4d-de1485e8911c'));
  await waitFor(() => expect(view.queryByTestId('review-reading-pending')).not.toBeNull());
  release(shared);
  await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
  const families = view.queryAllByTestId('review-family').map((block) => (block as HTMLElement).dataset.family);
  const hbjFamilyId = HBJ.payload!.family_context!.entries[0].family_id;
  expect(families).toContain(hbjFamilyId);

  const hbjRow = view
    .getAllByTestId('review-family-open')
    .find((row) => (row as HTMLElement).dataset.family === hbjFamilyId)!;
  fireEvent.click(hbjRow);
  await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBe(HBJ_FAMILY));
  expect(cardsCalls).toHaveLength(1);
  release(HBJ_FAMILY);
  await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
  await waitFor(() => expect(slotState(view)).toBe('answered'));
  expect(cardsCalls).toHaveLength(1);
});
