// L81-R1-F18 mounted proof: member clicks through the production workspace.
//
// The same three cases as the hook-level tests, driven through the real
// ReviewSurface member selection instead of the hook directly: an uncached member
// of the already displayed family with an unchanged invariant set sends no second
// cards request through pending or answer, a changed set's cards start before its
// subject resolves, and the old subject stays out from under the new labels while
// the new subject is pending. Only `fetch` is stubbed, with deferred subject
// answers so request order is observable.
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
const cardsBody = (marker: string) => ({
  state: 'trees',
  repository_id: 'r',
  master: 'm',
  leaf_id: 'l',
  knowledge_sides: [],
  comparison: {
    schema: 'ar-review-tree-comparison/v1',
    task_id: 't',
    leaf_id: 'l',
    number: 1,
    marker,
  },
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
  treeOrderStore.getState().setOrder('triage');
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
  await waitFor(() =>
    expect(view.queryByTestId('review-expression-cards')?.dataset.cardsState).toBe('ready'),
  );
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

it('an uncached member of the displayed family sends no second cards request', async () => {
  const { view, intentCalls, cardsCalls, release } = await mountSettledR6R();
  expect(intentCalls).toEqual([R6R_FAMILY]);
  expect(cardsCalls).toHaveLength(1);

  // INV-2E8MG43K was never selected: its subject resolves next, through pending.
  const member = 'invariant:97c94e1f-38e1-51db-ade2-f7bc3438024b';
  fireEvent.click(memberOpen(view, '97c94e1f-38e1-51db-ade2-f7bc3438024b'));
  await waitFor(() =>
    expect(view.queryByTestId('review-reading-pending')).not.toBeNull(),
  );
  expect(intentCalls).toEqual([R6R_FAMILY, member]);
  // The set is unchanged, so the kept answer stands: still no second cards request.
  expect(cardsCalls).toHaveLength(1);

  // The old subject stays out from under the new labels while it pends.
  const pending = view.getByTestId('review-reading-pending');
  expect(pending.dataset.pendingSubject).toBe(member);
  expect(view.queryByTestId('review-expression-cards')).toBeNull();
  expect(view.queryByTestId('review-center-expressions')).toBeNull();

  release(member);
  await waitFor(() => {
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  });
  await waitFor(() =>
    expect(view.queryByTestId('review-expression-cards')?.dataset.cardsState).toBe('ready'),
  );
  // Through the answer too: the remount reused the kept answer, one request total.
  expect(cardsCalls).toHaveLength(1);
});

it("a changed set's cards start before its subject resolves", async () => {
  const { view, cardsCalls, release } = await mountSettledR6R();
  const settledCards = cardsCalls.length;

  // The shared member's review displays both families; its set is the family's own.
  const shared = 'invariant:09b76b4c-9d5e-50a2-8a4d-de1485e8911c';
  fireEvent.click(memberOpen(view, '09b76b4c-9d5e-50a2-8a4d-de1485e8911c'));
  await waitFor(() =>
    expect(view.queryByTestId('review-reading-pending')).not.toBeNull(),
  );
  release(shared);
  await waitFor(() => {
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  });
  const families = view
    .queryAllByTestId('review-family')
    .map((block) => (block as HTMLElement).dataset.family);
  expect(families).toContain(HBJ.payload!.family_context!.entries[0].family_id);

  // Selecting the other family's row changes the set: its cards start now, while the
  // new family subject is still pending -- not after it resolves.
  const before = cardsCalls.length;
  const hbjFamilyId = HBJ.payload!.family_context!.entries[0].family_id;
  const hbjRow = view
    .getAllByTestId('review-family-open')
    .find((row) => (row as HTMLElement).dataset.family === hbjFamilyId)!;
  fireEvent.click(hbjRow);
  await waitFor(() => expect(cardsCalls.length).toBeGreaterThan(before));
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBe(HBJ_FAMILY);

  release(HBJ_FAMILY);
  await waitFor(() => {
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  });
  await waitFor(() =>
    expect(view.queryByTestId('review-expression-cards')?.dataset.cardsState).toBe('ready'),
  );
  // The hook mounted after the subject answer and reused the prefetched flight.
  expect(cardsCalls.length).toBe(before + 1);
  expect(settledCards).toBe(1);
});
