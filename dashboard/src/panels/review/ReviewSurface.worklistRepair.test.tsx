// W2 sealed F02/F06/F07/F11: actual L37 replies through the mounted production surface.
// Explicit wire variations below exercise failure/partial states; no capture is rewritten.
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewTreesResult, ReviewWorklistView } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { captured, catalogue, selectedMemberId, serve, target } from './l86.test-utils';
import { treeViewStore } from './triageViewPreference';

const trees = captured<ReviewTreesResult>('l86.trees.captured.json');
const familyCards = captured<ReviewTreesResult>('l86.cards.captured.json');
const memberCards = captured<ReviewTreesResult>('l86.member-cards.captured.json');
const familyId = catalogue.outline!.families.find((one) => one.id === 'FAM-2HBJREC2')!.selector_id;
const reason = 'Synthetic unreadable candidate code input';
beforeEach(() => {
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
  vi.stubGlobal('fetch', async (address: string) => {
    const url = new URL(address, 'http://localhost');
    if (
      url.pathname === '/api/review/trees' &&
      !['lane', 'invariants', 'file'].some((name) => url.searchParams.has(name))
    )
      return { ok: true, status: 200, json: async () => await leaf } as Response;
    if (cards && url.pathname === '/api/review/trees' && url.searchParams.has('invariants'))
      return { ok: true, status: 200, json: async () => cards } as Response;
    return original(address);
  });
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
function checkEntryIdentities(
  root: HTMLElement,
  entries: NonNullable<ReviewTreesResult['entries']>,
) {
  const details = root.querySelectorAll<HTMLDetailsElement>(
    'details[data-testid="review-entry-identifiers"]',
  );
  expect(details).toHaveLength(1);
  expect(details[0].dataset.disclosure).toBe('identifiers');
  expect(details[0].querySelector('[data-review-content]')).toBeNull();
  const rows = details[0].querySelectorAll<HTMLElement>('[data-entry]');
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

it.each(['family', 'invariant'] as const)(
  'F02/F07 joins the actual %s record and preserves every entry side identity',
  async (kind) => {
    replaceReplies();
    const view = mount(kind);
    const canonical = kind === 'family' ? 'FAM-2HBJREC2' : 'INV-1XKX9ERN';
    const block = await view.findByTestId('review-subject-worklist');
    const expected = trees.worklist!.items.filter((item) => item.subject === canonical);
    expect(expected).toHaveLength(kind === 'family' ? 1 : 2);
    await waitFor(() =>
      expect(within(block).getAllByTestId('review-worklist-item')).toHaveLength(expected.length),
    );
    expect(
      within(block)
        .getAllByTestId('review-worklist-item')
        .map((one) => one.dataset.kind),
    ).toEqual(expected.map((one) => one.kind));
    expect(
      within(block)
        .getAllByTestId('review-worklist-item')
        .every((one) => one.dataset.subject === canonical),
    ).toBe(true);
    noFalseAbsence(block);
    const identities = await view.findByTestId('review-entry-identifiers');
    expect(identities).toBeTruthy();
    const entries = (kind === 'family' ? familyCards : memberCards).entries!;
    checkEntryIdentities(view.getByTestId('review-surface'), entries);
    if (kind === 'invariant') expect(entries.some((one) => one.kind === 'proof')).toBe(true);
  },
);

it('F06 shows captured resolved-stale reasons and a supplied unverifiable proof-side reason outside disclosures', async () => {
  const cards = structuredClone(memberCards);
  const proof = cards.entries!.find(
    (one) => one.kind === 'proof' && one.after.state === 'resolved',
  )!;
  expect(proof).toBeTruthy();
  proof.after.currentness = 'unverifiable';
  proof.after.currentness_reason = 'Synthetic supplied unverifiable proof-side reason';
  replaceReplies(trees, cards);
  const view = mount();
  await view.findByTestId('review-entry-identifiers');
  const expected = cards.entries!.flatMap((entry) =>
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
    fireEvent.click(
      within(view.getByTestId('review-catalogue-navigation')).getByRole('button', {
        name: /^Worklist/,
      }),
    );
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
  fireEvent.click(
    within(view.getByTestId('review-catalogue-navigation')).getByRole('button', {
      name: /^Worklist/,
    }),
  );
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
  fireEvent.click(
    within(next.getByTestId('review-catalogue-navigation')).getByRole('button', {
      name: /^Worklist/,
    }),
  );
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
