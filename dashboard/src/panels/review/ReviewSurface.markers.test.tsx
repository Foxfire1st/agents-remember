// MIK-R34 on real data: following a per-hunk intent marker in the review workspace, and returning.
//
// The bodies are the REAL served answers of the reviewer routes for the 260928-MIK-L34 worker's
// converted scratch leaf, comparison 2 (provenance: markerReturn.capture-provenance.json). The leaf
// edits serving/notes.py inside `_confined_stat`, where the recorded ranges of INV-Z66EMHMH
// (FAM-4V4GSQCS) and INV-413DC8XE (no family) both lie, and deletes `list_notes`'s docstring line,
// where three ranges lie. The one synthetic body is the subject catalogue, answered empty so the
// surface opens on the task-context review; the leaf-wide read is held, as a slow one is. Only
// `fetch` is stubbed: `ReviewSurface` and everything under it is the real component tree.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { ReviewSurface } from './ReviewSurface';

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
const NOTES = 'mcp/src/agents_remember/serving/notes.py';
const EMPTY_CATALOGUE = { state: 'entries', entries: [], total_subjects: 0 };
const [edit] = file.file_classification.hunks;
const Z66 = edit.links.find((link) => link.invariant === 'INV-Z66EMHMH')!;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

const requested: URL[] = [];

function answer(url: URL): unknown {
  if (url.pathname.endsWith('/entries')) return EMPTY_CATALOGUE;
  if (url.pathname.endsWith('/source-content')) return source;
  if (url.pathname.endsWith('/trees')) {
    if (url.searchParams.get('lane')) return lane;
    if (url.searchParams.get('invariants')) return cards;
    return url.searchParams.get('file') === NOTES ? file : undefined;
  }
  const selector = url.searchParams.get('selectorId');
  if (selector === null) return task;
  return selector === Z66.invariant_key ? invariant : null;
}

function serve() {
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const body = answer(url);
      // The leaf-wide tree view (the gate's worklist) is held: it is not what this check is about.
      if (body === undefined) return new Promise<Response>(() => undefined);
      if (body === null) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function open() {
  const payload = task.payload!;
  return render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      onBack={() => undefined}
    />,
  );
}

async function marksOf(view: ReturnType<typeof open>, count: number) {
  await waitFor(() => expect(view.queryAllByTestId('review-hunk-mark')).toHaveLength(count));
  return Object.fromEntries(
    view.getAllByTestId('review-hunk-mark').map((mark) => [mark.dataset.hunk!, mark]),
  );
}

it('opens a changed file with one marker per hunk, from the one classification', async () => {
  serve();
  const view = open();
  fireEvent.click(await view.findByRole('button', { name: `▸ ${NOTES}` }));
  const marks = await marksOf(view, 2);
  // The edit inside _confined_stat meets two recorded ranges; the deleted docstring line three,
  // matched on the before side only (it changes no after line).
  expect(
    Object.values(marks).map((mark) => [mark.dataset.hunk, mark.textContent, mark.dataset.after]),
  ).toEqual([
    ['210:1:209:0', '3 intents', 'none (after L209)'],
    ['124:1:124:1', '2 intents', 'L124'],
  ]);
  const reads = requested.filter((url) => url.searchParams.get('file'));
  expect(
    reads.map((url) => [url.searchParams.get('file'), url.searchParams.get('comparison')]),
  ).toEqual([[NOTES, '2']]);
});

it('selects the tree position a marker names, and returns to the hunk with focus on the marker', async () => {
  serve();
  const scrolled = vi.fn();
  Element.prototype.scrollIntoView = scrolled;
  const view = open();
  fireEvent.click(await view.findByRole('button', { name: `▸ ${NOTES}` }));
  // The reader reads inline.
  fireEvent.change(await view.findByTestId('review-center-diff-layout'), {
    target: { value: 'inline' },
  });
  const marks = await marksOf(view, 2);
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
    const read = requested.find((url) => url.searchParams.get('selectorId'));
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
  expect(current.dataset.revision).toBe(Z66.invariant_revision_key);
  expect(current.closest('[data-testid="review-family"]')?.getAttribute('data-family')).toBe(
    Z66.families[0].family_key,
  );
  // The file the marker sat in is closed while the target is read; the way back is visible.
  expect(view.queryByTestId('review-opened-file')).toBeNull();
  const back = view.getByTestId('review-marker-return');
  // The target's cards mark the hunks their excerpts draw (the rulings round, Q4): the changed
  // _confined_stat range holds the line 124 edit, the shared list_notes range the line 210 deletion;
  // an unchanged range holds none. The file's one classification serves them all.
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
  await waitFor(() =>
    expect(excerptMarks('RLZ-7RNSV7PG,RLZ-9YY9Y1E1')).toEqual(['210:1:209:0 3 intents']),
  );
  expect(excerptMarks('RLZ-5KCQCHVQ')).toEqual([]);
  expect([back.textContent, back.dataset.returnPath]).toEqual(['← Back to notes.py', NOTES]);

  // Elsewhere the reader switches the layout; the return restores the one the hunk was read in.
  fireEvent.change(view.getByTestId('review-center-diff-layout'), { target: { value: 'split' } });
  fireEvent.click(back);
  const again = await marksOf(view, 2);
  const origin = again['124:1:124:1'];
  await waitFor(() => expect(document.activeElement).toBe(origin));
  expect(view.getByTestId('review-workspace').dataset.diffLayout).toBe('inline');
  expect(origin.getAttribute('aria-expanded')).toBe('true');
  expect(view.getByTestId('review-hunk-mark-panel').dataset.hunk).toBe('124:1:124:1');
  expect(scrolled.mock.contexts.some((host) => (host as Element).contains(origin))).toBe(true);
  expect(view.queryByTestId('review-marker-return')).toBeNull();
  // The task-context review and the file's classification were read once each.
  expect(requested.filter((url) => url.searchParams.get('file'))).toHaveLength(1);
});

// -- the rulings round, Q3: an unknown membership opens in its own state, never as "no family" -------
//
// Comparison 3 of the same scratch leaf (markerUnknown.capture-provenance.json): the leaf's
// FAM-4V4GSQCS record does not parse on the after side. In notes.py's line 124 hunk, INV-Z66EMHMH's
// before-side membership of FAM-4V4GSQCS is unknown (the family is named) and its after-side
// membership is unknown with no family; INV-413DC8XE has no family on the before side (confirmed)
// and an unknown one on the after side, where the review itself states a measured zero.
const unknownSet = {
  task: captured<ReviewResult>('markerUnknown.task.captured.json'),
  lane: captured<unknown>('markerUnknown.lane.captured.json'),
  file: captured<{ file_classification: ReviewFileClassification }>(
    'markerUnknown.file.captured.json',
  ),
  source: captured<unknown>('markerUnknown.source.captured.json'),
  memberUnknown: captured<ReviewResult>('markerUnknown.memberUnknown.captured.json'),
  noFamily: captured<ReviewResult>('markerUnknown.noFamily.captured.json'),
};
const unknownLinks = unknownSet.file.file_classification.hunks[0].links;
const keyOf = (id: string) => unknownLinks.find((link) => link.invariant === id)!.invariant_key;

function serveUnknown() {
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const selector = url.searchParams.get('selectorId');
      const body = url.pathname.endsWith('/entries')
        ? EMPTY_CATALOGUE
        : url.pathname.endsWith('/source-content')
          ? unknownSet.source
          : url.pathname.endsWith('/trees')
            ? url.searchParams.get('lane')
              ? unknownSet.lane
              : url.searchParams.get('file') === NOTES
                ? unknownSet.file
                : undefined
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
  const [mark] = Object.values(await marksOf(view, 2)).filter(
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

it('opens an unknown membership in its own Attribution unknown state, apart from no family', async () => {
  serveUnknown();
  const view = open();
  fireEvent.click(await view.findByRole('button', { name: `▸ ${NOTES}` }));
  fireEvent.click((await marksOf(view, 2))['124:1:124:1']);
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

  // The family is named and in the tree: the member's own row carries the state and its reason.
  await followFrom(view, 'FAM-4V4GSQCS r1 · membership unknown (before, r1)', 'INV-Z66EMHMH');
  const row = await waitFor(() => {
    const node = view.container.querySelector<HTMLElement>(
      '[data-tree-node="member"][aria-current="true"]',
    );
    expect(node?.querySelector('[data-testid="review-member-target-state"]')).toBeTruthy();
    return node!;
  });
  // The selected row is what receives focus, and the state is part of its accessible name.
  await waitFor(() => expect(document.activeElement).toBe(row));
  expect(row.textContent).toContain(
    'Attribution unknownnot every family record of the after knowledge could be read, so whether ' +
      'FAM-4V4GSQCS still lists INV-Z66EMHMH there is not established',
  );
  expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
  await backToNotes(view);

  // A confirmed absence reads "No recorded family", with no unknown state anywhere.
  await followFrom(view, 'No recorded family (before, r1)', 'INV-413DC8XE');
  expect(await view.findByRole('heading', { name: 'No recorded family' })).toBeTruthy();
  expect(view.queryByTestId('review-rail-target-state')).toBeNull();
  expect(view.queryByTestId('review-center-target-state')).toBeNull();
  expect((await view.findByTestId('review-center-member-family')).textContent).toBe(
    'No recorded family',
  );
  await backToNotes(view);

  // No family named, and the review states a measured zero: the invariant view shows the unknown
  // state and its reason, in the rail and the center, and says what the review's context reads.
  await followFrom(view, 'Attribution unknown (after, r1)', 'INV-413DC8XE');
  const rail = await view.findByTestId('review-rail-target-state');
  expect(within(rail).getByRole('heading').textContent).toBe('Attribution unknown');
  expect(rail.dataset.familyState).toBe('no_family_recorded');
  expect(rail.textContent).toContain(
    'INV-413DC8XE: not every family record of the after knowledge could be read, so whether a ' +
      'family lists INV-413DC8XE on that side is not established.',
  );
  expect(view.queryByRole('heading', { name: 'No recorded family' })).toBeNull();
  const center = await view.findByTestId('review-center-target-state');
  expect(center.textContent).toContain("The review's family context reads: No recorded family.");
  // What receives focus is the state itself: named "Attribution unknown", described by its reason.
  await waitFor(() => expect(document.activeElement).toBe(rail));
  expect(focusedAnnouncement()).toEqual({
    testid: 'review-rail-target-state',
    name: 'Attribution unknown',
    description: expect.stringContaining('INV-413DC8XE: not every family record'),
  });
  await backToNotes(view);

  // No family named while the review composes a tree (from the other side): the invariant view, and
  // the state above the tree is what receives focus -- not the tree's auto-selected row, whose name
  // says nothing of it (review R1 F3).
  await followFrom(view, 'Attribution unknown (after, r1)', 'INV-Z66EMHMH');
  expect((await view.findByTestId('review-center-target-state')).textContent).toContain(
    'INV-Z66EMHMH: not every family record of the after knowledge could be read',
  );
  expect(view.queryByTestId('review-member-target-state')).toBeNull();
  const above = await view.findByTestId('review-rail-target-state');
  expect(above.compareDocumentPosition(view.getByTestId('review-family-tree'))).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
  await waitFor(() => expect(document.activeElement).toBe(above));
  expect(focusedAnnouncement()).toEqual({
    testid: 'review-rail-target-state',
    name: 'Attribution unknown',
    description: expect.stringContaining(
      'INV-Z66EMHMH: not every family record of the after knowledge could be read',
    ),
  });
});
