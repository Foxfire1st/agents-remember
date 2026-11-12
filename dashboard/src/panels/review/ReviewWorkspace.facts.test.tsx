// The mounted review page over the L37 captures (l86.test-utils.ts) with synthetic movement facts on
// the captured answers: verifies the page's wiring, not classification. The movement owners' states
// stand in the status chip's four-row popover and their raw records in the closed Technical details
// (MIK-R89 v2 rule 6); the one leaf-wide read feeds the tree's code facts and Worklist row.
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewPayload, ReviewResult } from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { captured, catalogue, replyFor, target } from './l86.test-utils';

const taskCapture = captured<ReviewResult>('l86.task.captured.json');
const familyCapture = captured<ReviewResult>('l86.family.captured.json');
const familyOf = (id: string) => catalogue.outline!.families.find((one) => one.id === id)!.selector_id;
// The family the family capture answers, and another changed family.
const ANSWERED = familyOf('FAM-2HBJREC2');
const OTHER = familyOf('FAM-7B6N3Y4Q');

beforeEach(() => {
  localStorage.clear();
  resetCardCachesForTests();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type MovementState = NonNullable<ReviewPayload['sync_movement']>['binding_state'];
function movement(owner: 'sync' | 'external', state: MovementState, epoch = '') {
  const external = owner === 'external';
  const reason = state === 'not-measured'
    ? external ? 'The input was an uncommitted tree.' : 'The sync input was not measured.'
    : state === 'unavailable' ? `The ${owner} movement could not be read.` : undefined;
  return {
    binding_state: state,
    statement: `${epoch}${external ? 'No observed work-branch head.' : 'A sync moved the compared tree.'}`,
    reason: reason === undefined ? undefined : `${epoch}${reason}`,
    moved_identities: state === 'stale' ? [`code-work-branch:${epoch}synthetic-identity`] : [],
    successor_action: external ? 'Review the exact pair.' : 'Review a successor comparison.',
    owner_specific: { channel: owner, epoch, retained: ['owner-specific fact', { count: 2 }] },
  };
}
function movementBody(
  base: ReviewResult,
  sync: MovementState | null | undefined,
  external: MovementState | null | undefined,
  epoch = '',
) {
  const body = structuredClone(base.payload!);
  body.sync_movement = sync == null ? sync : movement('sync', sync, epoch);
  body.external_git_movement = external == null ? external : movement('external', external, epoch);
  return body;
}

interface Served {
  task?: ReviewPayload;
  // The answer of the ANSWERED family's read, held until `release`.
  next?: ReviewPayload;
  failing?: (url: URL) => boolean;
  hold?: (url: URL) => boolean;
}

function replyOf(url: URL, task: ReviewPayload, next: ReviewPayload | undefined): unknown {
  if (url.pathname !== '/api/review/intent') return replyFor(url);
  const selectorId = url.searchParams.get('selectorId');
  if (!selectorId) return { ...taskCapture, payload: task };
  return next && selectorId === ANSWERED ? { ...familyCapture, payload: next } : replyFor(url);
}

// Serves the L37 captures with the task context's answer carrying `task`.
function serve({ task = taskCapture.payload!, next, failing = () => false, hold = () => false }: Served = {}) {
  const held: (() => void)[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      if (failing(url)) throw new Error(`${url.pathname} could not be read.`);
      const response = new Response(JSON.stringify(replyOf(url, task, next)), { status: 200 });
      if (!hold(url)) return response;
      return new Promise<Response>((resolve) => held.push(() => resolve(response)));
    }),
  );
  return { release: () => held.splice(0).forEach((go) => go()) };
}

type View = ReturnType<typeof render>;
const label = (view: View) => view.getByTestId('review-status-label').textContent;

async function mounted(): Promise<View> {
  const view = render(<ReviewSurface {...target} />);
  await view.findByTestId('review-tree');
  // The task context's answer is admitted: its movement records replace "not read".
  await waitFor(() => expect(view.getByTestId('review-movement-facts').textContent).not.toContain('"sync_movement": "not read"'));
  await waitFor(() => expect(label(view)).not.toBe('reading…'));
  return view;
}

async function popoverRows(view: View): Promise<Record<string, string>> {
  fireEvent.click(view.getByTestId('review-status-chip'));
  const popover = await view.findByTestId('review-status-popover');
  expect(popover.textContent).not.toContain('synthetic-identity');
  const rows = within(popover).getAllByTestId('review-status-row') as HTMLTableRowElement[];
  const read = Object.fromEntries(rows.map((row) => [row.cells[0].textContent, row.cells[1].textContent]));
  // The chip toggles its popover: close it, so the next reading opens it rather than relying on an
  // unrelated focus move (a selection's answer landing focus on its tree row) to have closed it.
  fireEvent.keyDown(popover, { key: 'Escape' });
  await waitFor(() => expect(view.queryByTestId('review-status-popover')).toBeNull());
  return read;
}

// The raw record, once, inside the closed provenance disclosure at the bottom of the column.
function rawRecord(view: View) {
  const facts = view.getByTestId('review-movement-facts');
  expect(view.getAllByTestId('review-movement-facts')).toHaveLength(1);
  const details = facts.closest('details')!;
  expect(details.open).toBe(false);
  expect(details.dataset.disclosure).toBe('provenance');
  return JSON.parse(facts.textContent!);
}

const shownValue = (body: ReviewPayload, owner: 'sync_movement' | 'external_git_movement') => {
  const state = body[owner]?.binding_state;
  if (!state || state === 'not-measured') return 'not measured';
  return state === 'stale' ? 'moved' : state;
};

async function expectMovement(view: View, body: ReviewPayload, chip: string) {
  expect(label(view)).toBe(chip);
  const rows = await popoverRows(view);
  expect(rows['Sync movement']).toBe(shownValue(body, 'sync_movement'));
  expect(rows['External Git movement']).toBe(shownValue(body, 'external_git_movement'));
  const record = rawRecord(view);
  const supplied = {
    sync_movement: body.sync_movement ?? 'not measured',
    external_git_movement: body.external_git_movement ?? 'not measured',
  };
  expect({ sync_movement: record.sync_movement, external_git_movement: record.external_git_movement }).toEqual(
    JSON.parse(JSON.stringify(supplied)),
  );
}

it.each([
  ['current sync and current external', 'current', 'current', 'branches: current'],
  ['stale sync and stale external', 'stale', 'stale', 'branches: moved'],
  ['not-measured sync and not-measured external', 'not-measured', 'not-measured', 'branches: not measured'],
  ['unavailable sync and unavailable external', 'unavailable', 'unavailable', 'branches: unavailable'],
  ['mixed stale sync and not-measured external', 'stale', 'not-measured', 'branches: moved'],
  ['sync only', 'stale', null, 'branches: moved'],
  ['external only', undefined, 'unavailable', 'branches: unavailable'],
  ['neither owner', null, undefined, 'branches: not measured'],
] as const)('states both movement owners in the chip and keeps their records for %s', async (_label, sync, external, chip) => {
  const body = movementBody(taskCapture, sync, external);
  serve({ task: body });
  const view = await mounted();
  await expectMovement(view, body, chip);
});

// The chip is the task context's (Failure and Recovery): a subject's read leaves it as it was while
// pending, its answer brings that answer's movement, and its failure stays in the subject's slot.
it("shows the next subject's movement once it answered, and keeps a subject's pending read and failure out of the chip", async () => {
  const previous = movementBody(taskCapture, 'stale', 'not-measured', 'PREVIOUS-OWNER-EPOCH ');
  const next = movementBody(familyCapture, 'current', 'unavailable', 'NEXT-OWNER-EPOCH ');
  const { release } = serve({
    task: previous,
    next,
    hold: (url) => url.searchParams.get('selectorId') === ANSWERED,
    failing: (url) => url.searchParams.get('selectorId') === OTHER,
  });
  const view = await mounted();
  await expectMovement(view, previous, 'branches: moved');
  const chip = view.getByTestId('review-status-chip');
  const familyRow = (id: string) =>
    view.getAllByTestId('review-family-open').find((node) => node.dataset.family === id)!;
  fireEvent.click(familyRow(ANSWERED));
  await waitFor(() => expect(view.getByTestId('review-subject-slot').dataset.slotState).toBe('pending'));
  expect(label(view)).toBe('branches: moved');
  expect(view.getByTestId('review-status-chip')).toBe(chip);
  await act(async () => release());
  await waitFor(() => expect(label(view)).toBe('branches: unavailable'));
  await expectMovement(view, next, 'branches: unavailable');
  expect(view.getByTestId('review-movement-facts').textContent).not.toContain('PREVIOUS-OWNER-EPOCH');
  fireEvent.click(familyRow(OTHER));
  await waitFor(() => expect(view.getByTestId('review-subject-slot').dataset.slotState).toBe('unavailable'));
  expect(label(view)).toBe('branches: unavailable');
  expect(view.getByTestId('review-status-chip')).toBe(chip);
});

it("wires the one leaf-wide read into the tree's code facts, a code-changed row and the Worklist row", async () => {
  serve();
  const view = await mounted();
  const trees = captured<{ code_changes: { invariants: { invariant: string; implementation: string }[] } }>('l86.trees.captured.json');
  const established = new Set(
    trees.code_changes.invariants.filter((fact) => fact.implementation === 'established').map((fact) => fact.invariant),
  );
  const codeOnly = catalogue.outline!.invariants.filter((row) => row.change === 'unchanged' && established.has(row.id));
  // The line counts these invariants (finding 4): their record is unchanged, their code changed.
  expect(codeOnly.length).toBeGreaterThan(1);
  await waitFor(() =>
    expect(view.getByTestId('review-code-facts-status').textContent).toBe(
      `${codeOnly.length} invariants changed only in their code.`,
    ),
  );
  const row = view
    .getAllByTestId('review-family-member-open')
    .find((node) => node.dataset.subjectId === codeOnly[0].selector_id)!;
  expect(row.dataset.changePrimary).toBe('code changed');
  expect(row.getAttribute('aria-label')).toContain('code changed');
  expect(view.getByTestId('review-tree-worklist').getAttribute('aria-label')).toBe('Worklist 518 open');
});

it('separates file attribution from open work and does not call an unmeasured change set empty', async () => {
  const { release } = serve({
    failing: (url) => url.pathname === '/api/review/intent/summary' || url.searchParams.has('lane'),
    hold: (url) => url.pathname === '/api/review/trees' && !['lane', 'invariants', 'file'].some((name) => url.searchParams.has(name)),
  });
  const view = await mounted();
  await waitFor(() =>
    expect(view.getByTestId('review-tree-lane').getAttribute('aria-label')).toBe('Code without invariant unavailable'),
  );
  expect(view.getByTestId('review-tree-worklist').getAttribute('aria-label')).toBe('Worklist …');
  expect(view.getByTestId('review-summary-lane').textContent).toBe('files without invariant: unavailable');
  expect(view.getByTestId('review-tree-counts').textContent).not.toMatch(/\b0 files/);
  expect(view.getByTestId('review-tree-counts').textContent).toContain('file count unavailable');
  expect(view.getByTestId('review-tree-counts').textContent).not.toMatch(/unavailable files/);
  await act(async () => release());
  await waitFor(() =>
    expect(view.getByTestId('review-tree-worklist').getAttribute('aria-label')).toBe('Worklist 518 open'),
  );
  expect(view.getByTestId('review-tree-lane').getAttribute('aria-label')).toBe('Code without invariant unavailable');
});
