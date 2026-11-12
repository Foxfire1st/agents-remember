// W2 sealed F02/F06/F07/F11: actual L37 replies through the mounted production surface.
// Explicit wire variations below exercise failure/partial states; no capture is rewritten.
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewTreesResult, ReviewWorklistView } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { captured, catalogue, selectedMemberId, serve, target } from './l86.test-utils';
import { treeViewStore } from './triageViewPreference';
import { resetCardCachesForTests } from '../../data/reviewTrees';

const trees = captured<ReviewTreesResult>('l86.trees.captured.json');
const familyCards = captured<ReviewTreesResult>('l86.cards.captured.json');
const memberCards = captured<ReviewTreesResult>('l86.member-cards.captured.json');
const familyId = catalogue.outline!.families.find((one) => one.id === 'FAM-2HBJREC2')!.selector_id;
const reason = 'Synthetic unreadable candidate code input';
beforeEach(() => {
  resetCardCachesForTests();
  localStorage.clear();
  treeViewStore.getState().setView('all');
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function replaceReplies(
  leaf: ReviewTreesResult | Promise<ReviewTreesResult> = trees,
  cards?: ReviewTreesResult,
) {
  serve();
  const original = globalThis.fetch;
  const requests: URL[] = [];
  vi.stubGlobal('fetch', async (address: string) => {
    const url = new URL(address, 'http://localhost');
    requests.push(url);
    if (
      url.pathname === '/api/review/trees' &&
      !['lane', 'invariants', 'file'].some((name) => url.searchParams.has(name))
    )
      return { ok: true, status: 200, json: async () => await leaf } as Response;
    if (cards && url.pathname === '/api/review/trees' && url.searchParams.has('invariants'))
      return { ok: true, status: 200, json: async () => cards } as Response;
    return original(address);
  });
  return requests;
}
function mount(kind: 'family' | 'invariant' = 'invariant') {
  return render(
    <ReviewSurface
      {...target}
      selectorKind={kind}
      selectorId={kind === 'family' ? familyId : selectedMemberId}
    />,
  );
}
function noFalseAbsence(element: HTMLElement) {
  expect(element.textContent).not.toContain('No worklist item names this record.');
  expect(element.textContent).not.toMatch(/0 of 0 changed hunks/);
}
// The cards' entry identifiers: one list in the one provenance disclosure, Technical details
// (MIK-R89 v2 rule 6), for the cards the page read (read 5); no card holds a disclosure of its own.
function checkEntryIdentities(
  root: HTMLElement,
  entries: NonNullable<ReviewTreesResult['entries']>,
) {
  const lists = root.querySelectorAll<HTMLElement>('[data-testid="review-entry-identifiers"]');
  expect(lists).toHaveLength(1);
  const disclosure = lists[0].closest('details')!;
  expect(disclosure.dataset.testid).toBe('review-details');
  expect(disclosure.dataset.disclosure).toBe('provenance');
  expect(lists[0].querySelector('[data-review-content]')).toBeNull();
  const rows = lists[0].querySelectorAll<HTMLElement>(':scope > ul > [data-entry]');
  expect(rows).toHaveLength(entries.length);
  for (const entry of entries) {
    const row = [...rows].find((one) => one.dataset.entry === entry.id)!;
    expect(row.textContent).toContain(entry.id);
    for (const side of ['before', 'after'] as const) {
      const text = row.querySelector(`[data-side="${side}"]`)!.textContent!;
      if (entry[side].blob) expect(text).toContain(entry[side].blob);
      if (entry[side].content) expect(text).toContain(entry[side].content);
    }
  }
  expect(root.querySelectorAll('details').length).toBeLessThanOrEqual(20);
  expect(root.querySelector('[data-testid="review-expression-card"] details')).toBeNull();
}

// A selected invariant reads its own cards; a family's overview reads none (MIK-R211 v4 rule 10). The
// served set for that read is l86.test-utils' `cards` reply.
const readCards = (requests: URL[]) =>
  requests.filter((url) => url.pathname === '/api/review/trees' && url.searchParams.has('invariants'));

// Where a record's worklist items are read: an invariant's own panel after its code, and for a family,
// whose overview's slot draws its read's steps, judgment and facts but no worklist panel (MIK-R211 v4
// rule 10; FamilyState), the Worklist view.
async function worklistOf(view: ReturnType<typeof mount>, kind: 'family' | 'invariant') {
  if (kind === 'invariant') return view.findByTestId('review-subject-worklist');
  await view.findByTestId('review-family-overview');
  fireEvent.click(view.getByTestId('review-tree-worklist'));
  return view.getByTestId('review-worklist-view');
}

it.each(['family', 'invariant'] as const)(
  'F02/F07 joins the actual %s record and preserves every entry side identity',
  async (kind) => {
    const requests = replaceReplies();
    const view = mount(kind);
    const canonical = kind === 'family' ? 'FAM-2HBJREC2' : 'INV-1XKX9ERN';
    const block = await worklistOf(view, kind);
    const expected = trees.worklist!.items.filter((item) => item.subject === canonical);
    expect(expected).toHaveLength(kind === 'family' ? 1 : 2);
    const items = () =>
      within(block)
        .queryAllByTestId('review-worklist-item')
        .filter((one) => kind === 'invariant' || one.dataset.subject === canonical);
    await waitFor(() => expect(items()).toHaveLength(expected.length));
    expect(items().map((one) => one.dataset.kind)).toEqual(expected.map((one) => one.kind));
    expect(items().every((one) => one.dataset.subject === canonical)).toBe(true);
    noFalseAbsence(block);
    const identities = await view.findByTestId('review-entry-identifiers');
    expect(identities).toBeTruthy();
    if (kind === 'family') {
      // The family's overview reads no member's cards, so no entry identity is listed.
      expect(readCards(requests)).toHaveLength(0);
      expect(identities.querySelectorAll(':scope > ul > [data-entry]')).toHaveLength(0);
      return;
    }
    // One card read, for the selected invariant; its answer here is the captured family-set body.
    await waitFor(() => expect(readCards(requests)).toHaveLength(1));
    const entries = familyCards.entries!;
    await waitFor(() =>
      expect(identities.querySelectorAll(':scope > ul > [data-entry]')).toHaveLength(entries.length),
    );
    checkEntryIdentities(view.getByTestId('review-surface'), entries);
  },
);

it('F06 shows captured resolved-stale reasons and a supplied unverifiable proof-side reason outside disclosures', async () => {
  const cards = structuredClone(memberCards);
  const proof = cards.entries!.find(
    (one) => one.kind === 'proof' && one.after.state === 'resolved' && one.change === 'changed',
  )!;
  expect(proof).toBeTruthy();
  proof.after.currentness = 'unverifiable';
  proof.after.currentness_reason = 'Synthetic supplied unverifiable proof-side reason';
  replaceReplies(trees, cards);
  const view = mount();
  await view.findByTestId('review-entry-identifiers');
  await waitFor(() => expect(view.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  // A changed location is a card (MIK-R89 v2 rule 4); unchanged and not-comparable ones are the
  // lines of their subject's closed groups (rule 5), which carry path, role and range only.
  const expected = cards.entries!.filter((entry) => entry.change === 'changed').flatMap((entry) =>
    (['before', 'after'] as const)
      .filter(
        (side) =>
          entry[side].recorded &&
          entry[side].currentness !== 'current' &&
          entry[side].currentness_reason,
      )
      .map((side) => ({ entry, side })),
  );
  expect(
    expected.some(
      ({ entry, side }) => entry[side].state === 'resolved' && entry[side].currentness === 'stale',
    ),
  ).toBe(true);
  for (const { entry, side } of expected) {
    const mark = view.container.querySelector<HTMLElement>(
      `[data-testid="review-card-currentness"][data-entry="${entry.id}"][data-side="${side}"]`,
    )!;
    expect(mark.closest('details')).toBeNull();
    // The mark names the entry, its state and its side exactly (F4); the recorded reason reads
    // beside it, outside any disclosure.
    const reason = view.container.querySelector<HTMLElement>(
      `[data-testid="review-card-currentness-reason"][data-entry="${entry.id}"][data-side="${side}"]`,
    )!;
    expect(reason.textContent).toContain(entry[side].currentness_reason);
    expect(reason.closest('details')).toBeNull();
  }
  checkEntryIdentities(view.getByTestId('review-surface'), cards.entries!);
});

// Wire shapes match incomplete_worklist and the leaf route's absent view; their reasons are test inputs.
function wireWorklist(state: 'incomplete' | 'absent' | 'unbound'): ReviewTreesResult {
  const reply = structuredClone(trees);
  const worklist: ReviewWorklistView = {
    ...reply.worklist!,
    items: [],
    changes: [],
    history_rows: [],
    incomplete: [],
    bound: false,
  };
  if (state === 'absent') {
    worklist.source = 'absent';
    worklist.state = undefined;
    worklist.detail = 'no MIK-R08 worklist applies to this leaf, or none was persisted for it';
  } else if (state === 'incomplete') {
    worklist.state = 'incomplete';
    worklist.incomplete = [{ input: 'C', detail: reason }];
  }
  reply.worklist = worklist;
  return reply;
}
it.each(['incomplete', 'absent', 'unbound'] as const)(
  'F11 keeps %s wire state distinct from empty measurements in both views',
  async (state) => {
    replaceReplies(wireWorklist(state));
    const view = mount();
    const block = await view.findByTestId('review-subject-worklist');
    await waitFor(() => expect(within(block).getByTestId('review-worklist-problems')).toBeTruthy());
    expect(block.textContent).toContain(
      state === 'incomplete'
        ? reason
        : state === 'absent'
          ? 'none was persisted'
          : 'not bound to this comparison',
    );
    noFalseAbsence(block);
    fireEvent.click(view.getByTestId('review-tree-worklist'));
    const full = await view.findByTestId('review-worklist-view');
    noFalseAbsence(full);
    expect(within(full).getByTestId('review-worklist-linkage').textContent).toContain(
      'not been completely measured',
    );
  },
);

it('F11 permits complete bound emptiness and keeps returned partial facts explicitly partial', async () => {
  const complete = structuredClone(trees);
  complete.worklist = { ...complete.worklist!, source: 'computed', items: [], changes: [] };
  replaceReplies(complete);
  const view = mount();
  const block = await view.findByTestId('review-subject-worklist');
  await waitFor(() => expect(block.textContent).toContain('No worklist item names this record.'));
  fireEvent.click(view.getByTestId('review-tree-worklist'));
  expect((await view.findByTestId('review-worklist-view')).textContent).toContain(
    '0 of 0 changed hunks in 0 files',
  );
  cleanup();
  const partial = wireWorklist('incomplete');
  partial.worklist!.items = trees
    .worklist!.items.filter((one) => one.subject === 'INV-1XKX9ERN')
    .slice(0, 1);
  partial.worklist!.changes = trees.worklist!.changes.slice(0, 1);
  replaceReplies(partial);
  const next = mount();
  const part = await next.findByTestId('review-subject-worklist');
  await waitFor(() => expect(within(part).getAllByTestId('review-worklist-item')).toHaveLength(1));
  expect(part.querySelector('[data-worklist-complete="false"]')).toBeTruthy();
  noFalseAbsence(part);
  fireEvent.click(next.getByTestId('review-tree-worklist'));
  expect((await next.findByTestId('review-worklist-view')).textContent).toContain(
    'Partial reported linkage:',
  );
});

it('F02 preserves held, unavailable and mismatched comparison states without claiming record absence', async () => {
  let release!: (reply: ReviewTreesResult) => void;
  replaceReplies(
    new Promise<ReviewTreesResult>((resolve) => {
      release = resolve;
    }),
  );
  const view = mount();
  const block = await view.findByTestId('review-subject-worklist');
  expect(block.textContent).toContain("Reading the leaf's worklist");
  noFalseAbsence(block);
  await act(async () =>
    release({
      ...trees,
      state: 'refused',
      refusal: {
        code: 'candidate_unresolved',
        detail: 'Synthetic leaf read unavailable',
        next_action: 'Retry the leaf read',
      },
    }),
  );
  await waitFor(() => expect(block.textContent).toContain('Synthetic leaf read unavailable'));
  expect(block.textContent).toContain('Retry the leaf read');
  noFalseAbsence(block);
  cleanup();
  replaceReplies({
    ...trees,
    comparison: { ...trees.comparison!, number: trees.comparison!.number + 1 },
  });
  const next = mount();
  const mismatch = await next.findByTestId('review-subject-worklist');
  await waitFor(() => expect(mismatch.textContent).toContain('belongs to comparison'));
  noFalseAbsence(mismatch);
});
