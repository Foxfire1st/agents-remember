// The page's card reads through production clicks (MIK-R211 v4 rule 10; L81-R1-F18's successor). A
// family click, a return to the first family and another family read their subjects and never a card
// set; a member opened from the family's overview reads its own cards once. A card read that fails in
// transport says so on the subject (it is not kept); a remount asks again
// (data/reviewTrees.cards.test.ts).
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

describe("family selections never read cards", () => {
  it("reads a member's own cards once, says when that read failed, and reads none for a family", async () => {
    const fail = { first: true };
    const { treesCalls, gates } = holdReads(fail);
    const view = open(R6R);
    await waitFor(() => expect(gates.some((gate) => gate.kind === 'intent')).toBe(true));
    await releaseAll(gates);
    await waitFor(() => expect(view.getByTestId('review-family-overview').dataset.family).toBe(R6R_ID));
    await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
    // The family's overview reads no card set.
    expect(treesCalls).toEqual([]);

    // A member from the family's overview reads its own cards; that read fails in transport, and the
    // subject says its locations could not be read.
    const member = within(view.getByTestId('review-family-overview')).getAllByTestId('review-member-summary')[0];
    fireEvent.click(member.querySelector('button')!);
    await act(async () => {});
    await releaseAll(gates);
    await waitFor(() => expect(treesCalls).toEqual([member.dataset.subject]));
    await waitFor(() => expect(view.queryAllByTestId('review-subject-cards-unavailable').length).toBeGreaterThan(0));
    await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());

    // Another family: its subject is read, no card set.
    fireEvent.click(within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open'));
    // A review read starts a microtask after its owner asks (reviewTransport.ts `getReviewJson`).
    await waitFor(() => expect(gates.some((gate) => gate.kind === 'intent')).toBe(true));
    await releaseIntent(gates, HBJ_ID);
    await waitFor(() => expect(view.getByTestId('review-family-overview').dataset.family).toBe(HBJ_ID));
    expect(treesCalls).toHaveLength(1);

    // Back to the first family.
    fireEvent.click(within(familyBlock(view, R6R_ID)).getByTestId('review-family-open'));
    await act(async () => {});
    await releaseAll(gates);
    await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
    await waitFor(() => expect(view.getByTestId('review-family-overview').dataset.family).toBe(R6R_ID));
    expect(treesCalls).toHaveLength(1);
  });
});
