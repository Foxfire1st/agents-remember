// The page's one card read through production clicks (MIK-R89 v2 rule 2, W4; L81-R1-F18's
// successor). The cards are the opening's read 5: family clicks, member clicks in the centre and a
// return to the first family read their subjects and never a card set. A card read that fails in
// transport says so on the subjects (it is not kept) and is not asked again by any selection; a
// remount asks again (data/reviewTrees.cards.test.ts).
import { act, fireEvent, waitFor, within } from '@testing-library/react';
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

// Release every held subject read.
const releaseAll = (gates: Gate[]) =>
  act(async () => {
    for (const gate of gates.splice(0, gates.length)) if (gate.kind === 'intent') gate.settle(await gate.shadow!);
  });

describe("selections never read cards", () => {
  it('keeps the opening card read the only one, and says when it failed', async () => {
    const fail = { first: true };
    const { treesCalls, gates } = holdReads(fail);
    const view = open(R6R);
    await waitFor(() => expect(gates.some((gate) => gate.kind === 'intent')).toBe(true));
    await releaseAll(gates);
    // The opening's read 5 failed in transport: the subjects say their locations could not be read.
    await waitFor(() => expect(treesCalls).toHaveLength(1));
    await waitFor(() => expect(view.queryAllByTestId('review-subject-cards-unavailable').length).toBeGreaterThan(0));
    await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());

    // Another family: its subject is read, no card set.
    fireEvent.click(within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open'));
    await releaseIntent(gates, HBJ_ID);
    await waitFor(() => expect(view.getByTestId('review-center-family').dataset.family).toBe(HBJ_ID));
    expect(treesCalls).toHaveLength(1);

    // A member of the shown family, from the centre's roster.
    const member = within(view.getByTestId('review-center-family-details')).getAllByTestId('review-center-open-member')[0];
    fireEvent.click(member);
    await act(async () => {});
    await releaseAll(gates);
    expect(treesCalls).toHaveLength(1);

    // Back to the first family.
    fireEvent.click(within(familyBlock(view, R6R_ID)).getByTestId('review-family-open'));
    await act(async () => {});
    await releaseAll(gates);
    await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
    expect(treesCalls).toHaveLength(1);
  });
});
