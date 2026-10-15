// L81-R1-F18 mounted proof: production clicks through the real workspace drive the
// cards read. A family click on an unwalked family starts no cards read (the set is not
// nameable until its subject answers); the center's hook reads it on mount. A member
// click then reuses the flight with no second request. A changed-set click on a walked
// family starts its own read at once, before the member subject resolves, and the late
// first answer neither refetches nor replaces what the reader settled on.
import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  HBJ_ID,
  R6R,
  R6R_ID,
  familyBlock,
  installWorld,
  open,
} from './walk.test-utils';

installWorld();

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

const treesBody = (marker: string, entries: unknown[]) => ({
  state: 'trees',
  repository_id: 'agents-remember',
  master: 'review-navigation',
  leaf_id: 'review-navigation',
  knowledge_sides: [],
  comparison: {
    schema: 'ar-review-tree-comparison/v1',
    task_id: 't',
    leaf_id: 'l',
    number: 1,
    marker,
  },
  entries,
});

const oneEntry = {
  id: 'h1',
  kind: 'realization',
  invariant: 'INV-H',
  invariant_key: 'INV-H',
  before: { state: 'absent', path: 'h/before.py' },
  after: { state: 'resolved', path: 'h/after.py' },
  change: 'changed',
};

interface Gate {
  url: URL;
  kind: 'trees' | 'intent';
  shadow?: Promise<Response>;
  settle: (response: Response) => void;
}

// Hold every cards/intent reply behind a gate; everything else passes through.
// While `fail.first` is set the first cards set answers transport failure (never kept).
function holdReads(fail: { first: boolean }) {
  const inner = globalThis.fetch as typeof fetch;
  const treesCalls: string[] = [];
  const gates: Gate[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((address: string) => {
      const url = new URL(address, 'http://localhost');
      if (url.pathname === '/api/review/trees') {
        const invariants = url.searchParams.get('invariants') ?? '';
        // Comparison reads without a set pass through; only the cards sets hold.
        if (invariants === '') return inner(address);
        treesCalls.push(invariants);
        if (fail.first) {
          fail.first = false;
          return Promise.reject(new TypeError('fetch failed'));
        }
        return new Promise<Response>((settle) => gates.push({ url, kind: 'trees', settle }));
      }
      if (url.pathname === '/api/review/intent') {
        const shadow = inner(address);
        return new Promise<Response>((settle) =>
          gates.push({ url, kind: 'intent', shadow, settle }),
        );
      }
      return inner(address);
    }),
  );
  return { treesCalls, gates };
}

const releaseTrees = (gates: Gate[], invariants: string, body: unknown) =>
  act(async () => {
    const index = gates.findIndex(
      (gate) =>
        gate.kind === 'trees' && gate.url.searchParams.get('invariants') === invariants,
    );
    expect(index).toBeGreaterThanOrEqual(0);
    const [gate] = gates.splice(index, 1);
    gate.settle(ok(body));
  });

// Release the held intent whose selector names `wanted`.
const releaseIntent = (gates: Gate[], wanted: string) =>
  act(async () => {
    const index = gates.findIndex(
      (gate) =>
        gate.kind === 'intent' &&
        (gate.url.searchParams.get('selectorId') ?? '').includes(wanted),
    );
    expect(index).toBeGreaterThanOrEqual(0);
    const [gate] = gates.splice(index, 1);
    gate.settle(await gate.shadow!);
  });

const cardsState = (view: ReturnType<typeof render>) =>
  view.getByTestId('review-expression-cards').dataset.cardsState;

const cardCount = (view: ReturnType<typeof render>) =>
  view.getByTestId('review-expression-cards').dataset.cardCount;

describe('mounted member clicks drive the shared cards read', () => {
  it('starts the changed set before the first subject resolves, with one request per set', async () => {
    const fail = { first: true };
    const { treesCalls, gates } = holdReads(fail);
    const view = open(R6R);
    // The subject settles first so the center mounts; its first set fails in
    // transport and stays uncached.
    await waitFor(() => expect(gates.filter((gate) => gate.kind === 'intent').length).toBeGreaterThan(0));
    await act(async () => {
      for (const gate of gates.splice(0, gates.length)) {
        if (gate.kind === 'intent') gate.settle(await gate.shadow!);
      }
    });
    await waitFor(() => expect(treesCalls).toHaveLength(1));
    const rSet = treesCalls[0];
    await waitFor(() => {
      expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
    });

    // A family click on an unwalked family starts no cards read: the set is not
    // nameable until its subject answers. The subject request starts now.
    fireEvent.click(within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open'));
    // The click handler prefetches synchronously: no cards read starts for the
    // unwalked family, and flushing settles nothing else into flight.
    await act(async () => {});
    expect(treesCalls).toEqual([rSet]);
    // The family's subject resolves; the center mounts and its hook reads the set.
    await releaseIntent(gates, HBJ_ID);
    await waitFor(() => expect(treesCalls).toHaveLength(2));
    const hSet = treesCalls[1];
    expect(hSet.length).toBeGreaterThan(0);
    expect(hSet).not.toBe(rSet);
    await waitFor(() =>
      expect(view.getByTestId('review-center-family').dataset.family).toBe(HBJ_ID),
    );
    // H's cards stay held: the late answer must not disturb what settles next.
    const member = within(view.getByTestId('review-center-family')).getAllByTestId(
      'review-center-open-member',
    )[0];
    fireEvent.click(member);
    // The same set reuses the flight: no second request for the member click.
    await act(async () => {});
    expect(treesCalls).toEqual([rSet, hSet]);

    // The changed set starts at once, before the member subject resolves. The R6R
    // family needs no subject request: its admitted answer is kept and reused.
    fireEvent.click(within(familyBlock(view, R6R_ID)).getByTestId('review-family-open'));
    await waitFor(() => expect(treesCalls).toHaveLength(3));
    expect(treesCalls[2]).toBe(rSet);

    // The changed set settles: ready cards, no pending, selected R6R.
    await releaseTrees(gates, rSet, treesBody('R-cards', []));
    await waitFor(() => {
      expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
      expect(cardsState(view)).toBe('ready');
    });
    expect(cardCount(view)).toBe('0');

    // The late first answer lands without a refetch and without replacing the cards.
    // The member subject still held is the only intent left; release it, then H's cards.
    await act(async () => {
      for (const gate of gates.splice(0, gates.length)) {
        if (gate.kind === 'intent') gate.settle(await gate.shadow!);
        else gates.push(gate);
      }
    });
    const remaining = gates.filter((gate) => gate.kind === 'trees');
    expect(remaining).toHaveLength(1);
    await act(async () => {
      remaining[0].settle(ok(treesBody('H-cards-late', [oneEntry])));
    });
    gates.splice(gates.indexOf(remaining[0]), 1);
    // The late answer is fully processed inside the act above: no refetch fired
    // and the settled cards stand.
    await act(async () => {});
    expect(treesCalls).toEqual([rSet, hSet, rSet]);
    expect(cardsState(view)).toBe('ready');
    expect(cardCount(view)).toBe('0');
  });
});
