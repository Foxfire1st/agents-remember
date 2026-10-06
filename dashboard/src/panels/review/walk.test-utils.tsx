// Shared by the MIK-R39 mounted tests (ReviewSurface.walk*.test.tsx): a served world made of captured
// bodies, a `fetch` stub that answers from it and logs every request, and small readers of the mounted
// reviewer. `ReviewSurface` is the real component; only `fetch` is stubbed.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, expect, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  waitFor,
  within,
  type RenderResult,
} from '@testing-library/react';
import type { ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';

export const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;

// A cold render of a real answer (160 to 220 kB) under a loaded suite can exceed the 1 s default.
export const WAIT = { timeout: 8000 };
export const J = { key: 'j', code: 'KeyJ' };
export const K = { key: 'k', code: 'KeyK' };

const REFUSED = {
  state: 'refused',
  refusal: {
    code: 'not-found',
    detail: 'This route is not part of this check.',
    next_action: 'Open the mounted route.',
  },
};

export interface Served {
  // The review request: no selector asks for the task context.
  review: (url: URL) => unknown;
  entries: unknown;
  // The tree-comparison routes (lane, file classification, source content); refused when absent.
  tree?: (url: URL) => unknown;
}

export interface Requests {
  all: URL[];
  reviews: () => URL[];
}

// Stub `fetch` with the world. A `review` answer of `undefined` is a request the test did not expect.
export function serve(world: Served): Requests {
  const all: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      all.push(url);
      let body: unknown;
      if (url.pathname.endsWith('/entries')) body = world.entries;
      else if (url.pathname.endsWith('/trees') || url.pathname.endsWith('/source-content'))
        body = world.tree?.(url) ?? REFUSED;
      else body = world.review(url);
      if (body === undefined) throw new Error(`Unexpected review request: ${address}`);
      if (body instanceof Error) throw body;
      // A server answers with a new object each time, and the reviewer tells answers apart by identity.
      return { ok: true, status: 200, json: async () => structuredClone(body) } as Response;
    }),
  );
  return { all, reviews: () => all.filter((url) => url.pathname === '/api/review/intent') };
}

export const subjectOf = (result: ReviewResult) => {
  const selection = result.payload!.knowledge.revision_selection!;
  return `${selection.record_kind}:${selection.record_id}`;
};

const requested = (url: URL) =>
  url.searchParams.get('selectorId') === null
    ? 'task-context'
    : `${url.searchParams.get('selectorKind')}:${url.searchParams.get('selectorId')}`;

// The tree's family blocks, in displayed order, and the family ids they show.
export const families = (view: RenderResult) =>
  view.queryAllByTestId('review-family').map((block) => block.dataset.family!);

export const familyBlock = (view: RenderResult, familyId: string) =>
  view.queryAllByTestId('review-family').find((block) => block.dataset.family === familyId)!;

// The one tree node marked as the selection, as the reader sees it.
export function selectedNode(view: RenderResult): HTMLElement | null {
  const marked = view
    .getAllByTestId('review-family-tree')[0]
    .querySelectorAll<HTMLElement>('[data-tree-node][aria-current="true"]');
  expect(marked.length).toBeLessThanOrEqual(1);
  return marked[0] ?? null;
}

export const triageStatus = (view: RenderResult) =>
  view.getByTestId('review-change-status').textContent ?? '';

export const press = (key: { key: string; code: string }) =>
  fireEvent.keyDown(document.activeElement!, key);

// Wait until the reading area has settled on `subject` (the reviewer's own data attribute) and no
// read is pending.
async function settledOn(view: RenderResult, selected: () => HTMLElement | null) {
  await waitFor(() => {
    const surface = view.getByTestId('review-surface');
    expect(surface.dataset.reviewPending).toBeUndefined();
    expect(selected()).not.toBeNull();
  }, WAIT);
}

// The distinct member occurrences a family block shows (a revised member lists two revision rows).
export const memberOccurrences = (block: HTMLElement) =>
  new Set(
    within(block)
      .queryAllByTestId('review-family-member')
      .map((row) => row.dataset.member),
  );

// ---- the real-data world of lane D2's scratch leaf (walkReal.capture-provenance.json)

const entries = captured<unknown>('walkReal.entries.captured.json');
const task = captured<ReviewResult>('walkReal.task.captured.json');
// The seven changes of FAM-R6R095RW in the order `j` visits them: intent, implementation, membership.
export const CHANGES = [
  'INV-2E8MG43K',
  'INV-ZS9ZS878',
  'INV-555EHWM8',
  'INV-BR5MTSTY',
  'INV-H8EM1VJR',
  'INV-VPX81HXV',
  'INV-2TQGXFAX',
];
export const bodyOf = (name: string) => captured<ReviewResult>(`walkReal.${name}.captured.json`);
export const R6R = bodyOf('FAM-R6R095RW');
export const HBJ = bodyOf('FAM-2HBJREC2');
export const SHARED = bodyOf('INV-2TQGXFAX');
const bodies = new Map<string, ReviewResult>(
  [R6R, HBJ, ...CHANGES.map(bodyOf)].map((body) => [subjectOf(body), body]),
);
export const R6R_ID = R6R.payload!.family_context!.entries[0].family_id;
export const HBJ_ID = HBJ.payload!.family_context!.entries[0].family_id;
export const R6R_TITLE = R6R.payload!.family_context!.entries[0].display_label!;
export const HBJ_TITLE = HBJ.payload!.family_context!.entries[0].display_label!;
// The display label of every member revision the bodies serve.
const labelByRevision = new Map(
  [R6R, HBJ, SHARED].flatMap((body) =>
    body.payload!.family_context!.entries.flatMap((entry) =>
      [...entry.before.members, ...entry.after.members].map(
        (member) => [member.invariant_revision_id, member.display_label!] as const,
      ),
    ),
  ),
);

export function open(subject: ReviewResult) {
  const payload = subject.payload!;
  const selection = payload.knowledge.revision_selection!;
  return render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind={selection.record_kind}
      selectorId={selection.record_id}
      onBack={() => undefined}
    />,
  );
}

export type View = ReturnType<typeof open>;

// The revision id of a member, by its display label.
export const revisionOf = (label: string) =>
  [...labelByRevision].find(([, shown]) => shown === label)![0];

// The selected row as the reader names it: a member's label, or the family's title.
export function selectedName(view: View): string {
  const node = selectedNode(view)!;
  return node.dataset.revision
    ? labelByRevision.get(node.dataset.revision)!
    : `family ${node.dataset.family}`;
}

export const keptTags = (view: View) =>
  view.queryAllByTestId('review-family-kept').map((tag) => tag.textContent ?? '');

// The requests of the test in progress, and the stub that answers them.
export const world: {
  requests: Requests;
  answer: (url: URL, asked: string) => unknown;
  tree?: (url: URL) => unknown;
  entries: unknown;
} = {
  requests: { all: [], reviews: () => [] },
  answer: (_url, asked) => (asked === 'task-context' ? task : bodies.get(asked)),
  entries,
};
export const reviewCount = () => world.requests.reviews().length;

// Serve the world to the test in progress, with the triage order and a clean preference store. A test
// replaces `world.answer`, `world.tree` or `world.entries` to change what is served.
export function serveWorld() {
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
  world.answer = (_url, asked) => (asked === 'task-context' ? task : bodies.get(asked));
  world.tree = undefined;
  world.entries = entries;
  world.requests = serve({
    get entries() {
      return world.entries;
    },
    review: (url) => world.answer(url, requested(url)),
    tree: (url) => world.tree?.(url),
  });
}

// Serve the world to every test of the file.
export function installWorld() {
  // A cold render of a real answer under a loaded machine is slower than the library's 5 s default.
  vi.setConfig({ testTimeout: 60000 });
  beforeEach(serveWorld);
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });
}

export async function settled(view: View) {
  await settledOn(view, () => selectedNode(view));
  // The selection's answer has landed in the reading area and focus is on the selected node.
  await waitFor(() => expect(document.activeElement).toBe(selectedNode(view)), WAIT);
}

// The views whose keymap binding has taken a press.
const bound = new WeakSet<View>();

// One key press: wait for its effect (a new selection mark, or the end message), then for the answer,
// then count the review requests it made. A press made before the keymap's binding effect has run is
// ignored, as in a browser, so the first press on a view is made again until it takes effect. Every
// later press is made once: a press the reviewer drops in mid-walk fails the step. Nothing here
// waits for time.
export async function step(view: View, key: typeof J, requestsMade: number) {
  const before = reviewCount();
  const marked = selectedNode(view);
  const said = triageStatus(view);
  const taken = () => selectedNode(view) !== marked || triageStatus(view) !== said;
  if (bound.has(view)) press(key);
  await waitFor(() => {
    if (!bound.has(view) && !taken()) press(key);
    expect(taken(), 'the press takes effect').toBe(true);
  }, WAIT);
  bound.add(view);
  await settled(view);
  await waitFor(
    () => expect(reviewCount() - before, 'review requests of one press').toBe(requestsMade),
    WAIT,
  );
}

// The shared member's review shows both families, in the displayed order `shown`; a click on the
// second family's own row selects that family and (MIK-R39) leaves the first family in the tree as a
// kept family.
export async function clickedSecondFamily(view: View, shown = [R6R_ID, HBJ_ID]) {
  await waitFor(() => expect(families(view)).toEqual(shown), WAIT);
  fireEvent.click(within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open'));
  await waitFor(
    () => expect(view.getByTestId('review-center-family').dataset.family).toBe(HBJ_ID),
    WAIT,
  );
  await settled(view);
}
