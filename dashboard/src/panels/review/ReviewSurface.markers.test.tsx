// MIK-R34 on real data: following a per-hunk intent marker in the review workspace, and returning.
//
// The bodies are the REAL served answers of the reviewer routes for the 260928-MIK-L34 worker's
// converted scratch leaf, comparison 2 (provenance: markerReturn.capture-provenance.json; re-captured
// in the MIK-L33 merge round over the merged tree, so the invariant reviews carry change facts). The leaf
// edits serving/notes.py inside `_confined_stat`, where the recorded ranges of INV-Z66EMHMH
// (FAM-4V4GSQCS) and INV-413DC8XE (no family) both lie, and deletes `list_notes`'s docstring line,
// where three ranges lie. Explicit synthetic catalogue and family-question adapters open the page on
// the initial family. A card has no Full file of its own (MIK-R89 v2 rule 4), so the marks are the
// ones the cards' excerpts draw. Marker targets use untouched served answers. A card is its
// selected invariant's own read, which starts once the leaf-wide read has answered (MIK-R211 v4
// rule 10), so the leaf-wide read answers here. Only
// `fetch` is stubbed: `ReviewSurface` and everything under it is the real component tree.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { ReviewSurface } from './ReviewSurface';
import {
  syntheticMarkerCatalogue,
  syntheticMarkerFamilyReview,
} from './markerCatalogue.test-utils';
import { treeViewStore } from './triageViewPreference';
import { treeOrderStore } from './triageOrderPreference';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const task = captured<ReviewResult>('markerReturn.task.captured.json');
const lane = captured<unknown>('markerReturn.lane.captured.json');
const file = captured<{ file_classification: ReviewFileClassification }>(
  'markerReturn.file.captured.json',
);
const source = captured<unknown>('markerReturn.source.captured.json');
const invariant = captured<ReviewResult>('markerReturn.invariant.captured.json');
const cards = captured<unknown>('markerReturn.cards.captured.json');
const completeTrees = captured<unknown>('markerReturn.trees.captured.json');
const initialFamily = syntheticMarkerFamilyReview(invariant);
const NOTES = 'mcp/src/agents_remember/serving/notes.py';
const [edit] = file.file_classification.hunks;
const Z66 = edit.links.find((link) => link.invariant === 'INV-Z66EMHMH')!;
// The deleted docstring line of `list_notes` (line 210, no after line) lies in one shared range whose
// entries belong to INV-EJW15DXA (RLZ-7RNSV7PG) and INV-ZJS1XY4R (RLZ-9YY9Y1E1), both of
// FAM-4V4GSQCS, and to INV-SMYSQKTJ (no family).
const deletion = file.file_classification.hunks[1];
const owner = (invariant: string) => deletion.links.find((link) => link.invariant === invariant)!;
const LIST_NOTES_OWNERS = [owner('INV-EJW15DXA'), owner('INV-ZJS1XY4R')];
// Explicitly synthetic answers, not captures: those owners were never read by invariant selector.
// The captured INV-Z66EMHMH answer is re-addressed to each owner's record and recorded revision;
// its family context is the same family's capture. The cards, the classification and the catalogue
// stay the untouched served answers.
const ownerAnswers = new Map(
  LIST_NOTES_OWNERS.map((link) => {
    const answer = structuredClone(invariant);
    Object.assign(answer.payload!.knowledge.revision_selection!, {
      record_id: link.invariant_key,
      before_revision_id: link.invariant_revision_key,
      after_revision_id: link.invariant_revision_key,
      before_heads: [link.invariant_revision_key],
      after_heads: [link.invariant_revision_key],
      before_retained: [link.invariant_revision_key],
      after_retained: [link.invariant_revision_key],
    });
    return [link.invariant_key, answer] as const;
  }),
);

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

const requested: URL[] = [];

function answer(url: URL): unknown {
  if (url.pathname.endsWith('/entries')) return syntheticMarkerCatalogue(invariant);
  if (url.pathname.endsWith('/source-content')) return source;
  if (url.pathname.endsWith('/trees')) {
    if (url.searchParams.get('lane')) return lane;
    if (url.searchParams.get('invariants')) return cards;
    return url.searchParams.get('file') === NOTES ? file : completeTrees;
  }
  const selector = url.searchParams.get('selectorId');
  if (selector === null) return task;
  if (selector === initialFamily.payload!.knowledge.revision_selection!.record_id)
    return initialFamily;
  if (selector === Z66.invariant_key) return invariant;
  return ownerAnswers.get(selector) ?? null;
}

function serve() {
  treeViewStore.getState().setView('all');
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const body = answer(url);
      if (body === null) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function open(subject = initialFamily) {
  const payload = task.payload!;
  return render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind={subject.payload!.knowledge.revision_selection!.record_kind}
      selectorId={subject.payload!.knowledge.revision_selection!.record_id}
      onBack={() => undefined}
    />,
  );
}

// The card whose excerpt holds the _confined_stat edit (line 124) of notes.py.
async function openNotesCard(view: ReturnType<typeof open>) {
  return waitFor(() => {
    const one = view
      .queryAllByTestId('review-expression-card')
      .find((node) => node.dataset.entries === 'RLZ-7X2C4VRQ');
    expect(one).toBeTruthy();
    return one!;
  });
}
// MIK-R211 v4 rule 10: the family opens its overview, and a card is its invariant's own detail. The
// reader opens a member from the overview.
async function openMember(view: ReturnType<typeof open>, key: string) {
  const row = await waitFor(() => {
    const found = within(view.getByTestId('review-family-overview'))
      .getAllByTestId('review-member-summary')
      .find((node) => node.dataset.subject === key);
    expect(found).toBeTruthy();
    return found!;
  });
  fireEvent.click(row.querySelector('button')!);
}
// INV-Z66EMHMH's changed range holds the line 124 edit.
async function openZ66(view: ReturnType<typeof open>) {
  await openMember(view, Z66.invariant_key);
  return openNotesCard(view);
}
// The marks the cards' excerpts draw, by hunk (the first card that draws a hunk, in column order).
async function marksOf(view: ReturnType<typeof open>, count: number) {
  const marks = () => view.queryAllByTestId('review-hunk-mark');
  await waitFor(() => expect(new Set(marks().map((mark) => mark.dataset.hunk)).size).toBe(count));
  const byHunk: Record<string, HTMLElement> = {};
  for (const mark of marks()) byHunk[mark.dataset.hunk!] ??= mark;
  return byHunk;
}

it("marks each card excerpt's hunks, one marker per hunk, from the one classification", async () => {
  serve();
  const view = open();
  await openZ66(view);
  const marks = await marksOf(view, 1);
  // The edit inside _confined_stat meets two recorded ranges. The deleted docstring line lies only in
  // other invariants' ranges, whose cards are their own details (below); INV-Z66EMHMH's unchanged
  // range holds none.
  expect(
    Object.values(marks).map((mark) => [mark.dataset.hunk, mark.textContent, mark.dataset.after]),
  ).toEqual([['124:1:124:1', '2 intents', 'L124']]);
  expect(within(await openNotesCard(view)).getAllByTestId('review-hunk-mark')).toHaveLength(1);
  // The deleted docstring line, matched on the before side only (it changes no after line), on each
  // owner's own card of the shared list_notes range: one card per entry (W1), each with its marker.
  for (const link of LIST_NOTES_OWNERS) {
    fireEvent.click(
      view.getAllByTestId('review-family-open').find((node) => node.dataset.family === Z66.families[0].family_key)!,
    );
    await openMember(view, link.invariant_key);
    const card = await waitFor(() => {
      const cards = view.queryAllByTestId('review-expression-card');
      expect(cards.map((node) => node.dataset.entries)).toEqual([link.id]);
      expect(within(cards[0]).queryAllByTestId('review-hunk-mark')).toHaveLength(1);
      return cards[0];
    });
    expect(
      within(card).getAllByTestId('review-hunk-mark').map((mark) => [mark.dataset.hunk, mark.textContent, mark.dataset.after]),
    ).toEqual([['210:1:209:0', '3 intents', 'none (after L209)']]);
    expect(
      within(card).getAllByTestId('review-hunk-mark').map((mark) => `${mark.dataset.hunk} ${mark.textContent}`),
    ).toEqual(['210:1:209:0 3 intents']);
  }
  // One classification of notes.py, at the review's comparison. The lane section reads its own
  // files once they are in view (jsdom has no IntersectionObserver: everything is in view).
  const reads = requested.filter((url) => url.searchParams.get('file'));
  expect(reads.every((url) => url.searchParams.get('comparison') === '2')).toBe(true);
  expect(reads.filter((url) => url.searchParams.get('file') === NOTES)).toHaveLength(1);
});

it('selects the tree position a marker names, and returns to the hunk with focus on the marker', async () => {
  serve();
  const scrolled = vi.fn();
  Element.prototype.scrollIntoView = scrolled;
  const view = open();
  await openZ66(view);
  const marks = await marksOf(view, 1);
  fireEvent.click(marks['124:1:124:1']);
  const panel = view.getByTestId('review-hunk-mark-panel');
  expect(
    within(panel)
      .getAllByTestId('review-hunk-mark-occurrence')
      .map((one) => one.textContent),
  ).toEqual([
    'FAM-4V4GSQCS r1 · member (before and after, r1)',
    'No recorded family (before and after, r1)',
  ]);
  fireEvent.click(within(panel).getAllByTestId('review-hunk-mark-occurrence')[0]);

  // The target: INV-Z66EMHMH's own review, at its member row of FAM-4V4GSQCS.
  const subject = await waitFor(() => {
    const read = requested.find((url) => url.searchParams.get('selectorKind') === 'invariant');
    expect(read).toBeTruthy();
    return read!;
  });
  expect([
    subject.searchParams.get('selectorKind'),
    subject.searchParams.get('selectorId'),
  ]).toEqual(['invariant', Z66.invariant_key]);
  const current = await waitFor(() => {
    const node = view.container.querySelector<HTMLElement>(
      '[data-tree-node="member"][aria-current="true"]',
    );
    expect(node).toBeTruthy();
    return node!;
  });
  expect(current.dataset.subjectId).toBe(Z66.invariant_key);
  expect(current.dataset.family).toBe(Z66.families[0].family_key);
  // The way back is visible.
  const back = view.getByTestId('review-marker-return');
  // The target's cards mark the hunks their excerpts draw (the rulings round, Q4): the changed
  // _confined_stat range holds the line 124 edit; an unchanged range holds none. The shared
  // list_notes range belongs to other invariants, whose cards are their own details (MIK-R211 v4
  // rule 10; its marks are asserted on each owner in the test above). The file's one classification
  // serves them all.
  const card = (entries: string) =>
    view.getAllByTestId('review-expression-card').find((node) => node.dataset.entries === entries)!;
  await waitFor(() =>
    expect(within(card('RLZ-7X2C4VRQ')).queryAllByTestId('review-hunk-mark')).toHaveLength(1),
  );
  const excerptMarks = (entries: string) =>
    within(card(entries))
      .queryAllByTestId('review-hunk-mark')
      .map((mark) => `${mark.dataset.hunk} ${mark.textContent}`);
  expect(excerptMarks('RLZ-7X2C4VRQ')).toEqual(['124:1:124:1 2 intents']);
  expect(view.getAllByTestId('review-expression-card').map((node) => node.dataset.entries)).toEqual(['RLZ-7X2C4VRQ']);
  // The unchanged range is a line of the subject's closed group, never a card (MIK-R89 v2 rule 5).
  expect(view.queryAllByTestId('review-expression-card').some((node) => node.dataset.entries === 'RLZ-5KCQCHVQ')).toBe(false);
  expect([back.textContent, back.dataset.returnPath]).toEqual(['← Back to notes.py', NOTES]);

  // The diff layout follows the column's width (MIK-R89 v2 rule 4): the return has none to restore.
  fireEvent.click(back);
  const again = await marksOf(view, 1);
  const origin = again['124:1:124:1'];
  await waitFor(() => expect(document.activeElement).toBe(origin));
  expect(origin.getAttribute('aria-expanded')).toBe('true');
  expect(view.getByTestId('review-hunk-mark-panel').dataset.hunk).toBe('124:1:124:1');
  expect(scrolled.mock.contexts.some((host) => (host as Element).contains(origin))).toBe(true);
  expect(view.queryByTestId('review-marker-return')).toBeNull();
  // The file classification is shared by the family origin and the invariant target.
  expect(requested.filter((url) => url.searchParams.get('file') === NOTES)).toHaveLength(1);
});

it('releases a held marker return on j and selects the next block', async () => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('priority');
  serve();
  const view = open();
  await openZ66(view);
  const marks = await marksOf(view, 1);
  fireEvent.click(marks['124:1:124:1']);
  fireEvent.click(within(view.getByTestId('review-hunk-mark-panel'))
    .getAllByTestId('review-hunk-mark-occurrence')[0]);
  await view.findByTestId('review-marker-return');
  await waitFor(() => expect(view.container.querySelector(
    '[data-tree-node="member"][aria-current="true"]',
  )).toBeTruthy());
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
  // The return put the origin back; from its subject `j` selects the column's first block
  // (MIK-R89 v2 rule 11; changeTraversal.ts), whose tree location then holds the selection and focus.
  const first = view.getAllByTestId('review-expression-card')[0];
  fireEvent.keyDown(held, { key: 'j', code: 'KeyJ' });
  await waitFor(() => expect(first.dataset.selected).toBe('true'));
  const row = await waitFor(() => {
    const node = view.getByTestId('review-tree').querySelector<HTMLElement>('[data-tree-node="location"][aria-current="true"]');
    expect(node).toBeTruthy();
    return node!;
  });
  expect(first.dataset.entries?.split(',')).toContain(row.dataset.entry);
  await waitFor(() => expect(document.activeElement).toBe(row));
  expect(view.queryByTestId('review-marker-return')).toBeNull();
  await act(async () => { vi.advanceTimersToNextFrame(); });
  expect(document.activeElement).toBe(row);
});

// -- the rulings round, Q3: an unknown membership opens in its own state, never as "no family" -------
//
// Comparison 3 of the same scratch leaf (markerUnknown.capture-provenance.json): the leaf's
// FAM-4V4GSQCS record does not parse on the after side. In notes.py's line 124 hunk, INV-Z66EMHMH's
// before-side membership of FAM-4V4GSQCS is unknown (the family is named) and its after-side
// membership is unknown with no family; INV-413DC8XE has no family on the before side (confirmed)
// and an unknown one on the after side. Today's producer correctly calls that family context partial,
// never a measured zero. The unavailable roster cannot supply a synthetic selected member row.
const unknownSet = {
  task: captured<ReviewResult>('markerUnknown.task.captured.json'),
  lane: captured<unknown>('markerUnknown.lane.captured.json'),
  file: captured<{ file_classification: ReviewFileClassification }>(
    'markerUnknown.file.captured.json',
  ),
  source: captured<unknown>('markerUnknown.source.captured.json'),
  memberUnknown: captured<ReviewResult>('markerUnknown.memberUnknown.captured.json'),
  noFamily: captured<ReviewResult>('markerUnknown.noFamily.captured.json'),
  cards: captured<unknown>('markerUnknown.cards.captured.json'),
  entries: captured<unknown>('markerUnknown.entries.captured.json'),
  trees: captured<unknown>('markerUnknown.trees.captured.json'),
  family: captured<ReviewResult>('markerUnknown.family.captured.json'),
};
const unknownLinks = unknownSet.file.file_classification.hunks[0].links;
// In this comparison only the line 124 edit lies in a card's excerpt (the cards draw no other hunk).
const keyOf = (id: string) => unknownLinks.find((link) => link.invariant === id)!.invariant_key;

function serveUnknown() {
  treeViewStore.getState().setView('all');
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const selector = url.searchParams.get('selectorId');
      const body = url.pathname.endsWith('/entries')
        ? unknownSet.entries
        : url.pathname.endsWith('/source-content')
          ? unknownSet.source
          : url.pathname.endsWith('/trees')
            ? url.searchParams.get('lane')
              ? unknownSet.lane
              : url.searchParams.get('invariants')
                ? unknownSet.cards
                : url.searchParams.get('file') === NOTES
                  ? unknownSet.file
                  : unknownSet.trees
            : selector === unknownSet.family.payload!.knowledge.revision_selection!.record_id
              ? unknownSet.family
              : selector === null
                ? unknownSet.task
                : selector === keyOf('INV-Z66EMHMH')
                  ? unknownSet.memberUnknown
                  : selector === keyOf('INV-413DC8XE')
                    ? unknownSet.noFamily
                    : null;
      if (body === undefined) return new Promise<Response>(() => undefined);
      if (body === null) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

async function followFrom(view: ReturnType<typeof open>, occurrence: string, invariant: string) {
  const [mark] = Object.values(await marksOf(view, 1)).filter(
    (one) => one.dataset.hunk === '124:1:124:1',
  );
  if (mark.getAttribute('aria-expanded') !== 'true') fireEvent.click(mark);
  const panel = view.getByTestId('review-hunk-mark-panel');
  const button = within(panel)
    .getAllByTestId('review-hunk-mark-occurrence')
    .find((one) => one.dataset.invariant === invariant && one.textContent === occurrence)!;
  fireEvent.click(button);
  await view.findByTestId('review-marker-return');
}

// The focused element's accessible name and description, as its labelling ids give them.
function focusedAnnouncement() {
  const focused = document.activeElement as HTMLElement;
  const text = (ids: string | null) =>
    (ids ?? '')
      .split(' ')
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' ')
      .trim();
  return {
    testid: focused.dataset.testid,
    name: text(focused.getAttribute('aria-labelledby')),
    description: text(focused.getAttribute('aria-describedby')),
  };
}

async function backToNotes(view: ReturnType<typeof open>) {
  fireEvent.click(view.getByTestId('review-marker-return'));
  await waitFor(() =>
    expect(document.activeElement?.getAttribute('data-hunk')).toBe('124:1:124:1'),
  );
}

it('keeps the selected invariant source available when its family roster is unreadable', async () => {
  serveUnknown();
  const view = open(unknownSet.memberUnknown);
  await openNotesCard(view);
  await marksOf(view, 1);
});

it('shows the named unknown marker on the invariant when the family has no member row', async () => {
  serveUnknown();
  const view = open(unknownSet.memberUnknown);
  await openNotesCard(view);
  await followFrom(view, 'FAM-4V4GSQCS r1 · membership unknown (before, r1)', 'INV-Z66EMHMH');
  const rail = await view.findByTestId('review-rail-target-state');
  const occurrence = unknownLinks
    .find((link) => link.invariant === 'INV-Z66EMHMH' && link.side === 'before')!
    .families[0];
  expect(rail.textContent).toContain(occurrence.reason);
  expect(rail.dataset.familyState).toBe('partial');
  expect(view.getByTestId('review-center-target-state').textContent).toContain(occurrence.reason);
  await waitFor(() => expect(document.activeElement).toBe(rail));
  expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
  await backToNotes(view);
});

it('separates before-side absence from unknown after-side membership without inventing a roster', async () => {
  serveUnknown();
  const view = open(unknownSet.memberUnknown);
  await openNotesCard(view);
  fireEvent.click((await marksOf(view, 1))['124:1:124:1']);
  expect(
    within(view.getByTestId('review-hunk-mark-panel'))
      .getAllByTestId('review-hunk-mark-occurrence')
      .map((one) => `${one.dataset.invariant} ${one.dataset.state} ${one.textContent}`),
  ).toEqual([
    'INV-Z66EMHMH membership_unknown FAM-4V4GSQCS r1 · membership unknown (before, r1)',
    'INV-Z66EMHMH membership_unknown Attribution unknown (after, r1)',
    'INV-413DC8XE confirmed_no_family No recorded family (before, r1)',
    'INV-413DC8XE membership_unknown Attribution unknown (after, r1)',
  ]);

  // A named occurrence is real before-side evidence, not permission to fabricate an after roster.
  await followFrom(view, 'FAM-4V4GSQCS r1 · membership unknown (before, r1)', 'INV-Z66EMHMH');
  const named = await view.findByTestId('review-rail-target-state');
  const namedReason = unknownLinks.find(
    (link) => link.invariant === 'INV-Z66EMHMH' && link.side === 'before',
  )!.families[0].reason!;
  expect(named.textContent).toContain(namedReason);
  expect(named.dataset.familyState).toBe('partial');
  expect(named.closest('details')).toBeNull();
  expect(view.queryByTestId('review-member-target-state')).toBeNull();
  expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(named));
  await backToNotes(view);

  // Confirmed absence belongs only to the before-side marker; the current family read is unavailable.
  await followFrom(view, 'No recorded family (before, r1)', 'INV-413DC8XE');
  const statement = unknownSet.noFamily.payload!.knowledge.after_statement;
  const statementText = statement.text;
  if (statement.state !== 'present' || typeof statementText !== 'string')
    throw new Error('native selected invariant statement required');
  await waitFor(() => {
    const selected = view.getByTestId('review-center-member');
    expect(selected.dataset.subject).toBe(keyOf('INV-413DC8XE'));
    expect(selected.textContent?.replace(/\s+/g, ' '))
      .toContain(statementText.replace(/\s+/g, ' '));
  });
  expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
  // The unavailable family read is the meta line's one chip (MIK-R89 v2 rule 1, amended 9dde2b09).
  expect(view.queryByTestId('review-center-member-family')).toBeNull();
  expect(view.getByTestId('review-subject-family-chip').textContent).toBe('family context unavailable');
  await backToNotes(view);

  // An unknown after-side occurrence is announced in its own focusable state with the wire reason.
  for (const [invariant, familyState] of [
    ['INV-413DC8XE', 'unavailable'], ['INV-Z66EMHMH', 'partial'],
  ]) {
    await followFrom(view, 'Attribution unknown (after, r1)', invariant);
    const nativeReason = unknownLinks.find(
      (link) => link.invariant === invariant && link.side === 'after',
    )!.families[0].reason!;
    const rail = await view.findByTestId('review-rail-target-state');
    expect(within(rail).getByRole('heading').textContent).toBe('Attribution unknown');
    expect(rail.dataset.familyState).toBe(familyState);
    expect(rail.textContent).toContain(nativeReason);
    const center = await view.findByTestId('review-center-target-state');
    expect(center.textContent).toContain(nativeReason);
    expect(center.textContent).not.toContain("The review's family context reads: No recorded family.");
    expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
    expect(rail.closest('[data-tree-node="member"]')).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(rail));
    expect(focusedAnnouncement()).toEqual({
      testid: 'review-rail-target-state',
      name: 'Attribution unknown',
      description: expect.stringContaining(nativeReason),
    });
    await backToNotes(view);
  }
});
