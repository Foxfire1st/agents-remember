// MIK-L33 x MIK-L34 (the merge round) in the mounted reviewer, on real data: following an intent
// marker to a member whose membership is unknown, the member's one membership statement, the shared
// sticky offset while the way back is open, and j after a return.
//
// The bodies are the REAL served answers of MIK-L34's scenario rebuilt under /tmp/mik-l33-merge and
// re-captured over the merged tree, comparison 3 -- the leaf's FAM-4V4GSQCS record does not parse on
// the after side (markerUnknown.capture-provenance.json) -- and that comparison's expression cards
// (triageMarker.capture-provenance.json). The one synthetic body is the empty subject catalogue; reads
// no body answers are held, as a slow one is. `ReviewSurface` is the real component tree.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const task = captured<ReviewResult>('markerUnknown.task.captured.json');
const lane = captured<unknown>('markerUnknown.lane.captured.json');
const file = captured<{ file_classification: ReviewFileClassification }>(
  'markerUnknown.file.captured.json',
);
const source = captured<unknown>('markerUnknown.source.captured.json');
const memberUnknown = captured<ReviewResult>('markerUnknown.memberUnknown.captured.json');
const noFamily = captured<ReviewResult>('markerUnknown.noFamily.captured.json');
const cards = captured<unknown>('triageMarker.cards.captured.json');
const NOTES = 'mcp/src/agents_remember/serving/notes.py';
const EMPTY_CATALOGUE = { state: 'entries', entries: [], total_subjects: 0 };
const links = file.file_classification.hunks[0].links;
const keyOf = (id: string) => links.find((link) => link.invariant === id)!.invariant_key;
const Z66_REVISION = links.find(
  (link) => link.invariant === 'INV-Z66EMHMH',
)!.invariant_revision_key;
const J = { key: 'j', code: 'KeyJ' };
// A cold surface render under a loaded suite can exceed the library's 1 s default.
const WAIT = { timeout: 5000 };
const UNPARSED =
  'the after memory tree has family records that do not parse: ' +
  'knowledge/families/FAM-4V4GSQCS-Bounded-notes-listing-with-unchanged-meaning.json';

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
  if (selector === keyOf('INV-Z66EMHMH')) return memberUnknown;
  return selector === keyOf('INV-413DC8XE') ? noFamily : undefined;
}

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const body = answer(url);
      if (body === undefined) return new Promise<Response>(() => undefined);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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

type View = ReturnType<typeof open>;

// Open the hunk mark `hunk` inside `scope` and follow the occurrence named `occurrence`.
async function follow(view: View, scope: HTMLElement, hunk: string, occurrence: string) {
  const mark = await waitFor(() => {
    const found = within(scope)
      .queryAllByTestId('review-hunk-mark')
      .find((one) => one.dataset.hunk === hunk);
    expect(found).toBeTruthy();
    return found!;
  }, WAIT);
  if (mark.getAttribute('aria-expanded') !== 'true') fireEvent.click(mark);
  const panel = view.getByTestId('review-hunk-mark-panel');
  const button = within(panel)
    .getAllByTestId('review-hunk-mark-occurrence')
    .find((one) => one.textContent === occurrence);
  expect(button, occurrence).toBeTruthy();
  fireEvent.click(button!);
  return mark;
}

// A node's accessible name and description, as the role query computes them (whitespace collapsed).
const flat = (text: string) => text.replace(/\s+/g, ' ').trim();
function announcement(node: HTMLElement): { name: string; description: string } {
  let name = '';
  let description = '';
  within(node.parentElement!).getAllByRole('button', {
    name: (computed, element) => {
      if (element === node) name = computed;
      return true;
    },
    description: (computed, element) => {
      if (element === node) description = computed;
      return true;
    },
  });
  return { name: flat(name), description: flat(description) };
}
// A member revision's served statement.
const memberStatement = (revision: string) => {
  const [entry] = memberUnknown.payload!.family_context!.entries;
  return [...entry.before.members, ...entry.after.members].find(
    (one) => one.invariant_revision_id === revision,
  )!.statement!;
};

const selectedMember = (view: View) =>
  view.container.querySelector<HTMLElement>('[data-tree-node="member"][aria-current="true"]');
const texts = (ids: string | null) =>
  (ids ?? '').split(' ').map((id) => document.getElementById(id)?.textContent ?? '');
const card = (view: View, entries: string) =>
  view.getAllByTestId('review-expression-card').find((node) => node.dataset.entries === entries)!;

async function toUnknownMember(view: View) {
  fireEvent.click(await view.findByRole('button', { name: `▸ ${NOTES}` }, WAIT));
  await follow(
    view,
    view.container,
    '124:1:124:1',
    'FAM-4V4GSQCS r1 · membership unknown (before, r1)',
  );
  const row = await waitFor(() => {
    const node = selectedMember(view);
    expect(node?.dataset.revision).toBe(Z66_REVISION);
    return node!;
  }, WAIT);
  await waitFor(() => expect(document.activeElement).toBe(row), WAIT);
  return row;
}

it('states the followed unknown membership once on the member, below the way back', async () => {
  const view = open();
  const row = await toUnknownMember(view);
  // One membership statement: the comparison's membership line, carrying the marker's tag.
  const stated = row.querySelectorAll<HTMLElement>('[data-member-state="membership_unknown"]');
  expect([...stated].map((one) => one.dataset.testid)).toEqual(['review-change-membership-why']);
  expect(view.queryByTestId('review-member-target-state')).toBeNull();
  expect(stated[0].textContent).toBe(
    `Attribution unknown opened from an intent marker · membership unknown: ${UNPARSED}`,
  );
  const badge = within(row).getByTestId('review-change-badge');
  expect([badge.dataset.changeKind, badge.dataset.changeMarks]).toEqual([
    'implementation',
    'unknown',
  ]);
  // Its accessible name is the subject only: the statement and the side tag (review R3-1). Its
  // description gives the change kind, then the membership, once each (the kind is known here, so no
  // change-kind reason is drawn).
  const { name, description } = announcement(row);
  expect(name).toBe(flat(`${memberStatement(Z66_REVISION!)} · before only`));
  expect(description).toMatch(
    /^impl ?\+unknown Attribution unknown opened from an intent marker · membership unknown: the after memory tree has family records that do not parse: knowledge\/families\/FAM-4V4GSQCS-/,
  );
  expect(description.match(/Attribution unknown/g)).toHaveLength(1);
  expect(within(row).queryByTestId('review-change-why')).toBeNull();
  expect(texts(row.getAttribute('aria-describedby'))).toEqual([
    badge.textContent,
    stated[0].textContent,
  ]);
  // The way back is open, so the workspace sets the stacked layout's shared sticky offset, and the
  // triage bar is drawn under it.
  const back = view.getByTestId('review-marker-return');
  expect(view.getByTestId('review-workspace').dataset.markerReturn).toBe('open');
  expect(view.getByTestId('review-triage-bar')).toBeTruthy();
  fireEvent.click(back);
  await waitFor(
    () => expect(document.activeElement?.getAttribute('data-hunk')).toBe('124:1:124:1'),
    WAIT,
  );
  expect(view.getByTestId('review-workspace').dataset.markerReturn).toBeUndefined();
}, 20_000);

it('moves with j after a marker return, never taking focus from the held return first', async () => {
  const view = open();
  await toUnknownMember(view);
  // From the member view's own card excerpt, follow the confirmed absence, then come back.
  const origin = await follow(
    view,
    await waitFor(() => card(view, 'RLZ-7X2C4VRQ'), WAIT),
    '124:1:124:1',
    'No recorded family (before, r1)',
  );
  expect(await view.findByRole('heading', { name: 'No recorded family' }, WAIT)).toBeTruthy();
  fireEvent.click(view.getByTestId('review-marker-return'));
  const held = await waitFor(() => {
    const active = document.activeElement as HTMLElement | null;
    expect(active?.dataset.testid).toBe('review-hunk-mark');
    expect(active?.closest('[data-testid="review-expression-card"]')).toBeTruthy();
    return active!;
  }, WAIT);
  expect(held.dataset.hunk).toBe(origin.dataset.hunk);
  const row = selectedMember(view)!;
  expect(row.dataset.revision).toBe(Z66_REVISION);
  // Nothing of the triage moves focus on its own while the return is held.
  await new Promise((settle) => setTimeout(settle, 150));
  expect(document.activeElement).toBe(held);
  expect(view.getByTestId('review-triage-bar')).toBeTruthy();

  // j: the key ends the hold, and the move selects the next change after the selected member.
  const stops = [
    ...view.container.querySelectorAll<HTMLElement>(
      '[data-testid="review-family-tree"] [data-tree-node][data-change-primary]:not([data-change-primary="unchanged"])',
    ),
  ];
  const next = stops[stops.indexOf(row) + 1];
  expect(next).toBeTruthy();
  fireEvent.keyDown(held, J);
  expect(document.activeElement).toBe(next);
  // It selects that member's invariant, as a click does.
  const [entry] = memberUnknown.payload!.family_context!.entries;
  const member = [...entry.before.members, ...entry.after.members].find(
    (one) => one.invariant_revision_id === next.dataset.revision,
  )!;
  expect(requested.at(-1)?.searchParams.get('selectorId')).toBe(member.invariant_id);
  // The released hold does not take focus back.
  await new Promise((settle) => setTimeout(settle, 150));
  expect(document.activeElement).toBe(next);
}, 20_000);
